#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import time
from decimal import Decimal

from btfxwss import BtfxWss
from slacker import Slacker

from alec import config
from alec.api import BitfinexClientError
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None


def log(text):
    print(text)
    if slack:
        slack.chat.post_message(config.SLACK_CHANNEL, text)


class RateMonitor(object):
    def __init__(self, symbols):
        self.DEBUG = True
        self._rest_client = bitfinex_v1_rest.FullApi()
        self._wss = None
        self._symbols = symbols
        self._funding = {}
        self._credits = {}
        self._offers = {}
        self._wss = BtfxWss(
            key=config.BFX_API_KEY, secret=config.BFX_API_SECRET)
        self._wss.start()

    def reset(self):
        self._funding = {}
        self._credits = {}
        self._offers = {}

    def connect(self):
        log("Server connected")
        self._wss.authenticate()
        for symbol in self._symbols:
            self._wss.subscribe_to_trades(symbol)
            self._funding['latest_ts'] = 0
            self._funding['latest_rate'] = 0.0

    def run(self):
        while True:
            self.check_system()
            self.check_account_info()
            self.check_trades()
            time.sleep(0.5)

    def check_system(self):
        try:
            server_q = self._wss.opened
            while not server_q.empty():
                server_q.get()
                self.reset()
                self.connect()
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def check_account_info(self):
        try:
            wallets_q = self._wss.wallets
            while not wallets_q.empty():
                self.received_wallets(wallets_q.get())

            wallet_update_q = self._wss.wallet_update
            while not wallet_update_q.empty():
                self.received_wallet_update(wallet_update_q.get())

            credits_q = self._wss.credits
            while not credits_q.empty():
                self.received_credits(credits_q.get())

            offer_new_q = self._wss.offer_new
            while not offer_new_q.empty():
                self.received_offer_new(offer_new_q.get())

            offer_cancel_q = self._wss.offer_cancel
            while not offer_cancel_q.empty():
                self.received_offer_cancel(offer_cancel_q.get())

            credit_close_q = self._wss.credit_close
            while not credit_close_q.empty():
                self.received_credit_close(credit_close_q.get())

            credit_update_q = self._wss.credit_update
            while not credit_update_q.empty():
                self.received_credit_update(credit_update_q.get())

            q = self._wss.offer_update
            while not q.empty():
                print(q.get())
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def check_trades(self):
        for symbol in self._symbols:
            try:
                trades_q = self._wss.trades(symbol)
                while not trades_q.empty():
                    self.received_trades(symbol, trades_q.get())
            except KeyError:
                # KeyError means Btfxwss doesn't get related information yet.
                # It's fine to pass and check in the next time.
                pass

    def received_wallets(self, message):
        # pylint: disable=W0612
        data, ts = message
        for wallet in data[1]:
            self.process_wallet(wallet)

    def received_wallet_update(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_wallet(data[1])

    def received_credits(self, message):
        # pylint: disable=W0612
        data, ts = message
        self._funding['lent'] = 0
        for credit in data[1]:
            self._funding['lent'] += self.process_credit(credit)
        log("Funding usd lent: %f" % self._funding['lent'])

    def received_offer_new(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_offer(data[1])

    def received_offer_cancel(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_offer(data[1])

    def received_credit_close(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_credit(data[1])

    def received_credit_update(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_credit(data[1])

    def received_trades(self, symbol, message):
        # pylint: disable=W0612
        data, ts = message
        if isinstance(data[0], list):
            for transaction in data[0]:
                self.process_public_trade(symbol, transaction)
        elif data[0] == 'fte':
            self.process_public_trade(symbol, data[1])
            self.lend_strategy()

    def process_wallet(self, data):
        if self.DEBUG:
            print(data)
        wallet = bitfinex_v2_rest.Wallet(data)
        if wallet.wallet_type == 'funding' and wallet.currency == 'USD':
            self._funding['total'] = wallet.balance
            log("Funding usd: %f" % self._funding['total'])

    def process_credit(self, data):
        if self.DEBUG:
            print(data)
        credit = bitfinex_v2_rest.Credit(data)
        if credit.symbol == 'fUSD':
            if credit.status == 'ACTIVE':
                self._credits[credit.id] = credit.amount
                return credit.amount
            elif credit.status.startswith('CLOSED'):
                del self._credits[credit.id]
                self._funding['lent'] -= credit.amount
                log('Close a credit, amount: %f' % credit.amount)
                self.lend_strategy()
        return 0

    def process_offer(self, data):
        if self.DEBUG:
            print(data)
        offer = bitfinex_v2_rest.FundingOffer(data)
        if offer.symbol == 'fUSD':
            if offer.status == 'ACTIVE':
                if offer.id not in self._offers:
                    self._offers[offer.id] = offer.amount_orig
                    self._funding['lent'] += offer.amount_orig
                    log('Create an offer, amount: %f' % offer.amount_orig)
            elif offer.status == 'CANCEL':
                self._funding['lent'] -= offer.amount
                log('Cancel an offer, amount: %f' % offer.amount)
                del self._offers[offer.id]
            elif offer.status.startswith('EXECUTED'):
                if offer.id not in self._offers:
                    self._funding['lent'] += offer.amount_orig
                    log('Create an offer, amount: %f' % offer.amount_orig)
                else:
                    del self._offers[offer.id]

    def process_public_trade(self, symbol, data):
        trade = bitfinex_v2_rest.Trade(data)
        log("%s: Timestamp: %s, Rate: %f, Period: %d, Amount: %f" %
            (symbol, time.strftime("%H:%M:%S", time.localtime(trade.time)),
             trade.rate * 100, trade.period, abs(trade.amount)))

        if trade.time > self._funding['latest_ts']:
            self._funding['latest_ts'] = trade.time
            self._funding['latest_rate'] = trade.rate

    def lend_strategy(self):
        if 'total' in self._funding and 'lent' in self._funding:
            available = self._funding['total'] - self._funding['lent']
        else:
            return
        if 'available' not in self._funding or (available !=
                                                self._funding['available']):
            log('total: %f, lent: %f, available: %f' %
                (self._funding['total'], self._funding['lent'], available))
            self._funding['available'] = available

        # Re-write the strategy by yourself
        if available > 50:
            # rate 0 means FRR
            self.new_offer('USD', available - Decimal(0.000001), 0, 2)

    def new_offer(self, currency, amount, rate, period):
        """Create an new offer
        :param rate: Rate per day
        """
        try:
            result = self._rest_client.new_offer(currency, amount, rate,
                                                 period)
        except BitfinexClientError as e:
            log(e.value)
            raise

        log('Create an new %s offer with amount: %f, rate: %f, ' %
            (currency, amount, rate) + 'period: %d' % period)
        self._offers[result['offer_id']] = amount
        self._funding['lent'] += amount
        return result['offer_id']

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        try:
            self._rest_client.cancel_offer(offer_id)
        except BitfinexClientError as e:
            log(e.value)
            raise
        log('Cancel an offer with id: %d' % offer_id)


if __name__ == '__main__':
    log('=' * 30)
    log('RateMonitor started')
    monitor = RateMonitor(config.RATE_MONITOR_SYMBOLS)
    monitor.run()
