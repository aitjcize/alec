#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Trade Bot """

from __future__ import print_function

import argparse
import datetime
import logging
import math
import time
import os
from decimal import Decimal

import pytz
import requests
import slacker
from btfxwss import BtfxWss
from slacker import Slacker

from alec import config
from alec.api import BitfinexClientError
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

LOG_ERROR = 0
LOG_INFO = 1
LOG_VERBOSE = 2
LOG_DEBUG = 3

LOG_LEVEL = LOG_INFO

EMOJI_SELL = ':heart:'
EMOJI_BUY = ':blue_heart:'
EMOJI_DISCONNECT = ':exclamation:'
EMOJI_LIMIT = ':broken_heart:'
EMOJI_STOP_LEND = ':no_entry:'
EMOJI_START_LEND = ':arrows_counterclockwise:'
EMOJI_MOVE_WALLET = ':moneybag:'
EMOJI_ERROR = ':exclamation:'

SLACK = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None


def timestamp_to_string(timestamp):
    """ Return a timestamp string with Taipei timezone """
    zone = pytz.timezone('Asia/Taipei')
    local_time = datetime.datetime.fromtimestamp(int(timestamp), zone)
    return str(local_time.strftime('%Y-%m-%d %H:%M:%S'))


def log(level, text, emoji=None):
    """ Print a log to file, console, and slack """
    if level > LOG_LEVEL:
        return
    timestamp = timestamp_to_string(time.time())
    with open('log', 'a') as log_fd:
        log_fd.write(timestamp + '\t' + text + '\n')
    print(timestamp + '\t' + text)
    try:
        if SLACK:
            message = text
            if level == LOG_ERROR:
                emoji = EMOJI_ERROR
            if emoji:
                message = emoji + ' ' + text
            SLACK.chat.post_message(config.SLACK_CHANNEL, message)
    except slacker.Error:
        print('Slack api erorr')
    except requests.exceptions.HTTPError:
        print('Slack time out')


