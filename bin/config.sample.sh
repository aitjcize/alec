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
