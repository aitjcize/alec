#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import datetime
import decimal
import logging
import pprint
import sqlite3
import time

from slacker import Slacker

from alec import config
from alec import database_utils
from alec.api import BitfinexClientError
from alec.api import bitfinex_v1_rest
from alec.api import bitfinex_v2_rest

EMOJI_SELL = ':heart:'
EMOJI_BUY = ':blue_heart:'
EMOJI_NOT_ENOUGH_COIN = ':rocket:'
EMOJI_NOT_ENOUGH_FIAT = ':anchor:'

FIAT = 'usd'
MAX_CANCEL_ORDER_RETRIES = 5
MAX_ORDER_STATUS_RETRIES = 30

RATE_LIMIT_TIME = 120

slack = Slacker(config.SLACK_TOKEN) if config.SLACK_ENABLE else None
logger = logging.getLogger(__name__)


def log(text, exception=False, side=None, need_coin=False, need_fiat=False,
        admin=False):
    """Log to stdout and slack if enabled.

    Args:
      text: The content to log.
      exception: Add exception emoji for slack.
      side: Add order side emoji for 'buy' or 'sell'.
      need_coin: Add emoji when more coin is needed.
      need_fiat: Add emoji when more fiat is needed.
      admin: Notify slack admin.

    """
    print(str(datetime.datetime.now()) + '\t' + text)
    if slack:
        if exception:
            text = ':rotating_light: Exception:' + text
        if side == 'buy':
            text = EMOJI_BUY + ' ' + text
        elif side == 'sell':
            text = EMOJI_SELL + ' ' + text
        if need_coin:
            text = EMOJI_NOT_ENOUGH_COIN + ' ' + text
        if need_fiat:
            text = EMOJI_NOT_ENOUGH_FIAT + ' ' + text
        if admin:
            text = '@' + config.SLACK_ADMIN + ' ' + text

        slack.chat.post_message(config.SLACK_CHANNEL, text)


class TradeBotError(Exception):
    pass


