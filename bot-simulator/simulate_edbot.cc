#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
#include <assert.h>
#include <stdlib.h>

#include <cmath>
#include <deque>
#include <queue>

using namespace std;

double config_budget = 10000;
double config_amount = 1;
double config_take_profit_ratio = 0.02;
double config_trailing_stop_diff_ratio = 0.01;
double config_stop_loss_ratio = -0.01;
double config_init_backoff_time = 600;
double config_max_backoff_time = 86400;
double taker_fee = 0.002;
int config_delay = 10;
int flag_verbose = 0;
int num_trades = 0;
int config_check_price_time = 30;

// NOTE: Switch the flow.
// Use ratio flow for non-zero config_use_ratio.
int config_use_ratio = 0;
// Use life time flow for non-zero config_position_life.
// The time to close a position if it does not make new high.
int config_position_life_time = 0;

/* Utilities */
bool iszero(double a)
{
    return -1e-5 < a && a  < 1e-5;
}

bool isnear(double a, double b)
{
    return iszero(a - b);
}

/* TradeRecord */
struct TradeRecord {
    int32_t time;
    uint32_t trade_id;
    int64_t price;
    int64_t amount;
    uint8_t type;
} __attribute__((packed));

/* Trade */
struct Trade {
    int32_t time;
    uint32_t trade_id;
    double price;
    double amount;
    uint8_t type;
};

/* Order */
struct Order {
    enum Type {
        BUY = 'b',
        SELL = 's',
        UNKNOWN = ' ',
    };
    uint8_t type;
    uint32_t id;
    double price;
    // This is the amount needed to be filled.
    double amount;
    // Positive amount for buy. Negative amount for sell.
    double orig_amount;
    // Assume order is always market order.
    double executed_value;

    Order(Type type_, uint32_t id_, double price_, double amount_) :
            type(type_), id(id_), price(price_), amount(amount_),
            orig_amount(amount_), executed_value(0) {}
};

bool iszero(const Order& o) {
    return iszero(o.amount);
}

/* Position */
struct Position {
    enum Side {
        LONG = 'l',
        SHORT = 's',
        UNKNOWN = 'u',
    };
    uint8_t side;
    double cost;
    // Negative amount for short. Positive amount for long.
    double amount;

    Position() : side(Position::UNKNOWN), cost(0), amount(0) {}

    /* Update the base price of a position after executing an order.
    Args:
     pos[in]: The position.
     t[in]: The trade.
     fee[in]: The fee for this trade.

    Returns:
     0: Position updated with no error. Still open.
     -1: The position has a new side. This is unexpected.
     +1: The position is closed. User should take the gain.

    */
    int update_position_with_trade(const Trade t, double fee) {
        double new_amount;
        double fee_ratio;
        double new_cost;

        // Trade has positive amount for buy and negative amount for sell.
        new_amount = amount + t.amount;

        if (flag_verbose) {
            printf("%20s #%d POS: Update position with trade at price %f. "
                   "Original amount: %f, Trade amount: %f, new amount: %f\n",
                   " ", t.time, t.price, amount, t.amount, new_amount);
        }


        // Assume we never flip direction for a position.
        if (!iszero(new_amount) && !iszero(amount) && new_amount * amount < 0) {
            printf("%20s #%d POS: The position has a new side.",
                   " ", t.time);
            return -1;
        }

        // Buy takes larger cost, and sell takes less profit because of fee.
        fee_ratio = (t.type == Order::BUY) ? (1 + fee) : (1 - fee);
        // With negative amount for short and sell, we can compute the new
        // base price with the same formula.
        new_cost = cost + t.amount * t.price * fee_ratio;

        // Amount is changed from nonzero to 0. That is, this position is closed.
        // A negative new_cost means this position has gain.
        // A positive new_cost means this positon lose money.
        if (!iszero(amount) && iszero(new_amount)) {
            printf("%20s #%d POS: Position is closed with profit %f.\n",
                   " ", t.time, -new_cost);
            amount = 0;
            cost = new_cost;
            return 1;
        }

        // Amount is changed from 0 to nonzero. That is, this position is opened.
        // Set the side.
        if (iszero(amount) && !iszero(new_amount)) {
            printf("%20s #%d POS: Position is opened at base price %f.\n",
                   " ", t.time, new_cost / new_amount);
            if (new_amount > 0)
                side = Position::LONG;
            else
                side = Position::SHORT;
        }

        // Update the new base_price and amount.
        amount = new_amount;
        cost = new_cost;

        return 0;
    }
};

