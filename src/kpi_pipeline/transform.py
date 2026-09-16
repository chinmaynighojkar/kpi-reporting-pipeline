"""Raw -> clean transforms for the three department exports.

Each clean_* function takes raw rows (as landed in raw_sales_export /
raw_marketing_spend / raw_returns) and returns (clean_rows, report), where
report is a dict of counts explaining what happened to every input row --
the "does the pipeline actually clean things" evidence, not just an
aggregation step.
"""

from __future__ import annotations

import datetime as dt

from kpi_pipeline.generator import CATEGORIES, CHANNELS

CATEGORY_NAMES = [c["name"] for c in CATEGORIES]
CHANNEL_NAMES = [c["name"] for c in CHANNELS]

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]


def parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize(text: str | None, known_values: list[str]) -> str | None:
    if not text:
        return None
    cleaned = text.strip().lower()
    for known in known_values:
        if known.lower() == cleaned:
            return known
    return None


def normalize_category(text: str | None) -> str | None:
    return _normalize(text, CATEGORY_NAMES)


def normalize_channel(text: str | None) -> str | None:
    return _normalize(text, CHANNEL_NAMES)


def _row_key(row: dict, fields: tuple[str, ...]) -> tuple:
    return tuple(row.get(f) for f in fields)


def _dedupe_exact(rows: list[dict], key_fields: tuple[str, ...]) -> tuple[list[dict], int]:
    seen = set()
    deduped = []
    dropped = 0
    for row in rows:
        key = _row_key(row, key_fields)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, dropped


def clean_sales_rows(
    raw_rows: list[dict],
    customer_id_by_email: dict[str, int],
    product_id_by_category_name: dict[tuple[str, str], int],
    guest_customer_id: int,
) -> tuple[list[tuple], dict]:
    key_fields = (
        "order_id",
        "order_date",
        "customer_email",
        "product_name",
        "quantity",
        "unit_price",
        "discount",
    )
    rows, duplicates_dropped = _dedupe_exact(raw_rows, key_fields)

    clean: list[tuple] = []
    invalid_quantity = 0
    invalid_price = 0
    unparseable_date = 0
    unmatched_category = 0
    unmatched_product = 0

    for row in rows:
        order_date = parse_date(row["order_date"])
        if order_date is None:
            unparseable_date += 1
            continue

        if not row["quantity"] or row["quantity"] <= 0:
            invalid_quantity += 1
            continue

        if not row["unit_price"] or row["unit_price"] <= 0:
            invalid_price += 1
            continue

        category = normalize_category(row["category"])
        if category is None:
            unmatched_category += 1
            continue

        product_id = product_id_by_category_name.get((category, row["product_name"]))
        if product_id is None:
            unmatched_product += 1
            continue

        email = row["customer_email"]
        customer_id = customer_id_by_email.get(email, guest_customer_id) if email else guest_customer_id

        quantity = row["quantity"]
        unit_price = float(row["unit_price"])
        discount = float(row["discount"] or 0.0)
        line_revenue = round(quantity * unit_price * (1 - discount), 2)

        clean.append(
            (
                row["order_id"],
                customer_id,
                product_id,
                int(order_date.strftime("%Y%m%d")),
                quantity,
                unit_price,
                discount,
                line_revenue,
            )
        )

    report = {
        "raw_rows": len(raw_rows),
        "duplicates_dropped": duplicates_dropped,
        "invalid_quantity_dropped": invalid_quantity,
        "invalid_price_dropped": invalid_price,
        "unparseable_date_dropped": unparseable_date,
        "unmatched_category_dropped": unmatched_category,
        "unmatched_product_dropped": unmatched_product,
        "clean_rows": len(clean),
    }
    return clean, report


def clean_marketing_rows(raw_rows: list[dict]) -> tuple[list[tuple], dict]:
    key_fields = ("spend_date", "channel", "spend")
    rows, duplicates_dropped = _dedupe_exact(raw_rows, key_fields)

    parsed: list[tuple[int, str, float]] = []
    unparseable_date = 0
    unmatched_channel = 0

    for row in rows:
        spend_date = parse_date(row["spend_date"])
        if spend_date is None:
            unparseable_date += 1
            continue

        channel = normalize_channel(row["channel"])
        if channel is None:
            unmatched_channel += 1
            continue

        parsed.append((int(spend_date.strftime("%Y%m%d")), channel, float(row["spend"])))

    # Collapse any residual (date, channel) rows left after exact dedupe
    # (e.g. two partial exports for the same day) by summing spend.
    aggregated: dict[tuple[int, str], float] = {}
    for date_id, channel, spend in parsed:
        aggregated[(date_id, channel)] = aggregated.get((date_id, channel), 0.0) + spend

    clean = [(date_id, channel, round(spend, 2)) for (date_id, channel), spend in aggregated.items()]

    report = {
        "raw_rows": len(raw_rows),
        "duplicates_dropped": duplicates_dropped,
        "unparseable_date_dropped": unparseable_date,
        "unmatched_channel_dropped": unmatched_channel,
        "rows_aggregated": len(parsed) - len(clean),
        "clean_rows": len(clean),
    }
    return clean, report


def clean_returns_rows(raw_rows: list[dict]) -> tuple[list[tuple], dict]:
    key_fields = ("order_line_id", "return_date", "reason")
    rows, duplicates_dropped = _dedupe_exact(raw_rows, key_fields)

    clean: list[tuple] = []
    unparseable_date = 0
    negative_refund_fixed = 0

    for row in rows:
        return_date = parse_date(row["return_date"])
        if return_date is None:
            unparseable_date += 1
            continue

        refund_amount = float(row["refund_amount"])
        if refund_amount < 0:
            refund_amount = abs(refund_amount)
            negative_refund_fixed += 1

        reason = row["reason"] or "Unknown"

        clean.append(
            (
                row["order_line_id"],
                int(return_date.strftime("%Y%m%d")),
                reason,
                round(refund_amount, 2),
            )
        )

    report = {
        "raw_rows": len(raw_rows),
        "duplicates_dropped": duplicates_dropped,
        "unparseable_date_dropped": unparseable_date,
        "negative_refund_fixed": negative_refund_fixed,
        "clean_rows": len(clean),
    }
    return clean, report
