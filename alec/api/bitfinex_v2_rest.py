#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import datetime
import decimal
import hashlib
import hmac
import json
import logging
import re
import time

import requests

from alec import config
from alec.api import BitfinexClientError

logger = logging.getLogger(__name__)

MAX_RETRY = 5

REQUEST_TIMEOUT = 30


class Timestamp(float):
    def __new__(cls, value):
        # v2 always use millisecond
        return float.__new__(cls, value / 1000.0)

    def __repr__(self):
        return str(datetime.datetime.utcfromtimestamp(float(self)))


class BitfinexApiResponse(object):
    FIELDS = []

    def __init__(self, values):
        assert len(self.FIELDS) == len(values), (len(self.FIELDS), len(values))
        for i, key in enumerate(self.FIELDS):
            value = values[i]
            if not key:  # unknown
                if value not in [0, None]:
                    print('unknown %s.field[%d] has non trivial value (%r)' %
                          (self.__class__.__name__, i, value))
                continue
            self.set(key, value)

    def set(self, key, value):
        if value is None:
            pass
        elif key in ['time', 'created', 'updated', 'opening', 'last_payout']:
            value = Timestamp(value)
        elif key in ['amount', 'amount_orig', 'balance', 'rate', 'rate_real']:
            # `value` is (inaccurate) float but we don't want the exact
            # inaccurate value. Cast to str to get approximated decimal string.
            value = decimal.Decimal(str(value))
        setattr(self, key, value)

    def __repr__(self):
        fields = []
        for key in self.FIELDS:
            if key.startswith('_'):
                continue
            if key and hasattr(self, key):
                fields.append('%s=%r' % (key, getattr(self, key)))
        s = '<%s(%s)>' % (self.__class__.__name__, ', '.join(fields))
        return s


class Trade(BitfinexApiResponse):
    FIELDS = [
        'id',
        'time',
        'amount',
        'rate',
        'period',
    ]


class TradingBook(BitfinexApiResponse):
    FIELDS = [
        'price',
        'count',
        'amount',
    ]


class FundingBook(BitfinexApiResponse):
    FIELDS = [
        'rate',
        'period',
        'count',
        'amount',
    ]


class TradingTicker(BitfinexApiResponse):
    FIELDS = [
        'symbol',
        'bid',
        'bid_size',
        'ask',
        'ask_size',
        'daily_change',
        'daily_change_perc',
        'last_price',
        'volume',
        'high',
        'low',
    ]


class FundingTicker(BitfinexApiResponse):
    FIELDS = [
        'symbol',
        'frr',
        'bid',
        'bid_period',
        'bid_size',
        'ask',
        'ask_period',
        'ask_size',
        'daily_change',
        'daily_change_perc',
        'last_price',
        'volume',
        'high',
        'low',
    ]


class Ticker(BitfinexApiResponse):
    FIELDS = [
        'symbol',
        'frr',
        'bid',
        'bid_period',
        'bid_size',
        'ask',
        'ask_period',
        'ask_size',
        'daily_change',
        'daily_change_perc',
        'last_price',
        'volume',
        'high',
        'low',
    ]


class Candle(BitfinexApiResponse):
    FIELDS = [
        'time',
        'open',
        'close',
        'high',
        'low',
        'volumn',
    ]


class Order(BitfinexApiResponse):
    FIELDS = [
        'id',
        'gid',
        'cid',
        'symbol',
        'mts_create',
        'mts_update',
        'amount',
        'amount_orig',
        'type',
        'type_prev',
        '',
        '',
        'flags',
        'status',
        '',
        '',
        'price',
        'price_avg',
        'price_trailing',
        'price_aux_limit',
        '',
        '',
        '',
        'notify',
        'hidden',
        'placed_id',
    ]


class Position(BitfinexApiResponse):
    FIELDS = [
        'symbol',
        'status',
        'amount',
        'base_price',
        'margin_funding',
        'margin_funding_type',
        'pl',
        'pl_prec',
        'price_liq',
        'leverage',
    ]


class Wallet(BitfinexApiResponse):
    FIELDS = [
        'wallet_type',
        'currency',
        'balance',
        'unsettled_interest',
        'balance_available',
    ]


class FundingOffer(BitfinexApiResponse):
    FIELDS = [
        'id',  # Offer ID
        'symbol',  # The currency of the offer (fUSD, etc)
        'created',  # Time Stamp when the offer was created
        'updated',  # Time Stamp when the offer was created
        'amount',  # Amount the offer is for
        'amount_orig',  # Amount the offer was entered with originally
        'type',
        '',
        '',
        '',
        'status',  # Offer Status: ACTIVE, EXECUTED, PARTIALLY FILLED, CANCELED
        '',
        '',
        '',
        'rate',  # offer rate(day), may change if FRR
        'period',  # Period of the offer
        '',
        '',
        '',
        '',
        'rate_real',  # rate(day)
    ]


class Credit(BitfinexApiResponse):
    FIELDS = [
        'id',
        'symbol',
        'side',
        'created',
        'updated',
        'amount',
        'flags',
        'status',
        '',
        '',
        '',
        'rate',  # day rate, 0 means FRR
        'period',
        'opening',
        'last_payout',
        'notify',
        'hidden',
        'insure',
        'renew',
        'rate_real',  # day rate
        'no_close',
        'position_pair',
    ]


