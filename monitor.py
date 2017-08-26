#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import threading
import time

from slacker import Slacker
from ws4py.client import WebSocketBaseClient


_SLACK_TOKEN = 'xoxb-232301319077-M3eA0b6smXVcv3FAwjh1275f'
_SLACK_CHANNEL = '#trading'

_MONITOR_PAIRS = [
    'tBTCUSD',
    'tETHUSD',
    'tBCHUSD',
    'tXMRUSD',
    'tIOTUSD',
    'tXRPUSD',
    'tOMGUSD',
    'tDSHUSD',
    'tEOSUSD',
    'tETCUSD',
    'tZECUSD',
    'tSANUSD'
]

slack = Slacker(_SLACK_TOKEN)


def log(text):
    print(text)
    slack.chat.post_message(_SLACK_CHANNEL, text)


class TickerMonitor(WebSocketBaseClient):
    def __init__(self, symbols, threshold=0.01, window_size=30):
        super(TickerMonitor, self).__init__('wss://api.bitfinex.com/ws/2')
        self._threshold = threshold
        self._window_size = window_size
        self._symbols = symbols
        self._chanId2symbol = {}
        self._prices = {x: [] for x in symbols}
        self._moving_average = {x: 0 for x in symbols}

    def opened(self):
        for symbol in self._symbols:
            self.send(json.dumps({
                'event': 'subscribe',
                'channel': 'ticker',
                'symbol': symbol
            }))
            time.sleep(0.5)

    def received_message(self, msg):
        data = json.loads(msg.data)
        if 'event' in data:
            if data['event'] == 'subscribed':
                self._chanId2symbol[data['chanId']] = data['symbol']
                log('Pair %s at channel %d' % (data['pair'], data['chanId']))

        if type(data) == list:
            chan_id = data[0]

            if chan_id in self._chanId2symbol and data[1] != "hb":
                self.process_tick(self._chanId2symbol[chan_id], data[1])

    def process_tick(self, symbol, data):
        (BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC,
         LAST_PRICE, VOLUME, HIGH, LOW) = data

        new_avg_price = (BID + ASK) / 2.0
        if self._moving_average[symbol] > 0 and len(self._prices[symbol]):
            new_price_delta = new_avg_price - self._prices[symbol][-1]
            ratio = new_price_delta / self._moving_average[symbol]
            if abs(ratio) > self._threshold:
                log('Pair %s price changed, ratio = %.2f' % (symbol, ratio))

        self._prices[symbol].append(new_avg_price)
        self._prices[symbol] = self._prices[symbol][- self._window_size:]
        self._moving_average[symbol] = (sum(self._prices[symbol]) /
                                        len(self._prices[symbol]))
        print('MA[%s]: %.2f' % (symbol, self._moving_average[symbol]))


if __name__ == '__main__':
    log('=' * 30)
    log('TickerMonitor started')
    ws = TickerMonitor(_MONITOR_PAIRS)
    ws.connect()
    ws.run()
