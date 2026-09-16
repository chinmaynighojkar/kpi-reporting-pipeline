# Decisions

## Postgres over SQLite/CSV
Power BI Desktop has a native Postgres connector, giving a real live connection
rather than an ODBC workaround. Also makes the "SQL" part of the resume claim
literal — a real warehouse schema, not flat files.

## Cloud-hosted (Neon) over local-only
GitHub Actions runners are ephemeral and can't be the persistent target of a
scheduled refresh. A local-only DB would mean the Action just runs tests, not
"delivering consistent output" on a recurring cycle. Neon's free tier gives a
real persistent Postgres instance that both the scheduled pipeline and Power BI
connect to.

## Star schema (dim_customers, dim_products, dim_date, fact_orders)
Standard dimensional modeling, order-line grain on the fact table. Matches how
BI/reporting pipelines are actually built and keeps the KPI SQL (Phase 2)
straightforward — aggregate the fact table, join to dimensions for breakdowns.

## Three siloed department sources, not one clean feed
Revised after initial build: instead of one clean transactional table, the
generator produces three separate raw exports mimicking Sales, Marketing,
and Finance/Ops each reporting independently, the way manual KPI reporting
actually worked before this pipeline existed. Each has its own realistic
mess (see "Messy raw data" below), and each carries its own embedded
anomaly (see "Three embedded anomalies" below) — one per department, so the
story isn't "one lucky catch" but "the pipeline catches issues across the
business that siloed manual reporting was missing."

## Messy raw data, landed before cleaning
`raw_sales_export`, `raw_marketing_spend`, and `raw_returns`
(`sql/schema.sql`) hold data exactly as a department's export would look:
inconsistent date formats (ISO vs DD/MM/YYYY), inconsistent text casing/
whitespace on category and channel fields, duplicate rows, occasional bad
values (zero quantity, negative refund amounts), missing optional fields
(null reason codes, ~1% guest checkouts with no email). These land in
Postgres first — inspectable directly — then `src/kpi_pipeline/transform.py`
parses, validates, dedupes, and normalizes them into the clean star schema.
Each transform function returns a report of what happened to every row
(duplicates dropped, invalid rows dropped, values fixed), not just a row
count — this is what makes "consuming time" in the resume bullet concrete:
real cleaning work with a visible, auditable outcome, not a rename of an
already-clean table.

## Three embedded anomalies (one per department)
1. **Sales — Home & Kitchen decline.** `category_weights_for_date` decays
   Home & Kitchen's share of daily order volume over the final 70 days,
   bottoming out at 40% of baseline, with the removed share redistributed
   proportionally across the other 5 categories. Total revenue stays
   flat-to-growing while the category quietly declines.
2. **Marketing — Paid Social CAC blowout.** Paid Social ad spend ramps up
   2.6x over the final 84 days while that channel's share of new customer
   acquisition (a static weighted assignment, independent of time) doesn't
   grow to match — cost per acquired customer rises as a result.
3. **Finance/Ops — Electronics return spike.** Electronics orders placed
   15-70 days before the dataset's end date get a 16% return rate
   (defect-style) vs. a 3% baseline everywhere else, sampled against real,
   already-loaded `fact_orders` rows and landed as raw `raw_returns` rows
   with the same messiness as the other sources.

All three are invisible in a naive top-line total and only surface once
broken out the way the KPI views/dashboards do it — that's the actual
point of the project, not decoration.

Verified by direct SQL query after seeding (`scripts/seed_database.py`,
3,000 customers, 563 days, ~56k clean order lines):
- Home & Kitchen weekly revenue: ~€29k (early July) → ~€13k (early Sept),
  while total weekly revenue held in the €165k-190k band throughout.
- Paid Social CAC: ~€252 (May) → ~€325 (June) → ~€448 (July) → ~€632
  (August) — monthly, not weekly, because at this data volume weekly new-
  customer counts per channel are too small to show a clean signal; CAC is
  usually reported monthly in practice anyway, so this matches real
  practice rather than being a workaround.