class WebSocketApi(object):
    """ Wrapper to use BtfxWss. """
    def __init__(self, symbols=None, callbacks=None):
        """
        Args:
            symbols: A list used to subscribe tickers.
            callbacks: A list of functions to handle events:
                'reset',
                'process_wallet',
                'process_order',
                'process_tick',
                'process_notification'
        """
        required_callbacks = [
            'reset',
            'process_wallet',
            'process_order',
            'process_tick',
            'process_notification'
        ]
        if not symbols or not callbacks:
            log(LOG_ERROR, 'Require parameters symbols and callbacks')
            return
        for callback in required_callbacks:
            if callback not in callbacks:
                log(LOG_ERROR, 'Require %s callback function' % callback)
                return

        self._tick_symbols = symbols
        self._callbacks = callbacks

        self._received_order_snapshot = False
        self._received_wallet_snapshot = False
        self._wss = BtfxWss(
            key=config.BFX_API_KEY, secret=config.BFX_API_SECRET)
        self._wss.start()

    def __connect(self):
        """
        Reset data and subscribe tick data after connect to server.
        """
        log(LOG_INFO, "Server connected")
        self._received_order_snapshot = False
        self._received_wallet_snapshot = False

        self._wss.authenticate()
        for pair in self._tick_symbols:
            symbol = 't' + pair
            self._wss.subscribe_to_ticker(symbol)

    def __check_system(self):
        """ Check the connection is established or not. """
        try:
            server_q = self._wss.opened
            while not server_q.empty():
                server_q.get()
                self._callbacks['reset']()
                self.__connect()
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def __check_account_info(self):
        """ Check account information. """
        try:
            wallets_q = self._wss.wallets
            while not wallets_q.empty():
                self.__received_wallets(wallets_q.get()[0][1])

            wallet_update_q = self._wss.wallet_update
            while not wallet_update_q.empty():
                self.__received_wallet_update(
                    wallet_update_q.get()[0][1])

            orders_q = self._wss.orders
            while not orders_q.empty():
                self.__received_orders(orders_q.get()[0][1])

            order_new_q = self._wss.order_new
            while not order_new_q.empty():
                self.__received_order(order_new_q.get()[0][1])

            order_cancel_q = self._wss.order_cancel
            while not order_cancel_q.empty():
                self.__received_order(order_cancel_q.get()[0][1])

            notification_q = self._wss.notifications
            while not notification_q.empty():
                self.__received_notification(notification_q.get()[0][1])
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def __check_tickers(self):
        """ Check tick data. """
        for symbol in self._tick_symbols:
            try:
                trades_q = self._wss.tickers(symbol)
                while not trades_q.empty():
                    self.__received_tickers(symbol, trades_q.get()[0])
            except KeyError:
                # KeyError means Btfxwss doesn't get related information
                # yet. It's fine to pass and check in the next time.
                pass

    def __check_unused_info(self):
        """
        Websocket may have many events which are not used.
        Just pop it and ignore it. Otherwise, the queue may use too many
        memories.
        """
        def pop_queue(queue):
            """ pop unused queue """
            while not queue.empty():
                queue.get()

        try:
            queues = [
                self._wss.credits, self._wss.offer_new,
                self._wss.offer_cancel, self._wss.credit_new,
                self._wss.credit_close, self._wss.credit_update,
                self._wss.positions, self._wss.offer_update,
                self._wss.order_update, self._wss.position_update,
                self._wss.position_close, self._wss.loan_new,
                self._wss.loan_close, self._wss.loan_update,
                self._wss.unknown]

            for queue in queues:
                pop_queue(queue)
        except KeyError:
            # KeyError means Btfxwss doesn't get related information yet.
            # It's fine to pass and check in the next time.
            pass

    def __received_wallets(self, wallets):
        """
        Handle wallet snapshot.
        Args:
            wallets: balance of all currencies
        """
        for wallet in wallets:
            self._callbacks['process_wallet'](bitfinex_v2_rest.Wallet(wallet))
        self._received_wallet_snapshot = True

    def __received_wallet_update(self, wallet):
        """
        Handle wallet update.
        Args:
            wallets: balance of one currency
        """
        self._callbacks['process_wallet'](bitfinex_v2_rest.Wallet(wallet))

    def __received_orders(self, orders):
        """
        Handle order snapshot.
        Args:
            orders: current orders snapshot
        """
        for order in orders:
            self._callbacks['process_order'](bitfinex_v2_rest.Order(order))
        self._received_order_snapshot = True

    def __received_order(self, order):
        """
        Handle one order
        Args:
            order: order status
        """
        self._callbacks['process_order'](bitfinex_v2_rest.Order(order))

    def __received_tickers(self, pair, tickers):
        """
        Handle ticks
        Args:
            pair: ex BTCUSD
            tickers: tickers of the pair
        """
        if isinstance(tickers, list):
            for tick in tickers:
                self._callbacks['process_tick'](
                    bitfinex_v2_rest.TradingTicker([pair] + tick))

    def __received_notification(self, message):
        """
        Handle notification
        Args:
            message: notification from web socket
        """
        self._callbacks['process_notification'](
            bitfinex_v2_rest.Notifications(message))

    def new_order(self, pair, price, amount):
        """
        Create an new order.
        Args:
            pair: ex BTCUSD.
            price: 0 means the market order.
            amount: Positive number means buy order. Negative number means
                    sell order.
        """
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
        else:
            order = {
                'cid': cid,
                'type': "EXCHANGE MARKET",
                'symbol': symbol,
                'amount': str(amount),
                'hidden': 0
                }
        self._wss.new_order(**order)

    def cancel_order(self, order_id):
        """
        Cancel an order.
        Args:
            order_id: The id of the order.
        """
        value = {'id': order_id}
        self._wss.cancel_order(False, **value)

    def check_events(self):
        """ Check all events from web socket """
        self.__check_system()
        self.__check_account_info()
        self.__check_tickers()
        self.__check_unused_info()

    def is_received_order_snapshot(self):
        """ Return True if recevied order snapshot """
        return self._received_order_snapshot

    def is_received_wallet_snapshot(self):
        """ Return True if recevied wallet snapshot """
        return self._received_wallet_snapshot


