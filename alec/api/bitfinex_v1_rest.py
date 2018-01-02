#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import base64
import calendar
import datetime
import decimal
import hashlib
import hmac
import json
import logging
import time

import requests

from alec import config
from alec.api import BitfinexClientError

logger = logging.getLogger(__name__)

MAX_RETRY = 5

REQUEST_TIMEOUT = 30


def rate_limit(period):
    """Rate limit decorator

    Args:
        period: in seconds
    """
    last_call_time = {}

    if period < 6:
        period = 6

    def decorator(func):
        def wrapper(*args, **kargs):
            now = time.time()
            delta = now - last_call_time.get(func, 0)
            last_call_time[func] = now
            if delta < period:
                logger.debug(
                    '%s was called too frequently. delay %s seconds',
                    func.__name__, period - delta)
                time.sleep(period - delta)
            result = func(*args, **kargs)
            last_call_time[func] = time.time()
            return result

        return wrapper

    return decorator


def totimestamp(v):
    """Convert to timestamp

    Args:
        v: could be unix timestamp (str or number), datetime.date, or
           datetime.datetime

    Returns:
        unix timestamp (str)
    """
    # assume v is in UTC
    assert v
    if isinstance(v, (datetime.date, datetime.datetime)):
        v = (v - v.fromtimestamp(0)).total_seconds()
    return str(v)

class PublicApi(object):
    BASE_URL = 'https://api.bitfinex.com/'

    def public_req(self, path, params=None):
        url = self.BASE_URL + path
        logger.debug('public_req %s %s', path, params)

        for i in range(MAX_RETRY):
            timeout = False
            try:
                resp = requests.get(url, params=params, verify=True,
                                    timeout=REQUEST_TIMEOUT)
            except requests.exceptions.Timeout:
                timeout = True

            if timeout or 500 <= resp.status_code <= 599:
                logger.warning('server error, sleep a while')
                time.sleep(2**i)
                continue
            break

        logger.debug('response %d %s', resp.status_code, resp.content)
        if resp.status_code != 200:
            raise BitfinexClientError('%s %s' % (resp.status_code, resp.text))
        return resp.json()

    def _normalize(self, d):
        if isinstance(d, list):
            return list(map(self._normalize, d))

        result = {}
        for k, v in d.items():
            if isinstance(v, list):
                v = self._normalize(v)

            # decimal
            elif 'amount' in k or '_fees' in k or k in [
                    'available',
                    'balance',
                    'fee',
                    'rate',
                    'avg_execution_price',
                    'last_price',
            ]:
                v = decimal.Decimal(v)

            # time
            elif k in ['timestamp', 'timestamp_created']:
                v = float(v)

            # misc number
            elif k in ['period']:
                v = float(v)

            # In order history or status, price is None for market order.
            elif k in ['price']:
                if v is not None:
                    v = decimal.Decimal(v)

            result[k] = v
        return result

    @rate_limit(60. / 30)
    def ticker(self, symbol):
        return self._normalize(self.public_req('v1/ticker/%s' % symbol))

    @rate_limit(60. / 10)
    def stats(self, symbol):
        return self._normalize(self.public_req('v1/stats/%s' % symbol))

    @rate_limit(60. / 45)
    def funding_book(self, currency, limit_bids=None, limit_asks=None):
        params = {}
        if limit_bids is not None:
            params['limit_bids'] = limit_bids
        if limit_asks is not None:
            params['limit_asks'] = limit_asks
        return self._normalize(
            self.public_req('v1/lendbook/%s' % currency, params))

    @rate_limit(60. / 45)
    def trades(self, symbol, timestamp=None, limit=None):
        params = {}
        if timestamp:
            params['timestamp'] = timestamp
        if limit:
            params['limit_trades'] = limit
        return self._normalize(
            self.public_req('v1/trades/%s' % symbol, params))

    @rate_limit(60. / 60)
    def lends(self, currency, timestamp=None, limit=None):
        """Get a list of the most recent funding data for the given currency:
        total amount provided and Flash Return Rate (in % by 365 days) over
        time.
        """
        params = {}
        if timestamp:
            params['timestamp'] = timestamp
        if limit:
            params['limit_lends'] = limit
        return self._normalize(
            self.public_req('v1/lends/%s' % currency, params))

    @rate_limit(60. / 5)
    def symbols(self):
        return self.public_req('v1/symbols')


