from kpi_pipeline import transform


def test_parse_date_iso_format():
    assert transform.parse_date("2026-09-14").isoformat() == "2026-09-14"


def test_parse_date_alt_format():
    assert transform.parse_date("14/09/2026").isoformat() == "2026-09-14"


def test_parse_date_invalid_returns_none():
    assert transform.parse_date("not-a-date") is None
    assert transform.parse_date(None) is None


def test_normalize_category_case_insensitive():
    assert transform.normalize_category("  home & kitchen  ") == "Home & Kitchen"
    assert transform.normalize_category("ELECTRONICS") == "Electronics"


def test_normalize_category_unmatched_returns_none():
    assert transform.normalize_category("Not A Category") is None


def test_normalize_channel_case_insensitive():
    assert transform.normalize_channel("paid social") == "Paid Social"


def _sales_row(**overrides):
    row = {
        "order_id": 1,
        "order_date": "2026-09-01",
        "customer_email": "a@example.com",
        "customer_name": "A",
        "region": "Ireland",
        "acquisition_channel": "Email",
        "product_name": "Widget",
        "category": "Electronics",
        "quantity": 2,
        "unit_price": 10.0,
        "discount": 0.0,
    }
    row.update(overrides)
    return row


def test_clean_sales_rows_drops_invalid_quantity():
    rows = [_sales_row(quantity=0)]
    clean, report = transform.clean_sales_rows(rows, {"a@example.com": 5}, {("Electronics", "Widget"): 9}, guest_customer_id=1)
    assert clean == []
    assert report["invalid_quantity_dropped"] == 1


def test_clean_sales_rows_drops_exact_duplicates():
    rows = [_sales_row(), _sales_row()]
    clean, report = transform.clean_sales_rows(rows, {"a@example.com": 5}, {("Electronics", "Widget"): 9}, guest_customer_id=1)
    assert len(clean) == 1
    assert report["duplicates_dropped"] == 1


def test_clean_sales_rows_maps_null_email_to_guest():
    rows = [_sales_row(customer_email=None, customer_name=None)]
    clean, report = transform.clean_sales_rows(rows, {"a@example.com": 5}, {("Electronics", "Widget"): 9}, guest_customer_id=42)
    assert len(clean) == 1
    assert clean[0][1] == 42  # customer_id is the second tuple field
    assert report["clean_rows"] == 1


def test_clean_sales_rows_normalizes_mangled_category():
    rows = [_sales_row(category="  electronics  ")]
    clean, report = transform.clean_sales_rows(rows, {"a@example.com": 5}, {("Electronics", "Widget"): 9}, guest_customer_id=1)
    assert len(clean) == 1
    assert report["unmatched_category_dropped"] == 0


def test_clean_sales_rows_computes_line_revenue_with_discount():
    rows = [_sales_row(quantity=2, unit_price=10.0, discount=0.1)]
    clean, _ = transform.clean_sales_rows(rows, {"a@example.com": 5}, {("Electronics", "Widget"): 9}, guest_customer_id=1)
    line_revenue = clean[0][-1]
    assert line_revenue == 18.0  # 2 * 10 * 0.9


def test_clean_marketing_rows_dedupes_and_sums_leftover():
    rows = [
        {"spend_date": "2026-09-01", "channel": "Paid Social", "spend": 100.0},
        {"spend_date": "2026-09-01", "channel": "Paid Social", "spend": 100.0},  # exact dup, dropped
        {"spend_date": "2026-09-01", "channel": "paid social", "spend": 50.0},  # same day/channel, different casing
    ]
    clean, report = transform.clean_marketing_rows(rows)
    assert report["duplicates_dropped"] == 1
    assert len(clean) == 1
    assert clean[0][2] == 150.0  # 100 (survivor) + 50 (case variant) summed


def test_clean_marketing_rows_drops_unmatched_channel():
    rows = [{"spend_date": "2026-09-01", "channel": "Bogus Channel", "spend": 10.0}]
    clean, report = transform.clean_marketing_rows(rows)
    assert clean == []
    assert report["unmatched_channel_dropped"] == 1


def test_clean_returns_rows_fixes_negative_refund():
    rows = [{"order_line_id": 1, "category": "Electronics", "return_date": "2026-09-01", "reason": "Defective", "refund_amount": -50.0}]
    clean, report = transform.clean_returns_rows(rows)
    assert clean[0][-1] == 50.0
    assert report["negative_refund_fixed"] == 1


def test_clean_returns_rows_fills_missing_reason():
    rows = [{"order_line_id": 1, "category": "Electronics", "return_date": "2026-09-01", "reason": None, "refund_amount": 20.0}]
    clean, _ = transform.clean_returns_rows(rows)
    assert clean[0][2] == "Unknown"
