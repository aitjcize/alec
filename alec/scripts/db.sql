CREATE VIEW today
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, count(*), sum(price*amount)
FROM executed_orders
WHERE day = DATE('now') AND side = 'sell';

CREATE VIEW today_coin
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, symbol, count(*), sum(price*amount) AS value
FROM executed_orders
WHERE day = DATE('now') AND side = 'sell'
GROUP BY symbol
ORDER BY value;

CREATE VIEW history
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, count(*), sum(price*amount)
FROM executed_orders
WHERE side = 'sell'
GROUP BY day
ORDER BY day DESC;