# ref: https://bitfinex.readme.io/v1/docs/rest-auth
class AuthedReadonlyApi(PublicApi):
    KEY = config.BFX_API_KEY
    SECRET = config.BFX_API_SECRET.encode('utf-8')

    def _nonce(self):
        return str(int(round(time.time() * 1000)))

    def _headers(self, path, params):
        data = params.copy()
        data.update(request='/' + path, nonce=self._nonce())
        payload = base64.standard_b64encode(json.dumps(data).encode('utf8'))
        h = hmac.new(self.SECRET, payload, hashlib.sha384)
        signature = h.hexdigest()
        return {
            "X-BFX-APIKEY": self.KEY,
            "X-BFX-SIGNATURE": signature,
            "X-BFX-PAYLOAD": payload,
        }

    def auth_req(self, path, params=None, allow_retry=False):
        assert path.startswith('v1/')
        url = self.BASE_URL + path
        logger.debug('auth_req %s %s', path, params)

        for i in range(MAX_RETRY):
            headers = self._headers(path, params or {})
            try:
                resp = requests.post(url, headers=headers, verify=True,
                                     timeout=REQUEST_TIMEOUT)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if allow_retry:
                    logger.warning('connection error, sleep a while')
                    time.sleep(2**i)
                    continue
                raise BitfinexClientError('Connection error')
            if allow_retry:
                if 500 <= resp.status_code <= 599:
                    logger.warning('server error, sleep a while')
                    time.sleep(2**i)
                    continue
                if resp.status_code == 400 and 'Ratelimit' in resp.text:
                    logger.warning('hit rate limit, sleep 20 seconds')
                    time.sleep(20)
                    continue
            break

        logger.debug('response %d %s', resp.status_code, resp.content)
        if resp.status_code != 200:
            raise BitfinexClientError('%s %s' % (resp.status_code, resp.text))
        return resp.json()

    def is_currency(self, currency):
        return currency == currency.upper()

    def is_wallet(self, wallet):
        # funding=deposit
        return wallet in ['trading', 'exchange', 'deposit', 'funding']

    def account_info(self):
        return self._normalize(
            self.auth_req('v1/account_infos', allow_retry=True))

    def account_fees(self):
        return self._normalize(
            self.auth_req('v1/account_fees', allow_retry=True))

    def summary(self):
        return self._normalize(self.auth_req('v1/summary', allow_retry=True))

    def key_info(self):
        return self._normalize(self.auth_req('v1/key_info', allow_retry=True))

    def margin_info(self):
        return self._normalize(
            self.auth_req('v1/margin_infos', allow_retry=True))

    @rate_limit(60. / 20)
    def balances(self):
        return self._normalize(self.auth_req('v1/balances', allow_retry=True))

    def orders(self):
        return self._normalize(self.auth_req('v1/orders', allow_retry=True))

    def order_status(self, id):
        body = {
            'order_id': id,
        }
        return self._normalize(self.auth_req('v1/order/status', body, allow_retry=True))

    def cancel_order(self, id):
        body = {
            'order_id': id,
        }
        return self._normalize(self.auth_req('v1/order/cancel', body, allow_retry=True))

    @rate_limit(60. / 1)
    def orders_history(self):
        return self._normalize(
            self.auth_req('v1/orders/hist', allow_retry=True))

    def positions(self):
        return self._normalize(self.auth_req('v1/positions', allow_retry=True))

    @rate_limit(60. / 20)
    def history(self,
                currency,
                since=None,
                until=None,
                limit=None,
                wallet=None):
        """View all of your balance ledger entries.

        Args:
            since: could be timestamp, date, or datetime. Inclusive.
            until: could be timestamp, date, or datetime. Inclusive.
        """
        assert self.is_currency(currency)
        assert wallet is None or self.is_wallet(wallet)

        params = dict(currency=currency)
        if since:
            params['since'] = totimestamp(since)
        if until:
            params['until'] = totimestamp(until)
        if wallet:
            params['wallet'] = wallet
        if limit:
            params['limit'] = limit
        return self._normalize(
            self.auth_req('v1/history', params, allow_retry=True))

    def movements(self,
                  currency,
                  method=None,
                  since=None,
                  until=None,
                  limit=None):
        """View your past deposits/withdrawals.

        Args:
            since: could be timestamp, date, or datetime.
            until: could be timestamp, date, or datetime.
        """
        assert self.is_currency(currency)

        params = dict(currency=currency)
        if method:
            params['method'] = method
        if since:
            params['since'] = totimestamp(since)
        if until:
            params['until'] = totimestamp(until)
        if limit:
            params['limit'] = limit
        return self._normalize(
            self.auth_req('v1/history/movements', params, allow_retry=True))

    def mytrades(self,
                 symbol,
                 timestamp,
                 until=None,
                 limit_trades=None,
                 reverse=False):
        """View your past trades."""
        params = dict(
            symbol=symbol, timestamp=timestamp, reverse=1 if reverse else 0)
        if until:
            params['until'] = totimestamp(until)
        if limit_trades:
            params['limit_trades'] = limit_trades
        return self._normalize(self.auth_req('v1/mytrades', allow_retry=True))

    def credits(self):
        """View your funds currently taken (active credits)."""
        return self._normalize(self.auth_req('v1/credits', allow_retry=True))

    def offers(self):
        """View your active offers."""
        return self._normalize(self.auth_req('v1/offers', allow_retry=True))

    @rate_limit(60)
    def offers_history(self, limit=None):
        """View your latest inactive offers.

        Limited to last 3 days and 1 request per minute.
        """
        params = {}
        if limit:
            params['limit'] = limit
        return self._normalize(
            self.auth_req('v1/offers/hist', params, allow_retry=True))

    @rate_limit(60. / 45)
    def mytrades_funding(self, symbol):
        """View your past trades."""
        params = dict(symbol=symbol)
        return self._normalize(
            self.auth_req('v1/mytrades_funding', params, allow_retry=True))

    def taken_funds(self):
        """active margin funds"""
        return self._normalize(
            self.auth_req('v1/taken_funds', allow_retry=True))