double get_gain(Position p) {
    assert(iszero(p.amount));
    return -p.cost;
}

double get_base_price(Position p) {
    return p.cost / p.amount;
}

double get_current_value_ratio(Position pos, double price) {
    double base_price = get_base_price(pos);
    double ratio = (price - base_price) / base_price;
    if (pos.side == Position::LONG) {
        return ratio;
    } else if (pos.side == Position::SHORT) {
        return -ratio;
    } else {
        return 0;
    }
}

double get_current_value(Position pos, double price) {
    if (pos.side == Position::UNKNOWN)
        return 0;

    double base_price = get_base_price(pos);
    double diff = price - base_price;
    return diff * pos.amount;
}



struct Book {
    // Ordered from small to large
    // This should not matter for market order book.
    deque<Order> orders;

    // Sort the orders by price.
    void add(const Order& order) {
        unsigned idx;
        for (idx = 0; idx < orders.size(); idx++)
            if (orders[idx].price > order.price)
                break;

        orders.insert(orders.begin() + idx, order);
    }

    size_t size() const { return orders.size(); }
    bool empty() const { return orders.empty(); }
    void pop_front() { orders.pop_front(); }
    void pop_back() { orders.pop_back(); }

    Order& front() { return orders.front(); }
    const Order& front() const { return orders.front(); }
    Order& back() { return orders.back(); }
    const Order& back() const { return orders.back(); }
};

struct Event {
    enum What {
    EXECUTED,
    CREATE_ORDER,
        CHECK_PRICE,
    };
    What what;
    int32_t time;
    Order order;

    Event(What what, int32_t time, Order order) :what(what), time(time), order(order) {}
};


queue<Event> event_queue;

struct Exchange {
    Book buy_orders, sell_orders;
    Position pos;
    double money = 0;
    double volume = 0;
    Position last_pos;

    // Creating margin market order.
    // Assume no need to consider either current money or current coin
    // because there will only be one position.
    void create_order(int32_t now, Order order) {
        // Assume only market order.
        // The "not enough money" case will be rejected later
        // when processing orders.
        if (order.type == Order::BUY) {
            printf("%40s #%d EXG: created BUY id:%d, %f @ %f\n",
                   " ", now, order.id, order.orig_amount, order.price);
            buy_orders.add(order);
        } else {
            printf("%40s #%d EXG: created SELL id:%d, %f @ %f\n",
                   " ", now, order.id, order.orig_amount, order.price);
            sell_orders.add(order);
        }
    }

