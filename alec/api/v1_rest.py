#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import base64
import collections
import hashlib
import hmac
import json
import time

import requests

from alec import config
from alec.api import BitfinexClientError


class PublicApi(object):
    BASE_URL = 'https://api.bitfinex.com/'

    def public_req(self, path, params=None):
        url = self.BASE_URL + path
        resp = requests.get(url, params, verify=True)
        if resp.status_code != 200:
            raise BitfinexClientError(resp.text)
        return resp.json()

    def _normalize(self, d):
        if isinstance(d, list):
            return map(self._normalize, d)

        result = {}
        for k, v in d.items():
            # decimal
            if k in [
                    'amount', 'balance', 'fee_amount', 'fee',
                    'original_amount', 'remaining_amount', 'executed_amount',
                    'amount_lent', 'amount_used'
            ]:
                v = float(v)

            # time
            if k in ['timestamp', 'timestamp_created']:
                v = float(v)

            # misc number
            if k in ['rate', 'period']:
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

    def auth_req(self, path, params={}):
        assert path.startswith('v1/')
        headers = self._headers(path, params)
        url = self.BASE_URL + path
        resp = requests.post(url, headers=headers, verify=True)
        if resp.status_code != 200:
            raise BitfinexClientError(resp.text)
        return resp.json()

    def is_currency(self, currency):
        return currency == currency.upper()

    def is_wallet(self, wallet):
        # funding=deposit
        return wallet in ['trading', 'exchange', 'deposit', 'funding']

    def balances(self):
        return self._normalize(self.auth_req('v1/balances'))

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
        return self._normalize(self.auth_req('v1/history', params))

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
        if limit:
            params['limit'] = limit
        assert not (since or until), 'not implemented'
        return self._normalize(self.auth_req('v1/history/movements', params))

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
        return self._normalize(self.auth_req('v1/mytrades'))

    def credits(self):
        """View your funds currently taken (active credits)."""
        return self._normalize(self.auth_req('v1/credits'))

    def offers(self):
        """View your active offers."""
        return self._normalize(self.auth_req('v1/offers'))

    def offers_history(self, limit=None):
        """View your latest inactive offers.

        Limited to last 3 days and 1 request per minute.
        """
        return self._normalize(self.auth_req('v1/offers/hist'))

    def mytrades_funding(self, symbol):
        """View your past trades."""
        params = dict(symbol=symbol)
        return self._normalize(self.auth_req('v1/mytrades_funding', params))

    def taken_funds(self):
        """active margin funds"""
        return self._normalize(self.auth_req('v1/taken_funds'))


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
    bfx = AuthedReadonlyApi()

    print('lends')
    for x in bfx.lends('USD'):
        print(x)
    print()

    print('symbols')
    print(bfx.symbols())
    print()

    print('balances')
    for x in bfx.balances():
        print(x)
    print()

    print('history')
    for x in bfx.history('USD', wallet='funding'):
        print(x)
    print()


if __name__ == '__main__':
    example()
