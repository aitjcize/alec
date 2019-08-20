#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
#include <assert.h>
#include <stdlib.h>

#include <deque>
#include <queue>

using namespace std;

double config_amount = 200;
double config_budget = config_amount * 30;
double config_limit = config_budget - config_amount * 50;
double config_step = 1.025;
double config_profit = config_step * config_step;
double maker_fee = 0.001;
double taker_fee = 0.002;
int config_delay = 10;
int flag_verbose = 0;

struct TradeRecord {
    int32_t time;
    uint32_t trade_id;
    int64_t price;
    int64_t amount;
    uint8_t type;
} __attribute__((packed));

struct Trade {
    int32_t time;
    uint32_t trade_id;
    double price;
    double amount;
    uint8_t type;
};


bool iszero(double a)
{
    return -1e-5 < a && a  < 1e-5;
}

bool isnear(double a, double b)
{
    return iszero(a - b);
}

struct Order {
    enum Type {
        BUY = 'b',
        SELL = 's',
        UNKNOWN = ' ',
    };
    uint8_t type;
    double price;
    double amount;
    double orig_amount;

    Order(Type type_, double price_, double amount_) :type(type_), price(price_), amount(amount_), orig_amount(amount_) {}
};

bool iszero(const Order& o) {
    return iszero(o.amount / o.orig_amount);
}


struct Book {
    // ordered from small to large
    deque<Order> orders;

    bool has(const Order& order) {
	for (const auto &o : orders) {
	    if (isnear(o.orig_amount, order.orig_amount))
		return true;
	}
	return false;
    }

    void remove(const Order& order) {
	for (int i = 0; i < orders.size(); i++) {
	    if (isnear(orders[i].orig_amount, order.orig_amount)) {
		orders.erase(orders.begin() + i);
		break;
	    }
	}
    }

    void add(const Order& order) {
	int idx;
	for (idx = 0; idx < orders.size(); idx++)
	    // special case 0, which should be last
	    if ((order.price != 0 && orders[idx].price > order.price) ||
		    orders[idx].price == 0)
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
	CANCEL_ORDER,
    };
    What what;
    int32_t time;
    Order order;

    Event(What what, int32_t time, Order order) :what(what), time(time), order(order) {}
};


queue<Event> event_queue;

struct Exchange {
    Book buy_orders, sell_orders;
    double coin = 0;
    double coin_locked = 0;
    double money = 0;
    double money_locked = 0;
    double volume = 0;

    void create_order(int32_t now, Order order) {
	if (order.type == Order::BUY) {
	    // "money_locked" doesn't matter because they will be canceled
	    if (money - (order.price * order.amount) < config_limit) {
		if (flag_verbose)
		    printf("#%d EXG: money not enough to buy\n", now);
		return;
	    }
	    if (flag_verbose)
		printf("#%d EXG: created BUY %f@%f\n", now, order.amount, order.price);
	    money_locked += order.price * order.amount;
	    buy_orders.add(order);
	} else {
	    if (order.amount > coin) {
		if (flag_verbose)
		    printf("#%d EXG: coin not enough to sell %f@%f, will retry\n",
			    now,
			    order.amount, order.price);
		event_queue.emplace(Event::CREATE_ORDER, now + config_delay, order);
		return;
	    }
	    if (flag_verbose)
		printf("#%d EXG: created SELL %f@%f\n", now, order.amount, order.price);
	    coin_locked += order.amount;
	    coin -= order.amount;
	    sell_orders.add(order);
	}
    }

    void cancel_order(int32_t now, Order order) {
	if (order.type == Order::BUY) {
	    for (auto &o : buy_orders.orders) {
		if (isnear(o.orig_amount, order.orig_amount)) {
		    money_locked -= o.amount * o.price;
		    buy_orders.remove(o);
		    if (flag_verbose)
			printf("#%d EXG: canceled BUY %f@%f\n",
				now, order.amount, order.price);
		    break;
		}
	    }
	} else {
	    assert(0);
	}
    }

