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


class Candle(BitfinexApiResponse):
    FIELDS = [
        'time',
        'open',
        'close',
        'high',
        'low',
        'volumn',
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

    def _req(self, method, url, params=None, headers=None, allow_retry=False):
        assert method in ['GET', 'POST']
        for i in range(5):
            if method == 'GET':
                resp = requests.get(url, params, headers=headers, verify=True)
            else:
                resp = requests.post(url, params, headers=headers, verify=True)
            if allow_retry and 500 <= resp.status_code <= 599:
                logger.warning('server error, sleep a while')
                time.sleep(2**i)
                continue
            break
        return resp

    def is_symbol(self, symbol):
        return bool(re.match(r'^[ft][A-Z]+$', symbol))

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
            params['start'] = start
        if end:
            params['end'] = end
        if sort:
            params['sort'] = sort

        candles = self.public_req('v2/candles/trade:%s:%s/%s' %
                                  (time_frame, symbol, section), params)
        if section == 'last':
            candles = Candle(candles)
        else:
            for i, candle in enumerate(candles):
                candles[i] = Candle(candle)
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
            resp = requests.post(url, rawBody, headers=headers, verify=True)
            if allow_retry:
                if 500 <= resp.status_code <= 599:
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
        for i, wallet in enumerate(wallets):
            wallets[i] = Wallet(wallet)
        return wallets

    def orders(self, symbol=''):
        """Get active orders"""
        assert symbol == '' or self.is_symbol(symbol)
        return self.auth_req('v2/auth/r/orders/%s' % symbol, allow_retry=True)

    def orders_history(self, symbol=''):
        """Get orders history"""
        assert symbol == '' or self.is_symbol(symbol)
        if symbol:
            result = self.auth_req(
                'v2/auth/r/orders/%s/hist' % symbol, allow_retry=True)
        else:
            result = self.auth_req('v2/auth/r/orders/hist', allow_retry=True)
        return result

    def funding_offers(self, symbol=''):
        assert symbol == '' or self.is_symbol(symbol)
        offers = self.auth_req(
            'v2/auth/r/funding/offers/%s' % symbol, allow_retry=True)
        for i, offer in enumerate(offers):
            offers[i] = FundingOffer(offer)
        return offers

    def funding_offers_history(self, symbol=None):
        if symbol:
            assert self.is_symbol(symbol)
            offers = self.auth_req(
                'v2/auth/r/funding/offers/%s/hist' % symbol, allow_retry=True)
        else:
            offers = self.auth_req(
                'v2/auth/r/funding/offers/hist', allow_retry=True)
        for i, offer in enumerate(offers):
            offers[i] = FundingOffer(offer)
        return offers

    def funding_credits(self, symbol):
        # pylint: disable=W0622
        credits = self.auth_req(
            'v2/auth/r/funding/credits/%s' % symbol, allow_retry=True)
        for i, credit in enumerate(credits):
            credits[i] = Credit(credit)
        return credits

    def funding_credits_history(self, symbol, start=None, end=None,
                                limit=None):
        # pylint: disable=W0622
        params = dict()
        if start:
            params['start'] = start
        if end:
            params['end'] = end
        if limit:
            params['limit'] = limit
        credits = self.auth_req(
            'v2/auth/r/funding/credits/%s/hist' % symbol,
            params,
            allow_retry=True)
        for i, credit in enumerate(credits):
            credits[i] = Credit(credit)
        return credits

    def funding_trades(self, symbol=None):
        """
        One "offer" may generate several "trades".
        """
        if symbol:
            trades = self.auth_req(
                'v2/auth/r/funding/trades/%s/hist' % symbol, allow_retry=True)
        else:
            trades = self.auth_req(
                'v2/auth/r/funding/trades/hist', allow_retry=True)
        for i, trade in enumerate(trades):
            trades[i] = FundingTrade(trade)
        return trades

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
    output('candle(last)', bfx.candles('1m', 'tETHUSD', 'last'))
    output('candle(hist)', bfx.candles('1m', 'tETHUSD', 'hist', limit=3))

    print('=' * 10, 'authed', '=' * 10)
    output('wallets', bfx.wallets())
    output('active offers', bfx.funding_offers())
    output('offers history', bfx.funding_offers_history('fUSD'))
    output('offers credit', bfx.funding_credits('fUSD'))
    output('offers credit history', bfx.funding_credits_history('fUSD'))
    output('funding info', bfx.funding_info('fUSD'))
    output('funding trades', bfx.funding_trades())


if __name__ == '__main__':
    example()