    void process_orders_with_trade(Trade& trade) {
        int32_t now = trade.time;
        // p.s. before 2014-04-03, trades has no tag type=SELL or BUY.

        if (flag_verbose) {
            printf("#%d trade: id:%d, type: %c, amount: %f, price: %f\n",
                   now, trade.trade_id, trade.type, trade.amount, trade.price);
        }
        // Fill SELL orders by BUY history trades.
        // Assume orders are all market orders.
        while (trade.type == Order::BUY && trade.amount > 0 &&
               sell_orders.size() > 0) {
            Order& o = sell_orders.front();
            double amount = std::min(-o.amount, trade.amount);
            // o.amount is a negative number.
            // amount is a positive number.
            if (flag_verbose)
                printf("%40s #%d EXG: id:%d sell @%f (%f->%f)\n",
                       " ", now, o.id, trade.price, o.amount, o.amount + amount);

            trade.amount -= amount;
            o.amount += amount;
            double trade_value = trade.price * amount;
            o.executed_value += trade_value;
            volume += trade_value;

            // Trade is used to udpate position.
            Trade executed_trade;
            executed_trade.time = trade.time;
            executed_trade.trade_id = trade.trade_id;
            executed_trade.price = trade.price;
            // The type is flipped so it can be seen from user point of few.
            executed_trade.type = Order::SELL;
            executed_trade.amount = -amount;

            int rc;

            rc = pos.update_position_with_trade(executed_trade, taker_fee);
            if (rc == -1) {
                printf("Unexpected changing position side.\n");
                exit(1);
            } else if (rc == 1) {
                printf("%40s #%d EXG: Executed SELL to close position with gain %f.\n",
                       " ", now, get_gain(pos));
                update_money_and_close_position();
            } else if (rc != 0) {
                printf("Unexpected rc of position update.\n");
                exit(1);
            }

            if (iszero(o)) {
                printf("%40s #%d EXG: id:%d sell done. Notify bot.\n",
                       " ", now, o.id);
                event_queue.emplace(Event::EXECUTED, now + config_delay, o);
                sell_orders.pop_front();
                return;
           }
        }

        // Fill BUY orders by SELL history trades.
        // Assume orders are all market orders.
        while (trade.type == Order::SELL && trade.amount > 0 &&
               buy_orders.size() > 0) {
            Order& o = buy_orders.back();
            double amount = std::min(o.amount, trade.amount);
            if (flag_verbose)
                printf("%40s #%d EXG id:%d buy @%f (%f -> %f)\n",
                       " ", now, o.id, trade.price, o.amount, o.amount - amount);

            trade.amount -= amount;
            o.amount -= amount;

            double trade_value = trade.price * amount;
            o.executed_value += trade_value;
            volume += trade_value;

            // Trade is used to udpate position.
            Trade executed_trade;
            executed_trade.time = trade.time;
            executed_trade.trade_id = trade.trade_id;
            executed_trade.price = trade.price;
            // The type is flipped so it can be seen from user point of few.
            executed_trade.type = Order::BUY;
            executed_trade.amount = amount;

            int rc;

            rc = pos.update_position_with_trade(executed_trade, taker_fee);
            if (rc == -1) {
                printf("Unexpected changing position side.\n");
                exit(1);
            } else if (rc == 1) {
                printf("%40s #%d EXG Executed BUY to close position with gain %f.\n",
                       " ", now, get_gain(pos));
                update_money_and_close_position();
            } else if (rc != 0) {
                printf("Unexpected rc of position update.\n");
                exit(1);
            }

            if (iszero(o)) {
                printf("%40s #%d EXG id:%d buy done. Notify bot.\n",
                       " ", now, o.id);
                event_queue.emplace(Event::EXECUTED, now + config_delay, o);
                buy_orders.pop_back();
                return;
            }
        }
    }

    void update_money_and_close_position() {
        money += get_gain(pos);
        // Record in the last_pos so bot can check it.
        last_pos = pos;
        // Create a new empty position.
        pos = Position();
    }
};

double get_current_total_value(Exchange& ex, double price) {
    return ex.money + get_current_value(ex.pos, price);
}

struct Bot {
    Exchange &ex;
    Position::Side next_move;
    Book orders;
    int32_t backoff_time = config_init_backoff_time;
    int32_t next_order_id = 0;
    // When it is non zero, it means we want to protect the profit.
    // When position ratio is less than or equal to this value,
    // close position.
    double take_profit_ratio = 0;

    // Use this in check_life_time_flow.
    int32_t last_highest_ratio_time = 0;
    // Initialize highest ratio as a impossible negative value.
    double highest_ratio = -1;

    Bot(Exchange& exchange) :ex(exchange) {}

    // Assume the first move is buy.
    void init(int32_t now) {
        create_new_position(now, Position::LONG);
    }

