"""The 2.23.4 gate's eighth round (W-12 / NEW-138, NEW-137).

- W-12 / NEW-138: the Trial Balance and General Ledger took the statements'
  order in round 8 but printed a sub-account bare ("6210 Fuel" after a
  parent with no balance; two "Insurance" rows alike). Their rows carry the
  statements' path and label now, on the page, in the CSV and in the PDF.
- NEW-137: a Minutes-format timesheet opened in Excel or Numbers and saved
  as .xlsx keeps each duration as a time of day (0:20 → 00:20:00); the
  sheet reader writes such a cell as h:mm, and the importer reads h:mm:ss.
"""

import datetime as dt
import io
from decimal import Decimal
from pathlib import Path

import openpyxl

from app.models.accounts import Account, AccountType
from app.models.time_entries import TimeEntry
from app.services.accounting import create_journal_entry
from app.services.qb_time_import import _minutes

ROOT = Path(__file__).resolve().parents[1]
PERIOD = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def _upload(client, name, data):
    return client.post(
        "/api/csv/import/qb-report",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
    ).json()


def test_minutes_read_a_time_of_day_and_seconds():
    assert _minutes("00:20:00") == 20
    assert _minutes("1:00:00") == 60
    assert _minutes("2:15") == 135
    assert _minutes("0:20") == 20
    assert _minutes("91:30:00") == 5490  # a group total past a day, as [h]:mm
    assert _minutes("0.33") == 20


def _time_workbook() -> bytes:
    """Three 0:20 rows under 1:00, as Excel keeps a Minutes-format CSV it
    was asked to save as .xlsx: each duration a time of day, the group's
    total too; the grand total past a day as a duration."""
    book = openpyxl.Workbook()
    sheet = book.active
    rows = [
        [None, None, None, None, "Date", "Item", "Billing Status", "Duration", "Notes"],
        [" "] + [None] * 8,
        [None, "Alvarez, M"] + [None] * 7,
        [None, None, "Northline Overhead"] + [None] * 6,
    ]
    for day in (2, 3, 4):
        rows.append(
            [
                None,
                None,
                None,
                None,
                dt.datetime(2024, 1, day),
                "Indirect - Admin:Email/Phone",
                "Not Billable",
                dt.time(0, 20),
                None,
            ]
        )
    rows.append(
        [
            None,
            None,
            "Total Northline Overhead",
            None,
            None,
            None,
            None,
            dt.time(1, 0),
            None,
        ]
    )
    rows.append(
        [None, "Total Alvarez, M", None, None, None, None, None, dt.time(1, 0), None]
    )
    rows.append(
        ["TOTAL", None, None, None, None, None, None, dt.timedelta(hours=1), None]
    )
    for row in rows:
        sheet.append(row)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def test_a_minutes_format_timesheet_saved_as_xlsx_imports(
    client, db_session, seed_accounts
):
    r = _upload(client, "minutes.xlsx", _time_workbook())
    assert r["detected"] == "time" and r["errors"] == [], r
    assert r["time_entries"] == 3
    hours = [Decimal(str(e.hours_regular)) for e in db_session.query(TimeEntry)]
    assert all(h == Decimal("0.33") for h in hours), hours


def test_the_sheet_reader_writes_times_and_durations_as_hours_and_minutes():
    from app.routes.migration import _sheet_as_csv

    text = _sheet_as_csv(_time_workbook())
    assert ",0:20," in text and ",1:00," in text
    assert text.count("0:20") == 3


def _account(db, name, kind, number=None, parent=None):
    acct = Account(
        name=name,
        account_type=kind,
        account_number=number,
        balance=Decimal("0"),
        parent_id=parent.id if parent is not None else None,
    )
    db.add(acct)
    db.flush()
    return acct


def _books(db):
    by_number = {a.account_number: a for a in db.query(Account) if a.account_number}
    auto = _account(db, "Automobile", AccountType.EXPENSE, "8600")
    fuel = _account(db, "Fuel", AccountType.EXPENSE, "8610", parent=auto)
    insurance = _account(db, "Insurance", AccountType.EXPENSE, None, parent=auto)
    lines = [
        {
            "account_id": a.id,
            "debit": Decimal("10"),
            "credit": Decimal("0"),
            "description": a.name,
        }
        for a in (fuel, insurance)
    ]
    lines.append(
        {
            "account_id": by_number["1000"].id,
            "debit": Decimal("0"),
            "credit": Decimal("20"),
            "description": "the other side",
        }
    )
    create_journal_entry(db, dt.date(2026, 3, 1), "order test", lines)
    db.commit()


def test_the_trial_balance_and_general_ledger_name_a_sub_account_by_its_path(
    client, db_session, seed_accounts
):
    _books(db_session)
    tb = client.get("/api/reports/trial-balance", params=PERIOD).json()
    rows = {i["account_name"]: i for i in tb["items"]}
    assert rows["Fuel"]["account_label"] == "8610 - Automobile: Fuel"
    assert rows["Fuel"]["account_path"] == "Automobile: Fuel"
    assert rows["Insurance"]["account_label"] == "Automobile: Insurance"
    assert rows["Checking"]["account_label"] == "1000 - Checking"
    csv_text = client.get("/api/reports/trial-balance/csv", params=PERIOD).text
    assert (
        "8610,Automobile: Fuel," in csv_text and ",Automobile: Insurance," in csv_text
    )
    gl = client.get("/api/reports/general-ledger", params=PERIOD).json()
    heads = {a["account_name"]: a for a in gl["accounts"]}
    assert heads["Fuel"]["account_label"] == "8610 - Automobile: Fuel"
    assert heads["Insurance"]["account_path"] == "Automobile: Insurance"
    gl_csv = client.get("/api/reports/general-ledger/csv", params=PERIOD).text
    assert "Automobile: Fuel" in gl_csv and "Automobile: Insurance" in gl_csv
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert (
        js.count("acct.account_path || acct.account_name") >= 2
    )  # the General Ledger's headings
    assert (
        js.count("i.account_path || i.account_name") >= 2
    )  # the Trial Balance's name cell and its drill-down
