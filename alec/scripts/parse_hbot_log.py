#!/usr/bin/env python

""" Parse hbot log """

import argparse
import datetime
import decimal
import re
import time

import pytz
from slacker import Slacker

from alec import config

SLACK = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None

REGEXP = re.compile(r'Executed.*pair: (\w+), amount: (-?\d+\.\d+)'
                    r'.*avg_price: (\d+\.\d+)')
OLD_REGEXP = re.compile(r'Executed.*pair: (\w+), amount: (-?\d+\.\d+), '
                        r'price: (\d+\.\d+)')


def timestamp_to_string(timestamp):
    """ Return a timestamp string with Taipei timezone """
    zone = pytz.timezone('Asia/Taipei')
    local_time = datetime.datetime.fromtimestamp(int(timestamp), zone)
    return (str(local_time.strftime('%Y-%m-%d')),
            str(local_time.strftime('%H:%M:%S')))


def log(text, emoji=None):
    """ Print a log to slack """
    try:
        if SLACK:
            message = text
            if emoji:
                message = emoji + ' ' + text
            SLACK.chat.post_message(config.SLACK_CHANNEL, message)
    except slacker.Error:
        print("Slack api erorr")


def check_state(date=None):
    statistic = {}
    symbols = []
    earn_percent = {}
    for symbol in config.TRADE_HBOT_CONFIG['symbols']:
        symbols.append(symbol[:3])
        profit_percent = (config.TRADE_HBOT_CONFIG['symbols'][symbol]['percent'] **
                          config.TRADE_HBOT_CONFIG['symbols'][symbol]['profit'])
        earn_percent[symbol[:3]] = 1 - (1 / profit_percent)
    for symbol in symbols:
        statistic[symbol] = {'buy': (0, 0), 'sell': (0, 0)}

    day_time = ''
    if not date:
        (date, day_time) = timestamp_to_string(time.time())

    def add(t1, t2):
        return tuple(u + v for u, v in zip(t1, t2))

    with open('log') as f:
        for line in f:
            if not line.startswith(date):
                continue
            m = REGEXP.search(line)
            if not m:
                m = OLD_REGEXP.search(line)
                if not m:
                    continue
            pair, amount, avg_price = m.groups()
            amount = decimal.Decimal(amount)
            avg_price = decimal.Decimal(avg_price)

            symbol = pair[:-3]
            if symbol not in symbols:
                continue

            if amount > 0:
                action = 'buy'
            else:
                action = 'sell'
                amount = -amount

            statistic[symbol][action] = add(
                statistic[symbol][action], (1, amount * avg_price))

    print(date + ' ' + day_time)
    print('SYM: %12s\t%12s\t%12s\t%12s\t%12s' % (
        'BUY_COUNT', 'BUY_SUM', 'SELL_COUNT', 'SELL_SUM', 'EARN'))
    total_buy_count = 0
    total_buy_sum = 0
    total_sell_count = 0
    total_sell_sum = 0
    total_earn = 0
    slack_msg = date + ' ' + day_time + ' '
    for symbol, d in sorted(statistic.items()):
        buy_count, buy_sum = map(float, d['buy'])
        sell_count, sell_sum = map(float, d['sell'])
        earn = sell_sum * earn_percent[symbol]
        total_buy_sum += buy_sum
        total_buy_count += buy_count
        total_sell_sum += sell_sum
        total_sell_count += sell_count
        total_earn += earn
        print('%s: %12d\t%12.6f\t%12d\t%12.6f\t%12.6f' % (
            symbol, buy_count, buy_sum, sell_count, sell_sum, earn))
        slack_msg += '%s: %d/%d, ' % (symbol, buy_count, sell_count)

    print('     %12d\t%12.6f\t%12d\t%12.6f\t%12.6f\n' % (
        total_buy_count, total_buy_sum, total_sell_count, total_sell_sum, total_earn))
    slack_msg += 'Total: %d/%d' % (total_buy_count, total_sell_count)
    log(slack_msg, ':sunny:')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', help='Date to query. EX: 2017-12-21')

    args = parser.parse_args()
    if args.date:
        check_state(args.date)
    else:
        while True:
            check_state()
            time.sleep(600)