class TradeBot(object):
    NORMAL_INTERVAL = 30

    def __init__(self, targets, db):
        """Inits a trade bot.

        Args:
            targets: The target config defined in config.sh
                     A dict from symbol to config for that symbol, e.g.
                     {'ETCUSD':{'unit': 1, 'step': 0.01}}.
                     Unit is the amount of one order. Step is the ratio
                     for price difference.
             db: Path to the database to record order history.

        """
        self._v1_client = bitfinex_v1_rest.FullApi()
        self._v2_client = bitfinex_v2_rest.PublicApi()
        # A dict from symbol to config for that symbol, e.g.
        # {'ETCUSD':{'unit': 1, 'step': 0.01}}
        self._targets = targets
        self._normalize_target()
        self._wallet = None
        # from ID to order
        self._watched_orders = {}
        self._db = db
        # Record the order to be cancelled if one order is executed.
        self._paired_orders = {}

    def _normalize_target(self):
        """Normalize some configs."""
        for k, v in self._targets.iteritems():
            # Normalize to Decimal.
            v['unit'] = decimal.Decimal(v['unit'])
            v['step'] = decimal.Decimal(v['step'])
            # Remove fiat like 'USD' in target key to get currency for wallet.
            # E.g., target key 'ETHUSD', currency 'eth'
            assert k[-3:].lower() == FIAT and len(k) > 3
            v['currency'] = k[:-3].lower()

    def _get_last_price(self, symbol):
        """Gets latest price of a symbol."""
        ticker = self._v1_client.ticker(symbol)
        return ticker['last_price']

    def _log_total_value(self):
        """Logs total value."""
        total_value = self._get_account_info(print_log=False)
        log('Total value: %s' % total_value)

    def _get_account_info(self, print_log=False):
        """Shows balances and value and get total value.

        Args:
            print_log: True to print logs.

        Returns:
            Total value. This is fiat + exchange wallet of coins in targets.

        """
        balances = self._get_balances()
        # Reuse balances
        fiat_info = self._get_wallet_info(currency=FIAT, balances=balances)
        if print_log:
            log('%s amount %s, available: %s' % (fiat_info['currency'],
                                                 fiat_info['amount'],
                                                 fiat_info['available']))
        total_value = fiat_info['amount']
        for k, v in self._targets.iteritems():
            coin_info = self._get_wallet_info(currency=v['currency'],
                                              balances=balances)
            coin_amount = coin_info['amount']
            coin_price = self._get_last_price(k)
            coin_value = coin_amount * coin_price

            if print_log:
                log('coin %s amount: %s, price: %s, value: %s' % (
                    v['currency'], coin_amount, coin_price, coin_value))

            total_value = total_value + coin_value

        if print_log:
            log('total value: %s' % total_value)

        return total_value

    def run(self):
        """Runs main strategy in a loop."""
        self._get_account_info(print_log=True)
        while True:
            try:
                logger.info('=' * 20)
                log('=' * 20)
                sleep_time = self._trade_strategy()
                time.sleep(sleep_time)
            except BitfinexClientError as e:
                log('Bitfinex: ' + str(e), exception=True)
                if 'ERR_RATE_LIMIT' in str(e):
                    log('Bitfinex: sleep some time for rate limit')
                    time.sleep(RATE_LIMIT_TIME)
                    continue
                raise
            except Exception as e:
                log(str(e), exception=True)
                raise

    def _get_balances(self):
        """Gets balances from bitfinex api."""
        return self._v1_client.balances()

    def _get_wallet_info(self, currency, balances):
        """Gets one exchange wallet from balances."""
        for wallet in balances:
            if wallet['currency'].lower() == currency.lower() and (
                    wallet['type'] == 'exchange'):
                return wallet
        raise TradeBotError(
                'No wallet information for %s. You have never used this '
                'wallet for this coin. Put some coins into this wallet first' %
                currency)

    def _should_watch_this_order(self, order):
        """Checks if this order matches the unit set in targets."""
        # Check symbol.
        symbol = order['symbol']
        if symbol not in self._targets:
            return False

        # Check amount.
        target_config = self._targets[symbol]
        if order['original_amount'] != target_config['unit']:
            return False

        # Check it is live.
        if not order['is_live']:
            return False

        # Check it is a limit exchange order.
        if order['type'] != 'exchange limit':
            return False

        # Note: if you want, you can check order['src'] is 'api', or 'web'.

        return True

    def _check_new_watched_orders(self):
        """Finds any live order that should be put into the watchlist."""
        orders = self._v1_client.orders()
        for order in orders:
            order_id = order['id']
            # Already being watched, still, the price might be changed by
            # user manually.
            # So, skip checking whether this should be watched,
            # and update the status.
            if order_id in self._watched_orders:
                self._watched_orders[order_id] = order

            # Add an order that matches watching criteria to the watchlist
            # The key is order id. The value is order status.
            if self._should_watch_this_order(order):
                self._watched_orders[order_id] = order

    def _order_was_cancelled(self, order_status):
        """Checks if an order was cancelled."""
        return (order_status['is_live'] == False and
                order_status['is_cancelled'] == True)

    def _order_was_executed(self, order_status):
        """Checks if an order was executed."""
        return (order_status['is_live'] == False and
                order_status['is_cancelled'] == False)

    def _cancel_order(self, id):
        """Cancels one order by order id."""
        log('Cancel order %s' % id)
        # Retry some times to cancel an order.
        for i in xrange(MAX_CANCEL_ORDER_RETRIES):
            try:
                self._v1_client.cancel_order(id)
            except BitfinexClientError as e:
                if 'Order could not be cancelled.' in str(e):
                    logger.warning('Order %s could not be cancelled!', id)
                    time.sleep(1)
                    continue
                raise
            except Exception as e:
                logger.exception('Can not cancel order %s', id)
                log(str(e), exception=True)
                raise

            return

        log('Still can not cancel order for %s' % id, exception=True)

    def _get_order_status(self, id):
        """Gets status of one order by order id.

        Returns:
            order status if succeed to cancel the order.
            None otherwise.

        """
        for i in xrange(MAX_ORDER_STATUS_RETRIES):
            try:
                ret = self._v1_client.order_status(id=id)
            except BitfinexClientError as e:
                if 'No such order found' in str(e):
                    logger.warning('Can not find order status. retry.')
                    time.sleep(1)
                    continue
                raise
            except Exception as e:
                logger.exception('Can not find order status %s', id)
                log(str(e), exception=True)
                raise

            return ret

        # This is common on bitfinex v1 rest API.
        logger.warning('Still can not find order status for %s', id)
        return None

    def _check_watched_orders(self):
        """Queries order status and react to executed orders.

        For every executed order in watchlist, create one buy and one sell
        orders for it. Cancel the paired order that was created with it.

        """
        # This balances will be reused for this iteration.
        balances = self._get_balances()
        # Put watched ids in a list because we will remove items in the
        # dict in for loop. Use list since keys() returns an iterator
        # in Python 3.
        for id in list(self._watched_orders.keys()):
            # Queries the status of this order.
            order_status = self._get_order_status(id=id)
            # Can not find this order. Give up this time.
            if order_status is None:
                continue

            # Cancelled, so remove it from watchlist.
            if self._order_was_cancelled(order_status):
                self._watched_orders.pop(id)

            # Executed. Record and react on it.
            elif self._order_was_executed(order_status):
                # Log total value after an order is executed.
                self._log_total_value()

                logger.info('Executed: %s', order_status)
                # Store the executed order.
                self._record_executed(order_status)
                # Remove it from watchlist.
                self._watched_orders.pop(id)

                # Cancel the order of another direction which was created with
                # order. This is to keep number of orders remain constant.
                # Execute 1  -> cancel 1, create 1 buy, create 1 sell.
                if id in self._paired_orders:
                    another_id = self._paired_orders[id]
                    self._cancel_order(another_id)
                    self._paired_orders.pop(id)
                    self._paired_orders.pop(another_id)

                self._action_to_executed_order(order_status, balances=balances)

    def _record_executed(self, order_status):
        """Logs and possibly stores an executed order to database."""
        timestamp = order_status['timestamp']
        symbol = order_status['symbol']
        side = order_status['side']
        amount = order_status['original_amount']
        price = order_status['avg_execution_price']

        log('Executed: %s %s: %s @ %s' % (
            side, symbol, amount, price),
            side=side)

        if not self._db:
            return

        # Insert a row of data to executed_orders table.
        self._db.execute("INSERT INTO executed_orders VALUES (?,?,?,?,?)",
                         (timestamp, symbol, side, float(amount), float(price)))

    def _action_to_executed_order(self, order_status, balances):
        """Does some actions based on an executed order."""
        # Get needed info from order status.
        exec_price = order_status['avg_execution_price']
        symbol = order_status['symbol']
        amount = order_status['original_amount']

        self._create_two_paired_orders(mid_price=exec_price, symbol=symbol,
                                       amount=amount, balances=balances)

    def _create_two_paired_orders(self, mid_price, symbol, amount, balances):
        """Tries to create two paired orders.

        Create one sell order at a higher price and one buy order at a lower
        price, subjected to remaining fiat and coins.

        The content of the cached balances will be changed after placing orders.

        We need to use cached balances because bitfinex has strict rate limit
        for balances.

        """
        logger.debug('Create paired orders for %s, %s from %s',
                     symbol, amount, mid_price)
        target_config = self._targets[symbol]

        sell_order_id, buy_order_id = None, None

        # Set a sell order with higher price and the same amount.
        sell_target_ratio = decimal.Decimal(1.0) + target_config['step']
        sell_price = mid_price * sell_target_ratio

        # If there is still available target in wallet, create this order.
        wallet = self._get_wallet_info(currency=target_config['currency'],
                                       balances=balances)
        logger.debug('available coin: %s, amount: %s', wallet['available'],
                     amount)

        # If there is still coin in the exchange wallet, create this order.
        # Note that the balance quried from api might be out-dated.
        # Leave some margin for this kind of discrepancy.
        if wallet['available'] >= amount * 2:
            status = self._create_new_order(
                symbol=symbol, price=sell_price, amount=amount, side='sell')
            sell_order_id = status['id']
            # Add the new order to watchlist.
            self._watched_orders[sell_order_id] = status

            # Modify the wallet.
            wallet['available'] -= amount
        else:
            log('Not enough %s to create a sell order' %
                target_config['currency'], need_coin=True)

        # Set a buy order with less price and the same amount.
        buy_target_ratio = decimal.Decimal(1.0) - target_config['step']
        buy_price = mid_price * buy_target_ratio

        # If there is still fiat in the exchange wallet, create this order.
        # Note that the balance quried from api might be out-dated.
        # Leave some margin for this kind of discrepancy.
        wallet = self._get_wallet_info(FIAT, balances)
        if wallet['available'] >= buy_price * amount * 2:
            status = self._create_new_order(
                symbol=symbol, price=buy_price, amount=amount, side='buy')
            buy_order_id = status['id']
            # Add the new order to watchlist.
            self._watched_orders[buy_order_id] = status

            # Modify the wallet.
            wallet['available'] -= buy_price * amount
        else:
            log('Not enough %s to create a buy order' % FIAT,
                need_fiat=True)

        # Records this pair so one execution can cancel the other one in
        # the future.
        if sell_order_id and buy_order_id:
            self._paired_orders[sell_order_id] = buy_order_id
            self._paired_orders[buy_order_id] = sell_order_id

    def _create_new_order(self, symbol, price, amount, side):
        """Creates a new order and returns the order status."""
        status = self._v1_client.new_limit_order(
            symbol=symbol, amount=amount, price=price, side=side)
        logger.info('New order: %s: %s %s: %s @ %s', status['id'],
                    side, symbol, amount, price)
        log('Create %s: %s %s: %s @ %s' % (
            status['id'], side, symbol, amount, price))

        return status

    def _format_order_str(self, prefix, order_status):
        """Formats a string to show order."""
        return '%s: Order %s: %s %s: %s @ %s' % (
                    prefix,
                    '{:11d}'.format(order_status['id']),
                    '{:6s}'.format(order_status['side']),
                    '{:10s}'.format(order_status['symbol']),
                    '{:8s}'.format(str(order_status['original_amount'])),
                    '{:8s}'.format(str(order_status['price'])))

    def _log_one_live_order(self, prefix, order_status):
        """Logs one live order status."""
        string = self._format_order_str(prefix, order_status)
        logger.info(string)

    def _log_watched_orders(self):
        """Logs the summary of watched orders and their paired orders."""
        for id, status in self._watched_orders.iteritems():
            self._log_one_live_order('Watching ', status)
            if id in self._paired_orders:
                paired_order_id = self._paired_orders[id]
                paired_order_status = self._watched_orders[paired_order_id]
                self._log_one_live_order(' ==> Paired order ',
                                         paired_order_status)

    def _create_initial_orders(self):
        """Creates initial orders for targets.

        Set a pair of orders from latest price. One higher sell, one lower buy.

        """
        # Checks if there is any target that does not have a order.
        symbols_with_orders = set()
        for id, status in self._watched_orders.iteritems():
            symbols_with_orders.add(status['symbol'])

        # Reuse this balances to avoid bitfinex ERR_RATE_LIMIT for
        # balance query.
        balances = self._get_balances()
        for symbol, config in self._targets.iteritems():
            if symbol not in symbols_with_orders:
                log('Create initial orders for %s' % symbol)
                price = self._get_last_price(symbol)
                amount = config['unit']
                self._create_two_paired_orders(mid_price=price, symbol=symbol,
                                               amount=amount, balances=balances)

    def _trade_strategy(self):
        """Main logic of trade strategy."""
        # Check if there is any new open order to be watched.
        # This might happen if there is order before trade starts.
        self._check_new_watched_orders()

        # If there is no order being watched for a target, create two orders
        # from current price.
        self._create_initial_orders()

        # Check the watched order status.
        self._check_watched_orders()

        logger.debug('watched_orders: %s', pprint.pformat(self._watched_orders))
        logger.debug('paired_orders: %s', pprint.pformat(self._paired_orders))

        self._log_watched_orders()

        return self.NORMAL_INTERVAL

    def clean_up_orders(self):
        """Clean up all orders that has matched target/amount."""
        self._check_new_watched_orders()
        for id in self._watched_orders:
            self._cancel_order(id)

    def check_order_status(self, id):
        print(self._get_order_status(id=id))


