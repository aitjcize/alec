#!/usr/bin/env python
# -*- coding: utf-8 -*-

import api_config
import hashlib
import hmac
import json
import threading
import time

# from slacker import Slacker
from ws4py.client import WebSocketBaseClient

# _SLACK_TOKEN = 'xoxb-232301319077-M3eA0b6smXVcv3FAwjh1275f'
# _SLACK_CHANNEL = '#trading'

_SYMBOLS = [
    'fUSD'
]

# slack = Slacker(_SLACK_TOKEN)


def log(text):
    print(text)
#     slack.chat.post_message(_SLACK_CHANNEL, text)


class RateMonitor(WebSocketBaseClient):
    WEBSOCKET_URL = 'wss://api.bitfinex.com/ws/2'

    def __init__(self, symbols, threshold=0.01, window_size=30):
        super(RateMonitor, self).__init__(self.WEBSOCKET_URL)
        self._threshold = threshold
        self._window_size = window_size
        self._symbols = symbols
        self._chanId2symbol = {}
        self._prices = {x: [] for x in symbols}
        self._moving_average = {x: 0 for x in symbols}

    def opened(self):
        self.auth_key()
        self.register_symbols()

    def auth_key(self):
        auth_nonce = str(time.time() * 1000)
        auth_payload = 'AUTH' + auth_nonce
        print(auth_payload)
        auth_signature = hmac.new(api_config.API_SECRET, auth_payload, hashlib.sha384).hexdigest()
        print("sha: %s" % auth_signature)
        self.send(json.dumps({
            'event': 'auth',
            'apiKey': api_config.API_KEY,
            'authSig': auth_signature,
            'authPayload': auth_payload,
            'authNonce': auth_nonce
        }))
        time.sleep(0.5)

    def register_symbols(self):
        for symbol in self._symbols:
            self.send(json.dumps({
                'event': 'subscribe',
                'channel': 'trades',
                'symbol': symbol
            }))
            time.sleep(0.5)

    def received_message(self, msg):
        data = json.loads(msg.data)
        print(data)
        if 'event' in data:
            if data['event'] == 'subscribed':
                self._chanId2symbol[data['chanId']] = data['symbol']
                log('Symbol %s at channel %d' % (data['symbol'], data['chanId']))

        if type(data) == list:
            chan_id = data[0]

            if chan_id in self._chanId2symbol:
                if type(data[1]) == list:
                    for transaction in data[1]:
                        self.process_funding_trade(self._chanId2symbol[chan_id],transaction)
                elif data[1] == 'fte':
                    self.process_funding_trade(self._chanId2symbol[chan_id], data[2])

    def process_funding_trade(self, symbol, data):
        (ID, MTS, AMOUNT, RATE, PERIOD) = data
        print("Timestamp: %s, Rate: %f, Period: %d, Amount: %f" % (
            time.strftime("%H:%M:%S", time.localtime(MTS / 1000)), RATE, PERIOD, abs(AMOUNT)))

    def handshake_ok(self):
        pass

if __name__ == '__main__':
    log('=' * 30)
    log('RateMonitor started')
    ws = RateMonitor(_SYMBOLS)
    ws.connect()
    ws.run()
