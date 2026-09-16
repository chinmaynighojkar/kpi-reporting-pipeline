-- Star schema for the KPI reporting pipeline.
-- Run once by scripts/seed_database.py. Safe to re-run: drops and recreates.

DROP TABLE IF EXISTS fact_orders CASCADE;
DROP TABLE IF EXISTS dim_customers CASCADE;
DROP TABLE IF EXISTS dim_products CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

CREATE TABLE dim_customers (
    customer_id     SERIAL PRIMARY KEY,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL,
    region          TEXT NOT NULL,
    signup_date     DATE NOT NULL,
    activity_score  NUMERIC(6, 3) NOT NULL  -- relative purchase-frequency weight
);

CREATE TABLE dim_products (
    product_id      SERIAL PRIMARY KEY,
    product_name    TEXT NOT NULL,
    category        TEXT NOT NULL,
    unit_cost       NUMERIC(10, 2) NOT NULL,
    list_price      NUMERIC(10, 2) NOT NULL
);

CREATE TABLE dim_date (
    date_id         INTEGER PRIMARY KEY,     -- YYYYMMDD
    date            DATE NOT NULL UNIQUE,
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    day             INTEGER NOT NULL,
    day_of_week     INTEGER NOT NULL,        -- 0=Monday .. 6=Sunday
    day_name        TEXT NOT NULL,
    week_of_year    INTEGER NOT NULL,
    is_weekend      BOOLEAN NOT NULL
);

CREATE TABLE fact_orders (
    order_line_id   BIGSERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL,
    customer_id     INTEGER NOT NULL REFERENCES dim_customers(customer_id),
    product_id      INTEGER NOT NULL REFERENCES dim_products(product_id),
    date_id         INTEGER NOT NULL REFERENCES dim_date(date_id),
    quantity        SMALLINT NOT NULL,
    unit_price      NUMERIC(10, 2) NOT NULL,
    discount        NUMERIC(4, 3) NOT NULL DEFAULT 0,
    line_revenue    NUMERIC(12, 2) NOT NULL
);

CREATE INDEX idx_fact_orders_date ON fact_orders(date_id);
CREATE INDEX idx_fact_orders_product ON fact_orders(product_id);
CREATE INDEX idx_fact_orders_customer ON fact_orders(customer_id);