- Electronics return rate: 13.3% in the defect window vs. 2.4-2.9% for
  every other category in the same window.

These numbers are the concrete examples used in the README and the
interview STAR story — three separate, independently-verifiable findings.

## Customer base size (3,000, not 600)
Initially seeded with 600 customers; the Marketing CAC signal was too
noisy at that volume (single-digit new-customer counts per channel per
month). Increased to 3,000 so each anomaly has enough underlying volume to
produce a dashboard-presentable trend rather than requiring an explanation
of sampling noise. Order volume (`BASE_ORDERS_PER_DAY`) was left unchanged,
so this also slightly lowers average purchase frequency per customer,
which is realistic for a larger customer base.

## Phase 3: daily incremental pipeline design
`scripts/run_pipeline.py` reuses the exact same `generate_raw_*` functions
as the historical seed, called once per missing day, so there's one code
path for "how a day of data gets generated" rather than a seed-only path
and a separate incremental path that could drift apart.

Three things needed rethinking to go from a one-shot historical seed to a
script that runs forever on a daily cron:

1. **Growth/seasonality plateau.** `daily_order_count` and
   `category_weights_for_date` take a `start_date`/`end_date` pair and
   compute progress through that window. For incremental runs, `end_date`
   is passed as the day being processed itself, so `progress` always
   evaluates to 1.0 — order-volume growth and the Home & Kitchen decline
   both hold at the level they reached by `DATASET_END_DATE` rather than
   continuing to climb or decay indefinitely. Read as "the pre-launch
   ramp-up period is over; post-launch volume holds at the steady state it
   reached," which is a defensible real-world story, not an artifact.
2. **Returns anomaly moved to fixed calendar dates.** Originally
   `RETURNS_ANOMALY_WINDOW_START/END_DAYS` were offsets from `end_date`.
   That's fine for a one-shot seed but wrong for a recurring job: with
   `end_date` = today on every run, "70 days before end_date" would keep
   sliding forward and the defect spike would never stop reproducing.
   Replaced with fixed dates (`RETURNS_ANOMALY_ORDER_START/END`,
   2026-07-06 to 2026-08-30) so the anomaly is what it's supposed to be —
   a resolved past incident — and daily runs naturally stop generating it
   once real time passes 2026-08-30.
3. **Returns decided once per order line, not re-evaluated daily.** A
   return is only sampled on the day an order line turns exactly
   `RETURN_LAG_MAX_DAYS` (25) old, guaranteeing `return_date <= today` by
   construction and that each line gets exactly one Bernoulli trial. The
   alternative (re-scanning a rolling window every day) risks either
   multiple trials per line or future-dated returns.

Verified: `scripts/run_pipeline.py` run twice back-to-back is idempotent
(second call finds nothing to do and no-ops; see
`tests/test_pipeline.py::test_run_pipeline_is_idempotent`). Data-quality
assertions (no non-positive quantity/price, no negative refunds, every
processed day has a `dim_date` row) run at the end of every invocation and
raise loudly (`SystemExit`) rather than silently loading bad data.

## Customer purchase-frequency modeling
Each synthetic customer gets a lognormal `activity_score` at creation, used as
a selection weight when generating orders. This produces realistic variance
in purchase frequency (some repeat buyers, many one-time buyers) without
hand-scripting it, which is what makes a repeat-purchase-rate KPI meaningful
rather than trivially uniform.

## Seasonality model
Daily order volume = Poisson-sampled around an expected count that combines:
customer-base growth (linear, ~1.6x over 18 months), a 1.3x weekend
multiplier, and a 1.5x Nov 20-Dec 31 holiday multiplier. Chosen to make the
data look like real e-commerce traffic (not flat/uniform) without needing an
external seasonality dataset.

## Date range
2025-03-01 to 2026-09-14 (~18.5 months, 563 days), ending the day before the
project's "today." A recurring pipeline reports on completed days, so the
append-one-day function (used identically for seeding and for the daily
GitHub Actions run) always targets a fully elapsed day.