def bootstrap_db(db):
    """Creates a database to store executed orders."""
    db.execute("""CREATE TABLE executed_orders (timestamp, symbol, side, amount, price)""")


def main():
    """ This bot may raise exception. Suggest to run the bot by the command:
    while true; do trade_bot ; sleep 3; done

    When there is exception, the bot will post message to slack.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--bootstrap', help='setup database', action='store_true')
    parser.add_argument('--clean_up_orders',
                         help='cancel all orders with matched amount. '
                              'Default to True.',
                         default=True,
                         action='store_true')
    parser.add_argument('--order_status',
                        help='debug utility to check one order status by ID',
                        metavar='ID', type=int)

    opts = parser.parse_args()

    FORMAT = '%(asctime)-15s %(levelname)-10s %(message)s'
    if opts.debug:
        logging.basicConfig(level=logging.DEBUG, format=FORMAT)
    else:
        logging.basicConfig(level=logging.INFO, format=FORMAT)

    # Disable annoying Starting new HTTP connection (1): example.com.
    logging.getLogger("requests").setLevel(logging.WARNING)

    db = None
    # Connect database.
    if config.TRADE_BOT_DB:
        db = database_utils.DatabaseManager(config.TRADE_BOT_DB)
        # Create table.
        if opts.bootstrap:
            bootstrap_db(db)
            return

    log('config: %s' % str(config.TRADE_BOT_TARGETS))
    monitor = TradeBot(config.TRADE_BOT_TARGETS, db)

    if opts.order_status:
        monitor.check_order_status(opts.order_status)
        return

    # Possibly clean up orders first.
    if opts.clean_up_orders:
        log('Tradebot clean up orders', admin=True)
        monitor.clean_up_orders()

    # Notify admin when trade bot starts.
    # If there is something wrong and tradebot runs in a loop,
    # admin will know.
    log('Tradebot started', admin=True)
    monitor.run()


if __name__ == '__main__':
    main()
