#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import datetime
import logging
import os
import time

import pytz
from slacker import Slacker

from alec import config
from alec.api import BitfinexClientError
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None
logger = logging.getLogger(__name__)

def timestamp_to_string(t):
    tz = pytz.timezone('Asia/Taipei')
    local_time = datetime.datetime.fromtimestamp(int(t), tz)
    return str(local_time.strftime('%Y-%m-%d %H:%M:%S'))


def log(text):
    print(timestamp_to_string(time.time()) + '\t' + text)
    if slack:
        slack.chat.post_message(config.SLACK_CHANNEL, text)


class LendBot(object):
    NORMAL_INTERVAL = 60

    def __init__(self, currency, stop_file):
        self._v1_client = bitfinex_v1_rest.FullApi()
        self._v2_client = bitfinex_v2_rest.PublicApi()
        self._currency = currency
        self._wallet = []
        self._credits = []
        self._offers = []
        self._trades = []
        self._lendbot_file = stop_file

    def run(self):
        while True:
            try:
                self.get_account_info()
                self.get_public_trades()
                sleep_time = self.routine()
                time.sleep(sleep_time)
            except BitfinexClientError as e:
                log(str(e))
                raise
            except Exception as e:
                log(str(e))
                raise

    def routine(self):
        if os.path.exists(self._lendbot_file):
            return self.stop_lendbot()
        else:
            return self.lend_strategy()

    def stop_lendbot(self):
        for offer in self._offers:
            if offer['currency'] != self._currency:
                continue
            self.cancel_offer(offer)

        available = self._wallet['available']
        if available > 0:
            self.move_wallet(available)
        return self.NORMAL_INTERVAL

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
        log('Create an new %s offer with amount: %f, rate: %f, period: %d' %
            (currency, amount, rate, period))

    def cancel_offer(self, offer):
        """Cancel an offer"""
        self._v1_client.cancel_offer(offer['id'])
        log('Cancel an offer with amount: %f, rate: %f, period: %d' % (
            offer['remaining_amount'], offer['rate'] / 365, offer['period']))

    def move_wallet(self, amount):
        self._v1_client.transfer_wallet('USD', amount, 'deposit', 'exchange')
        log('Transfer %f from funding to exchange' % amount)


def main():
    """ This bot may raise exception. Suggest to run the bot by the command:
    while [ 1=1 ]; do ./lend_bot.py ; sleep 3; done
    """

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--stop_file', default=os.path.expanduser('~/.stop_lendbot'))
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    print('=' * 30)
    log('LendBot started')
    monitor = LendBot('USD', opts.stop_file)
    monitor.run()


if __name__ == '__main__':
    main()
