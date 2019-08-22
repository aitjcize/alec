from __future__ import print_function
import datetime
import decimal
import json
import logging
import re
import subprocess
import time

import requests
import urllib3

urllib3.disable_warnings()

class CobinhoodError(Exception):
    pass


def rate_limit(period):
    """Rate limit decorator

    Args:
        period: in seconds
    """
    last_call_time = {}

    def decorator(func):
        def wrapper(*args, **kargs):
            now = time.time()
            delta = now - last_call_time.get(func, 0)
            last_call_time[func] = now
            if delta < period:
                logging.debug(
                    '%s was called too frequently. delay %s seconds',
                    func.__name__, period - delta)
                time.sleep(period - delta)
            result = func(*args, **kargs)
            return result

        return wrapper

    return decorator
 

class CobinhoodRestApi(object):
    API_ENDPOINT = 'https://api.cobinhood.com'

    def __init__(self):
        self.access_token = ''
        self.token_file = '.cobinhood_token'

        self.session = requests.Session()

        self._load_token()

    def _update_token(self, token):
        data = subprocess.check_output(['jwt', '--no-verify', token])
        data = json.loads(data)
        self.token_expire_time = data['exp']
        print('token exp time', datetime.datetime.fromtimestamp(data['exp']))
        if data['exp'] < time.time():
            raise Exception('token expired')

        self.access_token = token

    def _load_token(self):
        token = file(self.token_file).read().strip()
        self._update_token(token)


    @rate_limit(0.1)
    def _request(self, path, payload=None, method='GET'):
        url = self.API_ENDPOINT + path
        logging.debug('request %s', path)
        headers = {
                'authorization': self.access_token,
                'origin': 'https://cobinhood.com',
                }

        if method == 'GET':
            r = self.session.get(url, verify=False, headers=headers)
        elif method == 'POST':
            headers['nonce'] = str(int(time.time() * 1000))
            r = self.session.post(url, data=json.dumps(payload), verify=False, headers=headers)
        elif method == 'DELETE':
            headers['nonce'] = str(int(time.time() * 1000))
            r = self.session.delete(url, data=payload, verify=False, headers=headers)
        else:
            assert 0, 'unknown method: ' + method

        if 100 <= r.status_code <= 399:
            result = r.json()
            self._normalize(path, result)
            assert result['success']
            return result['result']

        print(r.content)

        result = r.json()
        self._normalize(path, result)
        assert not result['success']
        raise CobinhoodError(result['error'])

    def _normalize(self, path, data):
        if isinstance(data, list):
            return [self._normalize(path, x) for x in data]

        for k, v in data.items():
            if isinstance(v, dict):
                self._normalize(path, v)
                continue

            if k in [
                    # /v1/market/currencies
                    'min_unit',
                    # /var/market/trading_pairs
                    'base_min_size',
                    'base_max_size',
                    'quote_increment',
                    # /v1/market/stats
                    'latestPrice',
                    'lowestAsk',
                    'highestBid',
                    'baseVolume',
                    'quoteVolume',
                    'high24hr',
                    'low24hr',
                    'percentChanged24hr',
                    # /v1/market/tickers/*
                    'price',
                    'highest_bid',
                    'lowest_ask',
                    '24h_volume',
                    '24h_high',
                    '24h_low',
                    '24h_open',
                    # /v1/chart/candles/*
                    'open',
                    'close',
                    'high',
                    'low',
                    'volume',
                    # /v1/trading/orders/*
                    'price',
                    'size',
                    'filled',
                    # /v1/trading/orders/<order_id>/trades
                    'price',
                    'size',
                    # /v1/wallet/ledger
                    'balance',
                    # /v1/wallet/balances
                    'total',
                    'on_order',
                    ]:
                data[k] = decimal.Decimal(v)
                continue

            if k in ['bids', 'asks']:
                data[k] = [map(decimal.Decimal, x) for x in v]
                continue

            if isinstance(v, list):
                data[k] = self._normalize(path, v)
                continue

        return data




    def is_pair(self, s):
        return re.match(r'^[A-Z]+-[A-Z]+$', s)

    def is_currency(self, s):
        return re.match(r'^[A-Z]+$', s)

    def is_side(self, s):
        return s in ['bid', 'ask']

    def is_order_type(self, s):
        return s in ['market', 'limit', 'stop', 'stop_limit']

    def refresh_token(self):
        result = self._request('/v1/account/refreshToken', method='POST')
        token = result['account']['token']
        self._update_token(token)
        with open(self.token_file, 'w') as f:
            f.write(token)
        return token

    def system_info(self):
        result = self._request('/v1/system/info')
        return result

    def time(self):
        result = self._request('/v1/system/time')
        return result['time']

    def currencies(self):
        result = self._request('/v1/market/currencies')
        return result['currencies']

    def trading_pairs(self):
        result = self._request('/v1/market/trading_pairs')
        return {x['id']: x for x in result['trading_pairs']}

    def order_book(self, pair, limit=None):
        assert re.match(r'^[A-Z]+-[A-Z]+$', pair)
        result = self._request('/v1/market/orderbooks/%s' % pair)
        return result['orderbook']

    def place_order(self, pair, side, type, price, size):
        assert side in ('buy', 'sell')  # easier
        side = {'sell': 'ask', 'buy': 'bid'}[side]
        assert self.is_pair(pair)
        assert self.is_side(side)
        assert self.is_order_type(type)

        payload = {
                'trading_pair_id': pair,
                'side': side,
                'type': type,
                'price': str(price),
                'size': str(size),
                }
        logging.debug('place_order %s', payload)
        result = self._request('/v1/trading/orders', payload, method='POST')
        return result['order']

    def cancel_order(self, order_id):
        self._request('/v1/trading/orders/%s' % order_id, method='DELETE')

    def wallet_balance(self):
        result = self._request('/v1/wallet/balances')
        return result['balances']


def main():
    api = CobinhoodRestApi()
    print(api.time())

    print('currencies')
    for c in api.currencies():
        print(c['currency'], c)

    for pair, info in api.trading_pairs().items():
        print(pair, info['quote_currency_id'], info['base_currency_id'], info)

    book = api.order_book('COB-ETH')
    print('bids')
    for bid in book['bids']:
        print('\t', bid)
    print('asks')
    for ask in book['asks']:
        print('\t', ask)

    for b in api.wallet_balance():
        print(b['currency'], b['total'], b['on_order'])

if __name__ == '__main__':
    main()
