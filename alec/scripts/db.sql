CREATE VIEW today
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, side, count(*), sum(price*amount)
FROM executed_orders
WHERE day = DATE('now')
GROUP BY side;


CREATE VIEW today_coin
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, side, symbol, count(*), sum(price*amount)
FROM executed_orders
WHERE day = DATE('now')
GROUP BY symbol, side
ORDER BY symbol, side;


CREATE VIEW history
AS
SELECT DATE(datetime(timestamp, 'unixepoch')) as day, count(*), sum(price*amount)
FROM executed_orders
WHERE side = 'sell'
GROUP BY day
ORDER BY day DESC;
