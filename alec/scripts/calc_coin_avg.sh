#!/bin/sh

DB=wallet.db

SELECT_BALANCE='(
    SELECT sum(amount)/100000000.
    FROM wallet
    WHERE currency=t.currency
)'
SELECT_COST='(
    SELECT -sum(amount)/100000000.
    FROM wallet
    WHERE currency="USD" AND 
        (pair=(t.currency||"USD") OR description LIKE ("%fee%"||t.currency||"USD%")
        )
)'

SELECT_PRICE='(
    SELECT price/100000000.
    FROM wallet
    WHERE pair=t.currency||"USD" AND kind="exchange"
    ORDER BY timestamp DESC
    LIMIT 1
)'

sqlite3 -column -header $DB "
SELECT  -- for display
    currency,
    balance,
    cost,
    (CASE WHEN cost > 0
          THEN round(cost / balance, 8)
	  ELSE '' END) AS unit_cost,
    last_price AS price,
    (CASE WHEN cost > 0
          THEN round(cost / balance / last_price, 4)
	  ELSE '' END) AS ratio
FROM (
    SELECT  -- to query fields
        currency,
        $SELECT_BALANCE AS balance,
        $SELECT_COST AS cost,
        $SELECT_PRICE AS last_price
        FROM wallet AS t
        GROUP BY currency
        ORDER BY currency
        )
"