    void process_orders(Trade& trade) {
	int32_t now = trade.time;
	// p.s. before 2014-04-03, trades has no tag type=SELL or BUY.

	while (trade.type == Order::BUY && trade.amount > 0 &&
		sell_orders.size() > 0 && sell_orders.front().price < trade.price) {
	    Order& o = sell_orders.front();
	    double amount = min(o.amount, trade.amount);
	    if (flag_verbose)
		printf("#%d EXG: sell @%f (%f->%f)\n",
			now,
			o.price, o.amount, o.amount - amount);
	    o.amount -= amount;
	    trade.amount -= amount;
	    coin_locked -= amount;
	    money += o.price * amount * (1.0-maker_fee);
	    volume += o.price * amount;

	    if (iszero(o)) {
		if (flag_verbose)
		    printf("#%d EXG: sell done, to notify\n", now);
		event_queue.emplace(Event::EXECUTED, now + config_delay, o);
		sell_orders.pop_front();
		return;
	    }
	}

	while (trade.type == Order::SELL && trade.amount > 0 &&
		buy_orders.size() > 0 && 
		(buy_orders.back().price == 0 || buy_orders.back().price > trade.price)) {
	    Order& o = buy_orders.back();
	    double amount = min(o.amount, trade.amount);
	    double price = o.price == 0? trade.price : o.price;
	    double fee = o.price == 0? taker_fee : maker_fee;
	    if (flag_verbose)
		printf("#%d EXG buy @%f (%f -> %f)\n",
			now,
			price, o.amount, o.amount - amount);
	    o.amount -= amount;
	    trade.amount -= amount;
	    coin += amount * (1.0 - fee);
	    money -= amount * price;
	    money_locked -= o.price * amount;
	    volume += price * amount;

	    if (iszero(o)) {
		if (flag_verbose)
		    printf("#%d EXG buy done, to notify\n", now);
		event_queue.emplace(Event::EXECUTED, now + config_delay, o);
		buy_orders.pop_back();
		return;
	    }
	}
    }
};

struct Bot {
    Exchange &ex;
    Book buy_orders, sell_orders;
    int64_t last_chase = 0;

    Bot(Exchange& exchange) :ex(exchange) {}

    void init(int32_t now, double last_price) {
	{
	    double price = last_price / config_step;
	    double amount = config_amount /price;
	    create_order(now, Order(Order::BUY, price, amount));
	}

	may_chase_coin(now, last_price);

	{
	    double price = last_price * config_profit;
	    double amount = config_amount / price;
	    assert(price > 0);
	    create_order(now, Order(Order::SELL, price, amount));
	}
    }

    void create_order(int32_t now, Order o) {
	if (o.type == Order::BUY) {
	    if (buy_orders.has(o))
		return;
	    if (o.price != 0)
		buy_orders.add(o);
	} else {
	    if (sell_orders.has(o))
		return;
	    if (o.price != 0)
		sell_orders.add(o);
	}
	if (flag_verbose)
	    printf("#%d BOT: create %s %f@%f\n",
		    now,
		    o.type == Order::BUY ? "BUY":"SELL",
		    o.amount, o.price);

	event_queue.emplace(Event::CREATE_ORDER, now + config_delay, o);
    }

    void cancel_order(int32_t now, Order o) {
	if (flag_verbose)
	    printf("#%d BOT: cancel %s %f@%f\n",
		    now,
		    o.type == Order::BUY ? "BUY":"SELL",
		    o.amount, o.price);
	event_queue.emplace(Event::CANCEL_ORDER, now + config_delay, o);
    }

    void may_chase_coin(int32_t now, double last_price) {
	if (last_chase + 60 > now)
	    return;

	if (ex.coin <= config_amount / last_price * 3) {
	    last_chase = now;
	    // i'm lazy, use stupid formula
	    int unit = 3 - int(ex.coin * last_price / config_amount) + 2;
	    if (flag_verbose)
		printf("#%d BOT: chase %d times coin\n", now, unit);
	    double price = last_price;
	    double amount = config_amount / price * unit;
	    create_order(now, Order(Order::BUY, 0, amount));
	}
    }