class FundingInfo(BitfinexApiResponse):
    FIELDS = [
        '_sym',
        'symbol',
        'info',
        # 'yield_loan',
        # 'yield_lend',
        # 'duration_loan',
        # 'duration_lend',
    ]


class FundingTrade(BitfinexApiResponse):
    FIELDS = [
        'id',
        'symbol',
        'created',
        'offer_id',
        'amount',
        'rate',
        'period',
        'maker',
    ]


class PublicApi(object):
    BASE_URL = 'https://api.bitfinex.com/'

    def is_trading_symbol(self, symbol):
        return bool(re.match(r'^t[A-Z]+$', symbol))

    def is_funding_symbol(self, symbol):
        return bool(re.match(r'^f[A-Z]+$', symbol))

    def is_symbol(self, symbol):
        return self.is_trading_symbol(symbol) or self.is_funding_symbol(symbol)

    def public_req(self, path, params=None):
        url = self.BASE_URL + path
        logger.debug('public_req %s %s', path, params)

        for i in range(MAX_RETRY):
            timeout = False
            try:
                resp = requests.get(url, params, verify=True,
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

    def tickers(self, *symbols):
        assert all(map(self.is_symbol, symbols))
        tickers = self.public_req('v2/tickers?symbols=%s' % ','.join(symbols))
        for i, ticker in enumerate(tickers):
            if self.is_trading_symbol(symbols[i]):
                tickers[i] = TradingTicker(ticker)
            else:
                tickers[i] = FundingTicker(ticker)
        return tickers

    def ticker(self, symbol):
        assert self.is_symbol(symbol)
        ticker = self.public_req('v2/ticker/%s' % symbol)
        # Prepend symbol to make it compatible with tickers()
        ticker = [symbol] + ticker
        if self.is_trading_symbol(symbol):
            ticker = TradingTicker(ticker)
        else:
            ticker = FundingTicker(ticker)
        return ticker

    def trades(self, symbol, limit=None, start=None, end=None,
               new_to_old=True):
        params = {}
        if limit:
            params['limit'] = limit
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if new_to_old:
            params['sort'] = -1 if new_to_old else 1
        trades = self.public_req('v2/trades/%s/hist' % symbol, params)
        return list(map(Trade, trades))

    def book(self, symbol, precision, limit=25):
        assert self.is_symbol(symbol)
        assert precision in ['P0', 'P1', 'P2', 'P3', 'R0']
        assert limit in [25, 100]
        params = dict(len=limit)
        book = self.public_req('v2/book/%s/%s' % (symbol, precision), params)
        if self.is_trading_symbol(symbol):
            book = list(map(TradingBook, book))
        else:
            book = list(map(FundingBook, book))
        return book

    def candles(self,
                time_frame,
                symbol,
                section,
                limit=None,
                start=None,
                end=None,
                sort=None):
        assert time_frame in [
            '1m', '5m', '15m', '30m', '1h', '3h', '6h', '12h', '1D', '7D',
            '14D', '1M'
        ]
        assert section in ['last', 'hist']

        params = {}
        if limit:
            params['limit'] = limit
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if sort:
            params['sort'] = sort

        candles = self.public_req('v2/candles/trade:%s:%s/%s' %
                                  (time_frame, symbol, section), params)
        if section == 'last':
            candles = Candle(candles)
        else:
            candles = list(map(Candle, candles))
        return candles


# dereived from https://bitfinex.readme.io/v2/docs/rest-auth
class AuthedReadonlyApi(PublicApi):
    KEY = config.BFX_API_KEY
    SECRET = config.BFX_API_SECRET.encode('utf8')

    def _nonce(self):
        return str(int(round(time.time() * 1000)))

    def _headers(self, path, nonce, body):
        signature = "/api/" + path + nonce + body
        h = hmac.new(self.SECRET, signature.encode('utf8'), hashlib.sha384)
        signature = h.hexdigest()
        return {
            "bfx-nonce": nonce,
            "bfx-apikey": self.KEY,
            "bfx-signature": signature,
            "content-type": "application/json"
        }

    def auth_req(self, path, params=None, allow_retry=False):
        logger.debug('auth_req %s %s', path, params)
        body = params or {}
        rawBody = json.dumps(body)
        url = self.BASE_URL + path

        for i in range(MAX_RETRY):
            nonce = self._nonce()
            headers = self._headers(path, nonce, rawBody)
            try:
                resp = requests.post(url, rawBody, headers=headers,
                                     verify=True, timeout=REQUEST_TIMEOUT)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                if allow_retry:
                    logger.warning('connection error, sleep a while')
                    time.sleep(2**i)
                    continue
                raise
            if allow_retry:
                if resp.status_code == 500:
                    print(resp.status_code, resp.text)
                    result = resp.json()
                    if result[0] == 'error' and result[1] in [
                            11000,  # ERR_READY
                            20060,  # maintenance
                    ]:
                        logger.warning('server error, sleep a while')
                        time.sleep(2**i)
                        continue
                    if result[0] == 'error' and result[1] == 11010:
                        logger.warning('hit rate limit, sleep 20 seconds')
                        time.sleep(20)
                        continue
                elif 501 <= resp.status_code <= 599:
                    print(resp.status_code, resp.text)
                    logger.warning('server error, sleep a while')
                    time.sleep(2**i)
                    continue
            break

        logger.debug('response %d %s', resp.status_code, resp.content)
        if resp.status_code != 200:
            raise BitfinexClientError('%s %s' % (resp.status_code, resp.text))
        return resp.json()

    def wallets(self):
        """Get account wallets"""
        wallets = self.auth_req('v2/auth/r/wallets', allow_retry=True)
        return list(map(Wallet, wallets))

    def orders(self, symbol=''):
        """Get active orders"""
        assert symbol == '' or self.is_symbol(symbol)
        return self.auth_req('v2/auth/r/orders/%s' % symbol, allow_retry=True)

    def orders_history(self, symbol=None, start=None, end=None, limit=None):
        """Get orders history"""
        assert symbol is None or self.is_symbol(symbol)
        params = {}
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if limit:
            params['limit'] = limit
        if symbol:
            result = self.auth_req(
                'v2/auth/r/orders/%s/hist' % symbol, params, allow_retry=True)
        else:
            result = self.auth_req(
                'v2/auth/r/orders/hist', params, allow_retry=True)
        return result

    def funding_offers(self, symbol=''):
        assert symbol == '' or self.is_funding_symbol(symbol)
        offers = self.auth_req(
            'v2/auth/r/funding/offers/%s' % symbol, allow_retry=True)
        return list(map(FundingOffer, offers))

    def funding_offers_history(self,
                               symbol=None,
                               start=None,
                               end=None,
                               limit=None):
        assert symbol == '' or self.is_funding_symbol(symbol)
        params = {}
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if limit:
            params['limit'] = limit
        if symbol:
            assert self.is_symbol(symbol)
            offers = self.auth_req(
                'v2/auth/r/funding/offers/%s/hist' % symbol,
                params,
                allow_retry=True)
        else:
            offers = self.auth_req(
                'v2/auth/r/funding/offers/hist', params, allow_retry=True)
        return list(map(FundingOffer, offers))

    def funding_credits(self, symbol):
        # pylint: disable=W0622
        assert self.is_funding_symbol(symbol)
        credits = self.auth_req(
            'v2/auth/r/funding/credits/%s' % symbol, allow_retry=True)
        return list(map(Credit, credits))

    def funding_credits_history(self, symbol, start=None, end=None,
                                limit=None):
        # pylint: disable=W0622
        assert self.is_funding_symbol(symbol)
        assert limit is None or limit <= 25  # ERR_PARAMS if limit > 25
        params = {}
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if limit:
            params['limit'] = limit
        credits = self.auth_req(
            'v2/auth/r/funding/credits/%s/hist' % symbol,
            params,
            allow_retry=True)
        return list(map(Credit, credits))

    def funding_trades(self, symbol=None, start=None, end=None, limit=None):
        """
        One "offer" may generate several "trades".
        """
        assert symbol is None or self.is_funding_symbol(symbol)
        assert limit is None or limit <= 250  # ERR_PARAMS if limit > 250
        params = {}
        if start:
            params['start'] = start * 1000
        if end:
            params['end'] = end * 1000
        if limit:
            params['limit'] = limit
        if symbol:
            trades = self.auth_req(
                'v2/auth/r/funding/trades/%s/hist' % symbol,
                params,
                allow_retry=True)
        else:
            trades = self.auth_req(
                'v2/auth/r/funding/trades/hist', params, allow_retry=True)
        return list(map(FundingTrade, trades))

    def funding_info(self, symbol):
        info = self.auth_req(
            'v2/auth/r/info/funding/%s' % symbol, allow_retry=True)
        return FundingInfo(info)


class FullApi(AuthedReadonlyApi):
    # TODO: add operations with side-effects
    pass


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
    output('tickers', bfx.tickers('tETHUSD', 'fUSD'))
    output('ticker', bfx.ticker('tBTCUSD'))
    output('candle(last)', bfx.candles('1m', 'tETHUSD', 'last'))
    output('candle(hist)', bfx.candles('1m', 'tETHUSD', 'hist', limit=3))
    output('trades', bfx.trades('fUSD'))
    output('book (tBTCUSD)', bfx.book('tBTCUSD', 'P0', limit=25)[:3])
    output('book (fUSD)', bfx.book('fUSD', 'P0', limit=25)[:3])

    print('=' * 10, 'authed', '=' * 10)
    output('wallets', bfx.wallets())
    output('active offers', bfx.funding_offers())
    output('offers history', bfx.funding_offers_history('fUSD'))
    output('offers credit', bfx.funding_credits('fUSD'))
    output('offers credit history', bfx.funding_credits_history('fUSD'))
    output('funding info', bfx.funding_info('fUSD'))
    output('funding trades', bfx.funding_trades(limit=5))


if __name__ == '__main__':
    example()