    void create_new_position(int32_t now, Position::Side s) {
        // Reset take_profit_ratio.
        take_profit_ratio = 0;

        // Reset highest ratio and its time.
        highest_ratio = -1.0;
        last_highest_ratio_time = now;

        if (!orders.empty()) {
            printf("%100s #%d BOT: Do not create new order because "
                   "order id %d is not executed yet.\n",
                   " ", now, orders.front().id);
            return;
        }
        if (s == Position::LONG) {
            create_market_buy(now, config_amount);
        } else {
            create_market_sell(now, config_amount);
        }
    }

    void close_position(int32_t now) {
        // Reset take_profit_ratio.
        take_profit_ratio = 0;

        if (!orders.empty()) {
            printf("%100s #%d BOT: Do not close position because "
                   "order id %d is not executed yet.\n",
                   " ", now, orders.front().id);
            return;
        }
        if (ex.pos.side == Position::LONG) {
            create_market_sell(now, config_amount);
        } else {
            create_market_buy(now, config_amount);
        }
    }

    // Assume market order only.
    void create_order(int32_t now, Order o) {
        printf("%100s #%d BOT: id:%d create %s %f\n",
               " ", now, o.id, o.type == Order::BUY ? "BUY":"SELL",
               o.orig_amount);

        orders.add(o);
        event_queue.emplace(Event::CREATE_ORDER, now + config_delay, o);
    }

    void create_market_buy(int32_t now, double amount) {
        struct Order o(Order::BUY, next_order_id, 0, amount);
        create_order(now, o);
        next_order_id++;
    }

    void create_market_sell(int32_t now, double amount) {
        // Set amount to a negative number in the order.
        struct Order o(Order::SELL, next_order_id, 0, -amount);
        create_order(now, o);
        next_order_id++;
    }

    void process_executed(int32_t now, Order o) {
        // o.orig_amount is negative for SELL order.
        // o.executed_value is positive.
        printf("%100s #%d BOT: Got executed %s %f @ %f (%f USD)\n",
               " ", now, o.type == Order::BUY ? "BUY": "SELL",
               o.orig_amount, o.executed_value / abs(o.orig_amount),
               o.executed_value);

        if (orders.front().id != o.id) {
            printf("%100s #%d BOT: Got executed id %d, but expect %d\n",
                   " ", now, o.id, orders.front().id);
            exit(1);
        }

        orders.pop_front();

        double last_gain = get_gain(ex.last_pos);
        int win = (last_gain > 0);

        // Checks if the position is closed.
        if (ex.pos.amount == 0) {
            printf("%100s #%d BOT: Closed a %s position\n",
                   " ", now, win ? "WIN" : "LOSS");

            // Logic of determining next move.

            // Last is LONG and WIN.
            if (win && ex.last_pos.side == Position::LONG) {
                next_move = Position::LONG;
                // Win, so reset backoff time.
                backoff_time = config_init_backoff_time;
            }
            // Last is LONG and LOSS.
            if (!win && ex.last_pos.side == Position::LONG) {
                next_move = Position::SHORT;
                backoff_time = backoff_time << 1;
            }
            // Last is SHORT and WIN.
            if (win && ex.last_pos.side == Position::SHORT) {
                next_move = Position::SHORT;
                // Win, so reset backoff time.
                backoff_time = config_init_backoff_time;
            }
            // Last is SHORT and LOSS.
            if (!win && ex.last_pos.side == Position::SHORT) {
                next_move = Position::LONG;
                backoff_time = backoff_time << 1;
            }

            // Do not backoff too long.
            if (backoff_time > config_max_backoff_time)
                backoff_time = config_max_backoff_time;

            printf("%100s #%d BOT: Create a new %s position after backoff time %d\n",
                   " ", now, next_move == Position::LONG ? "LONG" : "SHORT",
                   backoff_time) ;
            create_new_position(now + backoff_time, next_move);
        } else {
            // Create a new position.
        }
    }

