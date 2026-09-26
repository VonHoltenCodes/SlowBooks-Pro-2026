"""Line rates are stored to four places since 2.18 (sub-cent unit prices);
the CSV exports write them as a person would — 12.50, 0.045 — not 12.5000."""

import csv
import io

from app.services.csv_export import _rate_text


def test_rate_text_keeps_two_places_and_only_meaningful_extras():
    assert _rate_text("12.5000") == "12.50"
    assert _rate_text("0.0450") == "0.045"
    assert _rate_text("3") == "3.00"
    assert _rate_text("0.0001") == "0.0001"
    assert _rate_text("-2.1") == "-2.10"
    assert _rate_text(None) == ""


def test_the_sales_receipt_export_writes_rates_as_typed(client, seed_accounts):
    r = client.post(
        "/api/sales-receipts",
        json={
            "date": "2026-09-01",
            "lines": [
                {"description": "Loaf", "quantity": 2, "rate": 12.5},
                {"description": "Box", "quantity": 1000, "rate": 0.045},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    text = client.get("/api/csv/export/sales-receipts").content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["Rate"] for row in rows] == ["12.50", "0.045"]
