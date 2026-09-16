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

## The embedded "hidden trend"
The generator (`src/kpi_pipeline/generator.py`, `category_weights_for_date`)
decays Home & Kitchen's share of daily order volume over the final 70 days of
the dataset, bottoming out at 40% of its baseline weight, with the removed
share redistributed proportionally across the other 5 categories. This keeps
total revenue flat-to-growing while one category quietly declines.

Verified after seeding (see `scripts/seed_database.py` output, 56,773 order
lines across 563 days): weekly Home & Kitchen revenue fell from ~€34.5k
(week of 2026-06-29) to ~€14.5k (week of 2026-09-07) — a 58% decline — while
total weekly revenue stayed in the €130k-160k band the entire period with no
comparable trend. A raw top-line weekly revenue export would miss this
entirely; it only surfaces once revenue is broken out by category. This is
the concrete example used in the README and the interview STAR story.

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
