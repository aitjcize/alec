# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import hmac
import time
import hashlib
import collections
import base64

import requests

from alec import config

class BitfinexClientError(Exception):
    pass

class BitfinexClientV1(object):
    BASE_URL = 'https://api.bitfinex.com'
    KEY = config.BFX_API_KEY
    SECRET = config.BFX_API_SECRET

    def _nonce(self):
        return str(int(round(time.time() * 10000)))

    def _headers(self, path, body):
        body['request'] = path
        body['nonce'] = self._nonce()
        raw_body = json.dumps(body)
        payload = base64.b64encode(bytes(raw_body, 'UTF-8'))
        h = hmac.new(self.SECRET.encode(), payload, hashlib.sha384)
        signature = h.hexdigest()
        return {
            "X-BFX-APIKEY": self.KEY,
            "X-BFX-PAYLOAD": payload,
            "X-BFX-SIGNATURE": signature,
            "content-type": "application/json"
        }

    def req(self, path, params = {}):
        body = params.copy()
        headers = self._headers(path, body)
        url = self.BASE_URL + path
        resp = requests.post(url, headers=headers, verify=True)
        if resp.status_code != 200:
            raise BitfinexClientError(resp.text)
        return resp.json()

    def new_offer(self, currency, amount, rate, period, direction="lend"):
        """Request new offer
        :param rate: Rate per day
        """
        body = {'currency': currency, 'amount': str(amount),
                'rate': str(rate * 365), 'period': period,
                'direction': direction}
        return self.req('/v1/offer/new', body)

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        body = {'offer_id': offer_id}
        return self.req('/v1/offer/cancel', body)
