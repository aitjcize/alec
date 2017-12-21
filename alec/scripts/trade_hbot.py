#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import datetime
import logging
import math
import time
import os
from decimal import Decimal

import pytz
from btfxwss import BtfxWss
from slacker import Slacker

from alec import config
from alec.api import BitfinexClientError
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

EMOJI_SELL = ':heart:'
EMOJI_BUY = ':blue_heart:'
EMOJI_PARTIAL = ':green_heart:'
EMOJI_DISCONNECT = ':exclamation:'
EMOJI_LIMIT = ':broken_heart:'
EMOJI_STOP_LEND = ':no_entry:'
EMOJI_START_LEND = ':arrows_counterclockwise:'
EMOJI_MOVE_WALLET = ':moneybag:'
EMOJI_ERROR = ':exclamation:'

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None
logger = logging.getLogger(__name__)


def timestamp_to_string(t):
    tz = pytz.timezone('Asia/Taipei')
    local_time = datetime.datetime.fromtimestamp(int(t), tz)
    return str(local_time.strftime('%Y-%m-%d %H:%M:%S'))


def log(text, emoji=None, write_to_file=True):
    timestamp = timestamp_to_string(time.time())
    with open('log', 'a') as f:
        f.write(timestamp + '\t' + text + '\n')
    print(timestamp + '\t' + text)
    try:
        if slack:
            message = text
            if emoji:
                message = emoji + ' ' + text
            slack.chat.post_message(config.SLACK_CHANNEL, message)
    except:
        print("Slack api erorr")


