#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import collections
import datetime
import hashlib
import hmac
import json
import re
import time

import requests

from alec import config
from alec.api import BitfinexClientError


class Timestamp(float):
    def __new__(cls, value):
        if value is None:
            return None
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
                if value not in [0, 1, None]:
                    print('unknown %s.field[%d] has non trivial value (%r)' %
                          (self.__class__.__name__, i, value))
                continue
            self.set(key, value)

    def set(self, key, value):
        if key in ['time', 'created', 'updated', 'opening', 'last_payout']:
            value = Timestamp(value)
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
        'id',           # Offer ID
        'symbol',       # The currency of the offer (fUSD, etc)
        'created',      # Time Stamp when the offer was created
        'updated',      # Time Stamp when the offer was created
        'amount',       # Amount the offer is for
        'amount_orig',  # Amount the offer was entered with originally
        'type',         #
        '',             # None
        '',             # None
        '',             # 0
        'status',       # Offer Status: ACTIVE, EXECUTED, PARTIALLY FILLED,
                        #     CANCELED
        '',             # None
        '',             # None
        '',             # None
        'rate',         # offer rate(day), may change if FRR
        'period',       # Period of the offer
        '',             # 0
        '',             # 0
        '',             # None
        '',             # 0
        'rate_real',    # rate(day)
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

    def is_symbol(self, symbol):
        return bool(re.match(r'^[ft][A-Z]+$', symbol))

    def public_req(self, path, params=None):
        url = self.BASE_URL + path
        resp = requests.get(url, params, verify=True)
        if resp.status_code != 200:
            raise BitfinexClientError(resp.text)
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

    def auth_req(self, path, params={}):
        nonce = self._nonce()
        body = params
        rawBody = json.dumps(body)
        headers = self._headers(path, nonce, rawBody)
        url = self.BASE_URL + path
        resp = requests.post(url, headers=headers, data=rawBody, verify=True)
        if resp.status_code != 200:
            raise BitfinexClientError(resp.text)
        return resp.json()

    def wallets(self):
        """Get account wallets"""
        wallets = self.auth_req('v2/auth/r/wallets')
        for i, wallet in enumerate(wallets):
            wallets[i] = Wallet(wallet)
        return wallets

    def orders(self, symbol=''):
        """Get active orders"""
        assert symbol == '' or self.is_symbol(symbol)
        return self.auth_req('v2/auth/r/orders/%s' % symbol)

    def orders_history(self, symbol=''):
        """Get orders history"""
        assert symbol == '' or self.is_symbol(symbol)
        if symbol:
            return self.auth_req('v2/auth/r/orders/%s/hist' % symbol)
        else:
            return self.auth_req('v2/auth/r/orders/hist')

    def funding_offers(self, symbol=''):
        assert symbol == '' or self.is_symbol(symbol)
        offers = self.auth_req('v2/auth/r/funding/offers/%s' % symbol)
        for i, offer in enumerate(offers):
            offers[i] = FundingOffer(offer)
        return offers

    def funding_offers_history(self, symbol=None):
        if symbol:
            assert self.is_symbol(symbol)
            offers = self.auth_req('v2/auth/r/funding/offers/%s/hist' % symbol)
        else:
            offers = self.auth_req('v2/auth/r/funding/offers/hist')
        for i, offer in enumerate(offers):
            offers[i] = FundingOffer(offer)
        return offers

    def funding_credits(self, symbol):
        credits = self.auth_req('v2/auth/r/funding/credits/%s' % symbol)
        for i, credit in enumerate(credits):
            credits[i] = Credit(credit)
        return credits

    def funding_credits_history(self, symbol, start=None, end=None,
                                limit=None):
        params = dict()
        if start:
            params['start'] = start
        if end:
            params['end'] = end
        if limit:
            params['limit'] = limit
        credits = self.auth_req('v2/auth/r/funding/credits/%s/hist' % symbol,
                                params)
        for i, credit in enumerate(credits):
            credits[i] = Credit(credit)
        return credits

    def funding_trades(self, symbol=None):
        """
        One "offer" may generate several "trades".
        """
        if symbol:
            trades = self.auth_req('v2/auth/r/funding/trades/%s/hist' % symbol)
        else:
            trades = self.auth_req('v2/auth/r/funding/trades/hist')
        for i, trade in enumerate(trades):
            trades[i] = FundingTrade(trade)
        return trades

    def funding_info(self, symbol):
        info = self.auth_req('v2/auth/r/info/funding/%s' % symbol)
        return FundingInfo(info)


class FullApi(AuthedReadonlyApi):
    # TODO: add operations with side-effects
    pass


def example():
    bfx = AuthedReadonlyApi()

    print('candle')
    print(bfx.candles('1m', 'tETHUSD', 'last'))
    for candle in bfx.candles('1m', 'tETHUSD', 'hist', limit=3):
        print(candle)
    print()

    print('wallets')
    for wallet in bfx.wallets():
        print(wallet)
    print()

    print('active offers')
    for o in bfx.funding_offers():
        print(o)
    print()

    print('offers history')
    for o in bfx.funding_offers_history('fUSD'):
        print(o)
    print()

    print('offers credit')
    for c in bfx.funding_credits('fUSD'):
        print(c)
    print()

    print('offers credit history')
    for c in bfx.funding_credits_history('fUSD'):
        print(c)
    print()

    print('funding info')
    print(bfx.funding_info('fUSD'))
    print()

    print('funding trades')
    for x in bfx.funding_trades():
        print(x)
    print()


if __name__ == '__main__':
    example()
