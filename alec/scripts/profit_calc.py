#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import csv
import datetime
import sys


USD = 'USD'
POSITION_TEXT = 'Position closed'
FEE_TEXT = 'Trading fees'
FUNDCOST_TEXT = 'funding cost'
FUNDPAY_TEXT = 'Funding Payment'

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'


class ProfitCalc(object):
    def __init__(self, csvfile):
        self._csvfile = csvfile

    def print_amount(self, amount, desc=None):
        if desc:
            desc = ' (%s)' % desc
        print('%s%.2f%s' % (' ' if amount >= 0.0 else '',
                            amount,
                            desc or ''))

    def process(self):
        dates = []
        positions = []
        fees = []
        fundcosts = []
        fundpays = []

        with open(self._csvfile, 'r') as f:
            data = f.read()

        reader = csv.reader(data.splitlines())
        for row in reader:
            currency = row[0]
            if currency != USD:
                continue

            desc = row[2]
            amount = float(row[3])
            datestr = row[5]

            dates.append(datetime.datetime.strptime(datestr, DATETIME_FORMAT))
            if POSITION_TEXT in desc:
                positions.append(amount)
            elif FEE_TEXT in desc:
                fees.append(amount)
            elif FUNDCOST_TEXT in desc:
                fundcosts.append(amount)
            elif FUNDPAY_TEXT in desc:
                fundpays.append(amount)

        total_margin = sum(positions)
        total_fees = sum(fees)
        total_fundcosts = sum(fundcosts)
        total_funding = sum(fundpays)

        print('Profit from %s to %s:' % (min(dates), max(dates)))
        self.print_amount(total_margin, 'margin earnings')
        self.print_amount(total_fees, 'trading fees')
        self.print_amount(total_fundcosts, 'funding costs')
        self.print_amount(total_funding, 'funding earnings')
        print('=%.2f' % (total_margin + total_fees + total_fundcosts +
                         total_funding))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: %s ledger-downloaded-from-bitfinex.csv' % sys.argv[0])
        sys.exit()

    ProfitCalc(sys.argv[1]).process()
