"""Synthetic data generation for three siloed department exports.

This models the "before" state the resume bullet describes: Sales,
Marketing, and Finance/Ops each produce their own recurring export, each
with its own realistic mess (inconsistent formats, duplicates, missing
fields). generate_raw_*_rows functions in this module produce exactly that
-- raw, messy, human-readable rows, not clean typed records. Cleaning is
`transform.py`'s job, not this module's.

Three embedded anomalies (see docs/DECISIONS.md for the full rationale),
one per department, each invisible in a naive top-line view and visible
only once broken out the way the dashboards do it:

  Sales      Home & Kitchen's order-share decays over the final
             TREND_WINDOW_DAYS while total revenue holds flat.
  Marketing  Paid Social spend ramps up MARKETING_ANOMALY_MULTIPLIER-x over
             MARKETING_ANOMALY_WINDOW_DAYS while that channel's share of new
             customer acquisition stays flat -> CAC rises.
  Fin/Ops    Electronics orders placed during RETURNS_ANOMALY_WINDOW see a
             ANOMALY_RETURN_RATE return rate vs BASE_RETURN_RATE elsewhere
             (a defect-style spike).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from faker import Faker

CATEGORIES = [
    {"name": "Electronics", "base_weight": 0.22, "price_min": 25, "price_max": 450},
    {"name": "Home & Kitchen", "base_weight": 0.20, "price_min": 10, "price_max": 180},
    {"name": "Apparel", "base_weight": 0.20, "price_min": 12, "price_max": 120},
    {"name": "Beauty", "base_weight": 0.14, "price_min": 5, "price_max": 60},
    {"name": "Sports & Outdoors", "base_weight": 0.14, "price_min": 10, "price_max": 200},
    {"name": "Books", "base_weight": 0.10, "price_min": 6, "price_max": 35},
]

REGIONS = [
    {"name": "Ireland", "weight": 0.15},
    {"name": "United Kingdom", "weight": 0.25},
    {"name": "Germany", "weight": 0.20},
    {"name": "France", "weight": 0.15},
    {"name": "Spain", "weight": 0.10},
    {"name": "United States", "weight": 0.15},
]

# Acquisition channel: signup_weight drives how customers are attributed;
# daily_spend is the paid channel's baseline daily ad spend (0 = organic, no spend row).
CHANNELS = [
    {"name": "Organic / Direct", "signup_weight": 0.28, "daily_spend": 0},
    {"name": "Paid Search", "signup_weight": 0.24, "daily_spend": 750},
    {"name": "Paid Social", "signup_weight": 0.22, "daily_spend": 500},
    {"name": "Email", "signup_weight": 0.14, "daily_spend": 120},
    {"name": "Affiliate", "signup_weight": 0.12, "daily_spend": 280},
]

RETURN_REASONS = ["Defective", "Wrong item", "No longer needed", "Better price found", "Other"]

# --- Anomaly 1: Sales / Home & Kitchen decline -----------------------------
DECLINING_CATEGORY = "Home & Kitchen"
TREND_WINDOW_DAYS = 70
TREND_MIN_FACTOR = 0.4  # declining category's weight bottoms out at 40% of baseline

# --- Anomaly 2: Marketing / Paid Social CAC blowout ------------------------
MARKETING_ANOMALY_CHANNEL = "Paid Social"
MARKETING_ANOMALY_WINDOW_DAYS = 84
MARKETING_ANOMALY_MULTIPLIER = 2.6

# --- Anomaly 3: Finance/Ops / Electronics defect return spike --------------
# Fixed calendar dates, not "N days before end_date": this anomaly models a
# resolved past incident (a defect batch), not an ongoing state. Using a
# fixed window means daily incremental appends (Phase 3) correctly stop
# reproducing it once real time moves past RETURNS_ANOMALY_ORDER_END,
# instead of the spike looking permanent forever.
RETURNS_ANOMALY_CATEGORY = "Electronics"
RETURNS_ANOMALY_ORDER_START = dt.date(2026, 7, 6)   # DATASET_END_DATE - 70 days
RETURNS_ANOMALY_ORDER_END = dt.date(2026, 8, 30)    # DATASET_END_DATE - 15 days
BASE_RETURN_RATE = 0.03
ANOMALY_RETURN_RATE = 0.16
RETURN_LAG_MIN_DAYS = 5
RETURN_LAG_MAX_DAYS = 25

PRODUCTS_PER_CATEGORY = 8
N_CUSTOMERS = 3000
BASE_ORDERS_PER_DAY = 28

GUEST_EMAIL = "guest-checkout@kpi-pipeline.local"

# Shared with scripts/seed_database.py and scripts/run_pipeline.py so the
# growth-curve/anomaly-decay math has one source of truth for "day zero."
DATASET_START_DATE = dt.date(2025, 3, 1)
DATASET_END_DATE = dt.date(2026, 9, 14)


@dataclass
class OrderLineRef:
    """A real, already-loaded order line -- used only to sample returns against."""

    order_line_id: int
    category: str
    order_date: dt.date
    line_revenue: float


def category_weights_for_date(date: dt.date, end_date: dt.date) -> dict[str, float]:
    """Category selection weights for a given day, applying the embedded decay."""
    baseline = {c["name"]: c["base_weight"] for c in CATEGORIES}
    days_before_end = (end_date - date).days

    if days_before_end > TREND_WINDOW_DAYS or days_before_end < 0:
        return baseline

    progress = 1 - (days_before_end / TREND_WINDOW_DAYS)  # 0 at window start -> 1 at end_date
    decay_factor = 1 - progress * (1 - TREND_MIN_FACTOR)

    declining_base = baseline[DECLINING_CATEGORY]
    new_declining = declining_base * decay_factor
    removed = declining_base - new_declining
    others_total = 1 - declining_base

    weights = {}
    for name, w in baseline.items():
        if name == DECLINING_CATEGORY:
            weights[name] = new_declining
        else:
            weights[name] = w + removed * (w / others_total)
    return weights


def generate_products(rng: np.random.Generator) -> list[dict]:
    fake = Faker()
    Faker.seed(int(rng.integers(0, 2**31 - 1)))
    products = []
    for cat in CATEGORIES:
        for _ in range(PRODUCTS_PER_CATEGORY):
            list_price = round(float(rng.uniform(cat["price_min"], cat["price_max"])), 2)
            unit_cost = round(list_price * float(rng.uniform(0.45, 0.65)), 2)
            products.append(
                {
                    "product_name": f"{fake.word().capitalize()} {cat['name'].split(' ')[0]} {fake.word().capitalize()}",
                    "category": cat["name"],
                    "unit_cost": unit_cost,
                    "list_price": list_price,
                }
            )
    return products


def generate_customers(
    n: int, start_date: dt.date, end_date: dt.date, rng: np.random.Generator
) -> list[dict]:
    fake = Faker()
    Faker.seed(int(rng.integers(0, 2**31 - 1)))
    total_days = (end_date - start_date).days
    region_names = [r["name"] for r in REGIONS]
    region_weights = [r["weight"] for r in REGIONS]
    channel_names = [c["name"] for c in CHANNELS]
    channel_weights = [c["signup_weight"] for c in CHANNELS]

    customers = []
    # skew signups later in the window (beta(2,1.3)) -> customer base grows over time
    signup_progress = rng.beta(2.0, 1.3, size=n)
    activity_scores = rng.lognormal(mean=0.0, sigma=0.6, size=n)

    for i in range(n):
        signup_date = start_date + dt.timedelta(days=int(signup_progress[i] * total_days))
        customers.append(
            {
                "full_name": fake.name(),
                "email": fake.unique.email(),
                "region": str(rng.choice(region_names, p=region_weights)),
                "acquisition_channel": str(rng.choice(channel_names, p=channel_weights)),
                "signup_date": signup_date,
                "activity_score": round(float(activity_scores[i]), 3),
            }
        )
    return customers


def daily_order_count(
    date: dt.date, start_date: dt.date, end_date: dt.date, rng: np.random.Generator
) -> int:
    total_days = max((end_date - start_date).days, 1)
    progress = (date - start_date).days / total_days
    growth_factor = 1.0 + 0.6 * progress  # customer base grows ~1.6x over the window

    weekday_multiplier = 1.3 if date.weekday() >= 5 else 1.0

    is_holiday_season = dt.date(date.year, 11, 20) <= date <= dt.date(date.year, 12, 31)
    holiday_multiplier = 1.5 if is_holiday_season else 1.0

    expected = BASE_ORDERS_PER_DAY * growth_factor * weekday_multiplier * holiday_multiplier
    return int(rng.poisson(expected))


def _mangle_text(text: str, rng: np.random.Generator, rate: float) -> str:
    if rng.random() >= rate:
        return text
    variant = rng.choice(["lower", "upper", "pad"])
    if variant == "lower":
        return text.lower()
    if variant == "upper":
        return text.upper()
    return f"  {text}  "


def _format_date(date: dt.date, rng: np.random.Generator, alt_format_rate: float = 0.12) -> str:
    if rng.random() < alt_format_rate:
        return date.strftime("%d/%m/%Y")
    return date.strftime("%Y-%m-%d")


def inject_duplicates(rows: list[dict], rng: np.random.Generator, rate: float = 0.01) -> list[dict]:
    """Simulate a department export accidentally including some rows twice."""
    if not rows:
        return rows
    extra = [row for row in rows if rng.random() < rate]
    return rows + extra


def generate_raw_sales_rows_for_day(
    date: dt.date,
    start_order_id: int,
    eligible_customers: list[dict],
    products_by_category: dict[str, list[dict]],
    start_date: dt.date,
    end_date: dt.date,
    rng: np.random.Generator,
) -> tuple[list[dict], int]:
    """Raw Sales department export rows for one day. Reused for both
    historical seeding and the daily incremental append (same function,
    same weighting logic) -- only the output shape (messy, denormalized)
    differs from the old clean-OrderLine version."""
    n_orders = daily_order_count(date, start_date, end_date, rng)
    if n_orders == 0 or not eligible_customers:
        return [], start_order_id

    category_weights = category_weights_for_date(date, end_date)
    category_names = list(category_weights.keys())
    category_probs = list(category_weights.values())

    customer_weights = np.array([c["activity_score"] for c in eligible_customers], dtype=float)
    customer_probs = customer_weights / customer_weights.sum()

    rows: list[dict] = []
    order_id = start_order_id

    for _ in range(n_orders):
        customer = eligible_customers[rng.choice(len(eligible_customers), p=customer_probs)]
        is_guest = rng.random() < 0.01
        n_lines = int(rng.integers(1, 5))  # 1-4 line items per order
        order_date_text = _format_date(date, rng)

        for _ in range(n_lines):
            category = str(rng.choice(category_names, p=category_probs))
            product = products_by_category[category][
                rng.integers(0, len(products_by_category[category]))
            ]
            quantity = int(rng.integers(1, 4))
            if rng.random() < 0.015:
                quantity = 0  # bad data: dropped by transform

            price_variance = float(rng.uniform(0.95, 1.05))
            unit_price = round(product["list_price"] * price_variance, 2)
            discount = float(rng.choice([0.0, 0.0, 0.0, 0.1, 0.2], p=[0.6, 0.15, 0.1, 0.1, 0.05]))

            rows.append(
                {
                    "order_id": order_id,
                    "order_date": order_date_text,
                    "customer_email": None if is_guest else customer["email"],
                    "customer_name": None if is_guest else customer["full_name"],
                    "region": customer["region"],
                    "acquisition_channel": customer["acquisition_channel"],
                    "product_name": product["product_name"],
                    "category": _mangle_text(category, rng, rate=0.06),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "discount": discount,
                }
            )
        order_id += 1

    rows = inject_duplicates(rows, rng, rate=0.01)
    return rows, order_id


def generate_raw_marketing_rows_for_day(
    date: dt.date, start_date: dt.date, end_date: dt.date, rng: np.random.Generator
) -> list[dict]:
    rows = []
    for channel in CHANNELS:
        if channel["daily_spend"] <= 0:
            continue  # organic has no ad spend to report
        if rng.random() < 0.03:
            continue  # missing/late data for this channel today

        baseline = channel["daily_spend"]
        if channel["name"] == MARKETING_ANOMALY_CHANNEL:
            days_before_end = (end_date - date).days
            if 0 <= days_before_end <= MARKETING_ANOMALY_WINDOW_DAYS:
                progress = 1 - (days_before_end / MARKETING_ANOMALY_WINDOW_DAYS)
                baseline *= 1 + progress * (MARKETING_ANOMALY_MULTIPLIER - 1)

        spend = round(baseline * float(rng.uniform(0.85, 1.15)), 2)
        rows.append(
            {
                "spend_date": _format_date(date, rng),
                "channel": _mangle_text(channel["name"], rng, rate=0.08),
                "spend": spend,
            }
        )

    return inject_duplicates(rows, rng, rate=0.02)


def generate_raw_returns_rows(
    order_line_refs: list[OrderLineRef], end_date: dt.date, rng: np.random.Generator
) -> list[dict]:
    rows = []
    for ref in order_line_refs:
        in_anomaly_window = (
            ref.category == RETURNS_ANOMALY_CATEGORY
            and RETURNS_ANOMALY_ORDER_START <= ref.order_date <= RETURNS_ANOMALY_ORDER_END
        )
        return_rate = ANOMALY_RETURN_RATE if in_anomaly_window else BASE_RETURN_RATE
        if rng.random() >= return_rate:
            continue

        lag_days = int(rng.integers(RETURN_LAG_MIN_DAYS, RETURN_LAG_MAX_DAYS + 1))
        return_date = ref.order_date + dt.timedelta(days=lag_days)
        if return_date > end_date:
            continue  # not enough time to have been returned within the dataset window

        reason = None if rng.random() < 0.05 else str(rng.choice(RETURN_REASONS))
        refund_sign = -1 if rng.random() < 0.01 else 1  # occasional data-entry typo
        refund_amount = round(ref.line_revenue * refund_sign, 2)

        rows.append(
            {
                "order_line_id": ref.order_line_id,
                "category": _mangle_text(ref.category, rng, rate=0.05),
                "return_date": _format_date(return_date, rng),
                "reason": reason,
                "refund_amount": refund_amount,
            }
        )

    return inject_duplicates(rows, rng, rate=0.01)


def build_date_dimension(start_date: dt.date, end_date: dt.date) -> list[dict]:
    rows = []
    date = start_date
    while date <= end_date:
        iso = date.isocalendar()
        rows.append(
            {
                "date_id": int(date.strftime("%Y%m%d")),
                "date": date,
                "year": date.year,
                "month": date.month,
                "day": date.day,
                "day_of_week": date.weekday(),
                "day_name": date.strftime("%A"),
                "week_of_year": iso[1],
                "is_weekend": date.weekday() >= 5,
            }
        )
        date += dt.timedelta(days=1)
    return rows
