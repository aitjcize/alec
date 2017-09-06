#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import base64
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


def rate_limit(period):
    last_call_time = {}

    def decorator(func):
        def wrapper(*args, **kargs):
            now = time.time()
            delta = now - last_call_time.get(func, 0)
            last_call_time[func] = now
            if delta < period:
                logger.warning(
                    '%s was called too frequently. delay %s seconds',
                    func.__name__, period - delta)
                time.sleep(period - delta)
            return func(*args, **kargs)

        return wrapper

    return decorator


class PublicApi(object):
    BASE_URL = 'https://api.bitfinex.com/'

    def public_req(self, path, params=None):
        url = self.BASE_URL + path
        logger.debug('public_req %s %s', path, params)

        for i in range(MAX_RETRY):
            resp = requests.get(url, params, verify=True)
            if 500 <= resp.status_code <= 599:
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
                    'balance',
                    'fee',
            ]:
                v = decimal.Decimal(v)

            # time
            elif k in ['timestamp', 'timestamp_created']:
                v = float(v)

            # misc number
            elif k in ['rate', 'period']:
                v = float(v)

            result[k] = v
        return result

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
            resp = requests.post(url, headers=headers, verify=True)
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

    def balances(self):
        return self._normalize(self.auth_req('v1/balances', allow_retry=True))

    def orders(self):
        return self._normalize(self.auth_req('v1/orders', allow_retry=True))

    @rate_limit(60)
    def orders_history(self):
        return self._normalize(
            self.auth_req('v1/orders/hist', allow_retry=True))

    def positions(self):
        return self._normalize(self.auth_req('v1/positions', allow_retry=True))

    def history(self,
                currency,
                since=None,
                until=None,
                limit=None,
                wallet=None):
        """View all of your balance ledger entries."""
        assert self.is_currency(currency)
        assert wallet is None or self.is_wallet(wallet)

        params = dict(currency=currency)
        if since:
            params['since'] = since
        if until:
            params['until'] = until
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
        """View your past deposits/withdrawals."""
        assert self.is_currency(currency)

        params = dict(currency=currency)
        if method:
            params['method'] = method
        if since:
            params['since'] = since
        if until:
            params['until'] = until
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
            params['until'] = until
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
        return self.auth_req('v1/offer/new', body)

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        body = {'offer_id': offer_id}
        return self.auth_req('v1/offer/cancel', body)


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
            for x in result:
                print(x)
        else:
            print(result)
        print()

    print('=' * 10, 'public', '=' * 10)
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
