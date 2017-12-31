#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
import argparse
import datetime
import decimal
import json
import logging
import os
import re
import sqlite3
import textwrap
import time

import alec.api.bitfinex_v1_rest

logger = logging.getLogger(__name__)

secs_per_day = 86400
BASE = 100000000
WALLETS = ['exchange', 'trading', 'funding']


def timestamp_to_string(t):
    return str(datetime.datetime.utcfromtimestamp(float(t)))


def utcdate_to_timestamp(d):
    return (d - datetime.date(1970, 1, 1)).total_seconds()


def parse_opts_time(s, default, end_of_day=False):
    """Parses time string from command line

    Args:
        s: date (2017-01-01) or datetime (2017-01-01 00:00:00) string. Local
           timezone.
        default: default value if `s` is not specified
        end_of_day: if `s` is date string, the time is 00:00:00 or 23:59:59
    """
    if not s:
        return default

    if ' ' in s:
        t = int(time.mktime(time.strptime(s, '%Y-%m-%d %H:%M:%S')))
    else:
        t = int(time.mktime(time.strptime(s, '%Y-%m-%d')))
        if end_of_day:
            t += secs_per_day - 1

    return t


def xirr(flow, period=365 * secs_per_day, approx=None):
    """Calculates XIRR

    Args:
        flow is list of [ timestamp, cash flow ], where
            timestamp: in seconds
            cash flow: negative means 'in', positive means 'out'
        period: rate base in seconds. 1 day means rate/day. 1 year means
                rate/year.
        approx: aggregate multiple flow by timestamp. Only calculate
                approximated value to speed up calculation.

    Returns:
        value of XIRR, annualized effective compounded return rate
    """
    flow = sorted(flow)
    if flow[0][0] == flow[-1][0]:
        return 0

    if len(flow) > 1000 and approx:
        granularity = min(secs_per_day, (flow[-1][0] - flow[0][0]) / 1000)
        print(
            'too many transactions(%d), calculate XIRR approximately (granularity=%ss)'
            % (len(flow), granularity))
        tmp_flow = []
        for i, f in enumerate(flow):
            if i == 0 or i == len(flow) - 1:
                t = f[0]
            else:
                t = max(tmp_flow[-1][0], f[0] // granularity * granularity)

            if i == 0 or t != tmp_flow[-1][0]:
                tmp_flow.append([t, f[1]])
            else:
                tmp_flow[-1][1] += f[1]
        flow = tmp_flow

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


class Intervals(object):
    def __init__(self, data=None):
        self.data = data or []

    def add(self, start, end):
        if end < start:
            return

        new_data = []
        for it in self.data:
            assert it[0] <= it[1]
            if it[1] + 1 >= start and it[0] - 1 <= end:
                # overlap => union
                start, end = min(it[0], start), max(it[1], end)
            else:
                new_data.append(it)

        new_data.append([start, end])
        # I'm lazy to write O(n) code, so call sorted().
        self.data = sorted(new_data)

    def sub(self, start, end):
        assert start <= end
        result = []
        for it in self.data:
            if start > end or it[1] > end:
                break
            if start < it[0]:
                result.append((start, min(it[0] - 1, end)))
            start = max(start, it[1] + 1)

        if start <= end:
            result.append((start, end))

        return Intervals(result)


def normalize_wallet(wallet):
    """Normalize wallet name"""
    wallet = wallet.lower()
    # v1 api calls funding wallet as "deposit" which is confusing.
    if wallet == 'deposit':
        wallet = 'funding'
    assert wallet in WALLETS
    return wallet


class Database(object):
    def __init__(self, fn):
        self.conn = sqlite3.connect(fn)

    def _parse_description(self, desc):
        """Parses transaction description

        Args:
            desc: description

        Returns:
            wallet, kind, pair, price:
                wallet: wallet name
                kind: clasification result
                pair: (kind=exchange) exchange pair
                price: (kind=exchange) exchange price
        """
        m = re.search(r' on wallet (\w+)$', desc)
        assert m
        wallet = normalize_wallet(m.group(1))

        if re.match(r'^Trading fees', desc):
            kind = 'fee'
        elif re.match(r'Crypto Withdrawal fee', desc):
            kind = 'fee'
        elif re.match(r'Unused Margin Funding Fee', desc):
            kind = 'fee'
        elif re.match(r'Settlement @', desc):
            kind = 'fee'
        elif re.match(r'Position.*funding cost', desc):
            kind = 'fee'
        elif re.match(r'Claiming fee for Position claimed', desc):
            kind = 'fee'
        elif re.match(r'Position closed', desc):
            kind = 'fee'
        elif re.match(r'Adjustment Margin Funding Payment', desc) or \
                re.match(r'Margin Funding Payment', desc):
            kind = 'payment'
        elif re.match(r'^\w+ Withdrawal', desc):
            kind = 'withdraw'
        elif re.match(r'^Deposit ', desc):
            kind = 'deposit'
        elif re.match(r'Position claimed', desc):  # ?
            kind = 'deposit'
        elif re.match(r'^Exchange ', desc):
            kind = 'exchange'
        elif re.match(r'^Transfer', desc):
            kind = 'transfer'
        elif re.match(r'Bitcoin Gold snapshot step3', desc):
            kind = 'deposit'  # fork
        else:
            assert 0, 'unknown kind: %r' % desc

        m = re.search(r'Exchange \d+\.\d+ (\w+) for (\w+) @ (\d+\.\d+)', desc)
        if m:
            pair = m.group(1) + m.group(2)
            price = decimal.Decimal(m.group(3))
        else:
            pair, price = None, None

        return wallet, kind, pair, price

    def insert(self, entry):
        print('insert', entry)
        wallet, kind, pair, price = self._parse_description(
            entry['description'])
        c = self.conn.cursor()
        c.execute('''
        INSERT OR IGNORE INTO wallet
            (currency, timestamp, wallet, amount, balance, description,
             kind, pair, price)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (entry['currency'], entry['timestamp'], wallet,
              str(entry['amount'] * BASE), str(entry['balance'] * BASE),
              entry['description'], kind, pair,
              str(price * BASE) if price is not None else None))

    def query(self, sql, binding=()):
        c = self.conn.cursor()
        c.execute(sql, binding)
        return c

    def load_meta(self, key):
        row = self.query('SELECT value FROM meta WHERE key = ?',
                         ('fetch_info', )).fetchone()
        if row:
            return row[0]
        else:
            return None

    def save_meta(self, key, value):
        self.query('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
                   (key, value))

    def commit(self):
        self.conn.commit()


def parse_filter(s):
    if not s:
        return []
    result = []
    for x in s.split(','):
        if x in WALLETS or re.match(r'^[A-Z][A-Z][A-Z]$', x):
            result.append(x)
            continue
        elif ':' in x:
            currency, wallet = x.split(':', 1)
            if re.match(r'^[A-Z][A-Z][A-Z]$', currency) and wallet in WALLETS:
                result.append(x)
                continue
        assert 0, 'unknown filter format: ' + x
    return result


class Wallet(object):
    def __init__(self, opts, v1, db=None, now=None):
        self.opts = opts
        self.v1 = v1
        self.db = db or Database(opts.dbname)
        self.now = now or int(time.time())

        self.cached_price = {}
        self.fetched = {}
        self.include = parse_filter(opts.include)
        self.exclude = parse_filter(opts.exclude)

    def _fetch_one_currency(self, currency, since, until):
        limit = 1000
        while since <= until:
            logger.info('fetch history %s [%s, %s]', currency, since, until)
            result = self.v1.history(
                currency, since=since, until=until, limit=limit)
            if since == until:
                # assume there won't be too many records in one second.
                assert len(result) < limit

            seen = set()
            for entry in result:
                key = json.dumps(
                    {k: str(v)
                     for k, v in entry.items()}, sort_keys=True)
                assert key not in seen, 'two identical transactions in one second'
                seen.add(key)
                self.db.insert(entry)

            if len(result) < limit:
                self.fetched[currency].add(since, until)
                until = since - 1
            else:
                self.fetched[currency].add(result[-1]['timestamp'] + 1, until)
                # not "+1" because there may be more records in the same second
                until = result[-1]['timestamp']
            self._save_fetch_info()
            self.db.commit()

    def fetch(self, balances, since, until):
        currencies = balances.keys()

        fetched = self._load_fetch_info()
        for currency in currencies:
            self.fetched[currency] = Intervals(fetched.get(currency))

        for currency in sorted(currencies):
            logger.info('fetch %s %s', currency, '-' * 30)
            to_fetch = self.fetched[currency].sub(since, until)
            for it in to_fetch.data:
                self._fetch_one_currency(currency, it[0], it[1])

    def _load_fetch_info(self):
        data = self.db.load_meta('fetch_info')
        if data:
            return json.loads(data)
        return {}

    def _save_fetch_info(self):
        data = json.dumps(
            {k: v.data
             for k, v in self.fetched.items()},
            indent=2,
            sort_keys=True)
        self.db.save_meta('fetch_info', data)

    def query_price(self, pair):
        if pair not in self.cached_price:
            logging.debug('qeury current price for %s' % pair)
            ticker = self.v1.ticker(pair)
            self.cached_price[pair] = ticker['last_price']

        return self.cached_price[pair]

    def show_balance(self):
        print('Balances:')
        # XXX the balance may be wrong if more than two records have the same
        # timestamp. For example, fee and its corresponding operation.
        for wallet, currency, timestamp, balance in self.db.query('''
            SELECT wallet, currency, max(timestamp), balance
                FROM wallet
                GROUP BY wallet, currency
                ORDER BY wallet, currency
            ''').fetchall():
            if self.determine_inside((currency, wallet)) != 'inside':
                continue

            amount = decimal.Decimal(balance) / BASE
            if currency == 'USD':
                price = 1
            else:
                price = self.query_price(currency + 'USD')
            print('%10s %s %15.8f = %8.2f USD' % (wallet, currency, amount,
                                                  amount * price))
        print()

    def estimate_price(self, currency, timestamp):
        # TODO(kcwu): to estimate price via indirect pair
        pair = currency + 'USD'
        row = self.db.query('''
            SELECT timestamp, price
                FROM wallet
                WHERE currency = ? AND pair = ?
                ORDER BY abs(timestamp - ?)
                LIMIT 1
            ''', (currency, pair, timestamp)).fetchone()

        assert self.now >= timestamp
        if row is None or timestamp == self.now or (self.now - row[0] > secs_per_day
                           and self.now - timestamp < abs(row[0] - timestamp)):
            return self.now, self.query_price(pair)

        return int(row[0]), decimal.Decimal(row[1]) / BASE

    def get_currencies(self):
        for row in self.db.query('''
            SELECT currency FROM wallet GROUP BY currency
                ''').fetchall():
            yield row[0]

    def get_balance(self, currency, wallet, timestamp):
        row = self.db.query('''
            SELECT balance
                FROM wallet
                WHERE wallet = ? AND currency = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
                ''', (wallet, currency, timestamp)).fetchone()
        if row is None:
            return 0
        return decimal.Decimal(row[0]) / BASE

    def determine_inside(self, p):
        if isinstance(p, str):
            assert p in ('inside', 'outside')
            return p
        currency, wallet = p
        assert len(currency) == 3, currency
        assert wallet in WALLETS

        for e in self.exclude:
            if ':' in e:
                if currency + ':' + wallet == e:
                    return 'outside'
            elif e in WALLETS:
                if wallet == e:
                    return 'outside'
            else:
                if currency == e:
                    return 'outside'

        for e in self.include:
            if ':' in e:
                if currency + ':' + wallet == e:
                    return 'inside'
            elif e in WALLETS:
                if wallet == e:
                    return 'inside'
            else:
                if currency == e:
                    return 'inside'

        if self.include:
            return 'outside'
        else:
            return 'inside'

    def estimate_balance(self, timestamp):
        total_balance = 0
        warning = []
        for currency in self.get_currencies():
            balance = 0
            for wallet in WALLETS:
                if self.determine_inside((currency, wallet)) == 'outside':
                    continue
                balance += self.get_balance(currency, wallet, timestamp)
            if balance == 0:
                continue
            if currency == 'USD':
                total_balance += balance
                continue

            price_time, price = self.estimate_price(currency, timestamp)
            if abs(price_time - timestamp) >= secs_per_day:
                warning.append('%s(%dd)' %
                               (currency,
                                abs(price_time - timestamp) // secs_per_day))
            total_balance += balance * price
        if warning:
            logger.warning('price may be off: %s', ', '.join(warning))
        return total_balance

    def calculate_xirr_flow(self, since, until, view=False):
        since_balance = self.estimate_balance(since)
        until_balance = self.estimate_balance(until)
        flow = []
        flow.append((since, -since_balance))
        flow.append((until, until_balance))

        for row in self.db.query('''
            SELECT timestamp, currency, wallet, amount, description, kind, pair, price
                FROM wallet
                WHERE ? <= timestamp AND timestamp <= ?
                ORDER BY timestamp
            ''', (since, until)):
            timestamp, currency, wallet, amount, desc, kind, pair, price = row
            amount = decimal.Decimal(amount) / BASE
            if price:
                price = decimal.Decimal(price) / BASE

            if kind in ('fee', 'payment'):
                src = dst = currency, wallet
            elif kind == 'withdraw':
                src = currency, wallet
                dst = 'outside'
                amount *= -1
            elif kind == 'deposit':
                src = 'outside'
                dst = currency, wallet
            elif kind == 'transfer':
                m = re.search(r'from wallet (\w+) to (\w+)', desc)
                assert m
                src = currency, normalize_wallet(m.group(1))
                dst = currency, normalize_wallet(m.group(2))
                if amount < 0:  # avoid count twice
                    continue
            elif kind == 'exchange':
                src = pair[:3], 'exchange'
                dst = pair[-3:], 'exchange'
                if amount < 0:  # avoid count twice
                    continue
                if src[0] == currency:
                    src, dst = dst, src
            else:
                assert 0

            src = self.determine_inside(src)
            dst = self.determine_inside(dst)
            if src == dst:
                continue

            if currency != 'USD':
                if pair != currency + 'USD':
                    price_time, price = self.estimate_price(
                        currency, timestamp)
                amount *= price
            if dst == 'inside':
                amount *= -1

            if view:
                print('%s %s %8s %10.6f %s' % (timestamp_to_string(timestamp),
                                               currency, wallet, amount, desc))

            flow.append((timestamp, amount))
        return sorted(flow)

    def report(self, since, until, view=False):
        self.show_balance()

        flow = self.calculate_xirr_flow(since, until, view=view)

        since_balance = -flow[0][1]
        until_balance = flow[-1][1]
        print('since_balance', since_balance)
        print('until_balance', until_balance)

        print('len(flow)', len(flow))
        print('money flow in', sum(-f[1] for f in flow if f[1] < 0))
        print('money flow out', sum(f[1] for f in flow if f[1] > 0))
        print()

        print('Calculate xirr:')
        print('xirr(day)=%.4f%%' % (xirr(flow, period=secs_per_day, approx=True) * 100))
        print('xirr(year)=%.4f%%' % (xirr(flow, approx=True) * 100))
        print()

        print('Compare buy and hold (approx):')
        fake_flow = []
        for currency in self.get_currencies():
            if currency == 'USD':
                continue
            if self.determine_inside((currency, 'exchange')) != 'inside':
                continue

            start_time, start_price = self.estimate_price(currency, since)
            end_time, end_price = self.estimate_price(currency, until)
            if abs(start_time - end_time) < secs_per_day:
                print('(not enough price data for %s, ignore)' % currency)
                continue
            assert start_time < end_time
            fake_flow.append((start_time, -1))
            fake_flow.append((end_time, end_price / start_price))

        if fake_flow:
            print('xirr(day)=%.4f%%' % (xirr(fake_flow, period=secs_per_day) * 100))
            print('xirr(year)=%.4f%%' % (xirr(fake_flow) * 100))
        else:
            print('No crypto currency selected. skip')


def get_balances(v1):
    """
    Returns:
        dict: [currency][wallet] -> amount
    """
    balances = {}
    for x in v1.balances():
        wallet = normalize_wallet(x['type'])
        currency = x['currency'].upper()
        if currency not in balances:
            balances[currency] = {}
        balances[currency][wallet] = x['amount']
    return balances


def create_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent('''\
        Calculate bitfinex wallet stats

        Initial setup, run
          $ sqlite3 wallet.db < wallet.sql

        Then run "./wallet_stats.py --fetch" to download transaction history.

        Example command line:
            To calculate lend_bot.py performance
            $ ./wallet_stats.py --include USD:funding

            To calculate hbot or jbot performance; exclude some currency
            not managed by bot.
            $ ./wallet_stats.py --since 2017-12-16 --exclude BTC,BTG,ETH,funding

            If your your lend_bot cowork with hbot, to calculate their performance together
            $ ./wallet_stats.py --since 2017-12-20 --include exchange,USD:funding


        '''))
    parser.add_argument(
        '--dbname',
        default='wallet.db',
        help='filename of database (default: %(default)s)')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument(
        '--fetch', action='store_true', help='fetch trsanction history')
    parser.add_argument(
        '--view', action='store_true', help='print included transaction flow')
    parser.add_argument(
        '--since',
        help='start time (inclusive), default is beginning of account. '
        'Format: "YYYY-mm-dd HH:MM:SS" or "YYYY-mm-dd"')
    parser.add_argument(
        '--until',
        help='end time (inclusive), default is now. '
        'Format: "YYYY-mm-dd HH:MM:SS" or "YYYY-mm-dd"')
    parser.add_argument(
        '--include',
        help='What included in calculation. '
        'Format: {currency}:{wallet}, {currency}, or {wallet}. '
        'Multiple values are separated by comma. '
        'Example: "exchange,USD:funding"')
    parser.add_argument(
        '--exclude',
        help='What excluded in calculation. '
        'Format: {currency}:{wallet}, {currency}, or {wallet}. '
        'Multiple values are separated by comma. '
        'Example: "exchange,USD:funding"')
    return parser


def main():
    parser = create_parser()
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    v1 = alec.api.bitfinex_v1_rest.AuthedReadonlyApi()

    now = int(time.time())
    print('now', datetime.datetime.utcnow())

    until = min((now // 600 - 1) * 600,
                parse_opts_time(opts.until, now, end_of_day=True))
    since = parse_opts_time(opts.since, 0)
    assert since <= until

    wallet = Wallet(opts, v1, now=now)

    balances = get_balances(v1)
    currencies = balances.keys()
    if opts.fetch:
        wallet.fetch(balances, since, until)

    wallet.report(since, until, view=opts.view)


if __name__ == '__main__':
    main()
