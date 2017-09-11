#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import datetime
import re
import logging

import alec.api.bitfinex_v1_rest

logger = logging.getLogger(__name__)

# v1 api call funding wallet as 'deposit'
wallet_name = 'Deposit'
funding_fee = 0.15


def get_funding_balance(v1, currency):
    for x in v1.balances():
        if x['currency'].lower() != currency.lower():
            continue
        if x['type'].lower() == wallet_name.lower():
            return x
    assert 0


def timestamp_to_string(t):
    return str(datetime.datetime.utcfromtimestamp(float(t)))


def xirr(flow, period=365 * 86400):
    """Calculates XIRR

    Args:
        flow is list of [ timestamp, cash flow ], where
            timestamp: in seconds
            cash flow: negative means 'in', positive means 'out'

    Returns:
        value of XIRR, annualized effective compounded return rate
    """
    flow = sorted(flow)
    if flow[0][0] == flow[-1][0]:
        return 0
    flow = [(f[0], float(f[1])) for f in flow]

    def pv(rate):
        begin = flow[0][0]
        total = 0.0
        for f in flow:
            d = float(f[0] - begin) / period
            total += f[1] / pow(rate, 1.0 * d)
        return total

    l = 1e-10
    r = 1e10
    assert pv(l) * pv(r) <= 0
    while l + 0.00000001 < r:
        m = (l + r) / 2
        pv_m = pv(m)
        if pv_m == 0:
            return m - 1
        if pv_m < 0:
            r = m
        else:
            l = m
    return l - 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--currency', default='USD')
    parser.add_argument('--debug', action='store_true')
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    v1 = alec.api.bitfinex_v1_rest.AuthedReadonlyApi()

    print('now', datetime.datetime.utcnow())

    balance = get_funding_balance(v1, opts.currency)
    total = balance['amount']
    print('balance: available=%s, total=%s' % (balance['available'], total))
    print()

    print('recent funding money flow')
    flow = []
    current_amount = total
    last_payment = None
    # In reverse order
    # TODO(kcwu): support paging
    for h in v1.history(opts.currency, wallet=wallet_name.lower()):
        m = re.match(r'Transfer.* from wallet (\w+) to (\w+) on wallet (\w+)',
                     h['description'])
        if m:
            amount = h['amount']
            if m.group(3) != wallet_name:
                amount *= -1
            flow.append([h['timestamp'], -amount])
            description = '%s -> %s' % (m.group(1), m.group(2))

        elif re.match(r'Margin Funding Payment on wallet Deposit',
                      h['description']):
            amount = h['amount']
            description = 'funding payment'

            # Ignore cash flow after last payment.
            if last_payment is None:
                last_payment = h['timestamp']
                flow = [[h['timestamp'], current_amount]]
        elif re.match(r'Deposit.* on wallet Deposit', h['description']):
            amount = h['amount']
            flow.append([h['timestamp'], -amount])
            description = '-> %s' % wallet_name

        else:
            assert 0, 'unknown history: %s' % h

        print('%s\t%+15.8f %15.8f\t%s' % (timestamp_to_string(h['timestamp']),
                                          amount, current_amount, description))
        current_amount -= amount
        last_time = h['timestamp']

    # Initial value
    flow.append([last_time - 1, -current_amount])
    print('%s\t%15s %15.8f\t%s' % (timestamp_to_string(last_time), '-',
                                   current_amount, 'initial value'))
    print()

    print('Up to last payment')
    xirr_day = xirr(flow, 86400)
    print('xirr(day)=%.4f%%' % (xirr_day * 100))
    print(' -> plus fee = %.4f%%' % (xirr_day / (1.0 - funding_fee) * 100))
    print('xirr(year)=%.4f%%' % (xirr(flow) * 100))
    print()


if __name__ == '__main__':
    main()
