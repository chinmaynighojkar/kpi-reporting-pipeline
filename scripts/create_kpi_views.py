"""Creates/refreshes the KPI views (sql/kpi_views.sql) that Power BI reads from."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kpi_pipeline.db import get_connection  # noqa: E402


def main() -> None:
    sql = (Path(__file__).resolve().parents[1] / "sql" / "kpi_views.sql").read_text()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("KPI views created/refreshed.")


if __name__ == "__main__":
    main()
