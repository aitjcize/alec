#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import logging
import time

from slacker import Slacker

from alec import config
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None
logger = logging.getLogger(__name__)


def log(text):
    print(time.strftime("%H:%M:%S", time.localtime()) + ' ' + text)
    if slack:
        slack.chat.post_message(config.SLACK_CHANNEL, text)


class LendBot(object):
    NORMAL_INTERVAL = 60

    def __init__(self, currency):
        self._v1_client = bitfinex_v1_rest.FullApi()
        self._v2_client = bitfinex_v2_rest.PublicApi()
        self._currency = currency
        self._wallet = []
        self._credits = []
        self._offers = []
        self._trades = []

    def run(self):
        while True:
            self.get_account_info()
            self.get_public_trades()
            sleep_time = self.lend_strategy()
            time.sleep(sleep_time)

    def get_account_info(self):
        for wallet in self._v1_client.balances():
            if wallet['currency'].upper() == self._currency and (
                    wallet['type'] == 'deposit'):
                self._wallet = wallet
                break
        self._credits = self._v1_client.credits()
        self._offers = self._v1_client.offers()

    def get_public_trades(self):
        self._trades = self._v2_client.trades('f' + self._currency)

    def lend_strategy(self):
        logger.debug('wallet: %s', self._wallet)
        logger.debug('credit: %s', self._credits)
        logger.debug('offer: %s', self._offers)
        logger.debug('trades: %s', self._trades)
        # Re-write the strategy by yourself
        available = self._wallet['available']
        log('Availble: %f' % available)
        if available >= 50:
            # rate 0 means FRR
            self.new_offer(self._currency, available, 0, 2)
        return self.NORMAL_INTERVAL

    def new_offer(self, currency, amount, rate, period):
        """Create an new offer
        :param rate: Rate in percentage per day
        """
        self._v1_client.new_offer(currency, amount, rate, period)
        log('Create an new %s offer with amount: %f, rate: %f, period: %d' % (
            currency, amount, rate, period))

    def cancel_offer(self, offer_id):
        """Cancel an offer"""
        self._v1_client.cancel_offer(offer_id)
        log('Cancel an offer with id: %d' % offer_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    print('=' * 30)
    log('LendBot started')
    monitor = LendBot('USD')
    monitor.run()

if __name__ == '__main__':
    main()
