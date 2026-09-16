"""One-time historical seed: creates the schema, lands ~18 months of raw
messy department exports (Sales, Marketing, Finance/Ops) in Postgres, then
runs the same transform the recurring pipeline uses to produce the clean
star schema + fact tables. Re-running drops and recreates everything.

See docs/DECISIONS.md for the three embedded anomalies this seed produces.
"""

import datetime as dt
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kpi_pipeline import generator, transform  # noqa: E402
from kpi_pipeline.db import get_connection  # noqa: E402
from kpi_pipeline.generator import GUEST_EMAIL, OrderLineRef  # noqa: E402

START_DATE = dt.date(2025, 3, 1)
END_DATE = dt.date(2026, 9, 14)
SEED = 20260916


def _print_report(label: str, report: dict) -> None:
    print(f"  {label}: " + ", ".join(f"{k}={v}" for k, v in report.items()))


def main() -> None:
    rng = np.random.default_rng(SEED)
    conn = get_connection()
    cur = conn.cursor()

    print("Creating schema...")
    schema_sql = (Path(__file__).resolve().parents[1] / "sql" / "schema.sql").read_text()
    cur.execute(schema_sql)
    conn.commit()

    # --- Dimensions ---------------------------------------------------
    print("Generating dimensions...")
    dates = generator.build_date_dimension(START_DATE, END_DATE)
    customers = generator.generate_customers(generator.N_CUSTOMERS, START_DATE, END_DATE, rng)
    guest_customer = {
        "full_name": "Guest Checkout",
        "email": GUEST_EMAIL,
        "region": "Unknown",
        "acquisition_channel": "Unknown",
        "signup_date": START_DATE,
        "activity_score": 0.0,
    }
    customers = [guest_customer] + customers
    products = generator.generate_products(rng)

    print(f"Inserting {len(dates)} dates, {len(customers)} customers, {len(products)} products...")
    cur.executemany(
        """INSERT INTO dim_date
           (date_id, date, year, month, day, day_of_week, day_name, week_of_year, is_weekend)
           VALUES (%(date_id)s, %(date)s, %(year)s, %(month)s, %(day)s,
                   %(day_of_week)s, %(day_name)s, %(week_of_year)s, %(is_weekend)s)""",
        dates,
    )
    cur.executemany(
        """INSERT INTO dim_customers
           (full_name, email, region, acquisition_channel, signup_date, activity_score)
           VALUES (%(full_name)s, %(email)s, %(region)s, %(acquisition_channel)s,
                   %(signup_date)s, %(activity_score)s)""",
        customers,
    )
    cur.executemany(
        """INSERT INTO dim_products (product_name, category, unit_cost, list_price)
           VALUES (%(product_name)s, %(category)s, %(unit_cost)s, %(list_price)s)""",
        products,
    )
    conn.commit()

    # SERIAL ids are assigned in insertion order on a freshly (re)created table.
    for i, c in enumerate(customers, start=1):
        c["customer_id"] = i
    for i, p in enumerate(products, start=1):
        p["product_id"] = i

    guest_customer_id = customers[0]["customer_id"]
    customer_id_by_email = {c["email"]: c["customer_id"] for c in customers}
    product_id_by_category_name = {(p["category"], p["product_name"]): p["product_id"] for p in products}
    products_by_category: dict[str, list[dict]] = {}
    for p in products:
        products_by_category.setdefault(p["category"], []).append(p)

    real_customers = customers[1:]  # exclude guest from order-eligibility pool

    # --- Sales: raw -> clean --------------------------------------------
    print("Generating raw Sales export...")
    raw_sales_rows: list[dict] = []
    order_id = 1
    date = START_DATE
    while date <= END_DATE:
        eligible = [c for c in real_customers if c["signup_date"] <= date]
        day_rows, order_id = generator.generate_raw_sales_rows_for_day(
            date, order_id, eligible, products_by_category, START_DATE, END_DATE, rng
        )
        raw_sales_rows.extend(day_rows)
        date += dt.timedelta(days=1)

    print(f"  {len(raw_sales_rows)} raw sales rows generated. Landing in raw_sales_export...")
    cur.executemany(
        """INSERT INTO raw_sales_export
           (order_id, order_date, customer_email, customer_name, region, acquisition_channel,
            product_name, category, quantity, unit_price, discount)
           VALUES (%(order_id)s, %(order_date)s, %(customer_email)s, %(customer_name)s,
                   %(region)s, %(acquisition_channel)s, %(product_name)s, %(category)s,
                   %(quantity)s, %(unit_price)s, %(discount)s)""",
        raw_sales_rows,
    )
    conn.commit()

    print("  Transforming Sales export...")
    clean_sales, sales_report = transform.clean_sales_rows(
        raw_sales_rows, customer_id_by_email, product_id_by_category_name, guest_customer_id
    )
    _print_report("Sales", sales_report)

    cur.executemany(
        """INSERT INTO fact_orders
           (order_id, customer_id, product_id, date_id, quantity, unit_price, discount, line_revenue)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        clean_sales,
    )
    conn.commit()

    # --- Marketing: raw -> clean -----------------------------------------
    print("Generating raw Marketing export...")
    raw_marketing_rows: list[dict] = []
    date = START_DATE
    while date <= END_DATE:
        raw_marketing_rows.extend(
            generator.generate_raw_marketing_rows_for_day(date, START_DATE, END_DATE, rng)
        )
        date += dt.timedelta(days=1)

    print(f"  {len(raw_marketing_rows)} raw marketing rows generated. Landing in raw_marketing_spend...")
    cur.executemany(
        """INSERT INTO raw_marketing_spend (spend_date, channel, spend)
           VALUES (%(spend_date)s, %(channel)s, %(spend)s)""",
        raw_marketing_rows,
    )
    conn.commit()

    print("  Transforming Marketing export...")
    clean_marketing, marketing_report = transform.clean_marketing_rows(raw_marketing_rows)
    _print_report("Marketing", marketing_report)

    cur.executemany(
        """INSERT INTO fact_marketing_spend (date_id, channel, spend) VALUES (%s, %s, %s)""",
        clean_marketing,
    )
    conn.commit()

    # --- Finance/Ops returns: sample from real order lines, raw -> clean --
    print("Sampling order lines for returns...")
    cur.execute(
        """SELECT f.order_line_id, p.category, d.date, f.line_revenue
           FROM fact_orders f
           JOIN dim_products p ON f.product_id = p.product_id
           JOIN dim_date d ON f.date_id = d.date_id"""
    )
    order_line_refs = [
        OrderLineRef(order_line_id=row[0], category=row[1], order_date=row[2], line_revenue=float(row[3]))
        for row in cur.fetchall()
    ]

    raw_returns_rows = generator.generate_raw_returns_rows(order_line_refs, END_DATE, rng)
    print(f"  {len(raw_returns_rows)} raw return rows generated. Landing in raw_returns...")
    cur.executemany(
        """INSERT INTO raw_returns (order_line_id, category, return_date, reason, refund_amount)
           VALUES (%(order_line_id)s, %(category)s, %(return_date)s, %(reason)s, %(refund_amount)s)""",
        raw_returns_rows,
    )
    conn.commit()

    print("  Transforming Returns export...")
    clean_returns, returns_report = transform.clean_returns_rows(raw_returns_rows)
    _print_report("Returns", returns_report)

    cur.executemany(
        """INSERT INTO fact_returns (order_line_id, date_id, reason, refund_amount)
           VALUES (%s, %s, %s, %s)""",
        clean_returns,
    )
    conn.commit()

    print(
        f"Done. fact_orders={len(clean_sales)}, fact_marketing_spend={len(clean_marketing)}, "
        f"fact_returns={len(clean_returns)}."
    )

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