class TradeHelper(object):
    """ Helper functions to calculate price """

    AMOUNT_DIGIT = 4
    # Used to check buy currency at market price when the number of currency
    # is not enough.
    MAX_SELL_TIMES = 10

    def __init__(self):
        pass

    @classmethod
    def get_higher_price(cls, cfg, price, profit):
        """
        Use price and profit to calculate next higher price.
        Return 0 if error.
        """
        if not profit or profit < 0:
            log(LOG_ERROR, 'Profit parameter error: %f' % profit)
            return 0
        return price * (cfg['percent'] ** profit)

    @classmethod
    def get_lower_price(cls, cfg, price, profit):
        """
        Use price and profit to calculate next lower price.
        Return 0 if error.
        """
        if not profit or profit < 0:
            log(LOG_ERROR, 'Profit parameter error: %f' % profit)
            return 0
        return price / (cfg['percent'] ** profit)

    def get_buy_amount_at_price(self, cfg, price):
        """
        Get coin amount at a given price for buy order.
        Return 0 if error.
        """
        if cfg['type'].upper() == 'CRYPTO':
            return cfg['amount']
        elif cfg['type'].upper() == 'USD':
            return round(cfg['amount'] / price, self.AMOUNT_DIGIT)
        return 0

    def get_sell_amount_at_price(self, cfg, price):
        """
        Get coin amount at a given price for sell order.
        Return 0 if error.
        """
        buy_price = self.get_lower_price(cfg, price, cfg['profit'])
        return self.get_buy_amount_at_price(cfg, buy_price)

    @classmethod
    def is_bot_order(cls, cfg, order):
        """ Check the order is bot order not not. """
        if cfg['type'].upper() == 'CRYPTO':
            if math.isclose(abs(order.amount_orig), cfg['amount']):
                return True
        elif cfg['type'].upper() == 'USD':
            if order.amount_orig > 0:
                if math.isclose(order.amount_orig * Decimal(order.price),
                                cfg['amount'], rel_tol=0.1):
                    return True
            else:
                expect_sell_amount = cfg['amount'] * (
                    cfg['percent'] ** cfg['profit'])
                if math.isclose(-order.amount_orig * Decimal(order.price),
                                expect_sell_amount, rel_tol=0.1):
                    return True
        return False

    def get_remain_times(self, cfg, price, balance):
        """
        Get remain times of a pair for sell.
        Return times
        Return MAX_SELL_TIMES means too many
        Return -1 if error
        """
        balance -= Decimal(cfg['hold'])
        if cfg['type'].upper() == 'CRYPTO':
            return int(balance / Decimal(cfg['amount']))
        elif cfg['type'].upper() == 'USD':
            remain_balance = float(balance)
            remain_times = 0
            price = self.get_higher_price(cfg, price, 1)
            while remain_times < self.MAX_SELL_TIMES:
                amount = round(cfg['amount'] / price, self.AMOUNT_DIGIT)
                if remain_balance > amount:
                    remain_times += 1
                    remain_balance -= amount
                    price = self.get_higher_price(cfg, price, 1)
                else:
                    return remain_times
            return self.MAX_SELL_TIMES
        return -1

    def is_reach_limit(self, cfg, price, balance):
        """ Check the balance reach limit or not """
        if cfg['limit'] == 0:
            return False
        balance -= Decimal(cfg['hold'])
        if cfg['type'].upper() == 'CRYPTO':
            if float(balance) >= cfg['amount'] * cfg['limit']:
                return True
        elif cfg['type'].upper() == 'USD':
            remain_balance = float(balance)
            times = 0
            price = self.get_higher_price(cfg, price, cfg['profit'])
            while times < cfg['limit']:
                amount = round(cfg['amount'] / price, self.AMOUNT_DIGIT)
                if remain_balance > amount:
                    remain_balance -= amount
                    times += 1
                    price = self.get_higher_price(cfg, price, 1)
                else:
                    return False
            if times >= cfg['limit']:
                return True
        return False


class LendBotControl(object):
    """ Check balance and move wallet """

    MOVE_WALLET_COOLDOWN_SEC = 60
    # Use this value to calculate the minimum required balance.
    # If exchange balance is less than the value, stop lendbot.
    NUM_ORDERS_TO_PREPARE = 6

    def __init__(self, stop_file, bot_config):
        self._lendbot_file = stop_file
        self._lendbot_start = None
        self._last_move_wallet_timestamp = 0
        self._minimum_required_balance = Decimal(0)
        self._v1_client = bitfinex_v1_rest.FullApi()
        self.__get_minimum_required_balance(bot_config['symbols'])

        setting = bot_config['control_lendbot']
        self._enable = setting['enable']
        self._target = Decimal(setting['target'])
        self._start_threshold = Decimal(setting['start_threshold'])
        self._stop_threshold = Decimal(setting['stop_threshold'])

    def check_balance(self, exchange_balance, funding_balance):
        """ Check two wallets to decide to move USD or not """
        if not self._enable:
            return
        if time.time() - self._last_move_wallet_timestamp < (
                self.MOVE_WALLET_COOLDOWN_SEC):
            return
        if (exchange_balance > funding_balance * self._start_threshold) and (
                exchange_balance > self._minimum_required_balance):
            if os.path.exists(self._lendbot_file):
                os.unlink(self._lendbot_file)
            if self._lendbot_start is not True:
                log(LOG_INFO, 'Start lendbot', EMOJI_START_LEND)
                self._lendbot_start = True
            log(LOG_INFO, 'Exchange: %f, Funding: %f' % (
                exchange_balance, funding_balance))
            target_amount = (exchange_balance - (
                self._target * funding_balance)) / (self._target +
                                                    Decimal(1.0))
            if exchange_balance - target_amount < (
                    self._minimum_required_balance):
                target_amount = (
                    exchange_balance - self._minimum_required_balance)
            if target_amount > 0:
                self.__move_wallet(target_amount)
                self._last_move_wallet_timestamp = time.time()
        elif exchange_balance < (
                funding_balance * self._stop_threshold):
            if self._lendbot_start is not False:
                log(LOG_INFO, 'Stop lendbot', EMOJI_STOP_LEND)
                self._lendbot_start = False
            open(self._lendbot_file, 'w').close()

    def __move_wallet(self, amount):
        """ Call v1 REST api to move wallet """
        try:
            self._v1_client.transfer_wallet(
                'USD', amount, 'exchange', 'deposit')
            log(LOG_INFO, 'Transfer %f from exchange to funding' % amount,
                EMOJI_MOVE_WALLET)
        except BitfinexClientError:
            log(LOG_ERROR, 'Move wallet error', EMOJI_ERROR)

    def __get_minimum_required_balance(self, symbols):
        """ Calculate minimum balance to hold orders for each currency """
        for symbol in symbols:
            if symbols[symbol]['type'].upper() == 'USD':
                self._minimum_required_balance += Decimal(
                    symbols[symbol]['amount'] * self.NUM_ORDERS_TO_PREPARE)


