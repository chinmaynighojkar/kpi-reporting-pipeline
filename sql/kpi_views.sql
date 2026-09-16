-- KPI views the Power BI dashboards (Phase 5) read from.
-- Run by scripts/create_kpi_views.py. CREATE OR REPLACE: safe to re-run.

-- 1. Daily revenue, orders, AOV, net of refunds.
CREATE OR REPLACE VIEW vw_daily_revenue AS
SELECT
    d.date,
    d.day_name,
    d.is_weekend,
    COUNT(DISTINCT f.order_id)                              AS orders,
    SUM(f.quantity)                                          AS units,
    ROUND(SUM(f.line_revenue), 2)                            AS gross_revenue,
    ROUND(COALESCE(r.refunds, 0), 2)                         AS refunds,
    ROUND(SUM(f.line_revenue) - COALESCE(r.refunds, 0), 2)   AS net_revenue,
    ROUND(SUM(f.line_revenue) / NULLIF(COUNT(DISTINCT f.order_id), 0), 2) AS aov
FROM fact_orders f
JOIN dim_date d ON f.date_id = d.date_id
LEFT JOIN (
    SELECT date_id, SUM(refund_amount) AS refunds
    FROM fact_returns
    GROUP BY date_id
) r ON r.date_id = d.date_id
GROUP BY d.date, d.day_name, d.is_weekend, r.refunds;

-- 2. Category revenue by week, with each category's share of that week's total.
CREATE OR REPLACE VIEW vw_category_weekly AS
WITH weekly AS (
    SELECT
        d.year, d.week_of_year, MIN(d.date) AS week_start,
        p.category,
        SUM(f.line_revenue) AS revenue
    FROM fact_orders f
    JOIN dim_date d ON f.date_id = d.date_id
    JOIN dim_products p ON f.product_id = p.product_id
    GROUP BY d.year, d.week_of_year, p.category
)
SELECT
    week_start,
    category,
    ROUND(revenue, 2) AS revenue,
    ROUND(100.0 * revenue / SUM(revenue) OVER (PARTITION BY year, week_of_year), 2) AS pct_of_week
FROM weekly;

-- 3. Region summary: customers, orders, revenue, AOV.
CREATE OR REPLACE VIEW vw_region_summary AS
SELECT
    c.region,
    COUNT(DISTINCT c.customer_id)                            AS customers,
    COUNT(DISTINCT f.order_id)                                AS orders,
    ROUND(SUM(f.line_revenue), 2)                              AS revenue,
    ROUND(SUM(f.line_revenue) / NULLIF(COUNT(DISTINCT f.order_id), 0), 2) AS aov
FROM fact_orders f
JOIN dim_customers c ON f.customer_id = c.customer_id
WHERE c.region <> 'Unknown'
GROUP BY c.region;

-- 4. Repeat-purchase rate by signup cohort month.
CREATE OR REPLACE VIEW vw_customer_repeat_rate AS
WITH customer_orders AS (
    SELECT c.customer_id,
           DATE_TRUNC('month', c.signup_date)::date AS cohort_month,
           COUNT(DISTINCT f.order_id) AS n_orders
    FROM dim_customers c
    LEFT JOIN fact_orders f ON f.customer_id = c.customer_id
    WHERE c.region <> 'Unknown'
    GROUP BY c.customer_id, c.signup_date
)
SELECT
    cohort_month,
    COUNT(*)                                   AS customers,
    COUNT(*) FILTER (WHERE n_orders >= 2)      AS repeat_customers,
    ROUND(100.0 * COUNT(*) FILTER (WHERE n_orders >= 2) / NULLIF(COUNT(*), 0), 2) AS repeat_rate_pct
FROM customer_orders
GROUP BY cohort_month
ORDER BY cohort_month;

-- 5. Customer acquisition cost by month and paid channel.
CREATE OR REPLACE VIEW vw_marketing_cac_monthly AS
WITH monthly_spend AS (
    SELECT DATE_TRUNC('month', d.date)::date AS month, m.channel, SUM(m.spend) AS spend
    FROM fact_marketing_spend m
    JOIN dim_date d ON m.date_id = d.date_id
    GROUP BY DATE_TRUNC('month', d.date), m.channel
),
monthly_customers AS (
    SELECT DATE_TRUNC('month', c.signup_date)::date AS month, c.acquisition_channel AS channel,
           COUNT(*) AS new_customers
    FROM dim_customers c
    WHERE c.acquisition_channel <> 'Unknown'
    GROUP BY DATE_TRUNC('month', c.signup_date), c.acquisition_channel
)
SELECT
    s.month,
    s.channel,
    ROUND(s.spend, 2)                                       AS spend,
    COALESCE(mc.new_customers, 0)                            AS new_customers,
    ROUND(s.spend / NULLIF(mc.new_customers, 0), 2)          AS cac
FROM monthly_spend s
LEFT JOIN monthly_customers mc ON mc.month = s.month AND mc.channel = s.channel
ORDER BY s.month, s.channel;

-- 6b. Return rate by category and week, so a defect-style spike is visible
-- as a trend rather than diluted into a lifetime average.
CREATE OR REPLACE VIEW vw_return_weekly AS
WITH order_weeks AS (
    SELECT f.order_line_id, p.category, d.year, d.week_of_year, MIN(d.date) OVER (PARTITION BY d.year, d.week_of_year) AS week_start
    FROM fact_orders f
    JOIN dim_products p ON f.product_id = p.product_id
    JOIN dim_date d ON f.date_id = d.date_id
)
SELECT
    ow.week_start,
    ow.category,
    COUNT(DISTINCT ow.order_line_id)                                       AS order_lines,
    COUNT(DISTINCT r.order_line_id)                                        AS returned_lines,
    ROUND(100.0 * COUNT(DISTINCT r.order_line_id) / NULLIF(COUNT(DISTINCT ow.order_line_id), 0), 2) AS return_rate_pct
FROM order_weeks ow
LEFT JOIN fact_returns r ON r.order_line_id = ow.order_line_id
GROUP BY ow.week_start, ow.category;

-- 6. Return rate and refund impact by category (lifetime).
CREATE OR REPLACE VIEW vw_return_summary AS
SELECT
    p.category,
    COUNT(DISTINCT f.order_line_id)                                        AS order_lines,
    COUNT(DISTINCT r.order_line_id)                                        AS returned_lines,
    ROUND(100.0 * COUNT(DISTINCT r.order_line_id) / NULLIF(COUNT(DISTINCT f.order_line_id), 0), 2) AS return_rate_pct,
    ROUND(COALESCE(SUM(r.refund_amount), 0), 2)                            AS refund_amount
FROM fact_orders f
JOIN dim_products p ON f.product_id = p.product_id
LEFT JOIN fact_returns r ON r.order_line_id = f.order_line_id
GROUP BY p.category;
