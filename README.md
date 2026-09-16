# KPI Reporting Pipeline

An end to end pipeline that replaces manual, siloed KPI reporting with an automated data pipeline and a live dashboard, built to demonstrate the same pattern used in production reporting systems.

## Problem

Recurring KPI reporting is commonly done by hand: each department, sales, marketing, and finance, exports its own numbers on its own cycle, and a person consolidates them into a spreadsheet. Two failure modes follow directly from that process.

First, it consumes time every cycle and hides trends that only become visible once data is broken out by category, channel, or cohort rather than viewed as a single top-line number. Second, once the numbers are consolidated, they are usually left in raw tabular form, which is not interpretable by non-technical stakeholders who need a decision, not a spreadsheet.

## Solution

This project builds the pipeline that removes both failure modes.

- A Python and SQL pipeline lands raw, messy data exactly as three departments (Sales, Marketing, Finance/Ops) would export it, then parses, validates, deduplicates, and normalizes it into a Postgres star schema.
- A GitHub Actions workflow runs the pipeline on a daily schedule, appending any missing days and refreshing the KPI views, so the output stays current without manual intervention.
- Power BI dashboards connect live to those KPI views and translate the results into visuals built for two audiences: a technical view for the underlying trends, and a decision-ready view for stakeholders who need the finding, not the query.

## The three findings

The dataset embeds three real anomalies, one per department, each invisible in a naive top-line total and visible only once the data is broken out correctly. These are the concrete evidence that the pipeline does what the problem statement claims.

1. **Sales.** Home & Kitchen weekly revenue fell from approximately 29,000 EUR to approximately 13,000 EUR over ten weeks, while total weekly revenue held steady in the 165,000 to 190,000 EUR range across the same period. A top-line revenue report would show no problem at all.
2. **Marketing.** Paid Social customer acquisition cost rose from 252 EUR to 632 EUR over four months as ad spend increased 2.6x without a matching increase in acquired customers.
3. **Finance/Ops.** Electronics orders placed during a defined window carried a 13.3 percent return rate against a 2.4 to 2.9 percent baseline for every other category in the same window, consistent with a product defect.

Full methodology and the exact figures are documented in `docs/DECISIONS.md`.

## Architecture

```
Synthetic data generator (Python)
        |
        v
Raw landing tables in Postgres (raw_sales_export, raw_marketing_spend, raw_returns)
        |
        v
Transform layer (src/kpi_pipeline/transform.py): parse, validate, deduplicate, normalize
        |
        v
Star schema (dim_customers, dim_products, dim_date, fact_orders, fact_marketing_spend, fact_returns)
        |
        v
KPI views (sql/kpi_views.sql): revenue, AOV, category mix, retention, CAC, return rate
        |
        +--> GitHub Actions (daily cron): appends missing days, refreshes views, runs tests
        |
        +--> Power BI: connects live, builds the dashboards
```

## Tech stack

Python, PostgreSQL (hosted on Neon), SQL, GitHub Actions, Power BI, pytest.

## Repository layout

```
sql/                SQL: schema definition and KPI views
src/kpi_pipeline/    Generation and transform logic
scripts/             Entry points: seed, incremental run, view refresh, local export
tests/               43 tests covering generation, transform, views, and pipeline idempotency
docs/DECISIONS.md    Full decision log with reasoning and verification
.github/workflows/   Daily scheduled pipeline run
```

## Running it

```
pip install -e ".[dev]"
cp .env.example .env    # fill in a Postgres connection string
python scripts/seed_database.py       # one-time: schema, historical data, KPI views
python scripts/run_pipeline.py        # daily: appends missing days, refreshes views
pytest -q                             # 43 tests
```

## Status

The pipeline, database, and CI schedule are complete and running. Power BI dashboards are in progress. This README will be updated with dashboard screenshots and the final write-up once that work is done.