class SlackControl(object):
    """ Parse commands from slack daemon """

    SLACK_FILE = '.slack_file'

    def __init__(self, callback):
        self._callback = callback

    def check_files(self):
        """ Check files from slack daemon and validate command """
        if not os.path.exists(self.SLACK_FILE):
            return
        with open(self.SLACK_FILE, 'r') as slack_fd:
            commands = slack_fd.readlines()
        commands = [x.strip() for x in commands]
        for line in commands:
            words = line.split()
            command = words[0]
            args = []
            if command.upper() in ['INIT', 'RECOVER']:
                args = check_pairs_in_config(' '.join(words[1:]))
            elif command.upper() in ['ESCAPE', 'STATUS', 'WALLET']:
                pass
            else:
                log(LOG_ERROR, 'Unsupported command %s' % command)
                continue
            self._callback(command, args)
        os.unlink(self.SLACK_FILE)


class TradeBot(object):
    """ Trade bot """

    ORDER_TIMEOUT_SEC = 60
    BUY_CURRENCY_COOLDOWN_SEC = 60
    # This value should be less than TradeHelper.MAX_SELL_TIMES
    NUM_ORDERS_TO_HOLD = 3
    NO_BALANCE_RETRY_TIMES = 3

    def __init__(self, bot_config, args=None):
        if not args:
            log(LOG_ERROR, 'Missing args parameters')
            return
        self.buy_currency = bot_config['buy_currency']
        self.retry_order_in_error = bot_config['retry_in_error']
        self.retry_order_in_timeout = bot_config['retry_in_timeout']
        self._symbols = bot_config['symbols']
        self._init_pairs = args['init_pairs']
        self._recover_pairs = args['recover_pairs']
        self._escape = args['escape']

        self._orders = {}
        self._num_coins = {}
        self._last_price = {}
        self._unconfirm_orders = []
        self._last_add_timestamp = {}
        self._orders_before_disconnect = {}
        self._usd_wallet = {}
        self._received_order_snapshot = False
        self._received_wallet_snapshot = False
        for symbol in self._symbols:
            self._orders[symbol] = {}
            self._last_price[symbol] = 0
            self._last_add_timestamp[symbol] = 0

        callbacks = {
            'reset': self.reset,
            'process_wallet': self.process_wallet,
            'process_order': self.process_order,
            'process_tick': self.process_tick,
            'process_notification': self.process_notification
        }
        self._wsapi = WebSocketApi(self._symbols.keys(), callbacks)
        self._helper = TradeHelper()
        self._lendbot = LendBotControl(args['stop_file'], bot_config)
        self._slack = SlackControl(self.receive_slack_command)

    def reset(self):
        """ Reset necessary variables when websocket is connected """
        if not self._orders:
            self._orders_before_disconnect = self._orders
        self._orders = {}
        self._num_coins = {}
        self._usd_wallet = {}
        self._received_order_snapshot = False
        self._received_wallet_snapshot = False
        for symbol in self._symbols:
            self._orders[symbol] = {}
            self._last_price[symbol] = 0

    def run(self):
        """ Routine check in main thread """
        while True:
            self._wsapi.check_events()
            self.check_order_snapshot()
            self.check_wallet_snapshot()
            self.check_order_timeout()
            self.cancel_all_buy_orders()
            self.check_escape()
            self._slack.check_files()
            time.sleep(0.5)

    def check_order_snapshot(self):
        """ Check orders during disconnection """
        if self._received_order_snapshot:
            return
        self._received_order_snapshot = (
            self._wsapi.is_received_order_snapshot())
        if self._received_order_snapshot:
            self.check_orders_during_disconnect()

    def check_wallet_snapshot(self):
        """ Check wallet snapshot is received or not """
        if self._received_wallet_snapshot:
            return
        self._received_wallet_snapshot = (
            self._wsapi.is_received_wallet_snapshot())

    def process_wallet(self, wallet):
        """ Process wallet update """
        if wallet.currency == 'USD':
            self._usd_wallet[wallet.wallet_type] = wallet.balance
            self.check_lendbot()
        if wallet.wallet_type != 'exchange':
            return
        self._num_coins[wallet.currency] = wallet.balance
        pair = wallet.currency + 'USD'
        if pair not in self._symbols:
            return
        self.check_and_buy_currency(pair, self._symbols[pair], wallet.balance)

    def process_order(self, order):
        """ Process order update """
        pair = order.symbol[1:]
        if pair not in self._symbols:
            return
        cfg = self._symbols[pair]
        if order.status == 'ACTIVE':
            if order.id not in self._orders[pair]:
                self._orders[pair][order.id] = order
            self.remove_unconfirm_order(pair, order.price, order.amount_orig)
        elif 'PARTIALLY' in order.status:
            if order.id not in self._orders[pair]:
                self._orders[pair][order.id] = order
            self.remove_unconfirm_order(pair, order.price, order.amount_orig)
            self.handle_executed_order(order, cfg)
        elif order.status == 'CANCELED':
            log(LOG_VERBOSE, 'Cancelled an order, pair: %s, amount: %f, '
                'price: %f' % (pair, order.amount_orig, order.price))
            del self._orders[pair][order.id]
        elif order.status.startswith('EXECUTED'):
            self.handle_executed_order(order, cfg)
        else:
            log(LOG_ERROR, 'Error order status: %s' % order.status)

    def process_tick(self, ticker):
        """ Process tick update """
        pair = ticker.symbol
        self._last_price[pair] = ticker.last_price
        if self._received_order_snapshot and self._received_wallet_snapshot:
            if pair in self._init_pairs:
                self.create_init_orders(pair)
            if pair in self._recover_pairs:
                self.create_recover_orders(pair)

    def process_notification(self, note):
        """ Process notification update """
        if note.type == 'on-req' and note.status == 'ERROR':
            order = bitfinex_v2_rest.Order(note.notify_info)
            log(LOG_ERROR, 'Error new %s order with amount: %f, price: %f:'
                ' %s' % (order.symbol, order.amount_orig, order.price,
                         note.text))
            pair = order.symbol[1:]
            (retry, price) = self.remove_unconfirm_order(pair, order.price,
                                                         order.amount_orig)
            if self.retry_order_in_error:
                if 'not enough exchange' in note.text and retry <= 0:
                    pass
                elif 'minimum size' in note.text:
                    pass
                elif retry > 0:
                    log(LOG_INFO, 'Need to retry order, retry times: %d' %
                        retry)
                    self.new_order(pair, price, order.amount_orig,
                                   retry - 1)
        elif note.type == 'on-req' and note.status == 'SUCCESS':
            order = bitfinex_v2_rest.Order(note.notify_info)
            pair = order.symbol[1:]
            self.remove_unconfirm_order(pair, order.price, order.amount)
        elif note.type == 'on-req':
            order = bitfinex_v2_rest.Order(note.notify_info)
            log(LOG_ERROR, 'Unhandled notify new %s order with amount: %f, '
                'price: %f: %s' % (order.symbol, order.amount, order.price,
                                   note.text))

    def check_order(self, pair, price, amount, check_limit=True):
        """ Check the price and amount of new order exists or not """
        # Order is already in confirmed orders. Ignore it.
        for _, order in self._orders[pair].items():
            if (math.isclose(order.amount_orig, amount, rel_tol=0.01) and
                    math.isclose(order.price, price, rel_tol=0.01)):
                return True
        # Order is already in unconfirmed orders. Ignore it.
        for order in self._unconfirm_orders:
            if (order[0] == pair and
                    math.isclose(order[1], price, rel_tol=0.01) and
                    math.isclose(order[2], amount, rel_tol=0.01)):
                return True
        # check the value of coins reach the upper limit or not
        if check_limit and amount > 0 and self._helper.is_reach_limit(
                self._symbols[pair], self._last_price[pair],
                self._num_coins[pair[:3]]):
            log(LOG_INFO, 'Pair %s reach limit' % pair, EMOJI_LIMIT)
            return True
        return False

    def check_order_timeout(self):
        """ Check unconfirmed orders is timeout or not """
        if not self._received_order_snapshot:
            return
        temp_orders = list(self._unconfirm_orders)
        retry_orders = []
        for order in self._unconfirm_orders:
            if (time.time() - order[3]) > self.ORDER_TIMEOUT_SEC:
                log(LOG_ERROR, 'Order %s with amount: %f, price: %f timeout. '
                    'Need to retry, retry times: %d' % (
                        order[0], order[2], order[1], order[4]))
                temp_orders.remove(order)
                retry_orders.append(order)
        self._unconfirm_orders = temp_orders
        if self.retry_order_in_timeout:
            for order in retry_orders:
                if order[4] > 0:  # retry
                    self.new_order(order[0], order[2], order[1], order[4] - 1)

    def remove_unconfirm_order(self, pair, price, amount):
        """ Remove an order from unconfirmed list """
        temp_orders = self._unconfirm_orders
        retry_times = 0
        real_price = price
        for order in self._unconfirm_orders:
            if (order[0] == pair and
                    math.isclose(order[2], amount, rel_tol=0.01)):
                if (math.isclose(order[1], price, rel_tol=0.01) or
                        not order[1]):
                    retry_times = order[4]
                    # For market order, the price should be 0
                    real_price = order[1]
                    temp_orders.remove(order)
        self._unconfirm_orders = temp_orders
        return (retry_times, real_price)

    def check_and_buy_currency(self, pair, cfg, balance):
        """
        Check the number of orders to sell. If the number is less than expected
        number, buy two orders at market price.
        Return True if the number of orders is too small
        """
        price = self._last_price[pair]
        if price == 0:
            return False
        remain_times = self._helper.get_remain_times(cfg, price, balance)
        if remain_times <= self.NUM_ORDERS_TO_HOLD:
            log(LOG_INFO, 'Pair %s is not enough: %f' % (pair, balance))
            allow_buy = time.time() - self._last_add_timestamp[pair] > (
                self.BUY_CURRENCY_COOLDOWN_SEC)
            if self.buy_currency and allow_buy:
                units = self.NUM_ORDERS_TO_HOLD - remain_times + 2
                amount = self._helper.get_buy_amount_at_price(cfg, price)
                self.new_order(pair, 0, amount * units)
                self._last_add_timestamp[pair] = time.time()
            return True
        return False

    def handle_executed_order(self, order, cfg, normal=True):
        """
        Set corresponding buy/sell order according to an executed order
        """
        pair = order.symbol[1:]
        if normal:
            emoji = EMOJI_BUY if order.amount_orig > 0 else EMOJI_SELL
        else:
            emoji = EMOJI_DISCONNECT
            # The average price of an order during disconnection is 0.0. Set it
            # to price then we can get correct value when parsing log.
            order.price_avg = order.price

        # if normal is False, it means the function is called for disconnection
        # orders. We don't need to check amount since the amount is out of
        # date.
        if not math.isclose(order.amount, 0, rel_tol=0.00001) and normal:
            return
        else:
            log(LOG_INFO, 'Executed an order, pair: %s, amount: %f, price: %f,'
                ' avg_price: %f' % (pair, order.amount_orig, order.price,
                                    order.price_avg), emoji)
        if order.id in self._orders[pair]:
            del self._orders[pair][order.id]

        if not self._helper.is_bot_order(cfg, order):
            return
        side = 'BUY' if order.amount_orig > 0 else 'SELL'
        self.set_orders_by_price(pair, cfg, order.price, side)

    def set_orders_by_price(self, pair, cfg, base_price, side):
        """ Set orders according to the price and side """
        if side == 'BUY':
            price = self._helper.get_lower_price(cfg, base_price, 1)
            amount = self._helper.get_buy_amount_at_price(cfg, price)
            if not self.check_order(pair, price, amount):
                self.new_order(pair, price, amount)

            price = self._helper.get_higher_price(
                cfg, base_price, cfg['profit'])
            amount = self._helper.get_sell_amount_at_price(cfg, price)
            if not self.check_order(pair, price, -amount):
                self.new_order(pair, price, -amount)
        elif side == 'SELL':
            price = self._helper.get_lower_price(
                cfg, base_price, cfg['profit'])
            amount = self._helper.get_buy_amount_at_price(cfg, price)
            if not self.check_order(pair, price, amount):
                self.new_order(pair, price, amount)

            price = self._helper.get_higher_price(cfg, base_price, 1)
            amount = self._helper.get_sell_amount_at_price(cfg, price)
            if not self.check_order(pair, price, -amount):
                self.new_order(pair, price, -amount)

    def new_order(self, pair, price, amount, retry_times=None):
        """ Create an new order """
        if retry_times is None:
            retry_times = self.NO_BALANCE_RETRY_TIMES
        self._unconfirm_orders.append(
            [pair, price, amount, time.time(), retry_times])
        self._wsapi.new_order(pair, price, amount)
        log(LOG_VERBOSE, 'Create a new %s order with amount: %f, price: %f' %
            (pair, amount, price))

    def cancel_order(self, order):
        """ Cancel one order """
        self._wsapi.cancel_order(order.id)

    def cancel_all_buy_orders(self):
        """ Remove buy orders which price is too far from current price """
        cancel_orders = []
        for symbol, orders in self._orders.items():
            if self._last_price[symbol] == 0:
                continue
            cfg = self._symbols[symbol]
            cancel_price = self._helper.get_lower_price(
                cfg, self._last_price[symbol], 5)
            for _, order in orders.items():
                if not self._helper.is_bot_order(cfg, order):
                    continue
                if order.amount_orig > 0 and order.price < cancel_price:
                    cancel_orders.append(order)
        for order in cancel_orders:
            self.cancel_order(order)
            symbol = order.symbol[1:]
            del self._orders[symbol][order.id]

    def cancel_all_orders(self, pair):
        """ Cancel all orders according to the pair """
        for _, order in self._orders[pair].items():
            self.cancel_order(order)

    def create_init_orders(self, pair):
        """ Create initial buy/sell order of a pair """
        self.cancel_all_orders(pair)
        self._orders[pair] = {}

        cfg = self._symbols[pair]
        currency = pair[:3]
        num_coins = 0
        if currency in self._num_coins:
            num_coins = self._num_coins[currency]
        if self.check_and_buy_currency(pair, cfg, num_coins):
            log(LOG_INFO, 'Wait for currency ready')
            return

        self.set_orders_by_price(pair, cfg, self._last_price[pair], 'BUY')

        log(LOG_INFO, 'Already initialized %s' % pair)
        self._init_pairs.remove(pair)

    def create_recover_orders(self, pair):
        """ Create recover buy order of a pair """
        lowest_sell_price = 0
        for _, order in self._orders[pair].items():
            if order.amount_orig > 0:
                continue
            if lowest_sell_price == 0 or order.price < lowest_sell_price:
                lowest_sell_price = order.price

        cfg = self._symbols[pair]
        buy_price = self._helper.get_lower_price(
            cfg, lowest_sell_price, cfg['profit'] + 1)
        amount = self._helper.get_buy_amount_at_price(cfg, buy_price)
        if not self.check_order(pair, buy_price, amount, False):
            self.new_order(pair, buy_price, amount)

        log(LOG_INFO, 'Already recovered %s' % pair)
        self._recover_pairs.remove(pair)

    def view_status(self):
        """ Print how many orders should be added when bot stop """
        msg = ''
        active_pairs = 'Active:'
        for pair in sorted(self._symbols):
            if not self._last_price[pair]:
                continue
            lowest_price = 0
            no_miss = False
            for _, order in self._orders[pair].items():
                if order.amount_orig > 0:
                    no_miss = True
                    break
                if lowest_price == 0 or order.price < lowest_price:
                    lowest_price = order.price

            if not no_miss:
                cfg = self._symbols[pair]
                current_price = self._last_price[pair]
                miss_price = self._helper.get_lower_price(
                    cfg, lowest_price, cfg['profit'] + 1)
                miss_times = 1
                while miss_price > current_price:
                    miss_times += 1
                    miss_price = self._helper.get_lower_price(
                        cfg, miss_price, 1)
                msg += '%s miss %d, gap %.3f, now %.3f\n' % (
                    pair[:3], miss_times, lowest_price - current_price,
                    current_price)
            else:
                active_pairs += ' %s' % pair[:3]
        msg = active_pairs + '\n' + msg
        log(LOG_INFO, msg)

    def check_orders_during_disconnect(self):
        """ Check executed orders during disconnection """
        has_executed_order = False
        for symbol, orders in self._orders_before_disconnect.items():
            if symbol not in self._symbols:
                continue
            cfg = self._symbols[symbol]
            for order_id, order in orders.items():
                if order_id not in self._orders[symbol]:
                    has_executed_order = True
                    log(LOG_INFO, 'Missing an order, pair: %s, amount: %f, '
                        'price: %f' % (symbol, order.amount_orig, order.price),
                        EMOJI_DISCONNECT)
                    self.handle_executed_order(order, cfg, False)
        if not has_executed_order:
            log(LOG_INFO, 'No executed orders during disconnection',
                EMOJI_DISCONNECT)
        else:
            log(LOG_INFO, 'Fixed missing orders during disconnection',
                EMOJI_DISCONNECT)

    def check_lendbot(self):
        """ Cowork with lendbot """
        if ('exchange' not in self._usd_wallet or
                'funding' not in self._usd_wallet):
            return
        self._lendbot.check_balance(
            self._usd_wallet['exchange'], self._usd_wallet['funding'])

    def check_escape(self):
        """ Execute escape mode """
        if not self._escape:
            return
        if (not self._received_order_snapshot or
                not self._received_wallet_snapshot):
            return
        ans = input('Are you sure to cancel all orders and sell all '
                    'currencies? [YES/NO]: ')
        if ans != 'YES':
            return
        for symbol in self._symbols:
            self.cancel_all_orders(symbol)
            num_coins = self._num_coins[symbol[:3]]
            self.new_order(symbol, 0, -num_coins)
        self._escape = False

    def receive_slack_command(self, command, args):
        """ Callback function to execute slack command """
        if command.upper() == 'INIT':
            log(LOG_INFO, 'Received init command ' + str(args))
            self._init_pairs += args
        elif command.upper() == 'RECOVER':
            log(LOG_INFO, 'Received recover command ' + str(args))
            self._recover_pairs += args
        elif command.upper() == 'ESCAPE':
            log(LOG_INFO, 'Received escape command ' + str(args))
