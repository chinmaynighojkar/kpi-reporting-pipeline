"""Integration tests against the live Neon DB. Skipped if DATABASE_URL isn't set."""

import os

import pytest

from kpi_pipeline.db import get_connection

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

VIEWS = [
    "vw_daily_revenue",
    "vw_category_weekly",
    "vw_region_summary",
    "vw_customer_repeat_rate",
    "vw_marketing_cac_monthly",
    "vw_return_summary",
    "vw_return_weekly",
]


@pytest.fixture(scope="module")
def conn():
    with get_connection() as connection:
        yield connection


@pytest.mark.parametrize("view", VIEWS)
def test_view_returns_rows(conn, view):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {view}")
        (count,) = cur.fetchone()
    assert count > 0


def test_daily_revenue_net_equals_gross_minus_refunds(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, gross_revenue, refunds, net_revenue FROM vw_daily_revenue ORDER BY date DESC LIMIT 30"
        )
        for _, gross, refunds, net in cur.fetchall():
            assert net == pytest.approx(gross - refunds, abs=0.01)


def test_category_weekly_percentages_sum_to_roughly_100(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT week_start, SUM(pct_of_week) FROM vw_category_weekly GROUP BY week_start ORDER BY week_start"
        )
        rows = cur.fetchall()
    assert len(rows) > 0
    for _, total_pct in rows:
        assert float(total_pct) == pytest.approx(100.0, abs=0.5)


def test_return_summary_rates_are_valid_percentages(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT return_rate_pct FROM vw_return_summary")
        rates = [float(r[0]) for r in cur.fetchall()]
    assert all(0 <= r <= 100 for r in rates)


def test_return_weekly_electronics_defect_spike_present(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(return_rate_pct) FROM vw_return_weekly WHERE category = 'Electronics'"
        )
        (peak,) = cur.fetchone()
    assert float(peak) > 10.0  # baseline is ~3%; the embedded defect spike should clear this easily


def test_marketing_cac_paid_social_rises_over_anomaly_window(conn):
    # The 84-day anomaly window (see docs/DECISIONS.md) covers roughly the
    # dataset's last 3 full months. Early-dataset months are excluded: the
    # customer base is tiny right after START_DATE, so CAC there is noisy
    # for reasons unrelated to the embedded anomaly.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT month, cac FROM vw_marketing_cac_monthly
               WHERE channel = 'Paid Social' AND cac IS NOT NULL
               ORDER BY month DESC LIMIT 5"""
        )
        rows = cur.fetchall()[::-1]  # oldest to newest, excludes the partial current month via slicing below
    cacs = [float(r[1]) for r in rows]
    assert len(cacs) >= 4
    assert cacs[-2] > cacs[0]  # last full month's CAC exceeds the earliest of the recent window


def test_region_summary_has_no_unknown_region(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT region FROM vw_region_summary")
        regions = {r[0] for r in cur.fetchall()}
    assert "Unknown" not in regions