class FullApi(AuthedReadonlyApi):
    def new_offer(self, currency, amount, rate, period, direction="lend"):
        """Request new offer
        :param rate: Rate per day
        """
        body = {
            'currency': currency,
            'amount': str(amount),
            'rate': str(rate * 365),
            'period': period,
            'direction': direction,
        }
        return self._normalize(self.auth_req('v1/offer/new', body))

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        body = {'offer_id': offer_id}
        return self.auth_req('v1/offer/cancel', body)

    def new_limit_order(self, symbol, amount, price, side):
        body = {
            'symbol': symbol,
            'amount': str(amount),
            'price': str(price),
            'exchange': 'bitfinex',
            'side': side,
            'type': 'exchange limit',
        }
        # Do not retry because when connection error happens, the order might
        # already be created at server side.
        return self._normalize(self.auth_req('v1/order/new', body, allow_retry=False))

    def transfer_wallet(self, currency, amount, wallet_from, wallet_to):
        """Transfer available balances between wallets.
        :param currency: 'USD', 'BTC', or other crypto currencies
        :param amount: Amount to transfer
        :param wallet_from: 'trading', 'deposit', or 'exchange'
        :param wallet_to: 'trading', 'deposit', or 'exchange'
        """
        body = {
            'currency': currency,
            'amount': str(amount),
            'walletfrom': wallet_from,
            'walletto': wallet_to,
        }
        return self.auth_req('v1/transfer', body)

    def new_market_order(self, symbol, amount, side):
        """Creates a new market order."""
        # Fill anything positive for price.
        body = {
            'symbol': symbol,
            'amount': str(amount),
            'price': str(0.001),
            'exchange': 'bitfinex',
            'side': side,
            'type': 'exchange market',
        }
        return self._normalize(self.auth_req('v1/order/new', body, allow_retry=False))


def example():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    bfx = AuthedReadonlyApi()

    def output(title, result):
        print('-' * 5, title, '-' * 5)
        if isinstance(result, list):
            for i, x in enumerate(result):
                print(i, x)
        else:
            print(result)
        print()

    print('=' * 10, 'public', '=' * 10)
    output('trades', bfx.trades('ETHUSD', limit=10))
    output('ticker', bfx.ticker('ETHUSD'))
    output('stats', bfx.stats('ETHUSD'))
    output('funding_book', bfx.funding_book('USD', limit_bids=0)['asks'])
    output('lends', bfx.lends('USD', limit=5))
    output('symbols', bfx.symbols())

    print('=' * 10, 'authed', '=' * 10)
    output('account info', bfx.account_info())
    output('account fees', bfx.account_fees())
    output('summary', bfx.summary())
    output('key info', bfx.key_info())
    output('margin info', bfx.margin_info())

    # balance
    output('balances', bfx.balances())
    output('balance history', bfx.history('USD', wallet='funding'))
    output('movements', bfx.movements('BTC'))

    bfx_full_client = FullApi()

    # Test new order, check order stutus, and cancel it.
    # Make sure you really want to do this before enabling them.
    # new_order_status = bfx_full_client.new_limit_order(symbol='ETCUSD', amount=0.8, price='0.01', side='buy')
    # output('new order', new_order_status)

    # check order status.
    # output('one order', bfx.order_status(id=new_order_status['id']))

    # cancel order
    # output('cancel order', bfx_full_client.cancel_order(new_order_status['id']))


    # Test new market order, check order stutus.
    # Make sure you really want to do this before enabling them.
    # new_order_status = bfx_full_client.new_market_order(symbol='ETCUSD', amount=0.8, side='buy')
    # output('new market order', new_order_status)

    # check order status.
    # output('one market order', bfx.order_status(id=new_order_status['id']))

    # exchange
    output('orders', bfx.orders())
    output('orders history', bfx.orders_history())

    # position
    output('positions', bfx.positions())

    # funding
    output('active credits', bfx.credits())
    output('active offsers', bfx.offers())
    output('offers history', bfx.offers_history())
    output('mytrades funding', bfx.mytrades_funding('USD'))
    output('taken funds', bfx.taken_funds())


if __name__ == '__main__':
    example()