    void check_ratio_flow(int32_t now, float ratio, double price) {
        // Checks if need to stop loss.
        if (ratio < config_stop_loss_ratio) {
            printf("%80s #%d BOT: Price: %f Ratio: %f, Should close %s position to "
                   "stop loss.\n", " ", now, price, ratio,
                   ex.pos.side == Position::LONG ? "LONG" : "SHORT");
                   close_position(now);
            return;
        }

        // Checks if need to take profit.
        if (!iszero(take_profit_ratio) && ratio < take_profit_ratio) {
            printf("%80s #%d BOT: Price: %f Ratio: %f, Should close %s position to "
                   "take profit.\n ", " ", now, price, ratio,
                   ex.pos.side == Position::LONG ? "LONG" : "SHORT");
            close_position(now);
            return;
        }

        // Checks if need to set new take_profit_ratio.
        if (iszero(take_profit_ratio) && ratio > config_take_profit_ratio) {
            take_profit_ratio = ratio - config_trailing_stop_diff_ratio;
            printf("%80s #%d BOT: Price: %f Ratio: %f, Set take_profit_ratio %f to "
                   "protect profit.\n", " ", now, price, ratio,
                   take_profit_ratio);
            return;
        }

        // Checks if need to update take_profit_ratio.
        if (!iszero(take_profit_ratio)) {
            double new_take_profit_ratio = ratio - config_trailing_stop_diff_ratio;
            if (new_take_profit_ratio > take_profit_ratio) {
                take_profit_ratio = new_take_profit_ratio;
                printf("%80s #%d BOT: Price: %f Ratio: %f, Set higher take_profit_ratio %f.\n",
                       " ", now, price, ratio, take_profit_ratio);
                return;
            }
        }
    }

    /*
     * If there is no new highest ratio for config_position_life_time since
     * last high, close the position.
     * If there is a new highest ratio, update the higest ratio and
     * last highest ratio time.
     */
    void check_life_time_flow(int32_t now, float ratio, double price) {
        // New high. Update the timestamp and highest ratio.
        if (ratio > highest_ratio) {
            highest_ratio = ratio;
            last_highest_ratio_time = now;
            printf("%80s #%d BOT: Price: %f Ratio: %f, a new high\n",
                   " ", now, price, ratio);
            return;
        }

        if (now - last_highest_ratio_time >= config_position_life_time) {
            printf("%80s #%d BOT: Price: %f Ratio: %f, Should close %s position."
                   " This is a %s.\n",
                   "  ", now, price, ratio,
                   ex.pos.side == Position::LONG ? "LONG" : "SHORT",
                   ratio > 0 ? "WIN" : "LOSS");
            close_position(now);
        }
    }

    void check_price(int32_t now, double price) {
        // No position.
        if (ex.pos.side == Position::UNKNOWN)
            return;

        double ratio = get_current_value_ratio(ex.pos, price);

        if (flag_verbose) {
            printf("%80s #%d BOT: Ratio: %f, price: %f\n",
                   " ", now, ratio, price);
        }

        if (config_use_ratio) {
            check_ratio_flow(now, ratio, price);
        }

        if (config_position_life_time) {
            check_life_time_flow(now, ratio, price);
        }
    }
};

const int64_t RECORD_UNIT = 100000000;

void print_account_value(Exchange& ex, Trade t)
{
    double money = ex.money;
    double position_value = get_current_value(ex.pos, t.price);
    double position_ratio = get_current_value_ratio(ex.pos, t.price);
    double sum = money + position_value;
    printf("#%d price=%f: money=%f, pos value=%f, pos ratio=%f, "
           "total value=%f; ratio=%f\n",
           t.time, t.price, money, position_value, position_ratio,
           sum, sum / config_budget);
}

