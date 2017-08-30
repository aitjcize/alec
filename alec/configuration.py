# Configuration for the Alec trading bot.

import ast
import os


class Config(object):
    SLACK_ENABLE = bool(os.getenv('SLACK_ENABLE', False))
    SLACK_TOKEN = os.getenv('SLACK_TOKEN')
    SLACK_CHANNEL = os.getenv('SLACK_CHANNEL')

    BFX_WS_ENDPOINT = 'wss://api.bitfinex.com/ws/2'
    BFX_API_KEY = os.getenv('BFX_API_KEY')
    BFX_API_SECRET = os.getenv('BFX_API_SECRET')

    # Monitor config
    PRICE_MONITOR_THRESHOLD = float(os.getenv('PRICE_MONITOR_THRESHOLD', 0.01))
    PRICE_MONITOR_WINDOW_SIZE = int(os.getenv('PRICE_MONITOR_WINDOW_SIZE', 30))
    PRICE_MONITOR_PAIRS = (
        ast.literal_eval(os.getenv('PRICE_MONITOR_PAIRS')) or
        ['tBTCUSD', 'tETHUSD', 'tBCHUSD', 'tXMRUSD', 'tIOTUSD', 'tXRPUSD',
         'tOMGUSD', 'tDSHUSD', 'tEOSUSD', 'tETCUSD', 'tZECUSD', 'tSANUSD'])
