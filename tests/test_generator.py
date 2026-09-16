import datetime as dt

import numpy as np
import pytest

from kpi_pipeline import generator


def test_category_weights_baseline_outside_window():
    end_date = dt.date(2026, 9, 14)
    far_date = end_date - dt.timedelta(days=generator.TREND_WINDOW_DAYS + 30)
    weights = generator.category_weights_for_date(far_date, end_date)

    baseline = {c["name"]: c["base_weight"] for c in generator.CATEGORIES}
    assert weights == pytest.approx(baseline)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_category_weights_decay_at_end_date():
    end_date = dt.date(2026, 9, 14)
    weights = generator.category_weights_for_date(end_date, end_date)

    baseline_hk = next(c["base_weight"] for c in generator.CATEGORIES if c["name"] == generator.DECLINING_CATEGORY)
    assert weights[generator.DECLINING_CATEGORY] == pytest.approx(
        baseline_hk * generator.TREND_MIN_FACTOR, rel=1e-6
    )
    assert sum(weights.values()) == pytest.approx(1.0)


def test_category_weights_monotonic_decline_through_window():
    end_date = dt.date(2026, 9, 14)
    days = [generator.TREND_WINDOW_DAYS, 40, 10, 0]
    hk_weights = [
        generator.category_weights_for_date(end_date - dt.timedelta(days=d), end_date)[generator.DECLINING_CATEGORY]
        for d in days
    ]
    assert hk_weights == sorted(hk_weights, reverse=True)


def test_daily_order_count_weekend_exceeds_weekday():
    start_date = dt.date(2026, 1, 1)
    end_date = dt.date(2026, 9, 14)
    weekday = dt.date(2026, 3, 4)  # Wednesday, non-holiday
    weekend = dt.date(2026, 3, 7)  # Saturday, non-holiday

    rng = np.random.default_rng(42)
    weekday_counts = [generator.daily_order_count(weekday, start_date, end_date, rng) for _ in range(300)]
    weekend_counts = [generator.daily_order_count(weekend, start_date, end_date, rng) for _ in range(300)]

    assert np.mean(weekend_counts) > np.mean(weekday_counts)


def test_daily_order_count_holiday_exceeds_non_holiday():
    start_date = dt.date(2025, 3, 1)
    end_date = dt.date(2026, 9, 14)
    non_holiday = dt.date(2025, 11, 10)  # Monday, non-holiday
    holiday = dt.date(2025, 12, 8)  # Monday, in holiday window

    rng = np.random.default_rng(7)
    non_holiday_counts = [generator.daily_order_count(non_holiday, start_date, end_date, rng) for _ in range(300)]
    holiday_counts = [generator.daily_order_count(holiday, start_date, end_date, rng) for _ in range(300)]

    assert np.mean(holiday_counts) > np.mean(non_holiday_counts)


def test_build_date_dimension_covers_full_range():
    start_date = dt.date(2026, 1, 1)
    end_date = dt.date(2026, 1, 10)
    rows = generator.build_date_dimension(start_date, end_date)

    assert len(rows) == 10
    assert rows[0]["date"] == start_date
    assert rows[-1]["date"] == end_date
    assert rows[0]["date_id"] == 20260101
    saturday = next(r for r in rows if r["date"] == dt.date(2026, 1, 3))
    assert saturday["is_weekend"] is True
    monday = next(r for r in rows if r["date"] == dt.date(2026, 1, 5))
    assert monday["is_weekend"] is False


def test_generate_customers_shape_and_validity():
    rng = np.random.default_rng(1)
    start_date = dt.date(2025, 1, 1)
    end_date = dt.date(2025, 12, 31)
    customers = generator.generate_customers(100, start_date, end_date, rng)

    assert len(customers) == 100
    emails = [c["email"] for c in customers]
    assert len(emails) == len(set(emails))

    region_names = {r["name"] for r in generator.REGIONS}
    channel_names = {c["name"] for c in generator.CHANNELS}
    for c in customers:
        assert c["region"] in region_names
        assert c["acquisition_channel"] in channel_names
        assert start_date <= c["signup_date"] <= end_date
        assert c["activity_score"] > 0


def test_generate_products_count_and_price_bounds():
    rng = np.random.default_rng(2)
    products = generator.generate_products(rng)

    assert len(products) == len(generator.CATEGORIES) * generator.PRODUCTS_PER_CATEGORY

    price_bounds = {c["name"]: (c["price_min"], c["price_max"]) for c in generator.CATEGORIES}
    for p in products:
        lo, hi = price_bounds[p["category"]]
        assert lo <= p["list_price"] <= hi
        assert 0 < p["unit_cost"] < p["list_price"]


def test_inject_duplicates_rate_zero_and_one():
    rng = np.random.default_rng(3)
    rows = [{"x": i} for i in range(50)]

    assert generator.inject_duplicates(rows, rng, rate=0.0) == rows

    rng = np.random.default_rng(3)
    doubled = generator.inject_duplicates(rows, rng, rate=1.0)
    assert len(doubled) == len(rows) * 2
