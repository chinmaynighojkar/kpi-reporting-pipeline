# Decisions

This document records the engineering decisions behind the KPI Reporting Pipeline, in the order they were made. Each entry states the decision, the reasoning, and where relevant, the verification that confirmed it worked.

## Platform

### Postgres instead of SQLite or flat files
Power BI Desktop has a native PostgreSQL connector. That gives a direct live connection instead of an ODBC workaround, and it makes the "SQL" part of the project's premise literal: a real warehouse schema, not a set of CSVs.

### Neon (cloud-hosted Postgres) instead of a local database
GitHub Actions runners are ephemeral. They cannot serve as the persistent target of a scheduled refresh. A local-only database would mean the scheduled job just runs tests, not a pipeline that delivers consistent output on a recurring cycle. Neon's free tier provides a persistent Postgres instance that both the scheduled pipeline and Power BI connect to.

## Data model

### Star schema: dim_customers, dim_products, dim_date, fact_orders
Standard dimensional modeling, order-line grain on the fact table. This matches how production reporting pipelines are built and keeps the KPI SQL straightforward: aggregate the fact table, join to dimensions for breakdowns.

### Three siloed department sources instead of one clean feed
The generator produces three separate raw exports that mimic Sales, Marketing, and Finance/Ops each reporting independently, the way manual KPI reporting worked before this pipeline existed. Each source has its own realistic data-quality issues and its own embedded anomaly, so the project demonstrates catching problems across a business rather than one isolated finding.

### Messy raw data, landed before cleaning
`raw_sales_export`, `raw_marketing_spend`, and `raw_returns` (see `sql/schema.sql`) hold data exactly as a department's export would look: inconsistent date formats (ISO and DD/MM/YYYY), inconsistent casing and whitespace on category and channel fields, duplicate rows, occasional invalid values (zero quantity, negative refund amounts), and missing optional fields (null reason codes, roughly one percent guest checkouts with no email).

This data lands in Postgres first, where it can be inspected directly, before `src/kpi_pipeline/transform.py` parses, validates, deduplicates, and normalizes it into the clean star schema. Every transform function returns a report of what happened to each row: duplicates dropped, invalid rows dropped, values corrected. That report is what makes the pipeline's value concrete: it performs real, auditable cleaning work rather than a pass-through rename of an already-clean table.

### Three embedded anomalies, one per department
1. **Sales: Home & Kitchen decline.** `category_weights_for_date` decays Home & Kitchen's share of daily order volume over the final 70 days of the dataset, bottoming out at 40 percent of baseline, with the removed share redistributed proportionally across the other five categories. Total revenue stays flat to growing while the category quietly declines.
2. **Marketing: Paid Social CAC increase.** Paid Social ad spend ramps up 2.6x over the final 84 days while that channel's share of new customer acquisition, a static weighted assignment independent of time, does not grow to match. Cost per acquired customer rises as a result.
3. **Finance/Ops: Electronics return spike.** Electronics orders placed 15 to 70 days before the dataset's end date carry a 16 percent return rate, a defect-style pattern, against a 3 percent baseline everywhere else. Returns are sampled against real, already-loaded `fact_orders` rows and landed as raw `raw_returns` rows with the same data-quality issues as the other sources.

All three anomalies are invisible in a naive top-line total and only surface once the data is broken out the way the KPI views and dashboards do it. That is the point of the project.

Verified by direct SQL query after seeding (`scripts/seed_database.py`, 3,000 customers, 563 days, approximately 56,000 clean order lines):

- Home & Kitchen weekly revenue: approximately 29,000 EUR in early July, down to approximately 13,000 EUR by early September, while total weekly revenue held in the 165,000 to 190,000 EUR band throughout.
- Paid Social CAC: 252 EUR in May, 325 EUR in June, 448 EUR in July, 632 EUR in August. Reported monthly rather than weekly because at this data volume, weekly new-customer counts per channel are too small to show a clean signal. CAC is typically reported monthly in practice, so this matches standard usage rather than working around a limitation.
- Electronics return rate: 13.3 percent in the defect window against 2.4 to 2.9 percent for every other category in the same window.

These numbers are the concrete evidence behind the project's README and the interview narrative: three separate, independently verifiable findings.

### Customer base size: 3,000, not 600
The database was initially seeded with 600 customers. At that volume, the Marketing CAC signal was too noisy: single-digit new-customer counts per channel per month. The customer count was increased to 3,000 so each anomaly has enough underlying volume to produce a dashboard-presentable trend rather than requiring an explanation of sampling noise. Order volume (`BASE_ORDERS_PER_DAY`) was left unchanged, which also lowers average purchase frequency per customer, a realistic effect of a larger customer base.

## Pipeline design

### Daily incremental pipeline reuses the seed's generation logic
`scripts/run_pipeline.py` calls the same `generate_raw_*` functions as the historical seed, once per missing day. There is one code path for how a day of data gets generated, not a seed-only path and a separate incremental path that could drift apart over time.

Three design questions had to be resolved to take a one-shot historical seed and turn it into a script that runs indefinitely on a daily cron schedule.

