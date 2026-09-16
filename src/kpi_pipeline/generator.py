"""Synthetic e-commerce data generation.

Design note (see docs/DECISIONS.md): Home & Kitchen's order-share is made to
decay over the final TREND_WINDOW_DAYS before the dataset's end date, with
the removed share redistributed across the other categories. This keeps
total revenue flat-to-growing while one category quietly declines -- a
trend that's invisible in a raw total but visible once broken out by
category, which is the concrete thing the dashboards are built to surface.
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

DECLINING_CATEGORY = "Home & Kitchen"
TREND_WINDOW_DAYS = 70
TREND_MIN_FACTOR = 0.4  # declining category's weight bottoms out at 40% of baseline

PRODUCTS_PER_CATEGORY = 8
N_CUSTOMERS = 600
BASE_ORDERS_PER_DAY = 28


@dataclass
class OrderLine:
    order_id: int
    customer_id: int
    product_id: int
    date: dt.date
    quantity: int
    unit_price: float
    discount: float
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


def generate_orders_for_day(
    date: dt.date,
    start_order_id: int,
    eligible_customers: list[dict],
    products_by_category: dict[str, list[dict]],
    product_id_lookup: dict[tuple[str, str], int],
    customer_id_lookup: dict[str, int],
    start_date: dt.date,
    end_date: dt.date,
    rng: np.random.Generator,
) -> list[OrderLine]:
    """Generate order lines for one day. Reused for both historical seeding
    and the daily incremental append (same function, same weighting logic)."""
    n_orders = daily_order_count(date, start_date, end_date, rng)
    if n_orders == 0 or not eligible_customers:
        return []

    category_weights = category_weights_for_date(date, end_date)
    category_names = list(category_weights.keys())
    category_probs = list(category_weights.values())

    customer_weights = np.array([c["activity_score"] for c in eligible_customers], dtype=float)
    customer_probs = customer_weights / customer_weights.sum()

    order_lines: list[OrderLine] = []
    order_id = start_order_id

    for _ in range(n_orders):
        customer = eligible_customers[rng.choice(len(eligible_customers), p=customer_probs)]
        n_lines = int(rng.integers(1, 5))  # 1-4 line items per order

        for _ in range(n_lines):
            category = str(rng.choice(category_names, p=category_probs))
            product = products_by_category[category][
                rng.integers(0, len(products_by_category[category]))
            ]
            quantity = int(rng.integers(1, 4))
            price_variance = float(rng.uniform(0.95, 1.05))
            unit_price = round(product["list_price"] * price_variance, 2)
            discount = float(rng.choice([0.0, 0.0, 0.0, 0.1, 0.2], p=[0.6, 0.15, 0.1, 0.1, 0.05]))
            line_revenue = round(quantity * unit_price * (1 - discount), 2)

            order_lines.append(
                OrderLine(
                    order_id=order_id,
                    customer_id=customer_id_lookup[customer["email"]],
                    product_id=product_id_lookup[(product["category"], product["product_name"])],
                    date=date,
                    quantity=quantity,
                    unit_price=unit_price,
                    discount=discount,
                    line_revenue=line_revenue,
                )
            )
        order_id += 1

    return order_lines


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
