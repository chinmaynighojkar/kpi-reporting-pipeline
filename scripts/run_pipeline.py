"""Daily incremental pipeline: appends every fully-elapsed day missing since
the last run, using the same generator/transform functions as the
historical seed, then re-applies the KPI views and runs data-quality
assertions. This is the script GitHub Actions (Phase 4) calls on a
schedule.

Design notes (see docs/DECISIONS.md):
- Growth/seasonality and the Sales/Marketing anomalies are evaluated with
  end_date = the day being processed, so once real time moves past the
  original 2025-03-01 to 2026-09-14 history, those trends hold at the
  level they reached (a "new steady state"), rather than continuing to
  climb or decaying further. The DATASET_START_DATE anchor keeps the
  growth curve continuous across the seed/incremental boundary.
- Returns are decided exactly once per order line, on the day that line
  turns RETURN_LAG_MAX_DAYS old -- not re-evaluated daily -- so a return
  is never recorded before it could plausibly have happened, and the
  Electronics defect window (fixed calendar dates) naturally stops firing
  once real time moves past it.
"""

import datetime as dt
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kpi_pipeline import generator, transform  # noqa: E402
from kpi_pipeline.db import get_connection  # noqa: E402
from kpi_pipeline.generator import GUEST_EMAIL, OrderLineRef  # noqa: E402


def missing_dates(last_loaded_date: dt.date, today: dt.date) -> list[dt.date]:
    """Every fully-elapsed day after last_loaded_date, up to (not including) today."""
    dates = []
    d = last_loaded_date + dt.timedelta(days=1)
    while d < today:
        dates.append(d)
        d += dt.timedelta(days=1)
    return dates


def _print_report(label: str, report: dict) -> None:
    print(f"  {label}: " + ", ".join(f"{k}={v}" for k, v in report.items()))


