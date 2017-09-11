#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import time

from slacker import Slacker
from ws4py.client import WebSocketBaseClient

from alec import config

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None


def log(text):
    print(text)
    if slack:
        slack.chat.post_message(config.SLACK_CHANNEL, text)


class TickerMonitor(WebSocketBaseClient):
    def __init__(self, symbols, threshold=0.01, window_size=30):
        super(TickerMonitor, self).__init__(config.BFX_WS_ENDPOINT)
        self._threshold = threshold
        self._window_size = window_size
        self._symbols = symbols
        self._chanId2symbol = {}
        self._prices = {x: [] for x in symbols}
        self._moving_average = {x: 0 for x in symbols}

    def opened(self):
        for symbol in self._symbols:
            self.send(
                json.dumps({
                    'event': 'subscribe',
                    'channel': 'ticker',
                    'symbol': symbol
                }))
            time.sleep(0.5)

    def received_message(self, message):
        data = json.loads(message.data)
        if 'event' in data:
            if data['event'] == 'subscribed':
                self._chanId2symbol[data['chanId']] = data['symbol']

        if isinstance(data, list):
            chan_id = data[0]

            if chan_id in self._chanId2symbol and data[1] != "hb":
                self.process_tick(self._chanId2symbol[chan_id], data[1])

    def process_tick(self, symbol, data):
        # pylint: disable=W0612
        (BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC,
         LAST_PRICE, VOLUME, HIGH, LOW) = data

        new_avg_price = (BID + ASK) / 2.0
        if self._moving_average[symbol] > 0 and self._prices[symbol]:
            new_price_delta = new_avg_price - self._prices[symbol][-1]
            ratio = new_price_delta / self._moving_average[symbol]
            if abs(ratio) > self._threshold:
                arrow = ':arrow_up:' if ratio > 0 else ':arrow_down:'
                change_pct = '{0:+.02f}%'.format(ratio * 100.0)
                log('%s %s @ %.3f, %s' %
                    (arrow, symbol[1:], LAST_PRICE, change_pct))

        self._prices[symbol].append(new_avg_price)
        self._prices[symbol] = self._prices[symbol][-self._window_size:]
        self._moving_average[symbol] = (
            sum(self._prices[symbol]) / len(self._prices[symbol]))
        print('MA[%s]: %.2f' % (symbol, self._moving_average[symbol]))


if __name__ == '__main__':
    while True:
        ws = TickerMonitor(config.PRICE_MONITOR_PAIRS,
                           config.PRICE_MONITOR_THRESHOLD,
                           config.PRICE_MONITOR_WINDOW_SIZE)
        ws.connect()
        ws.run()
