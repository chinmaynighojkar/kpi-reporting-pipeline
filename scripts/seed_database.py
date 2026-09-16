"""One-time historical seed: creates the schema and generates ~18 months
of synthetic e-commerce data, including the embedded Home & Kitchen decay
(see docs/DECISIONS.md). Re-running drops and recreates all tables."""

import datetime as dt
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kpi_pipeline import generator  # noqa: E402
from kpi_pipeline.db import get_connection  # noqa: E402

START_DATE = dt.date(2025, 3, 1)
END_DATE = dt.date(2026, 9, 14)
SEED = 20260916


def main() -> None:
    rng = np.random.default_rng(SEED)
    conn = get_connection()
    cur = conn.cursor()

    print("Creating schema...")
    schema_sql = (Path(__file__).resolve().parents[1] / "sql" / "schema.sql").read_text()
    cur.execute(schema_sql)
    conn.commit()

    print("Generating dimensions...")
    dates = generator.build_date_dimension(START_DATE, END_DATE)
    customers = generator.generate_customers(generator.N_CUSTOMERS, START_DATE, END_DATE, rng)
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
        """INSERT INTO dim_customers (full_name, email, region, signup_date, activity_score)
           VALUES (%(full_name)s, %(email)s, %(region)s, %(signup_date)s, %(activity_score)s)""",
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

    customer_id_lookup = {c["email"]: c["customer_id"] for c in customers}
    product_id_lookup = {(p["category"], p["product_name"]): p["product_id"] for p in products}
    products_by_category: dict[str, list[dict]] = {}
    for p in products:
        products_by_category.setdefault(p["category"], []).append(p)

    print("Generating and inserting order lines day by day...")
    insert_sql = """
        INSERT INTO fact_orders
        (order_id, customer_id, product_id, date_id, quantity, unit_price, discount, line_revenue)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """

    order_id = 1
    total_lines = 0
    date = START_DATE
    while date <= END_DATE:
        eligible = [c for c in customers if c["signup_date"] <= date]
        lines = generator.generate_orders_for_day(
            date,
            order_id,
            eligible,
            products_by_category,
            product_id_lookup,
            customer_id_lookup,
            START_DATE,
            END_DATE,
            rng,
        )
        if lines:
            order_id = lines[-1].order_id + 1
            date_id = int(date.strftime("%Y%m%d"))
            rows = [
                (
                    line.order_id,
                    line.customer_id,
                    line.product_id,
                    date_id,
                    line.quantity,
                    line.unit_price,
                    line.discount,
                    line.line_revenue,
                )
                for line in lines
            ]
            cur.executemany(insert_sql, rows)
            total_lines += len(rows)
        date += dt.timedelta(days=1)

    conn.commit()
    n_days = (END_DATE - START_DATE).days + 1
    print(f"Done. {total_lines} order lines inserted across {n_days} days "
          f"({order_id - 1} orders).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