def run(rng: np.random.Generator | None = None, today: dt.date | None = None) -> list[dt.date]:
    rng = rng or np.random.default_rng()
    today = today or dt.date.today()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT MAX(date) FROM dim_date")
    (last_loaded_date,) = cur.fetchone()

    days_to_process = missing_dates(last_loaded_date, today)
    if not days_to_process:
        print(f"Up to date (last loaded: {last_loaded_date}). Nothing to append.")
        cur.close()
        conn.close()
        return []

    print(f"Appending {len(days_to_process)} day(s): {days_to_process[0]} to {days_to_process[-1]}")

    # Lookups needed by transform (rebuilt fresh each run so new dim rows are visible).
    cur.execute("SELECT customer_id, email FROM dim_customers")
    customer_id_by_email = {email: cid for cid, email in cur.fetchall()}
    cur.execute("SELECT product_id, category, product_name FROM dim_products")
    product_id_by_category_name = {(cat, name): pid for pid, cat, name in cur.fetchall()}
    cur.execute("SELECT product_name, category, list_price FROM dim_products")
    products_by_category: dict[str, list[dict]] = {}
    for name, cat, price in cur.fetchall():
        products_by_category.setdefault(cat, []).append(
            {"product_name": name, "category": cat, "list_price": float(price)}
        )
    guest_customer_id = customer_id_by_email[GUEST_EMAIL]

    cur.execute("SELECT MAX(order_id) FROM fact_orders")
    (max_order_id,) = cur.fetchone()
    order_id = (max_order_id or 0) + 1

    for target_date in days_to_process:
        print(f"Processing {target_date}...")

        cur.execute("SELECT COUNT(*) FROM dim_date WHERE date = %s", (target_date,))
        if cur.fetchone()[0] == 0:
            date_row = generator.build_date_dimension(target_date, target_date)[0]
            cur.execute(
                """INSERT INTO dim_date
                   (date_id, date, year, month, day, day_of_week, day_name, week_of_year, is_weekend)
                   VALUES (%(date_id)s, %(date)s, %(year)s, %(month)s, %(day)s,
                           %(day_of_week)s, %(day_name)s, %(week_of_year)s, %(is_weekend)s)""",
                date_row,
            )

        cur.execute(
            """SELECT full_name, email, region, acquisition_channel, activity_score
               FROM dim_customers WHERE signup_date <= %s AND email <> %s""",
            (target_date, GUEST_EMAIL),
        )
        eligible_customers = [
            {
                "full_name": r[0],
                "email": r[1],
                "region": r[2],
                "acquisition_channel": r[3],
                "activity_score": float(r[4]),
            }
            for r in cur.fetchall()
        ]

        # --- Sales ---
        raw_sales_rows, order_id = generator.generate_raw_sales_rows_for_day(
            target_date,
            order_id,
            eligible_customers,
            products_by_category,
            generator.DATASET_START_DATE,
            target_date,
            rng,
        )
        if raw_sales_rows:
            cur.executemany(
                """INSERT INTO raw_sales_export
                   (order_id, order_date, customer_email, customer_name, region, acquisition_channel,
                    product_name, category, quantity, unit_price, discount)
                   VALUES (%(order_id)s, %(order_date)s, %(customer_email)s, %(customer_name)s,
                           %(region)s, %(acquisition_channel)s, %(product_name)s, %(category)s,
                           %(quantity)s, %(unit_price)s, %(discount)s)""",
                raw_sales_rows,
            )
            clean_sales, sales_report = transform.clean_sales_rows(
                raw_sales_rows, customer_id_by_email, product_id_by_category_name, guest_customer_id
            )
            _print_report("Sales", sales_report)
            if clean_sales:
                cur.executemany(
                    """INSERT INTO fact_orders
                       (order_id, customer_id, product_id, date_id, quantity, unit_price, discount, line_revenue)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    clean_sales,
                )

        # --- Marketing ---
        raw_marketing_rows = generator.generate_raw_marketing_rows_for_day(
            target_date, generator.DATASET_START_DATE, target_date, rng
        )
        if raw_marketing_rows:
            cur.executemany(
                """INSERT INTO raw_marketing_spend (spend_date, channel, spend)
                   VALUES (%(spend_date)s, %(channel)s, %(spend)s)""",
                raw_marketing_rows,
            )
            clean_marketing, marketing_report = transform.clean_marketing_rows(raw_marketing_rows)
            _print_report("Marketing", marketing_report)
            if clean_marketing:
                cur.executemany(
                    """INSERT INTO fact_marketing_spend (date_id, channel, spend) VALUES (%s, %s, %s)""",
                    clean_marketing,
                )

        # --- Finance/Ops returns: decide once, for lines that just turned
        # RETURN_LAG_MAX_DAYS old (see module docstring) ---
        decision_date = target_date - dt.timedelta(days=generator.RETURN_LAG_MAX_DAYS)
        cur.execute(
            """SELECT f.order_line_id, p.category, d.date, f.line_revenue
               FROM fact_orders f
               JOIN dim_products p ON f.product_id = p.product_id
               JOIN dim_date d ON f.date_id = d.date_id
               WHERE d.date = %s""",
            (decision_date,),
        )
        order_line_refs = [
            OrderLineRef(order_line_id=r[0], category=r[1], order_date=r[2], line_revenue=float(r[3]))
            for r in cur.fetchall()
        ]
        if order_line_refs:
            raw_returns_rows = generator.generate_raw_returns_rows(order_line_refs, target_date, rng)
            if raw_returns_rows:
                cur.executemany(
                    """INSERT INTO raw_returns (order_line_id, category, return_date, reason, refund_amount)
                       VALUES (%(order_line_id)s, %(category)s, %(return_date)s, %(reason)s, %(refund_amount)s)""",
                    raw_returns_rows,
                )
                clean_returns, returns_report = transform.clean_returns_rows(raw_returns_rows)
                _print_report("Returns", returns_report)
                if clean_returns:
                    cur.executemany(
                        """INSERT INTO fact_returns (order_line_id, date_id, reason, refund_amount)
                           VALUES (%s, %s, %s, %s)""",
                        clean_returns,
                    )

        conn.commit()

    print("Refreshing KPI views...")
    kpi_views_sql = (Path(__file__).resolve().parents[1] / "sql" / "kpi_views.sql").read_text()
    cur.execute(kpi_views_sql)
    conn.commit()

    _run_data_quality_checks(cur, days_to_process)

    cur.close()
    conn.close()
    print(f"Done. Appended {len(days_to_process)} day(s).")
    return days_to_process


def _run_data_quality_checks(cur, days_processed: list[dt.date]) -> None:
    print("Running data-quality checks...")

    cur.execute("SELECT COUNT(*) FROM fact_orders WHERE quantity <= 0 OR unit_price <= 0 OR line_revenue < 0")
    (bad_orders,) = cur.fetchone()
    if bad_orders:
        raise SystemExit(f"Data quality FAILED: {bad_orders} fact_orders rows with invalid quantity/price/revenue")

    cur.execute("SELECT COUNT(*) FROM fact_returns WHERE refund_amount < 0")
    (bad_returns,) = cur.fetchone()
    if bad_returns:
        raise SystemExit(f"Data quality FAILED: {bad_returns} fact_returns rows with negative refund_amount")

    for target_date in days_processed:
        cur.execute("SELECT COUNT(*) FROM dim_date WHERE date = %s", (target_date,))
        if cur.fetchone()[0] == 0:
            raise SystemExit(f"Data quality FAILED: dim_date missing row for {target_date}")

    print("  All checks passed.")


if __name__ == "__main__":
    run()
