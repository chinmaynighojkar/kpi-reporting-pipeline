import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_pipeline  # noqa: E402
from kpi_pipeline.db import get_connection  # noqa: E402


def test_missing_dates_no_gap():
    today = dt.date(2026, 9, 16)
    assert run_pipeline.missing_dates(today - dt.timedelta(days=1), today) == []


def test_missing_dates_one_gap():
    today = dt.date(2026, 9, 16)
    assert run_pipeline.missing_dates(today - dt.timedelta(days=2), today) == [today - dt.timedelta(days=1)]


def test_missing_dates_multi_day_gap():
    today = dt.date(2026, 9, 16)
    last_loaded = today - dt.timedelta(days=5)
    result = run_pipeline.missing_dates(last_loaded, today)
    assert result == [last_loaded + dt.timedelta(days=i) for i in range(1, 5)]


def test_missing_dates_excludes_today_itself():
    today = dt.date(2026, 9, 16)
    assert today not in run_pipeline.missing_dates(today - dt.timedelta(days=10), today)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_run_pipeline_is_idempotent():
    run_pipeline.run()  # catch up to yesterday, if not already

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM dim_date")
        (after_first,) = cur.fetchone()

    run_pipeline.run()  # should be a clean no-op

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM dim_date")
        (after_second,) = cur.fetchone()

    assert after_first == after_second
    assert after_first == dt.date.today() - dt.timedelta(days=1)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_run_pipeline_does_not_duplicate_order_lines():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fact_orders")
        (before,) = cur.fetchone()

    run_pipeline.run()  # already caught up by the previous test; should no-op

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fact_orders")
        (after,) = cur.fetchone()

    assert before == after
