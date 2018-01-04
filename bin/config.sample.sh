export SLACK_ENABLE=false
export SLACK_ADMIN=
export SLACK_TOKEN=
export SLACK_CHANNEL=

export BFX_API_KEY=
export BFX_API_SECRET=

export PRICE_MONITOR_THRESHOLD=0.005
export PRICE_MONITOR_WINDOW_SIZE=30
export PRICE_MONITOR_PAIRS="['tBTCUSD', 'tETHUSD']"
export RATE_MONITOR_SYMBOLS="['fUSD']"

# Usage example:
# export TRADE_JBOT_TARGETS="{'etcusd':{'unit': '0.8', 'step': '0.01'}, 'iotusd':{'unit': '6', 'step': '0.01'}}"
# unit: amount of coin in one order.
# step: upon execution of an order, create one sell order at price * (1 + step) and one buy order at
#       price * (1 - step).
export TRADE_JBOT_TARGETS=
# The table to store executed orders. e.g. "tradebot.db"
export TRADE_JBOT_DB=

# Usage example:
# export TRADE_HBOT_CONFIG="{
#   'buy_currency': True,
#   'retry_in_error': True,
#   'retry_in_timeout': True,
#   'control_lendbot': {
#     'enable': True,
#     'target': 1.5,
#     'start_threshold': 1.6,
#     'stop_threshold': 1.0,
#   },
#   'symbols': {
#     'OMGUSD': {'percent': 1.025, 'profit': 2, 'amount': 200, 'type': 'usd', 'limit': 0, 'hold': 100}
#   }
# }"
#
# control_lendbot:
# If exchange balance > funding balance * start_threshold, start lendbot.
# If exchange balance < funding balance * stop_threshold, stop lendbot.
# The ratio should be start_threshold > target > stop_threshold.
#
# limit parameter is optional. If limit is 0 or omitted, it means unlimited.
# hold parameter is optional. It means how many cryptocurrency you want to hold.
# If type is crypto, buy/sell fixed amount of crypto currency
#   Take OMGUSDD for example, drop 2.5%, buy 25 OMG
#   rise (1.025)**2 = 5.06%, sell 25 OMG
#   The maximum amount is |amount| * |limit| coins.
# If type is usd, buy/sell fixed amount of USD
#   The maximum amount is |amount| * |limit| usd.
# Check https://api.bitfinex.com/v1/symbols to know symbols
export TRADE_HBOT_CONFIG="{}"
