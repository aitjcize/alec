#!/usr/bin/env python3.4
# -*- coding: utf-8 -*-

from __future__ import print_function

import time

from slacker import Slacker

from alec import config
from api import rest_client
from btfxwss import BtfxWss

_SYMBOLS = [
    'fUSD'
]

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None


def log(text):
    print(text)
    if slack:
        slack.chat.post_message(config.SLACK_CHANNEL, text)


class RateMonitor():
    def __init__(self, symbols):
        self._symbols = symbols
        self._latest_ts = 0
        self._latest_rate = 0.0
        self.connect()
        self.test = 0

    def connect(self):
        self._wss = BtfxWss(key=config.BFX_API_KEY,
                            secret=config.BFX_API_SECRET)
        self._wss.start()
        time.sleep(5)
        self._wss.authenticate()
        for symbol in self._symbols:
            self._wss.subscribe_to_trades(symbol)

    def run(self):
        while True:
            self.check_account_info()
            self.check_trades()
            time.sleep(0.5)

    def check_account_info(self):
        try:
            wallets_q = self._wss.wallets
            while not wallets_q.empty():
                self.received_wallets(wallets_q.get())
            credits_q = self._wss.credits
            while not credits_q.empty():
                self.received_credits(credits_q.get())
            # Just pop the following queues since we use REST v1 for offers
            q = self._wss.offer_new
            while not q.empty():
                q.get()
            q = self._wss.offer_update
            while not q.empty():
                q.get()
            q = self._wss.offer_cancel
            while not q.empty():
                q.get()
        except KeyError:
            pass

    def check_trades(self):
        for symbol in self._symbols:
            try:
                trades_q = self._wss.trades(symbol)
                while not trades_q.empty():
                    self.received_trades(symbol, trades_q.get())
            except KeyError:
                pass

    def received_wallets(self, message):
        data, ts = message
        for wallet in data[1]:
            (TYPE, SYMBOL, AMOUNT, INTEREST, AVAILABLE) = wallet
            if TYPE == 'funding' and SYMBOL == 'USD':
                self._funding_usd = AMOUNT
        log("Funding usd: %f" % self._funding_usd)

    def received_credits(self, message):
        data, ts = message
        self._funding_usd_lent = 0
        for credit in data[1]:
            used_fields = credit[0:10]
            (ID, SYMBOL, SIDE, MTS_CREATE, MTS_UPDATE, AMOUNT, FLAGS, STATUS,
                    RATE, PERIOD) = used_fields
            if SYMBOL == 'fUSD':
                self._funding_usd_lent += AMOUNT
        log("Funding usd lent: %f" % self._funding_usd_lent)

    def received_trades(self, symbol, message):
        data, ts = message
        if isinstance(data[0], list):
            for transaction in data[0]:
                self.process_funding_trade(symbol, transaction)
        elif data[0] == 'fte':
            self.process_funding_trade(symbol, data[1])
            self.lend_strategy()

    def process_funding_trade(self, symbol, data):
        # pylint: disable=W0612
        (ID, MTS, AMOUNT, RATE, PERIOD) = data
        log("%s: Timestamp: %s, Rate: %f, Period: %d, Amount: %f" % (
            symbol, time.strftime("%H:%M:%S", time.localtime(MTS / 1000)),
            RATE * 100, PERIOD, abs(AMOUNT)))

        if MTS > self._latest_ts:
            self._latest_ts = MTS
            self._latest_rate = RATE

    def lend_strategy(self):
        # Re-write the strategy by yourself
        if self.test == 0:
            offer_id = self.new_offer('USD', 0.5, 1, 2)
            time.sleep(5)
            self.cancel_offer(offer_id)
            self.test = 1

    def new_offer(self, currency, amount, rate, period):
        """Create an new offer
        :param rate: Rate per day
        """
        try:
            result = rest_client.new_offer(currency, amount, rate, period)
            log('Create an new %s offer with amount: %f, rate: %f, period: %d' % (
                currency, amount, rate, period))
        except BitfinexClientError:
            log(result)
        return result['offer_id']

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        try:
            result = rest_client.cancel_offer(offer_id)
            log('Cancel an offer with id: %d' % offer_id)
        except BitfinexClientError:
            log(result)

if __name__ == '__main__':
    log('=' * 30)
    log('RateMonitor started')
    monitor = RateMonitor(_SYMBOLS)
    monitor.run()
