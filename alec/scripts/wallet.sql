CREATE TABLE wallet (
    id INTEGER PRIMARY KEY,
    currency TEXT,
	timestamp INTEGER,
    wallet TEXT,

    -- values are *10^8.
    -- bitfinex used at least 8 digits. I don't know if 8 is enough or not.
	amount INTEGER,
    balance INTEGER,

    description TEXT,

    -- following are parsed information from description
    kind TEXT,  -- withdraw, deposit, transfer, fee, payment
    pair TEXT,  -- for exchange
    price INTEGER,  -- for exchange

    -- bitfinex v1 doesn't give us primary id, so just assume identical values are unique
    CONSTRAINT unique_record UNIQUE (currency, timestamp, wallet, amount, balance, description)
);

-- I'm lazy. Just put random thing.
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX wallet_currency_idx ON wallet(currency, timestamp);
