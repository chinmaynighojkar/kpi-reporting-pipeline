"""Exports every table and KPI view to local CSV files under data/export/.
A convenience mirror of the Neon DB for offline inspection/backup -- the
pipeline's source of truth remains Postgres; this is not re-imported by
anything."""

import csv
import sys
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kpi_pipeline.db import get_connection  # noqa: E402

TABLES_AND_VIEWS = [
    "dim_customers",
    "dim_products",
    "dim_date",
    "fact_orders",
    "fact_marketing_spend",
    "fact_returns",
    "raw_sales_export",
    "raw_marketing_spend",
    "raw_returns",
    "vw_daily_revenue",
    "vw_category_weekly",
    "vw_region_summary",
    "vw_customer_repeat_rate",
    "vw_marketing_cac_monthly",
    "vw_return_summary",
    "vw_return_weekly",
]

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "export"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        for name in TABLES_AND_VIEWS:
            with conn.cursor() as cur:
                # Table/view names can't be bound as query parameters (%s); sql.Identifier
                # is psycopg's safe way to compose an identifier instead of an f-string,
                # even though this list is hardcoded, not user input.
                cur.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(name)))
                cols = [c.name for c in cur.description]
                rows = cur.fetchall()

            out_path = OUT_DIR / f"{name}.csv"
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                writer.writerows(rows)

            print(f"  {name}: {len(rows)} rows -> {out_path.relative_to(OUT_DIR.parents[1])}")

    print(f"Done. {len(TABLES_AND_VIEWS)} tables/views exported to {OUT_DIR}")


if __name__ == "__main__":
    main()
