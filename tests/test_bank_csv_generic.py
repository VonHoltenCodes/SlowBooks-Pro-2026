"""Bank CSV import takes any file with a date, a description and an amount.

Exploratory 2.17.3 (skytech, W-L15): only the Bank of America, Chase and
PayPal layouts were accepted; a plain `Date,Description,Amount` file was
"Unknown CSV format", with no way to say which column is which. A header
that names the three (or money-out / money-in columns) is recognised now,
and for a file whose header doesn't, the import dialog asks — a column
mapping sent with the preview and the import. The named layouts and the
duplicate-safe re-import are unchanged."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.services.bank_csv_import import parse_csv

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)

PLAIN = (
    "Date,Description,Amount\n"
    "09/01/2026,COFFEE ROASTERS,-4.50\n"
    '09/02/2026,CUSTOMER DEPOSIT,"1,250.00"\n'
    "09/03/2026,CHECK 1050,($450.00)\n"
)

DEBIT_CREDIT = (
    "Transaction Date,Payee,Memo,Debit,Credit,Balance\n"
    "2026-09-04,Hardware Barn,shelving,45.00,,955.00\n"
    "2026-09-05,Refund,returned bracket,,12.50,967.50\n"
    "2026-09-06,Opening note,,,,967.50\n"
)

UNNAMED = (
    "When,What,How much,Ref\n"
    "25/09/2026,Sign vinyl,-120.00,1051\n"
    "26/09/2026,Walk-in sale,80.00,\n"
)

MAPPING = {
    "date": 0,
    "description": 1,
    "amount": 2,
    "check_number": 3,
    "date_format": "DD/MM/YYYY",
    "has_header": True,
}


def test_a_plain_date_description_amount_file_is_recognised():
    result = parse_csv(PLAIN)
    assert result["format"] == "generic" and result["error"] is None
    assert [(t["date"], t["payee"], t["amount"]) for t in result["transactions"]] == [
        (date(2026, 9, 1), "COFFEE ROASTERS", Decimal("-4.50")),
        (date(2026, 9, 2), "CUSTOMER DEPOSIT", Decimal("1250.00")),
        (date(2026, 9, 3), "CHECK 1050", Decimal("-450.00")),
    ]


def test_money_out_and_money_in_columns_become_one_signed_amount():
    result = parse_csv(DEBIT_CREDIT)
    assert result["format"] == "generic"
    txns = result["transactions"]
    # the row with neither side filled in is not a transaction
    assert [(t["date"], t["amount"]) for t in txns] == [
        (date(2026, 9, 4), Decimal("-45.00")),
        (date(2026, 9, 5), Decimal("12.50")),
    ]
    # Payee is the payee; the memo column is the description
    assert (txns[0]["payee"], txns[0]["description"]) == ("Hardware Barn", "shelving")


def test_withdrawals_and_deposits_headers_too():
    result = parse_csv(
        "Posting Date,Details,Withdrawals,Deposits\n"
        "09/10/2026,ATM,60.00,\n"
        "09/11/2026,Payroll,,900.00\n"
    )
    assert [t["amount"] for t in result["transactions"]] == [
        Decimal("-60.00"),
        Decimal("900.00"),
    ]


def test_an_unrecognised_header_offers_its_columns_for_mapping():
    result = parse_csv(UNNAMED)
    assert result["format"] == "unknown" and result["transactions"] == []
    assert result["error"].startswith("SlowBooks doesn't recognise this file's columns")
    assert result["header_row"] == ["When", "What", "How much", "Ref"]
    assert result["sample"][0] == ["25/09/2026", "Sign vinyl", "-120.00", "1051"]
    assert result["has_header"] is True


def test_a_mapping_reads_it_day_first():
    result = parse_csv(UNNAMED, MAPPING)
    assert result["format"] == "generic"
    assert [
        (t["date"], t["description"], t["amount"], t["check_number"])
        for t in result["transactions"]
    ] == [
        (date(2026, 9, 25), "Sign vinyl", Decimal("-120.00"), "1051"),
        (date(2026, 9, 26), "Walk-in sale", Decimal("80.00"), None),
    ]


def test_a_file_without_a_header_row_can_be_mapped():
    headerless = "09/12/2026,-9.99,STREAMING\n09/13/2026,-20.00,PARKING\n"
    offered = parse_csv(headerless)
    assert offered["format"] == "unknown" and offered["has_header"] is False
    result = parse_csv(
        headerless,
        {"date": 0, "amount": 1, "description": 2, "has_header": False},
    )
    assert [t["amount"] for t in result["transactions"]] == [
        Decimal("-9.99"),
        Decimal("-20.00"),
    ]


def test_an_incomplete_mapping_says_what_to_choose():
    result = parse_csv(UNNAMED, {"date": 0, "description": 1})
    assert result["error"] == (
        "Choose the amount column, or the money-out and money-in columns."
    )
    assert result["header_row"]  # the step can be shown again
    both = parse_csv(UNNAMED, {**MAPPING, "debit": 2})
    assert "not both" in both["error"]


def test_the_named_layouts_still_win():
    chase = (
        "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        "DEBIT,07/03/2026,CHECK 1041,-125.00,CHECK_PAID,3366.00,1041\n"
    )
    assert parse_csv(chase)["format"] == "chase_checking"
    bofa = (
        "Date,Description,Amount,Running Bal.\n"
        '01/20/2026,Interest Earned,4.90,"1,004.90"\n'
    )
    assert parse_csv(bofa)["format"] == "bofa_detail"


@pytest.fixture
def feed(client, seed_accounts):
    return client.post(
        "/api/banking/accounts",
        json={"name": "Community Bank", "account_id": seed_accounts["1000"].id},
    ).json()


def _upload(client, path, text, mapping=None):
    data = {"mapping": json.dumps(mapping)} if mapping else None
    return client.post(
        path,
        files={"file": ("statement.csv", text.encode(), "text/csv")},
        data=data,
    )


def test_a_plain_file_previews_and_imports_once(client, db_session, feed):
    r = _upload(client, "/api/bank-import/preview-csv", PLAIN)
    assert r.status_code == 200, r.text
    assert r.json()["format"] == "generic" and r.json()["count"] == 3
    r = _upload(client, f"/api/bank-import/import-csv/{feed['id']}", PLAIN)
    assert r.json()["imported"] == 3, r.text
    again = _upload(client, f"/api/bank-import/import-csv/{feed['id']}", PLAIN).json()
    assert (again["imported"], again["skipped"]) == (0, 3)
    rows = db_session.query(BankTransaction).all()
    assert {r.import_source for r in rows} == {"csv_generic"}


def test_the_dialog_mapping_previews_and_imports(client, db_session, feed):
    r = _upload(client, "/api/bank-import/preview-csv", UNNAMED)
    body = r.json()
    assert body["count"] == 0 and body["header_row"] == [
        "When",
        "What",
        "How much",
        "Ref",
    ]
    r = _upload(client, "/api/bank-import/preview-csv", UNNAMED, MAPPING)
    assert [t["date"] for t in r.json()["transactions"]] == [
        "2026-09-25",
        "2026-09-26",
    ]
    out = _upload(
        client, f"/api/bank-import/import-csv/{feed['id']}", UNNAMED, MAPPING
    ).json()
    assert out["imported"] == 2, out
    again = _upload(
        client, f"/api/bank-import/import-csv/{feed['id']}", UNNAMED, MAPPING
    ).json()
    assert (again["imported"], again["skipped"]) == (0, 2)
    check = db_session.query(BankTransaction).filter_by(check_number="1051").one()
    assert check.amount == Decimal("-120.00")


def test_unreadable_mapping_json_is_refused(client, feed):
    r = client.post(
        "/api/bank-import/preview-csv",
        files={"file": ("s.csv", UNNAMED.encode(), "text/csv")},
        data={"mapping": "{not json"},
    )
    assert r.status_code == 400 and "column choices" in r.json()["detail"]


def test_the_dialog_asks_which_column_is_which():
    preview = re.search(r"async previewOFX\(.*?\n    },\n", JS, re.S).group(0)
    assert "data.header_row" in preview and "_mappingStep(data" in preview
    assert "formData.append('mapping'" in preview
    confirm = re.search(r"async confirmOFXImport\(.*?\n    },\n", JS, re.S).group(0)
    assert "formData.append('mapping'" in confirm
    step = re.search(r"\n    _mappingStep\(layout, hasHeader\).*?\n    },\n", JS, re.S)
    assert step, "mapping step not found"
    for role in ("date", "description", "amount", "debit", "credit", "date_format"):
        assert f"csv-map-{role}" in step.group(0), role
    assert "previewMapped()" in step.group(0)