#             self.check_escape()
        elif command.upper() == 'STATUS':
            log(LOG_INFO, 'Received status command ' + str(args))
            self.view_status()
        elif command.upper() == 'WALLET':
            log(LOG_INFO, 'Received wallet command ' + str(args))
            log(LOG_INFO, 'Exchange: %f, Funding: %f' % (
                self._usd_wallet['exchange'], self._usd_wallet['funding']))


def check_pairs_in_config(pairs_str):
    """ Find pairs in config """
    symbols = config.TRADE_HBOT_CONFIG['symbols']
    pairs = []
    if pairs_str:
        if pairs_str.upper() == 'ALL':
            for symbol in symbols:
                pairs.append(symbol)
        else:
            for symbol in pairs_str.split():
                pair = symbol.upper() + 'USD'
                if pair in symbols:
                    pairs.append(pair)
                else:
                    print("Pair %s is not in symbols config" % pair)
    return pairs


def check_config():
    """ Check config parameters """
    if not config.TRADE_HBOT_CONFIG:
        print('TRADE_HBOT_CONFIG is empty')
        return False

    if 'symbols' not in config.TRADE_HBOT_CONFIG:
        print('TRADE_HBOT_CONFIG miss symbols')
        return False

    if not config.TRADE_HBOT_CONFIG['symbols']:
        print('Symbols in TRADE_HBOT_CONFIG is empty')
        return False

    for symbol in config.TRADE_HBOT_CONFIG['symbols']:
        if 'limit' not in config.TRADE_HBOT_CONFIG['symbols'][symbol]:
            config.TRADE_HBOT_CONFIG['symbols'][symbol]['limit'] = 0
        if 'hold' not in config.TRADE_HBOT_CONFIG['symbols'][symbol]:
            config.TRADE_HBOT_CONFIG['symbols'][symbol]['hold'] = 0
        if 'type' not in config.TRADE_HBOT_CONFIG['symbols'][symbol]:
            print('Pair %s miss type setting' % symbol)
            return False
        symbol_type = config.TRADE_HBOT_CONFIG['symbols'][symbol]['type']
        if symbol_type.upper() not in ['USD', 'CRYPTO']:
            print('Pair %s has error config type %s' % (symbol, symbol_type))
            return False

    if 'control_lendbot' in config.TRADE_HBOT_CONFIG:
        lendbot_setting = config.TRADE_HBOT_CONFIG['control_lendbot']
        if ('target' not in lendbot_setting or
                'start_threshold' not in lendbot_setting or
                'stop_threshold' not in lendbot_setting):
            print('Missing seeting in control_lendbot')
            return False
        if (lendbot_setting['start_threshold'] <= lendbot_setting['target'] or
                lendbot_setting['stop_threshold'] >= (
                    lendbot_setting['target'])):
            print('Threshold should be start > target > stop')
            return False
    else:
        config.TRADE_HBOT_CONFIG['control_lendbot'] = {'enable': False}
    return True


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
    parser.add_argument('--recover', help='Currencies which need recovery')
    parser.add_argument('--escape', action='store_true',
                        help='Cancel all orders and sell all currencies')
    parser.add_argument('--stop_file',
                        default=os.path.expanduser('~/.stop_lendbot'))
    opts = parser.parse_args()

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig()

    if opts.init and opts.recover:
        print('Init and recover cannot be used at the same time')
        return

    if not check_config():
        return

    address = '0x84d6bc1b4ebab26607279834a95c25a19bb8595e'
    print('If you like this bot, welcome to donate ETH to the address:\n' +
          address)
    print('=' * 30)
    log(LOG_INFO, 'TradeBot started')

    args = {}
    args['init_pairs'] = check_pairs_in_config(opts.init)
    args['recover_pairs'] = check_pairs_in_config(opts.recover)
    args['stop_file'] = opts.stop_file
    args['escape'] = opts.escape
    bot = TradeBot(config.TRADE_HBOT_CONFIG, args)
    bot.run()


if __name__ == '__main__':
    main()
