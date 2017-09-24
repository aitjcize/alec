#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import datetime
import decimal
import re
import logging
import time

import alec.api.bitfinex_v1_rest

logger = logging.getLogger(__name__)

# v1 api call funding wallet as 'deposit'
wallet_name = 'Deposit'
funding_fee = 0.15

secs_per_day = 86400


def get_funding_balance(v1, currency):
    for x in v1.balances():
        if x['currency'].lower() != currency.lower():
            continue
        if x['type'].lower() == wallet_name.lower():
            return x
    assert 0


def timestamp_to_string(t):
    return str(datetime.datetime.utcfromtimestamp(float(t)))


def xirr(flow, period=365 * secs_per_day):
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
    flow = [list(map(decimal.Decimal, f)) for f in flow]

    def pv(rate):
        rate = decimal.Decimal(rate)
        begin = flow[0][0]
        total = 0
        for f in flow:
            d = (f[0] - begin) / period
            total += f[1] / pow(rate, d)
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

    print('Recent funding money flow')
    flow = []
    current_amount = total
    last_time = time.time()
    last_payment = None
    # In reverse order
    # TODO(kcwu): support paging
    payment_by_day = {}
    weighted_amount_by_day = {}
    for h in v1.history(opts.currency, wallet=wallet_name.lower()):
        curr_time = h['timestamp']
        m = re.match(r'Transfer.* from wallet (\w+) to (\w+) on wallet (\w+)',
                     h['description'])
        if m:
            amount = h['amount']
            if m.group(3) != wallet_name:
                amount *= -1
            flow.append([curr_time, -amount])
            description = '%s -> %s' % (m.group(1), m.group(2))

        elif re.match(r'Margin Funding Payment on wallet Deposit',
                      h['description']):
            amount = h['amount']
            description = 'funding payment'
            payment_by_day[int(curr_time / secs_per_day) - 1] = float(amount)

            # Ignore cash flow after last payment.
            if last_payment is None:
                last_payment = curr_time
                flow = [[curr_time, current_amount]]
        elif re.match(r'Deposit.* on wallet Deposit', h['description']):
            amount = h['amount']
            flow.append([curr_time, -amount])
            description = '-> %s' % wallet_name

        else:
            assert 0, 'unknown history: %s' % h

        print('%s\t%+15.8f %15.8f\t%s' % (timestamp_to_string(curr_time),
                                          amount, current_amount, description))
        # between curr_time to last_time, cash in wallet is 'current_amount'
        for curr_day in range(
                int(curr_time / secs_per_day) * secs_per_day,
                int(last_time / secs_per_day + 1) * secs_per_day,
                secs_per_day):
            next_day = curr_day + secs_per_day
            b = max(curr_time, curr_day)
            e = min(next_day, last_time)
            duration = e - b
            if duration < 0:
                continue
            day = int(curr_day / secs_per_day)
            if day not in weighted_amount_by_day:
                weighted_amount_by_day[day] = 0
            weighted_amount_by_day[day] += duration * float(current_amount)

        current_amount -= amount
        last_time = h['timestamp']

    # Initial value
    flow.append([last_time - 1, -current_amount])
    print('%s\t%15s %15.8f\t%s' % (timestamp_to_string(last_time), '-',
                                   current_amount, 'initial value'))
    print()

    print('Effective pay rate per day')
    print('(including idle money)')
    for day, weighted_amount in sorted(weighted_amount_by_day.items()):
        avg_amount = weighted_amount / secs_per_day
        payment = payment_by_day.get(day, 0)
        rate = payment / avg_amount if avg_amount else 0
        print('%s\t%.2f/%.2f=%.5f%%/day (+fee=%.5f%%), %.4f%%/year' %
              (timestamp_to_string(day * secs_per_day), payment, avg_amount,
               rate * 100, rate / (1.0 - funding_fee) * 100, rate * 365 * 100))
    print()

    print('Up to last payment')
    xirr_day = xirr(flow, secs_per_day)
    print('xirr(day)=%.4f%%' % (xirr_day * 100))
    print(' -> plus fee = %.4f%%' % (xirr_day / (1.0 - funding_fee) * 100))
    print('xirr(year)=%.4f%%' % (xirr(flow) * 100))
    print()


if __name__ == '__main__':
    main()