int main(int argc, char*argv[])
{
    int ch;
    while ((ch = getopt(argc, argv, "b:a:t:d:p:s:l:w:m:n:r:o:v")) != -1) {
        switch (ch) {
            case 'b':
                config_budget = atof(optarg);
                break;
            case 'a':
                config_amount = atof(optarg);
                break;
            case 't':
                taker_fee = atof(optarg);
                break;
            case 'd':
                config_delay = atoi(optarg);
                break;
            case 'p':
                config_take_profit_ratio = atof(optarg);
                break;
            case 's':
                config_trailing_stop_diff_ratio = atof(optarg);
                break;
            case 'l':
                config_stop_loss_ratio = atof(optarg);
                break;
            case 'w':
                config_init_backoff_time = atoi(optarg);
                break;
            case 'm':
                config_max_backoff_time = atoi(optarg);
                break;
            case 'n':
                num_trades = atoi(optarg);
                break;
            case 'r':
                config_use_ratio = atoi(optarg);
                break;
            case 'o':
                config_position_life_time = atoi(optarg);
                break;
            case 'v':
                flag_verbose = 1;
                break;
            default:
                fprintf(stderr, "Unknown option %c\n", ch);
                break;
        }
    }
    argc -= optind;
    argv += optind;

    TradeRecord trade_record;
    Trade trade;

    Exchange exchange;
    Bot bot(exchange);
    bool init = false;
    int32_t begin_time = 0, now = 0;
    int last_day = -1;
    double init_price;
    Trade last_trade;
    double current_total_value;
    int num_simulated_trades = 0;
    int last_check_price_time = 0;
    for (int i = 0; i < argc; i++) {
        FILE *fp = fopen(argv[i], "rb");

        while (fread(&trade_record, sizeof(trade_record), 1, fp)) {
            // hard code to ignore QSHUSD bad record
            if (trade_record.trade_id == 105316808)
                continue;

            assert(now <= trade_record.time);
            now = trade.time = trade_record.time;
            trade.trade_id = trade_record.trade_id;
            trade.price = trade_record.price * 1.0 / RECORD_UNIT;
            trade.amount = trade_record.amount * 1.0 / RECORD_UNIT;
            trade.type = trade_record.type;
#if 0
            if (flag_verbose)
            printf("#%d %f\n", now, trade.price);
#endif

            if (begin_time == 0)
                begin_time = now;

            last_trade = trade;

            // Initialize the simulation.
            if (!init) {
                init = true;
                init_price = trade.price;
                exchange.money = config_budget;
                bot.init(now);
                if (flag_verbose) {
                    print_account_value(exchange, trade);
                    printf("\n");
                }
            }

            // Print price everyday.
            double day = (double)(trade.time - begin_time) / 86400;
            if (int(day) != last_day) {
                if (flag_verbose)
                    printf("\nday=%f ------------------- last_price = %f\n\n",
                            day, last_trade.price);
                last_day = int(day);
            }

            // Handle events that happens before now.
            while (event_queue.size() > 0 && event_queue.front().time <= now) {
                Event event = event_queue.front();
                event_queue.pop();
                switch (event.what) {
                    case Event::EXECUTED:
                        bot.process_executed(now, event.order);
                        break;
                    case Event::CREATE_ORDER:
                        exchange.create_order(now, event.order);
                        break;
                    case Event::CHECK_PRICE:
                        bot.check_price(now, event.order.price);
                        break;
                }
            }
            // Let bot check price periodically.
            if (trade.time - last_check_price_time > config_check_price_time) {
                // A dummy order to notify price.
                Order o(Order::UNKNOWN, 0, trade.price, 0);
                event_queue.emplace(Event::CHECK_PRICE, now + config_delay, o);
                last_check_price_time = trade.time;
            }
            // Process order at this time.
            exchange.process_orders_with_trade(trade);

            // Checks if the bot has lost all the money.
            current_total_value = get_current_total_value(
                    exchange, trade.price);
            if (current_total_value < 0) {
                printf("Lost all the money.\n");
                exit(0);
            }

            // Print account value.
            if (flag_verbose) {
                print_account_value(exchange, trade);
                printf("\n");
            }

            // May terminate early if user specify -n.
            num_simulated_trades++;
            if (num_trades && num_simulated_trades == num_trades)
                break;

        }
        fclose(fp);
    }

    printf("Simulation done\n");
    print_account_value(exchange, last_trade);
    printf("volume=%f\n", exchange.volume);
    printf("init_price = %f, last_price = %f, ratio = %f\n",
        init_price, last_trade.price, last_trade.price / init_price);
    return 0;
}
