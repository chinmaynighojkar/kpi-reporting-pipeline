-- Star schema + raw landing tables for the KPI reporting pipeline.
-- Run once by scripts/seed_database.py. Safe to re-run: drops and recreates.
--
-- Raw tables (raw_*) hold messy, denormalized rows as each department would
-- actually export them. The pipeline (src/kpi_pipeline/transform.py) reads
-- these, cleans/validates/dedupes, and loads the result into the clean
-- fact/dim tables below.

DROP TABLE IF EXISTS raw_returns CASCADE;
DROP TABLE IF EXISTS raw_marketing_spend CASCADE;
DROP TABLE IF EXISTS raw_sales_export CASCADE;
DROP TABLE IF EXISTS fact_returns CASCADE;
DROP TABLE IF EXISTS fact_marketing_spend CASCADE;
DROP TABLE IF EXISTS fact_orders CASCADE;
DROP TABLE IF EXISTS dim_customers CASCADE;
DROP TABLE IF EXISTS dim_products CASCADE;
DROP TABLE IF EXISTS dim_date CASCADE;

CREATE TABLE dim_customers (
    customer_id         SERIAL PRIMARY KEY,
    full_name           TEXT NOT NULL,
    email               TEXT NOT NULL,
    region              TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL,
    signup_date         DATE NOT NULL,
    activity_score      NUMERIC(6, 3) NOT NULL  -- relative purchase-frequency weight
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

CREATE TABLE fact_marketing_spend (
    spend_id        SERIAL PRIMARY KEY,
    date_id         INTEGER NOT NULL REFERENCES dim_date(date_id),
    channel         TEXT NOT NULL,
    spend           NUMERIC(10, 2) NOT NULL
);

CREATE INDEX idx_fact_marketing_date ON fact_marketing_spend(date_id);

CREATE TABLE fact_returns (
    return_id        SERIAL PRIMARY KEY,
    order_line_id    BIGINT NOT NULL REFERENCES fact_orders(order_line_id),
    date_id          INTEGER NOT NULL REFERENCES dim_date(date_id),
    reason           TEXT NOT NULL,
    refund_amount    NUMERIC(10, 2) NOT NULL
);

CREATE INDEX idx_fact_returns_date ON fact_returns(date_id);
CREATE INDEX idx_fact_returns_order_line ON fact_returns(order_line_id);

-- ---------------------------------------------------------------------
-- Raw landing tables: messy, denormalized, as each department exports it.
-- ---------------------------------------------------------------------

CREATE TABLE raw_sales_export (
    raw_id              SERIAL PRIMARY KEY,
    order_id            INTEGER,
    order_date          TEXT,        -- inconsistent formats (ISO or DD/MM/YYYY)
    customer_email      TEXT,        -- NULL for guest checkouts
    customer_name       TEXT,
    region              TEXT,
    acquisition_channel TEXT,
    product_name        TEXT,
    category            TEXT,        -- inconsistent casing/whitespace
    quantity            INTEGER,     -- occasionally 0 (bad data)
    unit_price          NUMERIC(10, 2),
    discount            NUMERIC(4, 3),
    loaded_at           TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE raw_marketing_spend (
    raw_id      SERIAL PRIMARY KEY,
    spend_date  TEXT,
    channel     TEXT,        -- inconsistent casing
    spend       NUMERIC(10, 2),
    loaded_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE raw_returns (
    raw_id          SERIAL PRIMARY KEY,
    order_line_id   BIGINT,
    category        TEXT,        -- inconsistent casing; not used for FK resolution
    return_date     TEXT,
    reason          TEXT,        -- sometimes missing
    refund_amount   NUMERIC(10, 2),  -- occasionally negative (data-entry typo)
    loaded_at       TIMESTAMP NOT NULL DEFAULT now()
);