    void process_executed(int32_t now, Order o) {
	if (flag_verbose)
	    printf("#%d BOT: Got executed %s %f@%f (%f USD)\n",
		    now,
		    o.type == Order::BUY ? "BUY": "SELL",
		    o.orig_amount, o.price,
		    o.orig_amount * o.price);

	if (o.type == Order::SELL) {
	    sell_orders.remove(o);

	    if (!isnear(config_amount * config_profit, o.price * o.orig_amount))
		return;

	    {
		double price = o.price / config_profit;
		double amount = config_amount / price;
		create_order(now, Order(Order::BUY, price, amount));
	    }

	    may_chase_coin(now, o.price);

	    {
		double price = o.price * config_step;
		double amount = o.orig_amount / config_step;
		assert(price > 0);
		create_order(now, Order(Order::SELL, price, amount));
	    }
	}

	if (o.type == Order::BUY) {
	    buy_orders.remove(o);

	    if (!isnear(config_amount, o.price * o.orig_amount))
		return;

	    {
		double price = o.price / config_step;
		double amount = config_amount /price;
		create_order(now, Order(Order::BUY, price, amount));
	    }

	    may_chase_coin(now, o.price);

	    {
		double price = o.price * config_profit;
		double amount = o.orig_amount;
		assert(price > 0);
		create_order(now, Order(Order::SELL, price, amount));
	    }
	}

	while (buy_orders.size() > 3) {
	    Order o = buy_orders.front();
	    cancel_order(now, o);
	    buy_orders.remove(o);
	}
    }
};

const int64_t RECORD_UNIT = 100000000;

template<class T>
void print_orders(T& a)
{
    printf("\tbuy orders(%zu): ", a.buy_orders.size());
    for (auto o : a.buy_orders.orders) {
        printf("%f@%f ", o.amount, o.price);
    }
    printf("\n");

    printf("\tsell orders(%zu): ", a.sell_orders.size());
    for (auto o : a.sell_orders.orders) {
        printf("%f@%f ", o.amount, o.price);
    }
    printf("\n");
}

template<class T>
void print_account_value(double price, T& a)
{
    double total = a.money + (a.coin + a.coin_locked) * price;
    printf("price=%f: money=%f, coin=%f (%f free), total value=%f; ratio=%f\n",
            price,
            a.money,
            a.coin + a.coin_locked,
	    a.coin,
            total,
	    total / config_budget);
}

int main(int argc, char*argv[])
{
    int ch;
    while ((ch = getopt(argc, argv, "b:s:p:a:m:t:d:v")) != -1) {
	switch (ch) {
	    case 'b':
		config_budget = atof(optarg);
		break;
	    case 's':
		config_step = atof(optarg);
		break;
	    case 'p':
		config_profit = atof(optarg);
		break;
	    case 'a':
		config_amount = atof(optarg);
		break;
	    case 'm':
		maker_fee = atof(optarg);
		break;
	    case 't':
		taker_fee = atof(optarg);
		break;
	    case 'd':
		config_delay = atoi(optarg);
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
    double last_coin = 0, last_money = 0;
    int32_t begin_time = 0, now = 0;
    int last_day = -1;
    double init_price, last_price;
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
	    last_price = trade.price;

	    if (!init) {
		init = true;
		init_price = trade.price;
		exchange.money = config_budget;
		bot.init(now, init_price);
		if (flag_verbose) {
		    print_orders(exchange);
		    print_account_value(trade.price, exchange);
		    printf("\n");
		}
	    }

	    double day = (double)(trade.time - begin_time) / 86400;
	    if (int(day) != last_day) {
		if (flag_verbose)
		    printf("day=%f ----------------------------- last_price = %f\n",
			    day, last_price);
		last_day = int(day);
	    }

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
		    case Event::CANCEL_ORDER:
			exchange.cancel_order(now, event.order);
		}
	    }
	    exchange.process_orders(trade);

	    if (exchange.coin != last_coin || exchange.money != last_money) {
		if (flag_verbose) {
		    print_orders(exchange);
		    print_account_value(trade.price, exchange);
		    printf("bot state:\n");
		    print_orders(bot);
		    printf("\n");
		}
		last_coin = exchange.coin;
		last_money = exchange.money;
	    }
	}
	fclose(fp);
    }
    if (flag_verbose) {
	print_orders(exchange);
    }
    print_account_value(last_price, exchange);
    printf("volume=%f\n", exchange.volume);
    printf("init_price = %f, last_price = %f, ratio = %f\n",
	    init_price, last_price, last_price / init_price);
    return 0;
}