1. **Growth and seasonality plateau at the dataset boundary.** `daily_order_count` and `category_weights_for_date` take a `start_date` and `end_date` pair and compute progress through that window. For incremental runs, `end_date` is passed as the day being processed, so progress always evaluates to 1.0. Order-volume growth and the Home & Kitchen decline both hold at the level they reached by `DATASET_END_DATE` instead of continuing to climb or decay indefinitely. This is read as: the pre-launch ramp-up period is over, and post-launch volume holds at the steady state it reached. That is a defensible business story, not an artifact of the code.
2. **The returns anomaly moved from a relative window to fixed calendar dates.** The window was originally defined as an offset from `end_date`. That works for a one-shot seed but breaks a recurring job: with `end_date` equal to today on every run, "70 days before end_date" would keep sliding forward and the defect spike would never stop reproducing. It was replaced with fixed dates, `RETURNS_ANOMALY_ORDER_START` and `RETURNS_ANOMALY_ORDER_END`, 2026-07-06 to 2026-08-30, so the anomaly behaves as what it represents: a resolved past incident. Daily runs stop generating it once real time passes 2026-08-30.
3. **Each order line is evaluated for a return exactly once, not re-evaluated daily.** A return is sampled only on the day an order line turns exactly `RETURN_LAG_MAX_DAYS` (25) days old. This guarantees `return_date` is never in the future relative to the run date, by construction, and that each line receives exactly one Bernoulli trial. The alternative, re-scanning a rolling window every day, risks either multiple trials per line or future-dated returns.

Verified: running `scripts/run_pipeline.py` twice in immediate succession is idempotent. The second call finds nothing to do and exits without side effects (see `tests/test_pipeline.py::test_run_pipeline_is_idempotent`). Data-quality assertions, checking for non-positive quantity or price, negative refunds, and a `dim_date` row for every processed day, run at the end of every invocation and raise `SystemExit` on failure rather than silently loading bad data.

### Customer purchase-frequency modeling
Each synthetic customer receives a lognormal `activity_score` at creation, used as a selection weight when generating orders. This produces realistic variance in purchase frequency, some repeat buyers and many one-time buyers, without hand-scripting the distribution. It is what makes a repeat-purchase-rate KPI meaningful rather than trivially uniform.

### Seasonality model
Daily order volume is Poisson-sampled around an expected count that combines customer-base growth (linear, approximately 1.6x over 18 months), a 1.3x weekend multiplier, and a 1.5x holiday multiplier for November 20 through December 31. This produces data that looks like real e-commerce traffic rather than a flat, uniform series, without requiring an external seasonality dataset.

### Date range
2025-03-01 to 2026-09-14, approximately 18.5 months, 563 days, ending the day before the project's reference date. A recurring pipeline reports on completed days, so the append-one-day logic, used identically for seeding and for the daily GitHub Actions run, always targets a fully elapsed day.

## Continuous integration

### GitHub Actions workflow registration
After pushing `.github/workflows/daily_pipeline.yml` and setting the `DATABASE_URL` secret, GitHub reported zero registered workflows for approximately 15 minutes. The API (`gh api .../actions/workflows`) returned `total_count: 0` and `gh workflow run` returned 404, despite the file being valid YAML on `master` and Actions being fully enabled at the repository level.

An empty commit did not fix it: an empty commit touches no files, so it gives GitHub's indexer nothing new to scan. Pushing an actual content change to the workflow file resolved it immediately. This is recorded because it looks identical to a genuine Actions or permissions failure until the actual cause, an indexing gap tied to file content changes, is known.

## Security review

Prompted by a direct question about whether the project had been security-tested. It had not: all 43 tests at that point were correctness tests. A review was run once the repository became public and started holding a real database credential path.

Findings verified clean:
- `.env` was never committed. This was checked against full git history, not just the current `.gitignore` state.
- `DATABASE_URL` is stored as a GitHub encrypted secret and is never printed in scripts or workflow output.
- The database connection uses `sslmode=require` and `channel_binding=require`.
- Every query value is passed through parameterized psycopg placeholders (`%s`). None are string-interpolated.
- `pip-audit` against the installed environment found no known vulnerabilities in the current dependency versions.
- All customer data is Faker-generated, not real personal information, so the public repository and CSV exports do not expose anyone's real data.

Findings fixed:
- Two locations built SQL table or view names using an f-string (`scripts/export_local_backup.py`, `tests/test_kpi_views.py`). This was not exploitable, since those names come from a hardcoded Python list rather than external input, but f-string SQL is the wrong pattern to leave in a codebase: it is easy to copy into a context where the input is not trusted. Both were switched to `psycopg.sql.Identifier`, the correct mechanism for composing identifiers, since table and column names cannot be bound as `%s` parameters at all.
- The GitHub Actions workflow had no `permissions` block, so `GITHUB_TOKEN` defaulted to the repository's read/write setting even though the job never calls the GitHub API. Added `permissions: contents: read`.
- GitHub's secret scanning was already enabled with zero alerts. Dependabot and vulnerability alerts were disabled and were turned on.

Noted but not changed: dependency versions use a lower bound only (`>=`), with no upper bound or lockfile. This is an acceptable tradeoff for a portfolio project's threat model. Pinning trades automatic security patches for reproducibility, and this pipeline does not run unattended in a context where that tradeoff matters.