class TradeBot(object):
    ORDER_TIMEOUT = 60
    REMAIN_TIMES = 3
    LAST_BUY_COOLDOWN_SEC = 60
    AMOUNT_DIGIT = 4
    LAST_MOVE_WALLET_COOLDOWN_SEC = 60

    def __init__(self, bot_config, stop_file, init_pairs=[]):
        self.BUY_CURRENCY = bot_config['buy_currency']
        self.RETRY_ORDER_IN_ERROR = bot_config['retry_in_error']
        self.RETRY_ORDER_IN_TIMEOUT = bot_config['retry_in_timeout']
        self._symbols = bot_config['symbols']
        if 'control_lendbot' in bot_config:
            self._lendbot_setting = bot_config['control_lendbot']
        else:
            self._lendbot_setting = {'enable': False}
        self._init_pairs = init_pairs
        self._orders = {}
        self._num_coins = {}
        self._last_price = {}
        self._unconfirm_orders = []
        self._last_add_timestamp = {}
        self._orders_before_disconnect = {}
        self._usd_wallet = {}
        self._received_order_snapshot = False
        self._received_wallet_snapshot = False
        self._lendbot_start = None
        self._lendbot_file = stop_file
        self._last_move_wallet_timestamp = 0
        for symbol in self._symbols:
            self._orders[symbol] = {}
            self._last_price[symbol] = 0
            self._last_add_timestamp[symbol] = 0
        self._wss = BtfxWss(
            key=config.BFX_API_KEY, secret=config.BFX_API_SECRET)
        self._wss.start()
        self._v1_client = bitfinex_v1_rest.FullApi()

    def reset(self):
        self._orders_before_disconnect = self._orders
        self._orders = {}
        self._num_coins = {}
        self._usd_wallet = {}
        self._received_order_snapshot = False
        self._received_wallet_snapshot = False
        for symbol in self._symbols:
            self._orders[symbol] = {}
            self._last_price[symbol] = 0

    def connect(self):
        log("Server connected")
        self._wss.authenticate()
        for pair in self._symbols:
            symbol = 't' + pair
            self._wss.subscribe_to_ticker(symbol)

    def run(self):
        while True:
            self.check_system()
            self.check_account_info()
            self.check_tickers()
            self.check_unused_info()
            self.check_order_timeout()
            self.cancel_buy_orders()
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

            orders_q = self._wss.orders
            while not orders_q.empty():
                self.received_orders(orders_q.get())

            order_new_q = self._wss.order_new
            while not order_new_q.empty():
                self.received_order_new(order_new_q.get())

            order_cancel_q = self._wss.order_cancel
            while not order_cancel_q.empty():
                self.received_order_cancel(order_cancel_q.get())

            notifications_q = self._wss.notifications
            while not notifications_q.empty():
                self.received_notifications(notifications_q.get())
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def check_tickers(self):
        for symbol in self._symbols:
            try:
                trades_q = self._wss.tickers(symbol)
                while not trades_q.empty():
                    self.received_tickers(symbol, trades_q.get())
            except KeyError as e:
                # KeyError means Btfxwss doesn't get related information yet.
                # It's fine to pass and check in the next time.
                pass

    def check_unused_info(self):
        def pop_queue(q):
            while not q.empty():
                q.get()

        try:
            queues = [
                self._wss.credits, self._wss.offer_new, self._wss.offer_cancel,
                self._wss.credit_new, self._wss.credit_close,
                self._wss.credit_update, self._wss.positions,
                self._wss.offer_update, self._wss.order_update,
                self._wss.position_update, self._wss.position_close,
                self._wss.loan_new, self._wss.loan_close, self._wss.loan_update,
                self._wss.unknown]

            for q in queues:
                pop_queue(q)
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def received_wallets(self, message):
        # pylint: disable=W0612
        data, ts = message
        for wallet in data[1]:
            self.process_wallet(wallet)
        self._received_wallet_snapshot = True

    def received_wallet_update(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_wallet(data[1])

    def received_orders(self, message):
        # pylint: disable=W0612
        data, ts = message
        for order in data[1]:
            self.process_order(order)
        self._received_order_snapshot = True
        self.check_orders_during_disconnect()

    def received_order_new(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_order(data[1])

    def received_order_cancel(self, message):
        # pylint: disable=W0612
        data, ts = message
        self.process_order(data[1])

    def received_tickers(self, pair, message):
        # pylint: disable=W0612
        data, ts = message
        if isinstance(data, list):
            for transaction in data:
                self.process_public_ticker(pair, transaction)
        if pair in self._init_pairs and self._received_order_snapshot and (
                self._received_wallet_snapshot):
            self.create_init_orders(pair)

    def received_notifications(self, message):
        data, ts = message
        self.process_notifications(data[1])

    def process_wallet(self, data):
        wallet = bitfinex_v2_rest.Wallet(data)
        if wallet.currency == 'USD':
            self._usd_wallet[wallet.wallet_type] = wallet
            self.check_lendbot()
        if wallet.wallet_type != 'exchange':
            return
        self._num_coins[wallet.currency] = wallet.balance
        pair = wallet.currency + 'USD'
        if pair not in self._symbols:
            return
        config = self._symbols[pair]
        if config['type'] == 'crypto':
            self.check_fixed_crypto_remain_times(pair, wallet.balance, config)
        elif config['type'] == 'usd':
            self.check_fixed_usd_remain_times(pair, wallet.balance, config)
        else:
            log('Pair %s has error config type %s' % (pair, config['type']))

    def process_order(self, data):
        order = bitfinex_v2_rest.Order(data)
        pair = order.symbol[1:]
        if pair not in self._symbols:
            return
        config = self._symbols[pair]
        if order.status == 'ACTIVE':
            if order.id not in self._orders[pair]:
                self._orders[pair][order.id] = order
            self.remove_unconfirm_order(pair, order.price, order.amount_orig)
        elif order.status.startswith('PARTIALLY'):
            if order.id not in self._orders[pair]:
                self._orders[pair][order.id] = order
            self.remove_unconfirm_order(pair, order.price, order.amount_orig)
            self.handle_executed_order(order, config)
        elif order.status == 'CANCELED':
            log('Cancelled an order, pair: %s, amount: %f, price: %f' %
                (pair, order.amount, order.price))
            del self._orders[pair][order.id]
        elif order.status.startswith('EXECUTED'):
            self.handle_executed_order(order, config)
        else:
            log('Error order status: %s' % order.status)

    def process_public_ticker(self, pair, data):
        ticker = bitfinex_v2_rest.TradingTicker([pair] + data)
        self._last_price[pair] = ticker.last_price

    def process_notifications(self, data):
        notification = bitfinex_v2_rest.Notifications(data)
        if notification.type == 'on-req' and notification.status == 'ERROR':
            order = bitfinex_v2_rest.Order(notification.notify_info)
            log('Error new %s order with amount: %f, price: %f: %s' %
                (order.symbol, order.amount, order.price, notification.text))
            currency = order.symbol[1:4]
            pair = order.symbol[1:]
            self.remove_unconfirm_order(pair, order.price, order.amount)
            if self.RETRY_ORDER_IN_ERROR:
                if 'not enough exchange' in notification.text:
                    pass
                elif 'minimum size' in notification.text:
                    pass
                else:
                    log('Need to retry order')
                    self.new_order(pair, order.price, order.amount)
        elif notification.type == 'on-req' and notification.status == 'SUCCESS':
            order = bitfinex_v2_rest.Order(notification.notify_info)
            pair = order.symbol[1:]
            self.remove_unconfirm_order(pair, order.price, order.amount)
        elif notification.type == 'on-req':
            order = bitfinex_v2_rest.Order(notification.notify_info)
            log('Notify new %s order with amount: %f, price: %f: %s' %
                (order.symbol, order.amount, order.price, notification.text))

    def check_order(self, pair, price, amount):
        for order_id, order in self._orders[pair].items():
            if (math.isclose(order.amount_orig, amount, rel_tol=0.01)
                    and math.isclose(order.price, price, rel_tol=0.01)):
                return True
        for order in self._unconfirm_orders:
            if order[0] == pair and math.isclose(order[1], price, rel_tol=0.01) and (
                    math.isclose(order[2], amount, rel_tol=0.01)):
                return True
        # check the value of coins reach the upper limit or not
        if amount > 0 and self.check_upper_limit(pair):
            return True
        return False

    def check_upper_limit(self, pair):
        config = self._symbols[pair]
        if 'limit' not in config or config['limit'] == 0:
            return False
        balance = float(self._num_coins[pair[:3]])
        if config['type'] == 'crypto':
            if balance >= config['amount'] * config['limit']:
                log('Pair %s balance: %f reach limit' % (pair, balance),
                    EMOJI_LIMIT)
                return True
        elif config['type'] == 'usd':
            price = self._last_price[pair] * config['percent'] ** config['profit']
            market_value = 0
            times = 0
            while balance > 0 and times < config['limit']:
                amount = round(config['amount'] / price, 4)
                market_value += price * amount
                balance -= amount
                price *= config['percent']
                times += 1
            if market_value >= config['amount'] * config['limit']:
                log('Pair %s current market value: %f reach limit' % (
                    pair, market_value), EMOJI_LIMIT)
                return True
        return False

    def check_order_timeout(self):
        temp_orders = list(self._unconfirm_orders)
        retry_orders = []
        for order in self._unconfirm_orders:
            if (time.time() - order[3]) > self.ORDER_TIMEOUT:
                log('Order %s with amount: %f, price: %f timeout. Need to retry' %
                    (order[0], order[2], order[1]))
                temp_orders.remove(order)
                retry_orders.append(order)
        self._unconfirm_orders = temp_orders
        if self.RETRY_ORDER_IN_TIMEOUT:
            for order in retry_orders:
                self.new_order(order.symbol[1:], order.price, order.amount)

    def remove_unconfirm_order(self, pair, price, amount):
        temp_orders = self._unconfirm_orders
        for order in self._unconfirm_orders:
            if order[0] == pair and math.isclose(order[1], price, rel_tol=0.01) and (
                    math.isclose(order[2], amount, rel_tol=0.01)):
                temp_orders.remove(order)
        self._unconfirm_orders = temp_orders

    def check_fixed_crypto_remain_times(self, pair, balance, config):
        remain_times = int((balance + Decimal(0.00001)) /
                Decimal(config['amount'])) 
        if remain_times <= self.REMAIN_TIMES:
            log('Pair %s is not enough: %f' % (pair, balance))
            allow_buy = time.time() - self._last_add_timestamp[pair] > (
                    self.LAST_BUY_COOLDOWN_SEC)
            if self.BUY_CURRENCY and allow_buy:
                units = self.REMAIN_TIMES + 2 - remain_times
                self.new_order(pair, 0, config['amount'] * units)
                self._last_add_timestamp[pair] = time.time()
            return True
        return False

    def check_fixed_usd_remain_times(self, pair, balance, config):
        price = self._last_price[pair]
        if price == 0:
            return False
        remain_balance = float(balance)
        remain_times = 0
        while True and remain_times < self.REMAIN_TIMES + 3:
            amount = round(config['amount'] / price, self.AMOUNT_DIGIT)
            if remain_balance > amount:
                remain_times += 1
                remain_balance -= amount
                price *= config['percent']
            else:
                break
        if remain_times <= self.REMAIN_TIMES:
            log('Pair %s is not enough: %f' % (pair, balance))
            allow_buy = time.time() - self._last_add_timestamp[pair] > (
                    self.LAST_BUY_COOLDOWN_SEC)
            if self.BUY_CURRENCY and allow_buy:
                units = self.REMAIN_TIMES + 2 - remain_times
                amount = round(config['amount'] / self._last_price[pair] * units,
                        self.AMOUNT_DIGIT)
                self.new_order(pair, 0, amount)
                self._last_add_timestamp[pair] = time.time()
            return True
        return False

    def handle_executed_order(self, order, config, normal=True):
        pair = order.symbol[1:]
        if normal:
            emoji = EMOJI_BUY if order.amount_orig > 0 else EMOJI_SELL
        else:
            emoji = EMOJI_DISCONNECT
        if not math.isclose(order.amount, 0, rel_tol=0.00001) and normal:
            log('Partially filled an order, pair: %s, amount: %f/%f, price: %f, avg_price: %f' %
                (pair, order.amount, order.amount_orig, order.price, order.price_avg),
                EMOJI_PARTIAL)
            return
        else:
            log('Executed an order, pair: %s, amount: %f, price: %f, avg_price: %f' %
                (pair, order.amount_orig, order.price, order.price_avg), emoji)
        if order.id in self._orders[pair]:
            del self._orders[pair][order.id]
        if config['type'] == 'crypto':
            self.exec_fixed_crypto(order, config)
        elif config['type'] == 'usd':
            self.exec_fixed_usd(order, config)

    def exec_fixed_crypto(self, order, config):
        if not math.isclose(abs(order.amount_orig), config['amount']):
            return
        pair = order.symbol[1:]
        if order.amount_orig > 0:  # buy
            lower_price = order.price / config['percent']
            if not self.check_order(pair, lower_price, config['amount']):
                self.new_order(pair, lower_price, config['amount'])
            upper_price = order.price * (config['percent'] ** config['profit'])
            if not self.check_order(pair, upper_price, -config['amount']):
                self.new_order(pair, upper_price, -config['amount'])
        else:  # sell
            lower_price = order.price / (config['percent'] ** config['profit'])
            if not self.check_order(pair, lower_price, config['amount']):
                self.new_order(pair, lower_price, config['amount'])
            upper_price = order.price * config['percent']
            if not self.check_order(pair, upper_price, -config['amount']):
                self.new_order(pair, upper_price, -config['amount'])

    def exec_fixed_usd(self, order, config):
        if order.amount_orig > 0:
            if not math.isclose(order.amount_orig * Decimal(order.price),
                    config['amount'], rel_tol=0.1):
                return
        else:
            if not math.isclose(-order.amount_orig * Decimal(order.price),
                    config['amount'] * (config['percent'] ** config['profit']),
                    rel_tol=0.1):
                return
        pair = order.symbol[1:]
        if order.amount_orig > 0:  # buy
            lower_price = order.price / config['percent']
            amount = round(config['amount'] / lower_price, self.AMOUNT_DIGIT)
            if not self.check_order(pair, lower_price, amount):
                self.new_order(pair, lower_price, amount)
            amount = round(config['amount'] / order.price, self.AMOUNT_DIGIT)
            upper_price = order.price * (config['percent'] ** config['profit'])
            if not self.check_order(pair, upper_price, -amount):
                self.new_order(pair, upper_price, -amount)
        else:  # sell
            lower_price = order.price / (config['percent'] ** config['profit'])
            amount = round(config['amount'] / lower_price, self.AMOUNT_DIGIT)
            if not self.check_order(pair, lower_price, amount):
                self.new_order(pair, lower_price, amount)
            upper_price = order.price * config['percent']
            ori_price = upper_price / (config['percent'] ** config['profit'])
            amount = round(config['amount'] / ori_price, self.AMOUNT_DIGIT)
            if not self.check_order(pair, upper_price, -amount):
                self.new_order(pair, upper_price, -amount)

    def create_fixed_crypto_init_orders(self, pair, config):
        price = self._last_price[pair]
        lower_price = price / config['percent']
        if not self.check_order(pair, lower_price, config['amount']):
            self.new_order(pair, lower_price, config['amount'])
        upper_price = price * (config['percent'] ** config['profit'])
        if not self.check_order(pair, upper_price, -config['amount']):
            self.new_order(pair, upper_price, -config['amount'])

    def create_fixed_usd_init_orders(self, pair, config):
        price = self._last_price[pair]
        lower_price = price / config['percent']
        amount = round(config['amount'] / lower_price, self.AMOUNT_DIGIT)
        if not self.check_order(pair, lower_price, amount):
            self.new_order(pair, lower_price, amount)
        amount = round(config['amount'] / price, self.AMOUNT_DIGIT)
        upper_price = price * (config['percent'] ** config['profit'])
        if not self.check_order(pair, upper_price, -amount):
            self.new_order(pair, upper_price, -amount)

    def new_order(self, pair, price, amount):
        symbol = 't' + pair
        cid = int(round(time.time() * 1000))
        if price > 0:
            order = {
                    'cid': cid,
                    'type': "EXCHANGE LIMIT",
                    'symbol': symbol,
                    'amount': str(amount),
                    'price': str(price),
                    'hidden': 0
                    }
            self._unconfirm_orders.append([pair, price, amount, time.time()])
        else:
            order = {
                    'cid': cid,
                    'type': "EXCHANGE MARKET",
                    'symbol': symbol,
                    'amount': str(amount),
                    'hidden': 0
                    }
        self._wss.new_order(**order)
        log('Create a new %s order with amount: %f, price: %f' %
            (pair, amount, price))

    def cancel_order(self, order):
        value = {
                'id': order.id,
                }
        self._wss.cancel_order(False, **value)

    def cancel_buy_orders(self):
        cancel_orders = []
        for symbol, orders in self._orders.items():
            if self._last_price[symbol] == 0:
                continue
            percent = self._symbols[symbol]['percent']
            cancel_price = self._last_price[symbol] / (percent ** 5)
            for order_id, order in orders.items():
                if order.amount_orig > 0 and order.price < cancel_price:
                    cancel_orders.append(order)
        for order in cancel_orders:
            self.cancel_order(order)
            symbol = order.symbol[1:]
            del self._orders[symbol][order.id]

    def cancel_all_orders(self, pair):
        for order_id, order in self._orders[pair].items():
            self.cancel_order(order)

    def create_init_orders(self, pair):
        self.cancel_all_orders(pair)
        self._orders[pair] = {}

        config = self._symbols[pair]
        num_coins = self._num_coins[pair[:3]]
        buy_currency = False
        if config['type'] == 'crypto':
            buy_currency = self.check_fixed_crypto_remain_times(pair, num_coins, config)
        elif config['type'] == 'usd':
            buy_currency = self.check_fixed_usd_remain_times(pair, num_coins, config)
        if buy_currency:
            log('Wait for currency ready')
            return

        if config['type'] == 'crypto':
            self.create_fixed_crypto_init_orders(pair, config)
        elif config['type'] == 'usd':
            self.create_fixed_usd_init_orders(pair, config)
        self._init_pairs.remove(pair)

    def check_orders_during_disconnect(self):
        has_executed_order = False
        for symbol, orders in self._orders_before_disconnect.items():
            if symbol not in self._symbols:
                continue
            config = self._symbols[symbol]
            for order_id, order in orders.items():
                if order_id not in self._orders[symbol]:
                    has_executed_order = True
                    log('Missing an order, pair: %s, amount: %f, price: %f' %
                        (symbol, order.amount_orig, order.price), EMOJI_DISCONNECT)
                    self.handle_executed_order(order, config, False)
        if not has_executed_order:
            log('No executed orders during disconnection', EMOJI_DISCONNECT)
        else:
            log('Fixed missing orders during disconnection', EMOJI_DISCONNECT)

    def check_lendbot(self):
        if not self._lendbot_setting['enable']:
            return
        if 'exchange' not in self._usd_wallet or 'funding' not in self._usd_wallet:
            return
        if time.time() - self._last_move_wallet_timestamp < (
                self.LAST_MOVE_WALLET_COOLDOWN_SEC):
            return
        if self._usd_wallet['exchange'].balance > (
                self._usd_wallet['funding'].balance *
                Decimal(self._lendbot_setting['start_threshold'])):
            if os.path.exists(self._lendbot_file):
                os.unlink(self._lendbot_file)
            if self._lendbot_start != True:
                log('Start lendbot', EMOJI_START_LEND)
                self._lendbot_start = True
            log('Exchange: %f, Funding: %f' % (
                self._usd_wallet['exchange'].balance,
                self._usd_wallet['funding'].balance))
            target_amount = (self._usd_wallet['exchange'].balance -
                    Decimal(self._lendbot_setting['target']) *
                    self._usd_wallet['funding'].balance) / (
                    Decimal(self._lendbot_setting['target'] + 1.0))
            if target_amount > 0:
                self.move_wallet(target_amount)
                self._last_move_wallet_timestamp = time.time()
        elif self._usd_wallet['exchange'].balance < (
                self._usd_wallet['funding'].balance *
                Decimal(self._lendbot_setting['stop_threshold'])):
            if self._lendbot_start != False:
                log('Stop lendbot', EMOJI_STOP_LEND)
                self._lendbot_start = False
            open(self._lendbot_file, 'w').close()

    def move_wallet(self, amount):
        try:
            self._v1_client.transfer_wallet('USD', amount, 'exchange', 'deposit')
            log('Transfer %f from exchange to funding' % amount, EMOJI_MOVE_WALLET)
        except BitfinexClientError:
            log('Move wallet error', EMOJI_ERROR)


def main():
    """
    This trading bot is according to the executed orders to setup next actions.
    Please have an order first then run this bot.
    Take fixed amount of crypto currency for example:
    1. Run the bot for the first time:
       ./trade_bot.py --init="BTC OMG"
    2. After initial orders are created, you can run the bot without --init
       parameters.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--init', help='Currencies which need initial orders')
    parser.add_argument('--stop_file',
            default=os.path.expanduser('~/.stop_lendbot'))
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    if not config.TRADE_HBOT_CONFIG:
        print('TRADE_HBOT_CONFIG is empty')
        return

    if 'symbols' not in config.TRADE_HBOT_CONFIG:
        print('TRADE_HBOT_CONFIG miss symbols')
        return

    if not config.TRADE_HBOT_CONFIG['symbols']:
        print('Symbols in TRADE_HBOT_CONFIG is empty')
        return

    if 'control_lendbot' in config.TRADE_HBOT_CONFIG:
        lendbot_setting = config.TRADE_HBOT_CONFIG['control_lendbot']
        if ('target' not in lendbot_setting or
            'start_threshold' not in lendbot_setting or
            'stop_threshold' not in lendbot_setting):
            print('Missing seeting in control_lendbot')
            return
        if (lendbot_setting['start_threshold'] <= lendbot_setting['target'] or
            lendbot_setting['stop_threshold'] >= lendbot_setting['target']):
            print('Threshold should be start > target > stop')
            return

    address = '0x84d6bc1b4ebab26607279834a95c25a19bb8595e'
    print('If you like this bot, welcome to donate ETH to the address:\n' + address)
    print('=' * 30)
    log('TradeBot started')

    symbols = config.TRADE_HBOT_CONFIG['symbols']
    init_pairs = []
    if opts.init:
        for symbol in opts.init.split():
            pair = symbol.upper() + 'USD'
            if pair in symbols:
                init_pairs.append(pair)
            else:
                log("Pair %s is not in symbols config" % pair)
    bot = TradeBot(config.TRADE_HBOT_CONFIG, opts.stop_file, init_pairs)
    bot.run()


if __name__ == '__main__':
    main()
