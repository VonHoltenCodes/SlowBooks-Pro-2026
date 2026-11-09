"""Memorized transactions (#605): memorized bills and journal entries on a
schedule, entered by the scheduler (auto-enter) or from the due list
(remind); every frequency across month ends and February; end dates and
a number of times; pause and resume; the one list of every schedule; the
page's wiring and the migration."""

import asyncio
import json
import runpy
import shutil
import sqlite3
import subprocess
import threading
import types
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

import app.database
import app.services.recurring_service as recurring_service
from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.memorized import MemorizedTransaction
from app.models.transactions import Transaction, TransactionLine
from app.schemas.memorized import EnterResponse
from app.services import memorized_service, scheduler
from app.services.memorized_service import enter_due_memorized
from app.services.recurring_service import (
    _advance_next_due,
    advance_date,
    anchor_day_of,
    frequency_label,
    normalize_frequency,
    roll_next_due_forward,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_RECURRING = ROOT / "scripts" / "run_recurring.py"
REVISION = "c9e5a7b3d1f2"
BEFORE = "c1e5a7b9d2f4"  # the sub-customers migration (#638), which this one follows


def _vendor(client, name="Harbor Power & Light", **more):
    r = client.post("/api/vendors", json={"name": name, **more})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bill_schedule(client, vendor_id, accounts, **over):
    body = {
        "kind": "bill",
        "name": "Electric bill",
        "mode": "auto",
        "frequency": "monthly",
        "start_date": "2026-01-31",
        "vendor_id": vendor_id,
        "terms": "Net 15",
        "lines": [
            {
                "account_id": accounts["6500"].id,
                "description": "Sanctuary",
                "amount": 400,
            },
            {
                "account_id": accounts["6100"].id,
                "description": "Parsonage",
                "quantity": 2,
                "rate": 50,
            },
        ],
    }
    body.update(over)
    r = client.post("/api/memorized", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _journal_schedule(client, accounts, **over):
    body = {
        "kind": "journal",
        "name": "Monthly depreciation",
        "mode": "auto",
        "frequency": "monthly",
        "start_date": "2026-01-31",
        "description": "Depreciation for the month",
        "reference": "DEP",
        "lines": [
            {"account_id": accounts["6000"].id, "debit": 250, "description": "Van"},
            {"account_id": accounts["1000"].id, "credit": 250, "description": "Van"},
        ],
    }
    body.update(over)
    r = client.post("/api/memorized", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _recurring_invoice(client, customer_id, start="2026-01-01"):
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": customer_id,
            "frequency": "monthly",
            "start_date": start,
            "lines": [{"description": "Monthly service", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bills(db_session):
    return db_session.query(Bill).order_by(Bill.id).all()


def _journal_sides(db_session, txn_id):
    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(r.debit)) for r in rows), Decimal("0"))
    cr = sum((Decimal(str(r.credit)) for r in rows), Decimal("0"))
    return dr, cr, rows


# ---------------------------------------------------------------- auto-enter


def test_auto_enter_bill_is_posted_by_the_scheduler_on_its_date_and_advances(
    client, db_session, seed_accounts
):
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts)
    assert mem["mode"] == "auto" and mem["next_date"] == "2026-01-31"
    assert mem["frequency_label"] == "Monthly" and mem["status"] == "active"
    assert Decimal(str(mem["amount"])) == Decimal("500.00")

    # not due the day before
    assert enter_due_memorized(db_session, as_of=date(2026, 1, 30)) == []
    assert _bills(db_session) == []

    entered = enter_due_memorized(db_session, as_of=date(2026, 1, 31))
    assert len(entered) == 1 and entered[0]["kind"] == "bill"
    (bill,) = _bills(db_session)
    assert entered[0]["document_id"] == bill.id
    assert bill.vendor_id == vendor_id
    assert bill.date == date(2026, 1, 31)
    assert bill.terms == "Net 15" and bill.due_date == date(2026, 2, 15)
    assert bill.bill_number == "20260131-HPL"
    assert bill.total == Decimal("500.00") == bill.balance_due
    # the lines as memorized: account, memo, quantity and rate, amount
    lines = sorted(bill.lines, key=lambda ln: ln.line_order)
    assert [(ln.account_id, ln.description) for ln in lines] == [
        (seed_accounts["6500"].id, "Sanctuary"),
        (seed_accounts["6100"].id, "Parsonage"),
    ]
    assert [Decimal(str(ln.amount)) for ln in lines] == [
        Decimal("400.00"),
        Decimal("100.00"),
    ]
    assert [Decimal(str(ln.quantity)) for ln in lines] == [Decimal("1"), Decimal("2")]
    # posted as Enter Bill posts: each line's account debited, A/P credited
    dr, cr, rows = _journal_sides(db_session, bill.transaction_id)
    assert dr == cr == Decimal("500.00")
    ap = [r for r in rows if r.account_id == seed_accounts["2000"].id]
    assert len(ap) == 1 and Decimal(str(ap[0].credit)) == Decimal("500.00")
    assert bill.transaction.source_type == "bill"

    # the schedule moved on, holding to the 31st
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-02-28"
    assert got["entered_count"] == 1
    assert got["last_entered_on"] == "2026-01-31"
    assert got["last_document_id"] == bill.id

    assert enter_due_memorized(db_session, as_of=date(2026, 2, 27)) == []
    entered = enter_due_memorized(db_session, as_of=date(2026, 2, 28))
    assert len(entered) == 1
    second = _bills(db_session)[1]
    assert second.date == date(2026, 2, 28)
    assert second.bill_number == "20260228-HPL"
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-03-31"
    assert got["entered_count"] == 2


def test_a_fixed_bill_number_is_suffixed_when_the_vendor_already_has_it(
    client, db_session, seed_accounts
):
    mem = _bill_schedule(client, _vendor(client), seed_accounts, bill_number="RENT")
    enter_due_memorized(db_session, as_of=date(2026, 1, 31))
    enter_due_memorized(db_session, as_of=date(2026, 2, 28))
    assert [b.bill_number for b in _bills(db_session)] == ["RENT", "RENT-2"]
    assert client.get(f"/api/memorized/{mem['id']}").json()["bill_number"] == "RENT"


def test_a_closed_period_skips_one_schedule_and_the_rest_go_through(
    client, db_session, seed_accounts
):
    vendor_id = _vendor(client)
    _bill_schedule(
        client, vendor_id, seed_accounts, name="Old", start_date="2026-01-05"
    )
    _bill_schedule(
        client, vendor_id, seed_accounts, name="New", start_date="2026-02-05"
    )
    r = client.put("/api/settings", json={"closing_date": "2026-01-31"})
    assert r.status_code == 200, r.text
    skipped: list = []
    entered = enter_due_memorized(db_session, as_of=date(2026, 2, 5), skipped=skipped)
    assert [e["name"] for e in entered] == ["New"]
    assert len(skipped) == 1 and skipped[0]["name"] == "Old"
    assert "closing date" in skipped[0]["message"]
    assert len(_bills(db_session)) == 1


# ---------------------------------------------------------------- remind


def test_remind_mode_posts_nothing_until_enter_now(client, db_session, seed_accounts):
    mem = _bill_schedule(
        client, _vendor(client), seed_accounts, mode="remind", start_date="2026-01-15"
    )
    assert enter_due_memorized(db_session, as_of=date(2026, 3, 1)) == []
    assert _bills(db_session) == []

    due = client.get("/api/memorized/due?as_of=2026-03-01").json()
    assert [(d["kind"], d["id"], d["is_due"]) for d in due] == [
        ("bill", mem["id"], True)
    ]
    assert due[0]["mode"] == "remind"

    # Enter now: dated the scheduled date
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "bill" and r.json()["date"] == "2026-01-15"
    assert r.json()["next_date"] == "2026-02-15"
    assert "next on 2026-02-15" in r.json()["message"]
    (bill,) = _bills(db_session)
    assert bill.date == date(2026, 1, 15) and r.json()["document_id"] == bill.id

    # or dated today; the schedule still moves on from the scheduled date
    r = client.post(f"/api/memorized/{mem['id']}/enter", json={"date": "2026-03-01"})
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-03-01" and r.json()["next_date"] == "2026-03-15"
    assert _bills(db_session)[1].date == date(2026, 3, 1)

    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["entered_count"] == 2 and got["last_entered_on"] == "2026-03-01"
    # entered, so no longer due on that day
    assert client.get("/api/memorized/due?as_of=2026-03-01").json() == []


# ---------------------------------------------------------------- frequencies


@pytest.mark.parametrize(
    "start, frequency, interval, anchor, expected",
    [
        # monthly on the 31st: the last day of a shorter month, the 31st again
        (
            date(2026, 1, 31),
            "monthly",
            1,
            31,
            [
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 4, 30),
                date(2026, 5, 31),
            ],
        ),
        # a leap year's February
        (date(2028, 1, 31), "monthly", 1, 31, [date(2028, 2, 29), date(2028, 3, 31)]),
        # every two months on the 30th
        (
            date(2025, 12, 30),
            "monthly",
            2,
            30,
            [date(2026, 2, 28), date(2026, 4, 30), date(2026, 6, 30)],
        ),
        # quarterly on the 30th
        (
            date(2025, 11, 30),
            "quarterly",
            1,
            30,
            [
                date(2026, 2, 28),
                date(2026, 5, 30),
                date(2026, 8, 30),
                date(2026, 11, 30),
            ],
        ),
        # yearly on the 29th of February
        (
            date(2028, 2, 29),
            "yearly",
            1,
            29,
            [
                date(2029, 2, 28),
                date(2030, 2, 28),
                date(2031, 2, 28),
                date(2032, 2, 29),
            ],
        ),
        # weekly and every two weeks across the end of February
        (date(2026, 2, 24), "weekly", 1, 24, [date(2026, 3, 3), date(2026, 3, 10)]),
        (date(2026, 2, 24), "weekly", 2, 24, [date(2026, 3, 10), date(2026, 3, 24)]),
        # every three days, and daily into a leap day
        (date(2026, 2, 27), "daily", 3, 27, [date(2026, 3, 2), date(2026, 3, 5)]),
        (date(2028, 2, 28), "daily", 1, 28, [date(2028, 2, 29), date(2028, 3, 1)]),
        # twice a month: the day and fifteen days later, cut back to the month's end
        (
            date(2026, 1, 1),
            "semimonthly",
            1,
            1,
            [date(2026, 1, 16), date(2026, 2, 1), date(2026, 2, 16), date(2026, 3, 1)],
        ),
        (
            date(2026, 1, 15),
            "semimonthly",
            1,
            15,
            [
                date(2026, 1, 30),
                date(2026, 2, 15),
                date(2026, 2, 28),
                date(2026, 3, 15),
                date(2026, 3, 30),
            ],
        ),
        (
            date(2026, 1, 16),
            "semimonthly",
            1,
            31,
            [
                date(2026, 1, 31),
                date(2026, 2, 16),
                date(2026, 2, 28),
                date(2026, 3, 16),
                date(2026, 3, 31),
            ],
        ),
    ],
)
def test_each_frequency_advances_across_month_ends_and_february(
    start, frequency, interval, anchor, expected
):
    got = []
    d = start
    for _ in expected:
        d = advance_date(d, frequency, interval, anchor)
        got.append(d)
    assert got == expected


def test_recurring_invoices_hold_to_their_start_day():
    """2.31.1 gate W-85: a recurring invoice stepped from the clamped date
    with no anchor, so one begun on the 31st stayed on the 28th after
    February. Its start date's day is its anchor, as a memorized schedule's
    anchor_day is; a step with no anchor still starts from the date given."""
    anchor = anchor_day_of(types.SimpleNamespace(start_date=date(2026, 1, 31)))
    assert anchor == 31
    assert _advance_next_due(date(2026, 1, 31), "monthly", anchor) == date(2026, 2, 28)
    assert _advance_next_due(date(2026, 2, 28), "monthly", anchor) == date(2026, 3, 31)
    assert _advance_next_due(date(2026, 2, 28), "monthly") == date(2026, 3, 28)
    assert _advance_next_due(date(2026, 1, 31), "quarterly", anchor) == date(
        2026, 4, 30
    )
    assert _advance_next_due(date(2026, 4, 30), "quarterly", anchor) == date(
        2026, 7, 31
    )
    leap = anchor_day_of(types.SimpleNamespace(start_date=date(2028, 2, 29)))
    assert _advance_next_due(date(2028, 2, 29), "yearly", leap) == date(2029, 2, 28)
    assert _advance_next_due(date(2031, 2, 28), "yearly", leap) == date(2032, 2, 29)
    # weekly takes no day of the month; an unknown word steps a month
    assert _advance_next_due(date(2026, 1, 1), "weekly", 1) == date(2026, 1, 8)
    assert _advance_next_due(date(2026, 1, 1), "whatever", 1) == date(2026, 2, 1)
    assert roll_next_due_forward(
        date(2026, 1, 1), "monthly", date(2026, 7, 15)
    ) == date(2026, 8, 1)
    # with an anchor day, the roll forward holds to it too
    assert roll_next_due_forward(
        date(2026, 1, 31), "monthly", date(2026, 4, 15), 1, 31
    ) == date(2026, 4, 30)


def test_frequency_words_and_labels():
    assert normalize_frequency("biweekly", None) == ("weekly", 2)
    assert normalize_frequency("Every two months", 1) == ("monthly", 2)
    assert normalize_frequency("twice a year", 1) == ("monthly", 6)
    assert normalize_frequency("monthly", 4) == ("monthly", 4)
    with pytest.raises(ValueError):
        normalize_frequency("semimonthly", 2)
    with pytest.raises(ValueError):
        normalize_frequency("fortnightly", 1)
    with pytest.raises(ValueError):
        normalize_frequency("monthly", 0)
    assert frequency_label("weekly", 2) == "Every two weeks"
    assert frequency_label("monthly", 6) == "Twice a year"
    assert frequency_label("monthly", 5) == "Every 5 months"
    assert frequency_label("daily", 5) == "Every 5 days"
    assert frequency_label("semimonthly", 1) == "Twice a month"
    assert frequency_label("quarterly", 1) == "Quarterly"
    assert frequency_label("yearly", 1) == "Yearly"


def test_the_api_takes_every_frequency(client, db_session, seed_accounts):
    vendor_id = _vendor(client)
    mem = _bill_schedule(
        client, vendor_id, seed_accounts, frequency="biweekly", start_date="2026-02-24"
    )
    assert (mem["frequency"], mem["interval"]) == ("weekly", 2)
    assert mem["frequency_label"] == "Every two weeks"
    enter_due_memorized(db_session, as_of=date(2026, 2, 24))
    assert client.get(f"/api/memorized/{mem['id']}").json()["next_date"] == "2026-03-10"

    custom = _bill_schedule(
        client,
        vendor_id,
        seed_accounts,
        name="Every 10 days",
        frequency="daily",
        interval=10,
        start_date="2026-02-20",
    )
    assert custom["frequency_label"] == "Every 10 days"
    enter_due_memorized(db_session, as_of=date(2026, 2, 20))
    assert (
        client.get(f"/api/memorized/{custom['id']}").json()["next_date"] == "2026-03-02"
    )

    for bad in (
        {"frequency": "semimonthly", "interval": 2},
        {"frequency": "fortnightly"},
        {"frequency": "monthly", "interval": 0},
    ):
        body = {
            "kind": "bill",
            "name": "Bad",
            "start_date": "2026-01-01",
            "vendor_id": vendor_id,
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
            **bad,
        }
        assert client.post("/api/memorized", json=body).status_code == 422, bad


# ---------------------------------------------------------------- ends


def test_an_end_date_stops_it(client, db_session, seed_accounts):
    mem = _bill_schedule(client, _vendor(client), seed_accounts, end_date="2026-02-28")
    for _ in range(3):
        enter_due_memorized(db_session, as_of=date(2026, 12, 31))
    assert [b.date for b in _bills(db_session)] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
    ]
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["status"] == "ended" and got["next_date"] == "2026-03-31"
    assert got["is_due"] is False
    assert client.get("/api/memorized/due?as_of=2026-12-31").json() == []
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409 and "ended" in r.json()["detail"]
    # a later end date brings it back
    r = client.put(f"/api/memorized/{mem['id']}", json={"end_date": "2026-03-31"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    # an end before the first date is refused, as on a recurring invoice
    r = client.put(f"/api/memorized/{mem['id']}", json={"end_date": "2026-01-01"})
    assert r.status_code == 422


def test_a_number_of_times_stops_it(client, db_session, seed_accounts):
    mem = _bill_schedule(client, _vendor(client), seed_accounts, occurrences=2)
    assert mem["occurrences"] == 2
    for _ in range(3):
        enter_due_memorized(db_session, as_of=date(2026, 12, 31))
    assert len(_bills(db_session)) == 2
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["status"] == "ended" and got["entered_count"] == 2
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409 and "both occurrences are entered" in r.json()["detail"]
    rows = client.get("/api/memorized?as_of=2026-12-31").json()
    assert rows[0]["status"] == "ended" and rows[0]["entered_count"] == 2


# ---------------------------------------------------------------- journal entries


def test_a_memorized_journal_entry_posts_balanced(client, db_session, seed_accounts):
    mem = _journal_schedule(client, seed_accounts)
    assert Decimal(str(mem["amount"])) == Decimal("250.00")
    entered = enter_due_memorized(db_session, as_of=date(2026, 1, 31))
    assert len(entered) == 1 and entered[0]["kind"] == "journal"
    txn = db_session.get(Transaction, entered[0]["document_id"])
    assert txn.source_type == "manual"
    assert txn.date == date(2026, 1, 31)
    assert txn.description == "Depreciation for the month"
    assert txn.reference == "DEP"
    dr, cr, rows = _journal_sides(db_session, txn.id)
    assert dr == cr == Decimal("250.00")
    assert {
        (r.account_id, Decimal(str(r.debit)), Decimal(str(r.credit))) for r in rows
    } == {
        (seed_accounts["6000"].id, Decimal("250.00"), Decimal("0.00")),
        (seed_accounts["1000"].id, Decimal("0.00"), Decimal("250.00")),
    }
    assert all(r.description == "Van" for r in rows)
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-02-28" and got["last_document_id"] == txn.id
    # it reads as any hand-typed entry does
    page = client.get(f"/api/journal/{txn.id}").json()
    assert page["total_debit"] == 250 and page["total_credit"] == 250


def test_an_unbalanced_or_one_line_journal_is_refused(client, seed_accounts):
    base = {
        "kind": "journal",
        "name": "Bad",
        "frequency": "monthly",
        "start_date": "2026-01-01",
        "description": "Bad",
    }
    r = client.post(
        "/api/memorized",
        json={
            **base,
            "lines": [
                {"account_id": seed_accounts["6000"].id, "debit": 250},
                {"account_id": seed_accounts["1000"].id, "credit": 200},
            ],
        },
    )
    assert r.status_code == 422 and "out of balance by $50.00" in r.text
    r = client.post(
        "/api/memorized",
        json={
            **base,
            "lines": [{"account_id": seed_accounts["6000"].id, "debit": 250}],
        },
    )
    assert r.status_code == 422 and "two lines" in r.text
    r = client.post(
        "/api/memorized",
        json={
            **base,
            "lines": [
                {"account_id": 999999, "debit": 5},
                {"account_id": seed_accounts["1000"].id, "credit": 5},
            ],
        },
    )
    assert r.status_code == 404


# ---------------------------------------------------------------- pause and resume


def test_pause_stops_posting_and_resume_skips_the_missed_dates(
    client, db_session, seed_accounts
):
    mem = _bill_schedule(
        client, _vendor(client), seed_accounts, start_date="2020-01-15"
    )
    r = client.post(f"/api/memorized/{mem['id']}/pause")
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert r.json()["is_active"] is False
    assert enter_due_memorized(db_session, as_of=date(2026, 1, 1)) == []
    assert _bills(db_session) == []
    assert client.get("/api/memorized/due").json() == []
    rows = client.get("/api/memorized").json()
    assert rows[0]["status"] == "paused" and rows[0]["is_due"] is False

    r = client.post(f"/api/memorized/{mem['id']}/resume")
    assert r.status_code == 200 and r.json()["status"] == "active"
    nxt = date.fromisoformat(r.json()["next_date"])
    today = date.today()
    assert nxt >= today and nxt.day == 15
    assert (nxt.year, nxt.month) in {
        (today.year, today.month),
        (
            (
                today
                + __import__(
                    "dateutil.relativedelta", fromlist=["relativedelta"]
                ).relativedelta(months=1)
            ).year,
            (
                today
                + __import__(
                    "dateutil.relativedelta", fromlist=["relativedelta"]
                ).relativedelta(months=1)
            ).month,
        ),
    }
    assert _bills(db_session) == []


# ---------------------------------------------------------------- the one list


def test_the_due_list_has_every_kind(client, db_session, seed_accounts, seed_customer):
    inv_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    vendor_id = _vendor(client)
    bill = _bill_schedule(
        client, vendor_id, seed_accounts, mode="remind", start_date="2026-01-05"
    )
    je = _journal_schedule(client, seed_accounts, start_date="2026-01-10")
    later = _bill_schedule(
        client, vendor_id, seed_accounts, name="Later", start_date="2099-01-01"
    )

    due = client.get("/api/memorized/due?as_of=2026-01-10").json()
    assert [(d["kind"], d["id"]) for d in due] == [
        ("invoice", inv_id),
        ("bill", bill["id"]),
        ("journal", je["id"]),
    ]
    assert all(d["is_due"] for d in due)
    inv_row = due[0]
    assert inv_row["name"] == "Recurring invoice: Test Customer"
    assert inv_row["party"] == "Test Customer"
    assert inv_row["mode"] == "generate" and inv_row["frequency_label"] == "Monthly"
    assert inv_row["entered_count"] == 0 and inv_row["last_entered_on"] is None
    assert Decimal(str(inv_row["amount"])) == Decimal("100.00")
    assert due[1]["mode"] == "remind" and due[1]["party"] == "Harbor Power & Light"

    rows = client.get("/api/memorized?as_of=2026-01-10").json()
    assert [(r["kind"], r["id"], r["is_due"]) for r in rows] == [
        ("invoice", inv_id, True),
        ("bill", bill["id"], True),
        ("journal", je["id"], True),
        ("bill", later["id"], False),
    ]
    assert [
        r["kind"]
        for r in client.get("/api/memorized?kind=bill&as_of=2026-01-10").json()
    ] == ["bill", "bill"]
    assert [
        r["id"]
        for r in client.get("/api/memorized?kind=bill&due=true&as_of=2026-01-10").json()
    ] == [bill["id"]]
    assert client.get("/api/memorized?kind=pledge").status_code == 422

    # the recurring invoice's last entry is read off the invoices it made
    r = client.post("/api/recurring/generate?as_of=2026-01-01")
    assert r.status_code == 200 and r.json()["invoices_created"] == 1
    inv_row = client.get("/api/memorized?kind=invoice").json()[0]
    assert inv_row["last_entered_on"] == "2026-01-01"
    assert inv_row["last_document_id"] == r.json()["invoice_ids"][0]
    assert inv_row["entered_count"] == 1 and inv_row["next_date"] == "2026-02-01"


def test_a_recurring_invoice_can_be_entered_one_at_a_time(
    client, db_session, seed_accounts, seed_customer
):
    rec_id = _recurring_invoice(client, seed_customer.id, start="2099-01-01")
    # the daily run leaves a future schedule alone, as it always has
    assert client.post("/api/recurring/generate").json()["invoices_created"] == 0
    r = client.post(f"/api/recurring/{rec_id}/generate")
    assert r.status_code == 200, r.text
    assert r.json()["invoices_created"] == 1 and r.json()["next_due"] == "2099-02-01"
    inv = db_session.get(Invoice, r.json()["invoice_ids"][0])
    assert inv.date == date(2099, 1, 1) and inv.recurring_invoice_id == rec_id
    # dated today instead; the schedule still moves on from the scheduled date
    r = client.post(f"/api/recurring/{rec_id}/generate?dated=2026-03-01")
    assert r.status_code == 200, r.text
    inv = db_session.get(Invoice, r.json()["invoice_ids"][0])
    assert inv.date == date(2026, 3, 1) and inv.due_date == date(2026, 3, 31)
    assert inv.transaction.date == date(2026, 3, 1)
    assert r.json()["next_due"] == "2099-03-01"
    assert client.post("/api/recurring/999999/generate").status_code == 404


def test_enter_due_endpoint_and_the_scheduler_script(
    client, db_session, seed_accounts, monkeypatch, capsys
):
    _bill_schedule(client, _vendor(client), seed_accounts, start_date="2026-01-31")
    _journal_schedule(client, seed_accounts, mode="remind", start_date="2026-01-31")
    r = client.post("/api/memorized/enter-due?as_of=2026-01-31")
    assert r.status_code == 200, r.text
    assert r.json()["entered_count"] == 1 and r.json()["skipped"] == []
    assert r.json()["entered"][0]["kind"] == "bill"
    assert len(_bills(db_session)) == 1
    assert (
        client.get("/api/memorized/due?as_of=2026-01-31").json()[0]["kind"] == "journal"
    )

    # the cron script runs both generators and prints what each did
    class _DummySession:
        def __init__(self):
            self.info = {}  # the script names the run for the audit log

        def close(self):
            pass

    monkeypatch.setattr(app.database, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        recurring_service,
        "generate_due_invoices",
        lambda db, as_of=None, skipped=None, taken=None: [],
    )
    from app.services import memorized_service

    def fake_enter(db, as_of=None, skipped=None):
        skipped.append({"message": "Old (due 2026-01-05) was not entered: closed"})
        return [
            {
                "name": "Electric bill",
                "kind": "bill",
                "date": "2026-01-31",
                "next_date": "2026-02-28",
            }
        ]

    monkeypatch.setattr(memorized_service, "enter_due_memorized", fake_enter)
    runpy.run_path(str(RUN_RECURRING), run_name="__main__")
    out = capsys.readouterr().out
    assert "No recurring invoices due." in out
    assert "Entered 1 memorized transaction(s):" in out
    assert "Electric bill (bill dated 2026-01-31, next 2026-02-28)" in out
    assert "Skipped 1 memorized transaction(s):" in out
    assert "Old (due 2026-01-05) was not entered: closed" in out


# ---------------------------------------------------------------- CRUD


def test_create_read_update_delete(client, db_session, seed_accounts):
    vendor_id = _vendor(client)
    base = {
        "kind": "bill",
        "name": "X",
        "frequency": "monthly",
        "start_date": "2026-01-01",
    }
    lines = [{"account_id": seed_accounts["6500"].id, "amount": 10}]
    r = client.post("/api/memorized", json={**base, "lines": lines})
    assert r.status_code == 422 and "needs a vendor" in r.text
    r = client.post(
        "/api/memorized", json={**base, "vendor_id": 999999, "lines": lines}
    )
    assert r.status_code == 404
    r = client.post(
        "/api/memorized",
        json={**base, "kind": "invoice", "vendor_id": vendor_id, "lines": lines},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/memorized",
        json={**base, "mode": "later", "vendor_id": vendor_id, "lines": lines},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/memorized",
        json={
            **base,
            "vendor_id": vendor_id,
            "lines": [
                {"account_id": seed_accounts["6500"].id, "description": "nothing"}
            ],
        },
    )
    assert r.status_code == 400 and "more than zero" in r.text

    mem = _bill_schedule(client, vendor_id, seed_accounts, mode="remind")
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["vendor_name"] == "Harbor Power & Light"
    assert got["anchor_day"] == 31 and got["start_date"] == "2026-01-31"
    assert [(ln["description"], ln["amount"]) for ln in got["lines"]] == [
        ("Sanctuary", "400.00"),
        ("Parsonage", "100.00"),
    ]
    assert got["lines"][1]["quantity"] == "2.00" and got["lines"][1]["rate"] == "50.00"

    r = client.put(
        f"/api/memorized/{mem['id']}",
        json={
            "mode": "auto",
            "name": "Electric",
            "next_date": "2026-03-05",
            "frequency": "quarterly",
            "lines": [
                {
                    "account_id": seed_accounts["6500"].id,
                    "description": "All",
                    "amount": 90,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["mode"] == "auto" and got["name"] == "Electric"
    assert got["next_date"] == "2026-03-05" and got["anchor_day"] == 5
    assert got["frequency"] == "quarterly" and got["frequency_label"] == "Quarterly"
    assert [ln["description"] for ln in got["lines"]] == ["All"]
    assert Decimal(str(got["amount"])) == Decimal("90.00")
    # the edit meets the create rules
    r = client.put(
        f"/api/memorized/{mem['id']}", json={"frequency": "quarterly", "interval": 2}
    )
    assert r.status_code == 422
    r = client.put(f"/api/memorized/{mem['id']}", json={"name": None})
    assert r.status_code == 422
    r = client.put(f"/api/memorized/{mem['id']}", json={"vendor_id": 999999})
    assert r.status_code == 404

    enter_due_memorized(db_session, as_of=date(2026, 3, 5))
    assert len(_bills(db_session)) == 1
    r = client.delete(f"/api/memorized/{mem['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/memorized/{mem['id']}").status_code == 404
    # what it entered stays
    assert len(_bills(db_session)) == 1
    assert db_session.query(MemorizedTransaction).count() == 0


def test_a_class_or_job_a_memorized_transaction_uses_is_not_deleted(
    client, db_session, seed_accounts
):
    r = client.post("/api/classes", json={"name": "Building"})
    assert r.status_code == 201, r.text
    class_id = r.json()["id"]
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts, class_id=class_id)
    r = client.delete(f"/api/classes/{class_id}")
    assert r.status_code == 400 and "Electric bill" in r.json()["detail"]
    # an account a memorized line uses is named by the account delete's own
    # reference walk
    r = client.delete(f"/api/accounts/{seed_accounts['6100'].id}")
    assert r.status_code == 409 and "memorized transaction lines" in r.text
    client.delete(f"/api/memorized/{mem['id']}")
    assert client.delete(f"/api/classes/{class_id}").status_code == 200


# ---------------------------------------------------------------- the page


def test_the_page_is_wired():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'href="#/memorized"' in html and 'data-page="memorized"' in html
    assert "Memorized Transactions" in html
    assert '<script src="/static/js/memorized.js"></script>' in html
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "'/memorized':" in app_js and "MemorizedPage.render()" in app_js
    for action in (
        "MemorizedPage.enter",
        "MemorizedPage.pause",
        "MemorizedPage.resume",
        "MemorizedPage.del",
    ):
        assert f"'{action}'" in app_js

    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    # the API it drives
    for call in (
        "API.get('/memorized')",
        "/memorized/${id}/enter`",
        "/memorized/${id}/pause`",
        "/memorized/${id}/resume`",
        "API.post('/memorized/enter-due')",
        "/recurring/${id}/generate",
        "API.put(`/recurring/${id}`, { is_active: false })",
    ):
        assert call in js, call
    # the list: type, next date, how often, mode, last entered, actions, a Due filter
    for words in (
        "Next date",
        "How often",
        "Mode",
        "Last entered",
        "Enter now",
        "Pause",
        "Resume",
        "Edit",
        "Delete",
        "Due now",
    ):
        assert words in js, words
    assert "RecurringPage.showForm(id)" in js
    # the forms draw the bill form's grid and the journal form's
    assert "BillsPage.addLine(l)" in js and "BillsPage.collectLines()" in js
    assert 'id="bill-lines"' in js and "BillsPage.recalc()" in js
    assert "JournalPage.addLine()" in js and "JournalPage.fillLine(" in js
    assert "JournalPage.collectLines()" in js and 'id="je-lines"' in js
    # every frequency QuickBooks offers, and a custom one
    for words in (
        "Every two weeks",
        "Twice a month",
        "Every two months",
        "Quarterly",
        "Twice a year",
        "Yearly",
        "'custom'",
    ):
        assert words in js, words
    assert "memorizeBill(" in js and "memorizeJournal(" in js

    bills_js = (ROOT / "app/static/js/bills.js").read_text(encoding="utf-8")
    assert "collectLines() {" in bills_js
    assert "const { lines, missing } = BillsPage.collectLines();" in bills_js
    assert (
        "MemorizedPage.memorizeBill(${bill.id})" in bills_js
        and ">Memorize<" in bills_js
    )
    journal_js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert "collectLines() {" in journal_js and "fillLine(row, l) {" in journal_js
    assert "const lines = JournalPage.collectLines();" in journal_js
    assert "MemorizedPage.memorizeJournal(${entry.id})" in journal_js
    recurring_js = (ROOT / "app/static/js/recurring.js").read_text(encoding="utf-8")
    assert "App.navigate(RecurringPage.backTo())" in recurring_js


# ---------------------------------------------------------------- the migration


def test_the_migration_creates_the_tables_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "memorized.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == BEFORE
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    names = set(inspect(engine).get_table_names())
    assert {"memorized_transactions", "memorized_transaction_lines"} <= names
    cols = {c["name"] for c in inspect(engine).get_columns("memorized_transactions")}
    for name in (
        "kind",
        "mode",
        "frequency",
        "interval",
        "next_date",
        "anchor_day",
        "end_date",
        "occurrences_limit",
        "entered_count",
        "is_active",
        "vendor_id",
        "basis",
    ):
        assert name in cols, name
    line_cols = {
        c["name"] for c in inspect(engine).get_columns("memorized_transaction_lines")
    }
    for name in (
        "account_id",
        "item_id",
        "quantity",
        "rate",
        "debit",
        "credit",
        "job_id",
        "class_id",
        "cost_code_id",
        "cost_type",
        "function",
        "is_billable",
    ):
        assert name in line_cols, name
    engine.dispose()
    command.downgrade(cfg, BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    names = set(inspect(engine).get_table_names())
    assert "memorized_transactions" not in names
    assert "memorized_transaction_lines" not in names
    engine.dispose()


# ---------------------------------------------------------------------------
# from the review of #605
# ---------------------------------------------------------------------------


def test_an_edit_that_keeps_the_date_keeps_the_anchor(
    client, db_session, seed_accounts
):
    """The form sends the date it shows on every save, which is the clamped
    one (Feb 28 for a schedule on the 31st); a rename must not re-anchor the
    schedule to the 28th. A date the person changed still does."""
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts)
    enter_due_memorized(db_session, as_of=date(2026, 1, 31))
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-02-28"

    r = client.put(
        f"/api/memorized/{mem['id']}",
        json={"name": "Renamed", "next_date": "2026-02-28"},
    )
    assert r.status_code == 200, r.text
    enter_due_memorized(db_session, as_of=date(2026, 2, 28))
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-03-31"

    r = client.put(f"/api/memorized/{mem['id']}", json={"next_date": "2026-04-15"})
    assert r.status_code == 200, r.text
    enter_due_memorized(db_session, as_of=date(2026, 4, 15))
    assert client.get(f"/api/memorized/{mem['id']}").json()["next_date"] == "2026-05-15"


def test_a_cadence_word_without_a_count_resets_the_count(client, seed_accounts):
    """PUT {"frequency": "biweekly"} on a schedule stored weekly every 2 is
    still every two weeks, not every four."""
    vendor_id = _vendor(client)
    r = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Every two weeks",
            "frequency": "biweekly",
            "start_date": "2026-01-05",
            "vendor_id": vendor_id,
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
        },
    )
    assert r.status_code == 201, r.text
    mem = r.json()
    assert (mem["frequency"], mem["interval"]) == ("weekly", 2)
    r = client.put(f"/api/memorized/{mem['id']}", json={"frequency": "biweekly"})
    assert r.status_code == 200, r.text
    assert (r.json()["frequency"], r.json()["interval"]) == ("weekly", 2)
    # a count sent with the word is taken as sent
    r = client.put(
        f"/api/memorized/{mem['id']}", json={"frequency": "weekly", "interval": 3}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["frequency"], r.json()["interval"]) == ("weekly", 3)


def test_enter_now_refuses_a_paused_schedule(
    client, db_session, seed_accounts, seed_customer
):
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts)
    assert client.post(f"/api/memorized/{mem['id']}/pause").status_code == 200
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409, r.text
    assert "paused" in r.json()["detail"]
    assert _bills(db_session) == []
    # a recurring invoice the same way
    rec_id = _recurring_invoice(client, seed_customer.id, start="2099-01-01")
    assert (
        client.put(f"/api/recurring/{rec_id}", json={"is_active": False}).status_code
        == 200
    )
    r = client.post(f"/api/recurring/{rec_id}/generate")
    assert r.status_code == 409, r.text
    assert "paused" in r.json()["detail"]


def test_the_page_hides_enter_now_on_a_paused_row_and_keeps_the_bills_tax_rate():
    root = Path(__file__).resolve().parent.parent
    js = (root / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert "ended || r.status === 'paused' ? '' : `<button" in js
    assert "tax_rate: BillsPage._taxRate || 0" in js
    script = (root / "scripts/run_recurring.py").read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env python3")
    assert "except (OperationalError, ProgrammingError)" in script


# ---------------------------------------------------------------------------
# from the 2.31.1 Windows gate, round 2 (W-57, W-63, W-64, W-65, W-66)
# ---------------------------------------------------------------------------

WAIT = 10  # seconds a racer waits at a barrier before the test fails

# A locked file's 409, whatever holds the lock: not "another entry" or
# "another installment" of the schedule (2.31.1 Windows round 3 wording note).
BUSY_ENTRY = (
    "The company file is busy saving another change, so nothing was posted. "
    "Wait a moment and try again."
)
BUSY_INSTALLMENT = (
    "The company file is busy saving another change, so nothing was created. "
    "Wait a moment and try again."
)


@pytest.fixture
def file_company(tmp_path, monkeypatch):
    """A company in a file, opened the way the app opens one (WAL, the busy
    timeout), so two sessions on two threads contend as two requests do; the
    suite's one shared connection cannot race. An authenticated client on it
    for the set-up, the session factory for the racers, and the chart's ids
    in the shape _bill_schedule takes."""
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    import app.main as main
    from app.database import Base, enable_sqlite_tuning, get_db
    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
    from tests.conftest import _wire_app

    engine = sa.create_engine(
        "sqlite:///" + (tmp_path / "race.db").as_posix(),
        connect_args={"check_same_thread": False},
    )
    enable_sqlite_tuning(engine)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as s:
        accounts = {}
        for data in CHART_OF_ACCOUNTS:
            a = Account(
                account_number=data["account_number"],
                name=data["name"],
                account_type=AccountType(data["account_type"]),
                bank_kind=data.get("bank_kind"),
                detail_type=data.get("detail_type"),
                is_system=True,
                balance=Decimal("0"),
            )
            s.add(a)
            accounts[a.account_number] = a
        s.commit()
        ids = {n: types.SimpleNamespace(id=a.id) for n, a in accounts.items()}
    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(app.database, "SessionLocal", Session)
    _wire_app(Session)
    client = TestClient(main.app)
    r = client.post("/api/auth/setup", json={"password": "test-password-123"})
    assert r.status_code == 200, r.text
    try:
        yield client, Session, ids
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _race(*workers):
    """Each worker on its own thread, its result or its exception."""
    out = [None] * len(workers)

    def run(i, fn):
        try:
            out[i] = fn()
        except Exception as exc:  # noqa: BLE001 - a 500 is a finding too
            out[i] = exc

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert not any(th.is_alive() for th in threads), "a racer hung"
    return out


def _claim_after_both_read(monkeypatch):
    """Both racers have read the schedule (the same next date) before
    either claims it: the barrier sits in front of the claim, once per
    thread, so the contest is in the database, not in the test's timing."""
    both_read = threading.Barrier(2)
    real = memorized_service.claim_occurrence
    passed = threading.local()

    def claim(db, mem, *args, **kw):
        if not getattr(passed, "done", False):
            passed.done = True
            both_read.wait(WAIT)
        return real(db, mem, *args, **kw)

    monkeypatch.setattr(memorized_service, "claim_occurrence", claim)


def _words(doc):
    """A doc's text with its line wrapping undone, for a sentence check."""
    return " ".join((ROOT / doc).read_text(encoding="utf-8").split())


def _one_bill_entered_once(Session, mem_id, number="20260131-HPL"):
    with Session() as db:
        bills = db.query(Bill).order_by(Bill.id).all()
        assert [b.bill_number for b in bills] == [number]
        row = db.get(MemorizedTransaction, mem_id)
        assert row.entered_count == 1
        assert row.next_date == date(2026, 2, 28)
        assert row.last_entered_on == date(2026, 1, 31)
        assert row.last_document_id == bills[0].id


def test_two_enter_nows_at_once_post_one_document(file_company, monkeypatch):
    """W-57: two Enter nows that both read the same next date each posted a
    bill and wrote next date and count back once (two bills, entered_count
    1). The schedule is now moved on in one guarded UPDATE before anything
    posts; the second request matches no row and answers 409, naming the
    schedule as it now stands, with nothing posted."""
    from app.routes import memorized as memorized_routes

    client, Session, ids = file_company
    mem = _bill_schedule(client, _vendor(client), ids, mode="remind")
    _claim_after_both_read(monkeypatch)

    def enter_now():
        with Session() as db:
            return memorized_routes.enter_now(mem["id"], None, db)

    a, b = _race(enter_now, enter_now)
    outcomes = sorted(
        (
            "409"
            if isinstance(x, HTTPException) and x.status_code == 409
            else "ok" if isinstance(x, EnterResponse) else repr(x)
        )
        for x in (a, b)
    )
    assert outcomes == ["409", "ok"], (a, b)
    refused = a if isinstance(a, HTTPException) else b
    assert refused.detail.startswith("That occurrence was just entered")
    assert (
        "was due 2026-01-31 and its next date is now 2026-02-28, with 1 entered"
        in refused.detail
    )
    won = b if refused is a else a
    assert won.next_date == date(2026, 2, 28) and "next on 2026-02-28" in won.message
    _one_bill_entered_once(Session, mem["id"])
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["entered_count"] == 1 and got["next_date"] == "2026-02-28"


def test_enter_all_due_racing_enter_now_posts_one_document(file_company, monkeypatch):
    """W-57 for the run (and W-66's guard): Enter All Due and an Enter now
    on the same occurrence at once. Whichever claims first enters it; the
    run leaves a taken occurrence to the other window without calling it
    skipped, and the request that lost answers 409."""
    from app.routes import memorized as memorized_routes

    client, Session, ids = file_company
    mem = _bill_schedule(client, _vendor(client), ids)  # auto, due 2026-01-31
    _claim_after_both_read(monkeypatch)

    def run():
        with Session() as db:
            return scheduler.run_due_schedules(db, date(2026, 1, 31))

    def enter_now():
        with Session() as db:
            return memorized_routes.enter_now(mem["id"], None, db)

    a, b = _race(run, enter_now)
    assert isinstance(a, dict), a  # the run never raises
    assert a["skipped"] == [] and a["invoices_created"] == 0
    if a["entered_count"] == 1:
        assert isinstance(b, HTTPException) and b.status_code == 409, b
        assert b.detail.startswith("That occurrence was just entered")
    else:
        assert a["entered_count"] == 0 and a["entered"] == []
        assert isinstance(b, EnterResponse), b
    _one_bill_entered_once(Session, mem["id"])


def test_a_locked_file_is_a_409_and_a_skip_never_a_500(
    client, db_session, seed_accounts, monkeypatch
):
    """W-57: SQLite's "database is locked" from the claim (another entry
    held the file past the busy timeout) is a clean 409 on Enter now and a
    skip with the sentence in the run, and nothing is posted."""
    from sqlalchemy.orm import Query

    mem = _bill_schedule(client, _vendor(client), seed_accounts)

    def locked(self, *args, **kw):
        raise OperationalError(
            "UPDATE memorized_transactions ...",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr(Query, "update", locked)
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == BUSY_ENTRY
    assert _bills(db_session) == []
    skipped: list = []
    assert (
        enter_due_memorized(db_session, as_of=date(2026, 1, 31), skipped=skipped) == []
    )
    assert len(skipped) == 1
    assert skipped[0]["message"] == (
        "Electric bill (due 2026-01-31) was not entered: " + BUSY_ENTRY
    )
    assert _bills(db_session) == []
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["entered_count"] == 0 and got["next_date"] == "2026-01-31"


def test_the_page_sends_one_enter_at_a_time():
    """W-57: the Enter modal's button and Enter All Due are disabled while
    the request is in flight, and a 409 (entered meanwhile) reloads the
    list so the schedule shows as it now stands."""
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert "if (button && button.disabled) return;" in js
    assert js.count("if (button) button.disabled = true;") == 2
    assert (
        "if (err.status === 409) { closeModal(); App.navigate('#/memorized'); }" in js
    )
    assert 'id="mem-enter-all"' in js and "MemorizedPage.enterAllDue(this)" in js
    # one request for both kinds: the run the app does by itself
    assert "API.post('/memorized/enter-due')" in js
    assert "API.post('/recurring/generate')" not in js


def test_the_bill_number_rule_is_one_sentence(client, db_session, seed_accounts):
    """W-63: left blank, each bill is numbered the date and the vendor's
    initials (unique per day); a number on the template is used as it is
    on the first bill and -2, -3 after that, since a vendor's bill number
    cannot repeat (so a template number the vendor already has starts at
    -2); Memorize from a bill leaves the number blank on purpose. The
    sentence is under the number box and in the docs."""
    vendor_id = _vendor(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": "HPL-1041",
            "date": "2026-01-05",
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
        },
    )
    assert r.status_code == 201, r.text
    _bill_schedule(
        client, vendor_id, seed_accounts, name="Fixed", bill_number="HPL-1041"
    )
    _bill_schedule(client, vendor_id, seed_accounts, name="Blank")
    for as_of in (date(2026, 1, 31), date(2026, 2, 28)):
        enter_due_memorized(db_session, as_of=as_of)
    assert [b.bill_number for b in _bills(db_session)] == [
        "HPL-1041",
        "HPL-1041-2",
        "20260131-HPL",
        "HPL-1041-3",
        "20260228-HPL",
    ]

    rule = "Left blank, each bill is numbered the date and the vendor's initials"
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    hint = js[js.index('id="mem-bill-number-hint"') :]
    hint = hint[: hint.index("</div>")]
    assert rule in hint
    assert "-2, -3... after that, since a vendor's bill number cannot repeat" in hint
    assert "Memorize from a bill leaves it blank on purpose" in hint
    prefill = js[js.index("async memorizeBill(") : js.index("async showJournalForm(")]
    assert "bill_number" not in prefill
    for doc in ("docs/features.md", "CHANGELOG.md"):
        text = _words(doc)
        assert (
            "left blank, each bill is numbered the date and the vendor's initials"
            in text
        ), doc
        assert "since a vendor's bill number cannot repeat" in text, doc
        assert "leaves the number blank" in text, doc


def test_enter_now_on_an_ended_schedule_says_ended_not_paused(
    client, db_session, seed_accounts, seed_customer
):
    """W-64: the generator marks a recurring invoice past its end date
    inactive, so Enter now on it said "paused" and offered a resume that
    would not help. Ended is checked first, on both kinds."""
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "lines": [{"description": "Monthly service", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    rec_id = r.json()["id"]
    r = client.post("/api/recurring/generate?as_of=2026-03-01")
    assert r.status_code == 200 and r.json()["invoices_created"] == 1
    assert client.get(f"/api/recurring/{rec_id}").json()["is_active"] is False
    r = client.post(f"/api/recurring/{rec_id}/generate")
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("This schedule has ended")
    assert "2026-01-31" in r.json()["detail"] and "paused" not in r.json()["detail"]
    assert client.get("/api/memorized?kind=invoice").json()[0]["status"] == "ended"

    # a memorized schedule paused, then ended by an edit of its end date
    mem = _bill_schedule(client, _vendor(client), seed_accounts)
    enter_due_memorized(db_session, as_of=date(2026, 1, 31))
    assert client.post(f"/api/memorized/{mem['id']}/pause").status_code == 200
    r = client.put(f"/api/memorized/{mem['id']}", json={"end_date": "2026-02-15"})
    assert r.status_code == 200 and r.json()["status"] == "ended", r.text
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409, r.text
    assert "has ended" in r.json()["detail"] and "paused" not in r.json()["detail"]
    assert len(_bills(db_session)) == 1


def test_a_cadence_word_takes_no_interval(client, seed_accounts):
    """W-65: "every two weeks" with an interval of 2 was stored as every
    four weeks. A word that carries its own count takes no interval and is
    refused with one, naming the word, on create and on update alike; a
    plain word (daily, weekly, monthly, yearly) takes the interval."""
    vendor_id = _vendor(client)
    base = {
        "kind": "bill",
        "name": "Cadence",
        "start_date": "2026-01-05",
        "vendor_id": vendor_id,
        "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
    }
    r = client.post(
        "/api/memorized", json={**base, "frequency": "every two weeks", "interval": 2}
    )
    assert r.status_code == 422 and "every two weeks takes no interval" in r.text
    assert recurring_service.normalize_frequency("every_two_weeks", 1) == ("weekly", 2)
    with pytest.raises(ValueError, match="every two weeks takes no interval"):
        recurring_service.normalize_frequency("every_two_weeks", 2)

    mem = _bill_schedule(
        client, vendor_id, seed_accounts, frequency="biweekly", start_date="2026-01-05"
    )
    assert (mem["frequency"], mem["interval"]) == ("weekly", 2)
    r = client.put(
        f"/api/memorized/{mem['id']}",
        json={"frequency": "every two weeks", "interval": 2},
    )
    assert r.status_code == 422
    assert (
        "every two weeks takes no interval" in r.json()["detail"][0]["message"].lower()
    )
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert (got["frequency"], got["interval"]) == ("weekly", 2)
    for word in (
        "twice a year",
        "twice a month",
        "quarterly",
        "semimonthly",
        "bimonthly",
        "annually",
    ):
        r = client.put(
            f"/api/memorized/{mem['id']}", json={"frequency": word, "interval": 2}
        )
        assert r.status_code == 422, word
        message = r.json()["detail"][0]["message"].lower()
        assert f"{word} takes no interval" in message, word
    # the word with no interval, or 1, is the count it carries
    r = client.put(
        f"/api/memorized/{mem['id']}", json={"frequency": "twice a year", "interval": 1}
    )
    assert r.status_code == 200 and (r.json()["frequency"], r.json()["interval"]) == (
        "monthly",
        6,
    )
    assert r.json()["frequency_label"] == "Twice a year"
    # a plain word takes the interval
    r = client.put(
        f"/api/memorized/{mem['id']}", json={"frequency": "monthly", "interval": 2}
    )
    assert r.status_code == 200 and r.json()["frequency_label"] == "Every two months"
    r = client.post(
        "/api/memorized", json={**base, "frequency": "daily", "interval": 10}
    )
    assert r.status_code == 201 and r.json()["frequency_label"] == "Every 10 days"


def test_one_run_enters_every_missed_occurrence_in_order(
    client, db_session, seed_accounts, seed_customer
):
    """W-66: a run (Enter All Due, the startup run, the daily run) used to
    enter one occurrence per schedule per press. Now it enters every
    missed occurrence up to today, the earliest date first across every
    schedule, recurring invoices included, each committed as it is made."""
    _bill_schedule(client, _vendor(client), seed_accounts)  # monthly from Jan 31
    _journal_schedule(client, seed_accounts, start_date="2026-02-10")
    inv_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")

    r = client.post("/api/memorized/enter-due?as_of=2026-03-15")
    assert r.status_code == 200, r.text
    out = r.json()
    assert [(e["name"], e["date"]) for e in out["entered"]] == [
        ("Electric bill", "2026-01-31"),
        ("Monthly depreciation", "2026-02-10"),
        ("Electric bill", "2026-02-28"),
        ("Monthly depreciation", "2026-03-10"),
    ]
    assert out["entered_count"] == 4 and out["skipped"] == []
    assert out["invoices_created"] == 3 and len(out["invoice_ids"]) == 3
    assert [b.date for b in _bills(db_session)] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
    ]
    assert [b.bill_number for b in _bills(db_session)] == [
        "20260131-HPL",
        "20260228-HPL",
    ]
    invoices = db_session.query(Invoice).order_by(Invoice.id).all()
    assert [i.date for i in invoices] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    rows = {(r["kind"], r["id"]): r for r in client.get("/api/memorized").json()}
    assert rows[("invoice", inv_id)]["next_date"] == "2026-04-01"
    assert rows[("invoice", inv_id)]["entered_count"] == 3
    bill_row = next(r for r in rows.values() if r["kind"] == "bill")
    assert bill_row["entered_count"] == 2 and bill_row["next_date"] == "2026-03-31"
    # nothing left for a second run
    again = client.post("/api/memorized/enter-due?as_of=2026-03-15").json()
    assert again["entered"] == [] and again["invoices_created"] == 0


def test_the_task_runs_at_startup_and_again_when_the_day_changes(
    client, db_session, seed_accounts, monkeypatch
):
    """W-66: the task the lifespan starts runs once at startup and again
    each time the calendar day changes (looked at every poll), with a fake
    clock and a fake sleep; the run goes through the task's own session."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    _bill_schedule(client, _vendor(client), seed_accounts, start_date="2026-03-15")
    clock = {"today": date(2026, 3, 15)}
    naps: list = []

    class Done(Exception):
        pass

    async def nap(seconds):
        naps.append(seconds)
        if len(naps) == 1:
            clock["today"] = date(2026, 3, 16)  # a new day, nothing due
        elif len(naps) == 2:
            clock["today"] = date(2026, 4, 16)  # asleep a month: April 15 is due
        else:
            raise Done

    async def inline(fn, *args):  # the test's own connection, not a thread
        return fn(*args)

    with pytest.raises(Done):
        asyncio.run(
            scheduler.keep_entering(
                today=lambda: clock["today"], sleep=nap, to_thread=inline
            )
        )
    assert naps == [scheduler.POLL_SECONDS] * 3
    assert [b.date for b in _bills(db_session)] == [
        date(2026, 3, 15),
        date(2026, 4, 15),
    ]
    last = scheduler.last_run()
    assert last["origin"] == "daily" and last["as_of"] == "2026-04-16"
    assert last["entered_count"] == 1 and last["entered"][0]["date"] == "2026-04-15"
    assert last["ran_at"]


def test_the_setting_off_enters_nothing_by_itself(
    client, db_session, seed_accounts, monkeypatch
):
    """W-66: auto_enter_on_open (on by default) off means the startup and
    daily runs enter nothing; Enter All Due and Enter now still do."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    assert client.get("/api/settings").json()["auto_enter_on_open"] == "true"
    mem = _bill_schedule(client, _vendor(client), seed_accounts)
    assert (
        client.put("/api/settings", json={"auto_enter_on_open": "maybe"}).status_code
        == 422
    )
    r = client.put("/api/settings", json={"auto_enter_on_open": "false"})
    assert r.status_code == 200 and r.json()["auto_enter_on_open"] == "false", r.text
    assert (
        scheduler.run_in_this_process("startup", today=lambda: date(2026, 3, 15))
        is None
    )
    assert _bills(db_session) == []
    assert scheduler.last_run() is None
    assert client.get("/api/memorized/entered-recently").json() == {}
    r = client.post("/api/memorized/enter-due?as_of=2026-03-15")
    assert r.status_code == 200 and r.json()["entered_count"] == 2
    r = client.put("/api/settings", json={"auto_enter_on_open": "true"})
    assert r.status_code == 200
    _bill_schedule(
        client, _vendor(client, name="Water Works"), seed_accounts, name="Water"
    )
    out = scheduler.run_in_this_process("startup", today=lambda: date(2026, 1, 31))
    assert out["entered_count"] == 1 and out["entered"][0]["name"] == "Water"
    assert len(_bills(db_session)) == 3
    assert client.get(f"/api/memorized/{mem['id']}").json()["entered_count"] == 2


def test_the_page_is_told_what_was_entered_while_away(
    client, db_session, seed_accounts, monkeypatch
):
    """W-66: the run's note for the page ("Entered 3 scheduled documents
    while you were away"): what the app's last run entered, read off
    /api/memorized/entered-recently, shown once by the page."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    assert client.get("/api/memorized/entered-recently").json() == {}
    mem = _bill_schedule(client, _vendor(client), seed_accounts)
    out = scheduler.run_in_this_process("startup", today=lambda: date(2026, 2, 10))
    assert out["entered_count"] == 1
    got = client.get("/api/memorized/entered-recently").json()
    assert got["origin"] == "startup" and got["ran_at"] and got["as_of"] == "2026-02-10"
    assert got["entered_count"] == 1 and got["invoices_created"] == 0
    assert got["entered"][0]["name"] == "Electric bill"
    assert got["entered"][0]["date"] == "2026-01-31"
    assert got["entered"][0]["memorized_id"] == mem["id"]
    assert got["skipped"] == []

    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert "API.get('/memorized/entered-recently')" in js
    assert "scheduled document${n === 1 ? '' : 's'} while you were away" in js
    assert "_shownRun" in js and "whileAwayHtml(" in js


def test_the_app_starts_the_scheduler_when_it_boots(monkeypatch, lifespan_client):
    """W-66: the lifespan starts the task (the desktop launcher serves
    app.main.app through uvicorn, so it gets it too); the suite keeps it
    off with SLOWBOOKS_AUTO_ENTER=0, and start() answers None then."""
    import app.main as main

    calls: list = []
    ran = threading.Event()

    def record(origin="startup", today=date.today):
        calls.append((origin, today))
        ran.set()
        return None

    monkeypatch.setattr(scheduler, "run_in_this_process", record)
    monkeypatch.setenv("SLOWBOOKS_AUTO_ENTER", "1")
    with lifespan_client as c:
        assert c.get("/api/accounts").status_code == 200
        assert ran.wait(WAIT), "the startup run never came"
    assert [origin for origin, _ in calls] == ["startup"]
    assert calls[0][1]() == date.today()
    assert "_scheduler.start()" in Path(main.__file__).read_text(encoding="utf-8")

    monkeypatch.setenv("SLOWBOOKS_AUTO_ENTER", "0")

    async def off():
        return scheduler.start()

    assert asyncio.run(off()) is None


def test_the_page_offers_the_setting_and_the_docs_say_so():
    """W-66: the box on the page, the hint, the mode's words, the docs and
    the changelog say the app enters due memorized bills and journal entries
    when it opens and once a day, that recurring invoices wait for Generate
    or Enter All Due as before, and that the cron script stays."""
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert (
        "Enter due memorized bills and journal entries when the app opens and once a day"
        in js
    )
    assert (
        'name="auto_enter_on_open"' in js and "MemorizedPage.setAutoEnter(this)" in js
    )
    assert (
        "API.put('/settings', { auto_enter_on_open: box.checked ? 'true' : 'false' })"
        in js
    )
    assert (
        "when it opens and once a day while it runs, every missed date in order" in js
    )
    assert "A server install can run scripts/run_recurring.py from cron instead." in js
    assert "Auto-enter: posted on its date (when the app opens, or its daily run)" in js
    from app.models.settings import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["auto_enter_on_open"] == "true"
    for doc in ("docs/features.md", "CHANGELOG.md"):
        text = _words(doc)
        assert "when it opens and once a day while it runs" in text, doc
        assert "not entered on their own" in text, doc
        assert "`scripts/run_recurring.py` from cron instead" in text, doc
        assert "takes no interval" in text, doc
        assert "has ended" in text, doc
    features = _words("docs/features.md")
    assert "`/api/memorized/entered-recently` | GET" in features
    assert "`auto_enter_on_open`" in features
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = changelog[changelog.index("### v2.31.1") : changelog.index("### v2.31.0")]
    paragraph = section[section.index("**Memorized bills and journal entries") :]
    paragraph = paragraph[: paragraph.index("\n\n")]
    assert "\u2014" not in paragraph  # no em-dashes
    assert "was entered while you were away" in " ".join(paragraph.split())
    assert "`auto_enter_on_open`" in paragraph


def test_the_automatic_runs_leave_recurring_invoices_to_generate(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    """2.31.1 gate, Linux round 2: the Riverbend comparison found two monthly
    recurring invoices overdue since September posted on the first open of
    2.31.1, $700 nobody asked for. The startup and daily runs enter due
    auto-enter memorized bills and journal entries only; a recurring invoice
    waits for Generate or Enter All Due, as it always has."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    mem = _bill_schedule(client, _vendor(client), seed_accounts)
    out = scheduler.run_in_this_process("startup", today=lambda: date(2026, 3, 15))
    assert out["invoices_created"] == 0 and out["invoice_ids"] == []
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 0
    assert out["entered_count"] == 2  # the memorized bill's Jan 31 and Feb 28
    assert client.get(f"/api/memorized/{mem['id']}").json()["entered_count"] == 2
    # Enter All Due, pressed by a person, enters both kinds
    r = client.post("/api/memorized/enter-due?as_of=2026-03-15")
    assert r.status_code == 200, r.text
    assert r.json()["invoices_created"] == 3  # Jan 1, Feb 1, Mar 1
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 3


# ---------------------------------------------------------------------------
# from the 2.31.1 Windows gate, round 3 (W-70, W-71, W-75 to W-78, W-81)
# ---------------------------------------------------------------------------


def _outcome(x):
    """A route function's answer as the status the page would see."""
    if isinstance(x, HTTPException):
        return str(x.status_code)
    if isinstance(x, EnterResponse) or isinstance(x, dict):
        return "200"
    return repr(x)


def test_two_clicks_for_the_same_occurrence_post_one_document(
    file_company, monkeypatch
):
    """W-75: Enter now carries the occurrence the page showed. Two windows
    asking for the same occurrence at once: one 200, one 409 naming the
    occurrence and the next date, and one bill, the second click never
    posting the next occurrence early."""
    from app.routes import memorized as memorized_routes
    from app.schemas.memorized import EnterRequest

    client, Session, ids = file_company
    mem = _bill_schedule(client, _vendor(client), ids, mode="remind")
    _claim_after_both_read(monkeypatch)

    def click():
        with Session() as db:
            return memorized_routes.enter_now(
                mem["id"], EnterRequest(occurrence=date(2026, 1, 31)), db
            )

    a, b = _race(click, click)
    assert sorted(_outcome(x) for x in (a, b)) == ["200", "409"], (a, b)
    refused = a if isinstance(a, HTTPException) else b
    assert "'Electric bill' was due 2026-01-31" in refused.detail
    assert "its next date is now 2026-02-28, with 1 entered" in refused.detail
    won = b if refused is a else a
    assert won.occurrence == won.date == date(2026, 1, 31)
    _one_bill_entered_once(Session, mem["id"])


def test_a_page_left_open_is_refused_rather_than_posting_the_next_one(
    client, db_session, seed_accounts
):
    """W-75: a page left open after the scheduler entered January asked to
    enter January and got February, early; with Today it posted a second
    January-period bill and February was silently used up. The request
    names its occurrence: one already entered is a 409 naming it and the
    schedule's next date, nothing posted, the schedule untouched. Dated
    today still enters THAT occurrence; without one (an API caller) the
    next date is entered, as before."""
    mem = _bill_schedule(client, _vendor(client), seed_accounts)  # auto, Jan 31
    page_showed = client.get("/api/memorized").json()[0]["next_date"]
    assert page_showed == "2026-01-31"
    assert len(enter_due_memorized(db_session, as_of=date(2026, 1, 31))) == 1

    for body in (
        {"occurrence": page_showed},
        {"occurrence": page_showed, "date": "2026-02-05"},  # "Today"
    ):
        r = client.post(f"/api/memorized/{mem['id']}/enter", json=body)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "nothing was posted" in detail
        assert "'Electric bill' was due 2026-01-31" in detail
        assert "its next date is now 2026-02-28, with 1 entered" in detail
        assert len(_bills(db_session)) == 1
        got = client.get(f"/api/memorized/{mem['id']}").json()
        assert got["next_date"] == "2026-02-28" and got["entered_count"] == 1

    # the occurrence the page shows now, dated today: that one, not the next
    r = client.post(
        f"/api/memorized/{mem['id']}/enter",
        json={"occurrence": "2026-02-28", "date": "2026-02-05"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["occurrence"] == "2026-02-28" and out["date"] == "2026-02-05"
    assert out["next_date"] == "2026-03-31"
    assert out["message"] == (
        "Entered the bill dated 2026-02-05 for the 2026-02-28 occurrence; "
        "next on 2026-03-31."
    )
    assert _bills(db_session)[-1].date == date(2026, 2, 5)
    # an API caller that names no occurrence enters the next date
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 200, r.text
    assert r.json()["occurrence"] == r.json()["date"] == "2026-03-31"
    assert (
        r.json()["message"] == "Entered the bill dated 2026-03-31; next on 2026-04-30."
    )
    assert len(_bills(db_session)) == 3


def test_the_enter_modal_sends_the_occurrence_it_was_opened_for():
    """W-75: the modal keeps the row's next date and sends it; a 409 shows
    the sentence and reloads the list."""
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    enter = js[js.index("    enter(kind, id) {") : js.index("    async pause(")]
    assert (
        '<input type="hidden" name="occurrence" value="${r ? escapeHtml(r.next_date) : \'\'}">'
        in enter
    )
    assert "const occurrence = form.occurrence.value || null;" in enter
    assert "if (occurrence) body.occurrence = occurrence;" in enter
    assert "API.post(`/memorized/${id}/enter`, body)" in enter
    assert "toast(err.message, 'error');" in enter
    assert (
        "if (err.status === 409) { closeModal(); App.navigate('#/memorized'); }"
        in enter
    )


def _installment_claimed_after_both_read(monkeypatch):
    """_claim_after_both_read for a recurring invoice's installment: both
    racers have read the schedule and checked the closing date (the last
    read before anything is written, then and now) before either writes,
    once per thread."""
    import app.services.closing_date as closing_date

    both_read = threading.Barrier(2)
    real = closing_date.check_closing_date
    passed = threading.local()

    def checked(db, txn_date, *args, **kw):
        if not getattr(passed, "done", False):
            passed.done = True
            both_read.wait(WAIT)
        return real(db, txn_date, *args, **kw)

    monkeypatch.setattr(closing_date, "check_closing_date", checked)


def _one_invoice_made_once(Session, rec_id):
    from app.models.recurring import RecurringInvoice

    with Session() as db:
        made = db.query(Invoice).filter(Invoice.recurring_invoice_id == rec_id).all()
        assert [i.date for i in made] == [date(2026, 1, 1)]
        rec = db.get(RecurringInvoice, rec_id)
        assert rec.next_due == date(2026, 2, 1) and rec.invoices_created == 1


def test_two_enter_nows_on_a_recurring_invoice_make_one_invoice(
    file_company, monkeypatch
):
    """W-71: recurring invoices had no claim, so two generations at once
    each invoiced the same period. Enter now on the same installment from
    two windows at once: one 200, one 409 "That installment was just
    generated", and one invoice."""
    from app.routes import recurring as recurring_routes

    client, Session, ids = file_company
    r = client.post("/api/customers", json={"name": "Riverbend Dental"})
    assert r.status_code == 201, r.text
    rec_id = _recurring_invoice(client, r.json()["id"], start="2026-01-01")
    _installment_claimed_after_both_read(monkeypatch)

    def click():
        with Session() as db:
            return recurring_routes.generate_one(
                rec_id, dated=None, occurrence=date(2026, 1, 1), db=db
            )

    a, b = _race(click, click)
    assert sorted(_outcome(x) for x in (a, b)) == ["200", "409"], (a, b)
    refused = a if isinstance(a, HTTPException) else b
    assert refused.detail.startswith("That installment was just generated")
    assert "the schedule for Riverbend Dental was due 2026-01-01" in refused.detail
    assert "its next due date is now 2026-02-01" in refused.detail
    won = b if refused is a else a
    assert won["invoices_created"] == 1 and won["next_due"] == date(2026, 2, 1)
    _one_invoice_made_once(Session, rec_id)


def test_two_generations_at_once_invoice_a_period_once(file_company, monkeypatch):
    """W-71: two runs of Generate (or Enter All Due, or the cron script) at
    once. Whichever claims the installment first makes the invoice; the
    other finds the schedule moved on and leaves it, without calling it
    skipped and without an error."""
    client, Session, ids = file_company
    r = client.post("/api/customers", json={"name": "Riverbend Dental"})
    rec_id = _recurring_invoice(client, r.json()["id"], start="2026-01-01")
    _installment_claimed_after_both_read(monkeypatch)
    skips: list = [[], []]

    def generate(i):
        def run():
            with Session() as db:
                return recurring_service.generate_due_invoices(
                    db, date(2026, 1, 15), skipped=skips[i]
                )

        return run

    a, b = _race(generate(0), generate(1))
    assert isinstance(a, list) and isinstance(b, list), (a, b)
    assert sorted([len(a), len(b)]) == [0, 1] and skips == [[], []]
    _one_invoice_made_once(Session, rec_id)


def test_a_stale_recurring_installment_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    """W-71 with W-75's occurrence: the page showed January, and January
    was generated meanwhile. Enter now for January, dated its date or
    today, is a 409 and makes nothing; the installment the page shows now
    is made, and an API caller that names none gets the next due date."""
    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    r = client.post("/api/recurring/generate?as_of=2026-01-15")
    assert r.json()["invoices_created"] == 1
    for query in ("occurrence=2026-01-01", "occurrence=2026-01-01&dated=2026-01-20"):
        r = client.post(f"/api/recurring/{rec_id}/generate?{query}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"].startswith("That installment was just generated")
        assert "was due 2026-01-01 and its next due date is now 2026-02-01" in (
            r.json()["detail"]
        )
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 1
    r = client.post(
        f"/api/recurring/{rec_id}/generate?occurrence=2026-02-01&dated=2026-01-20"
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["occurrence"] == "2026-02-01" and r.json()["next_due"] == "2026-03-01"
    )
    inv = db_session.get(Invoice, r.json()["invoice_ids"][0])
    assert inv.date == date(2026, 1, 20)
    r = client.post(f"/api/recurring/{rec_id}/generate")
    assert r.status_code == 200 and r.json()["occurrence"] == "2026-03-01"
    row = client.get("/api/memorized?kind=invoice").json()[0]
    assert row["entered_count"] == 3 and row["next_date"] == "2026-04-01"


def test_a_locked_file_while_generating_is_a_409_and_a_skip(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    """W-71: SQLite's "database is locked" at the claim is a clean 409 on
    Enter now and a named skip in Generate, never a 500, nothing posted and
    the schedule where it was."""
    from sqlalchemy.orm import Query

    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")

    def locked(self, *args, **kw):
        raise OperationalError(
            "UPDATE recurring_invoices ...",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr(Query, "update", locked)
    r = client.post(f"/api/recurring/{rec_id}/generate")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == BUSY_INSTALLMENT
    r = client.post("/api/recurring/generate?as_of=2026-01-15")
    assert r.status_code == 200, r.text
    assert r.json()["invoices_created"] == 0
    (skip,) = r.json()["skipped"]
    assert skip["recurring_id"] == rec_id
    assert skip["message"] == (
        "The schedule for Test Customer (due 2026-01-01) was not generated this "
        "time: the company file was busy saving another change. Run it again in "
        "a moment."
    )
    monkeypatch.undo()
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 0
    got = client.get(f"/api/recurring/{rec_id}").json()
    assert got["next_due"] == "2026-01-01" and got["invoices_created"] == 0


def test_the_comments_and_the_page_say_both_kinds_are_claimed():
    """W-71: the scheduler's and the cron script's notes said no occurrence
    could be entered twice, which held for memorized ones only; both name
    both claims now. The page sends the installment it showed."""
    note = " ".join(
        (ROOT / "app/services/scheduler.py").read_text(encoding="utf-8").split()
    )
    assert "memorized_service.claim_occurrence" in note
    assert "recurring_service.claim_installment" in note
    script = " ".join(RUN_RECURRING.read_text(encoding="utf-8").split())
    assert (
        "a memorized occurrence and a recurring invoice's installment are each "
        "claimed in the database before anything is posted" in script
    )
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert "occurrence ? `occurrence=${occurrence}` : ''" in js
    assert "API.post(`/recurring/${id}/generate` + (query ? `?${query}` : ''))" in js


def test_twice_a_month_takes_one_or_none_and_names_itself_otherwise(
    client, seed_accounts
):
    """W-76: "twice a month" was not a word the schedule knew, so it was
    refused even with an interval of 1 (or none), with the list of words
    rather than its own name. A word that carries its own count takes 1 or
    none; only another interval is refused, naming the word."""
    assert normalize_frequency("twice a month", 1) == ("semimonthly", 1)
    assert normalize_frequency("twice a month", None) == ("semimonthly", 1)
    assert normalize_frequency("Twice-a-Month", None) == ("semimonthly", 1)
    assert normalize_frequency("semi-monthly", 1) == ("semimonthly", 1)
    with pytest.raises(ValueError) as refused:
        normalize_frequency("twice a month", 2)
    assert str(refused.value) == (
        "twice a month takes no interval; for every N months use monthly with "
        "an interval"
    )
    base = {
        "kind": "bill",
        "name": "Twice a month",
        "start_date": "2026-01-01",
        "vendor_id": _vendor(client),
        "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
    }
    for sent in ({"interval": 1}, {}):
        r = client.post(
            "/api/memorized", json={**base, "frequency": "twice a month", **sent}
        )
        assert r.status_code == 201, (sent, r.text)
        assert (r.json()["frequency"], r.json()["interval"]) == ("semimonthly", 1)
        assert r.json()["frequency_label"] == "Twice a month"
    r = client.post(
        "/api/memorized", json={**base, "frequency": "twice a month", "interval": 2}
    )
    assert r.status_code == 422, r.text
    assert [e["message"] for e in r.json()["detail"]] == [
        "Twice a month takes no interval; for every N months use monthly with "
        "an interval."
    ]


def test_an_edit_refused_says_one_plain_sentence(client, seed_accounts):
    """W-77: an edit the create rules refuse answered the validator's dump
    of the whole request ("1 validation error for MemorizedCreate ...
    input_value={...}"). It answers the API's one shape for a refused
    field now, the list a create's refusal and the request's own
    validation answer (an unknown field, a missing date), each error with
    one plain sentence in `message`, the field named in words, as the
    page shows it."""
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts)
    url = f"/api/memorized/{mem['id']}"
    cadence = {"frequency": "every two weeks", "interval": 2}
    created = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "New",
            "start_date": "2026-01-05",
            "vendor_id": vendor_id,
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": 10}],
            **cadence,
        },
    )
    assert created.status_code == 422
    for body, sentence in (
        (
            cadence,
            "Every two weeks takes no interval; for every N weeks use weekly "
            "with an interval.",
        ),
        ({"name": "   "}, "Name may not be blank."),
        ({"mode": "later"}, None),
        (
            {"end_date": "2026-01-01"},
            "The end date (2026-01-01) is before the first date (2026-01-31).",
        ),
        (
            {"lines": [{"account_id": seed_accounts["6500"].id, "amount": -5}]},
            "Line 1: Amount must be non-negative; use a vendor credit for refunds.",
        ),
    ):
        r = client.put(url, json=body)
        assert r.status_code == 422, (body, r.text)
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail, detail
        message = detail[0]["message"]
        for dump in ("validation error", "input_value", "pydantic", "[type="):
            assert dump not in message, (body, message)
        assert message[0].isupper() and message.endswith("."), message
        if sentence:
            assert message == sentence, message
    # the same sentence, in the same shape, the create's refusal gives
    assert client.put(url, json=cadence).json()["detail"][0]["message"] == (
        created.json()["detail"][0]["message"]
    )
    # and the shape the request's own validation answers on this router
    unknown = client.put(url, json={"colour": "blue"}).json()["detail"]
    assert unknown[0]["message"] == "'colour' is not a field this accepts."
    # and a create's refusal of a line no longer carries the line's dump
    r = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "New",
            "frequency": "monthly",
            "start_date": "2026-01-05",
            "vendor_id": vendor_id,
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": -5}],
        },
    )
    assert r.status_code == 422
    assert [e["message"] for e in r.json()["detail"]] == [
        "Line 1: Amount must be non-negative; use a vendor credit for refunds."
    ]
    assert (
        client.put(url, json={"mode": "later"})
        .json()["detail"][0]["message"]
        .startswith("Mode must be auto")
    )
    got = client.get(url).json()
    assert got["name"] == "Electric bill" and got["end_date"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_run_that_only_skipped_says_what_could_not_be_entered():
    """W-78: a run that entered nothing and skipped something said
    "Entered 0 scheduled documents while you were away". It says "N
    scheduled documents could not be entered", with the reasons, and
    nothing at all when nothing was entered or skipped."""
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "memorized_while_away_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    closed = "Rent (due 2026-01-05) was not entered: the closing date is 2026-01-31."
    one = got["skippedOne"]
    assert one["text"] == f"1 scheduled document could not be entered. {closed}"
    assert one["toasts"] == [] and 'role="status"' in one["html"]
    two = got["skippedTwo"]
    assert two["text"].startswith(
        "2 scheduled documents could not be entered (today's run). " + closed
    )
    assert "Water (due 2026-01-07) was not entered" in two["text"]
    for key in ("skippedOne", "skippedTwo"):
        assert "Entered 0" not in got[key]["text"], key
        assert "Not entered:" not in got[key]["text"], key
    both = got["both"]
    assert both["text"].startswith(
        "Entered 1 scheduled document while you were away. Electric bill dated "
        "2026-01-31 Not entered: " + closed
    )
    assert both["toasts"] == ["Entered 1 scheduled document while you were away"]
    assert got["neither"]["html"] == "" and got["again"]["html"] == ""


def test_a_recurring_backlog_past_a_thousand_is_caught_up_in_one_run(
    monkeypatch, caplog
):
    """W-81: a run made at most 1,000 passes, one installment per schedule
    each, so a longer backlog needed a second run. It passes until a pass
    generates nothing; the cap left is a safety net no backlog meets, and
    reaching it is logged."""
    due = {"left": 1500}

    def one_more(db, as_of=None, skipped=None, taken=None):
        if not due["left"]:
            return []
        due["left"] -= 1
        return [1500 - due["left"]]

    monkeypatch.setattr(recurring_service, "generate_due_invoices", one_more)
    monkeypatch.setattr(
        memorized_service, "enter_due_memorized", lambda db, as_of, skipped: []
    )
    run = scheduler.run_due_schedules(None, date(2026, 3, 1))
    assert run["invoices_created"] == 1500 and run["invoice_ids"][-1] == 1500
    assert run["skipped"] == [] and due["left"] == 0
    assert scheduler.PASS_SAFETY_CAP >= 100_000

    monkeypatch.setattr(scheduler, "PASS_SAFETY_CAP", 5)
    monkeypatch.setattr(
        recurring_service,
        "generate_due_invoices",
        lambda db, as_of, skipped, taken=None: [7],
    )
    with caplog.at_level("WARNING", logger="app.services.scheduler"):
        ids, _ = scheduler.catch_up_recurring(None, date(2026, 3, 1))
    assert ids == [7] * 5
    assert "stopped after 5 passes" in caplog.text


def test_the_cron_script_catches_up_both_kinds_as_enter_all_due_does(
    client, db_session, seed_accounts, seed_customer, TestSession, monkeypatch, capsys
):
    """W-81: the cron script generated one installment of each recurring
    invoice per run but entered every missed memorized occurrence. It
    catches up both, the way Enter All Due does (the same two steps), so a
    server's cron and the button agree; the docstring and the docs say so."""
    from datetime import timedelta

    today = date.today()
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "weekly",
            "start_date": (today - timedelta(days=21)).isoformat(),
            "lines": [{"description": "Weekly service", "quantity": 1, "rate": 25}],
        },
    )
    assert r.status_code == 201, r.text
    rec_id = r.json()["id"]
    mem = _bill_schedule(
        client,
        _vendor(client),
        seed_accounts,
        frequency="weekly",
        start_date=(today - timedelta(days=14)).isoformat(),
    )
    monkeypatch.setattr(app.database, "SessionLocal", TestSession)
    runpy.run_path(str(RUN_RECURRING), run_name="__main__")
    out = capsys.readouterr().out
    assert "Generated 4 invoice(s):" in out
    assert "Entered 3 memorized transaction(s):" in out
    db_session.expire_all()
    made = db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).all()
    assert sorted(i.date for i in made) == [
        today - timedelta(days=d) for d in (21, 14, 7, 0)
    ]
    assert len(_bills(db_session)) == 3
    assert client.get(f"/api/memorized/{mem['id']}").json()["entered_count"] == 3
    # nobody signs in to a cron run (W-70): the audit log names it
    assert {n for b in _bills(db_session) for n in _who_entered(db_session, b)} == {
        "scheduled run"
    }
    # Enter All Due finds nothing left: the two did the same catch-up
    again = client.post("/api/memorized/enter-due").json()
    assert again["invoices_created"] == 0 and again["entered_count"] == 0

    script = " ".join(RUN_RECURRING.read_text(encoding="utf-8").split())
    assert "It catches up both kinds the way Enter All Due does" in script
    assert "scheduler.catch_up_recurring(db)" in script
    for doc in ("docs/features.md", "CHANGELOG.md"):
        assert "catches up both kinds" in _words(doc), doc


def test_ticking_active_on_an_ended_schedule_says_it_has_ended(
    client, db_session, seed_accounts, seed_customer
):
    """W-81: re-ticking Active on a recurring invoice past its end date
    answered 200 and changed nothing that mattered: it could never bill
    again. It is a 409 with the ended sentence, the schedule as it was; an
    end date cleared or moved past the next due date in the same request is
    what brings it back. Resume on an ended memorized schedule the same."""

    def ended_schedule():
        r = client.post(
            "/api/recurring",
            json={
                "customer_id": seed_customer.id,
                "frequency": "monthly",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "lines": [{"description": "Monthly", "quantity": 1, "rate": 100}],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    rec_id = ended_schedule()
    assert (
        client.post("/api/recurring/generate?as_of=2026-03-01").json()[
            "invoices_created"
        ]
        == 1
    )
    url = f"/api/recurring/{rec_id}"
    before = client.get(url).json()
    assert before["is_active"] is False and before["next_due"] == "2026-02-01"
    sentence = (
        "This schedule has ended: its end date was 2026-01-31; move or clear "
        "the end date first."
    )
    form = {
        "frequency": "monthly",
        "end_date": "2026-01-31",
        "terms": "Net 30",
        "tax_rate": 0,
        "notes": "ticked again",
        "is_active": True,
        "lines": [{"description": "Monthly", "quantity": 1, "rate": 100}],
    }
    for body in ({"is_active": True}, form, {**form, "end_date": "2026-01-15"}):
        r = client.put(url, json=body)
        assert r.status_code == 409, (body, r.text)
        assert r.json()["detail"].startswith("This schedule has ended: its end date")
        assert r.json()["detail"].endswith("move or clear the end date first.")
    assert client.put(url, json={"is_active": True}).json()["detail"] == sentence
    after = client.get(url).json()
    assert after == before  # nothing changed, notes included
    # an edit that does not tick Active is still an edit
    r = client.put(url, json={"notes": "kept for the file"})
    assert r.status_code == 200 and r.json()["is_active"] is False
    # moving the end date in the same request brings it back
    r = client.put(url, json={**form, "end_date": "2099-12-31"})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True
    assert date.fromisoformat(r.json()["next_due"]) >= date.today()
    # and so does clearing it
    other = ended_schedule()
    client.post("/api/recurring/generate?as_of=2026-03-01")
    r = client.put(
        f"/api/recurring/{other}", json={"is_active": True, "end_date": None}
    )
    assert r.status_code == 200 and r.json()["is_active"] is True, r.text

    mem = _bill_schedule(client, _vendor(client), seed_accounts, end_date="2026-01-31")
    enter_due_memorized(db_session, as_of=date(2026, 3, 1))
    assert client.post(f"/api/memorized/{mem['id']}/pause").status_code == 200
    r = client.post(f"/api/memorized/{mem['id']}/resume")
    assert r.status_code == 409, r.text
    assert "has ended: its end date was 2026-01-31" in r.json()["detail"]
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["is_active"] is False and got["next_date"] == "2026-02-28"


@pytest.mark.parametrize(
    "times, entered, words",
    [
        (1, 1, "the 1 occurrence is entered."),
        (2, 2, "both occurrences are entered."),
        (3, 3, "all 3 occurrences are entered."),
        (12, 12, "all 12 occurrences are entered."),
        (2, 3, "its number of times (2) is reached, with 3 entered."),
    ],
)
def test_the_ended_sentence_reads_for_any_number_of_times(
    client, db_session, seed_accounts, times, entered, words
):
    """Mac gate nit: a schedule set to enter once ended with "all 1
    occurrences are entered". The sentence reads for one, two and more,
    and for a number of times lowered below what was already entered."""
    mem = _bill_schedule(client, _vendor(client), seed_accounts, occurrences=times)
    row = db_session.get(MemorizedTransaction, mem["id"])
    row.entered_count = entered
    db_session.commit()
    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        f"'Electric bill' has ended: {words} Edit it and raise the number of "
        "times to enter it again."
    )
    assert "1 occurrences" not in r.json()["detail"]


# The `client` fixture's password (conftest), a module constant.
FIXTURE_PW = "test-password-123"


def _who_entered(db_session, bill):
    from app.models.audit import AuditLog

    rows = (
        db_session.query(AuditLog)
        .filter_by(table_name="bills", record_id=bill.id, action="INSERT")
        .all()
    )
    return [r.username for r in rows]


def test_with_a_password_the_startup_run_waits_for_a_sign_in(
    client, db_session, seed_accounts, monkeypatch
):
    """W-70: the run at server start entered a memorized schedule before
    the company's password was entered. With a password set, the startup
    run enters nothing until someone signs in (a request that is not
    signed in, or an API token's, is not a sign-in), then enters, and the
    audit log names the person signed in."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    _bill_schedule(client, _vendor(client), seed_accounts)  # auto, due Jan 31
    token = client.post(
        "/api/tokens", json={"label": "reader", "role": "readonly"}
    ).json()["token"]
    assert client.post("/api/auth/logout").status_code == 200
    monkeypatch.setattr(scheduler, "_signed_in_as", None)  # the server starts

    def startup():
        return scheduler.run_in_this_process("startup", today=lambda: date(2026, 2, 10))

    assert startup() == scheduler.WAITING_FOR_SIGN_IN
    assert client.get("/api/accounts").status_code == 401
    r = client.get("/api/accounts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert startup() == scheduler.WAITING_FOR_SIGN_IN
    assert _bills(db_session) == [] and scheduler.last_run() is None
    assert client.get("/api/memorized/entered-recently").status_code == 401

    assert client.post("/api/auth/login", json={"password": FIXTURE_PW}).is_success
    out = startup()
    assert out["origin"] == "startup" and out["entered_count"] == 1
    (bill,) = _bills(db_session)
    assert bill.date == date(2026, 1, 31)
    assert _who_entered(db_session, bill) == ["admin"]
    assert client.get("/api/memorized/entered-recently").json()["entered_count"] == 1
    # the daily run goes too, now someone has signed in since the start
    daily = scheduler.run_in_this_process("daily", today=lambda: date(2026, 2, 28))
    assert daily["entered_count"] == 1


def test_a_read_only_sign_in_lets_the_run_go_and_is_never_credited(
    client, db_session, seed_accounts, monkeypatch
):
    """Any sign-in lets the waiting run go, but what it enters is credited
    to a person who may post it: after a read-only sign-in the audit log
    names the scheduled run, and once a bookkeeper has signed in, the
    bookkeeper."""
    from tests.test_rbac_users import _login_as, _mk_user

    monkeypatch.setattr(scheduler, "_last_run", None)
    _bill_schedule(client, _vendor(client), seed_accounts)  # auto, due Jan 31
    _mk_user(db_session, "viewer", "a-test-password-123", "readonly")
    _mk_user(db_session, "keeper", "a-test-password-123", "bookkeeper")
    db_session.commit()
    assert client.post("/api/auth/logout").status_code == 200
    monkeypatch.setattr(scheduler, "_signed_in_as", None)  # the server starts
    _login_as(client, "viewer", "a-test-password-123")
    out = scheduler.run_in_this_process("startup", today=lambda: date(2026, 2, 10))
    assert out["entered_count"] == 1
    (january,) = _bills(db_session)
    assert _who_entered(db_session, january) == [scheduler.SCHEDULED_RUN]

    _login_as(client, "keeper", "a-test-password-123")
    daily = scheduler.run_in_this_process("daily", today=lambda: date(2026, 2, 28))
    assert daily["entered_count"] == 1
    (february,) = [b for b in _bills(db_session) if b.id != january.id]
    assert _who_entered(db_session, february) == ["keeper"]


def test_the_task_runs_as_soon_as_someone_signs_in(
    client, db_session, seed_accounts, monkeypatch
):
    """W-70: the task's startup run waits, as the startup run still, and
    goes once a person signs in; the daily runs follow."""
    monkeypatch.setattr(scheduler, "_last_run", None)
    _bill_schedule(client, _vendor(client), seed_accounts)
    client.post("/api/auth/logout")
    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    clock = {"today": date(2026, 2, 10)}
    naps: list = []

    class Done(Exception):
        pass

    async def nap(seconds):
        naps.append(seconds)
        if len(naps) == 1:
            assert _bills(db_session) == []  # waited: nobody signed in
            assert client.post("/api/auth/login", json={"password": FIXTURE_PW})
        elif len(naps) == 2:
            clock["today"] = date(2026, 3, 1)  # the next day's run
        else:
            raise Done

    async def inline(fn, *args):
        return fn(*args)

    with pytest.raises(Done):
        asyncio.run(
            scheduler.keep_entering(
                today=lambda: clock["today"], sleep=nap, to_thread=inline
            )
        )
    assert [b.date for b in _bills(db_session)] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
    ]
    assert scheduler.last_run()["origin"] == "daily"


def test_a_sign_in_wakes_the_waiting_task_at_once(monkeypatch):
    """W-70: the task does not sit out its fifteen-minute look while the
    startup run waits: the first sign-in wakes it, from any thread."""
    import time

    calls: list = []
    # a person signs in on another thread, a moment after the run waited
    signer = threading.Timer(0.2, scheduler.note_sign_in, args=("admin",))

    class Done(Exception):
        pass

    def run(origin, today):
        calls.append(origin)
        if len(calls) == 1:
            signer.start()
            return scheduler.WAITING_FOR_SIGN_IN
        raise Done

    async def inline(fn, *args):
        return fn(*args)

    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    monkeypatch.setattr(scheduler, "run_in_this_process", run)
    started = time.monotonic()
    try:
        with pytest.raises(Done):
            asyncio.run(scheduler.keep_entering(to_thread=inline, poll_seconds=60))
    finally:
        signer.cancel()
    assert calls == ["startup", "startup"]
    assert time.monotonic() - started < 10  # not the sixty-second look
    assert scheduler._signed_in_as == "admin"


def test_a_remembered_sign_in_counts_one_from_before_a_restart_does_not(
    client, seed_accounts, monkeypatch
):
    """W-70: the desktop window that opens signed in (ask_password_on_start
    off, the default) has a person in it: its first request lets the run
    go. With the setting on, a session from before the restart is signed
    out, and only the sign-in that follows counts."""
    from app.routes import auth as auth_routes
    from app.services import auth as auth_service

    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    assert client.get("/api/accounts").status_code == 200
    assert scheduler._signed_in_as == "admin"

    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    assert client.put(
        "/api/settings", json={"ask_password_on_start": "true"}
    ).is_success
    monkeypatch.setattr(auth_service, "BOOT_ID", "a-later-start")
    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    assert client.get("/api/accounts").status_code == 401
    assert scheduler._signed_in_as is None
    assert client.post("/api/auth/login", json={"password": FIXTURE_PW}).is_success
    assert scheduler._signed_in_as == "admin"

    # a sign-in to another company this server serves is not one to this one
    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    user = types.SimpleNamespace(username="bookkeeper")
    auth_routes._note_sign_in({}, "another-company.db", user)
    assert scheduler._signed_in_as is None
    # nor is a one-time password not yet replaced
    auth_routes._note_sign_in({"must_change_password": True}, None, user)
    assert scheduler._signed_in_as is None
    auth_routes._note_sign_in({}, None, user)
    assert scheduler._signed_in_as == "bookkeeper"


def test_without_a_password_the_startup_run_enters_at_open(
    unauthed_client, db_session, seed_accounts, monkeypatch
):
    """W-70: a company with no password yet shows no sign-in screen, so
    the startup run enters at open, as it always has, and the audit log
    names it "scheduled run"."""
    from app.models.contacts import Vendor
    from app.models.memorized import MemorizedTransactionLine

    monkeypatch.setattr(scheduler, "_last_run", None)
    monkeypatch.setattr(scheduler, "_signed_in_as", None)
    assert unauthed_client.get("/api/auth/status").json()["setup_needed"] is True
    vendor = Vendor(name="Harbor Power & Light")
    db_session.add(vendor)
    db_session.flush()
    mem = MemorizedTransaction(
        kind="bill",
        name="Electric bill",
        mode="auto",
        frequency="monthly",
        interval=1,
        start_date=date(2026, 1, 31),
        next_date=date(2026, 1, 31),
        anchor_day=31,
        vendor_id=vendor.id,
        terms="Net 15",
    )
    db_session.add(mem)
    db_session.flush()
    db_session.add(
        MemorizedTransactionLine(
            memorized_id=mem.id,
            account_id=seed_accounts["6500"].id,
            description="Sanctuary",
            quantity=1,
            rate=400,
        )
    )
    db_session.commit()
    out = scheduler.run_in_this_process("startup", today=lambda: date(2026, 2, 10))
    assert out["entered_count"] == 1
    (bill,) = _bills(db_session)
    assert _who_entered(db_session, bill) == [scheduler.SCHEDULED_RUN]
    assert scheduler.SCHEDULED_RUN == "scheduled run"


def test_the_page_and_the_docs_say_the_runs_wait_for_a_sign_in():
    """W-70: the hint under the list, the changelog and the features doc
    say the app's own runs wait for someone to sign in, and who the audit
    log names."""
    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    assert "every missed date in order, once someone has signed in." in js
    for doc in ("docs/features.md", "CHANGELOG.md"):
        text = _words(doc)
        assert "those runs wait until someone has signed in since the app" in text
        assert "nothing is entered behind the sign-in screen" in text, doc
        assert "scheduled run" in text, doc


# ---------------------------------------------------------------------------
# from the 2.31.1 Windows gate's round 3 findings (W-84, W-86 and the wording
# notes); W-85's generator tests are in tests/test_recurring_service.py
# ---------------------------------------------------------------------------


def _another_window_generates_first(monkeypatch, Session, rec_id, occurrence):
    """Another window's Enter now on `occurrence` lands between a run's read
    of the schedule and its claim, once: it sits in front of the closing
    date check, the run's last read before it claims (where
    _installment_claimed_after_both_read puts its barrier), on a session of
    its own, so it commits as another request does."""
    import app.services.closing_date as closing_date
    from app.routes import recurring as recurring_routes

    real = closing_date.check_closing_date
    state = {"done": False}

    def checked(db, txn_date, *args, **kw):
        if not state["done"]:
            state["done"] = True
            with Session() as other:
                won = recurring_routes.generate_one(
                    rec_id, dated=None, occurrence=occurrence, db=other
                )
            assert won["invoices_created"] == 1, won
        return real(db, txn_date, *args, **kw)

    monkeypatch.setattr(closing_date, "check_closing_date", checked)


def _made_for(Session, rec_id):
    from app.models.recurring import RecurringInvoice

    with Session() as db:
        made = (
            db.query(Invoice)
            .filter(Invoice.recurring_invoice_id == rec_id)
            .order_by(Invoice.date, Invoice.id)
            .all()
        )
        rec = db.get(RecurringInvoice, rec_id)
        return [(i.id, i.date) for i in made], rec.next_due, rec.invoices_created


def test_enter_all_due_keeps_going_after_another_window_takes_an_installment(
    file_company, monkeypatch
):
    """W-84: when another window's Enter now (or a Generate) won the claim
    on the only due schedule's first installment, Enter All Due's first pass
    made nothing and the catch-up stopped there, answering 200 with 0 made
    while two more installments were due. A lost claim means the schedule
    has moved on, so the run keeps going and makes the rest."""
    client, Session, ids = file_company
    r = client.post("/api/customers", json={"name": "Riverbend Dental"})
    assert r.status_code == 201, r.text
    rec_id = _recurring_invoice(client, r.json()["id"], start="2026-01-01")
    _another_window_generates_first(monkeypatch, Session, rec_id, date(2026, 1, 1))

    r = client.post("/api/memorized/enter-due?as_of=2026-03-15")
    assert r.status_code == 200, r.text
    assert r.json()["invoices_created"] == 2 and r.json()["skipped"] == []
    made, next_due, count = _made_for(Session, rec_id)
    assert [when for _, when in made] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert r.json()["invoice_ids"] == [made[1][0], made[2][0]]
    assert next_due == date(2026, 4, 1) and count == 3


def test_a_generation_that_loses_a_claim_names_the_schedule(file_company, monkeypatch):
    """W-84: generate_due_invoices reports, in `taken`, each schedule whose
    installment another window generated first: not skipped and not an
    error, and named so the catch-up knows the schedule moved on."""
    client, Session, ids = file_company
    r = client.post("/api/customers", json={"name": "Riverbend Dental"})
    rec_id = _recurring_invoice(client, r.json()["id"], start="2026-01-01")
    _another_window_generates_first(monkeypatch, Session, rec_id, date(2026, 1, 1))
    skipped: list = []
    taken: list = []
    with Session() as db:
        made = recurring_service.generate_due_invoices(
            db, date(2026, 3, 15), skipped=skipped, taken=taken
        )
    assert made == [] and skipped == [] and taken == [rec_id]
    rows, next_due, count = _made_for(Session, rec_id)
    assert [when for _, when in rows] == [date(2026, 1, 1)]
    assert next_due == date(2026, 2, 1) and count == 1


def test_a_lost_claim_is_not_the_end_of_the_backlog(monkeypatch, caplog):
    """W-84: the catch-up makes passes while a pass makes an invoice or
    loses a claim, so Enter All Due and the cron script agree on a backlog
    however the windows race; a claim lost on every pass still stops at the
    safety cap, logged."""
    passes = iter([([], [7]), ([101], []), ([102], [7]), ([], [])])
    asked = []

    def generate(db, as_of=None, skipped=None, taken=None):
        made, lost = next(passes)
        asked.append(as_of)
        taken.extend(lost)
        return made

    monkeypatch.setattr(recurring_service, "generate_due_invoices", generate)
    assert scheduler.catch_up_recurring(None, date(2026, 3, 1)) == ([101, 102], [])
    assert asked == [date(2026, 3, 1)] * 4

    monkeypatch.setattr(scheduler, "PASS_SAFETY_CAP", 5)
    calls = []

    def always_lost(db, as_of=None, skipped=None, taken=None):
        calls.append(as_of)
        taken.append(7)
        return []

    monkeypatch.setattr(recurring_service, "generate_due_invoices", always_lost)
    with caplog.at_level("WARNING", logger="app.services.scheduler"):
        assert scheduler.catch_up_recurring(None, date(2026, 3, 1)) == ([], [])
    assert len(calls) == 5
    assert "stopped after 5 passes" in caplog.text


def test_a_recurring_invoice_is_listed_as_generated_not_auto_enter(
    client, db_session, seed_accounts, seed_customer
):
    """W-86: the app's own runs no longer make recurring invoices (W-70),
    yet the page listed them as Mode Auto-enter. Their rows' mode is
    "generate", which the page shows as Generated, with a title naming what
    does make one; a memorized bill or journal entry cannot take it."""
    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    vendor_id = _vendor(client)
    _bill_schedule(client, vendor_id, seed_accounts)
    rows = {r["kind"]: r for r in client.get("/api/memorized").json()}
    assert rows["invoice"]["id"] == rec_id and rows["invoice"]["mode"] == "generate"
    assert rows["bill"]["mode"] == "auto"
    assert memorized_service.MODE_GENERATE == "generate"
    r = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Not a recurring invoice",
            "mode": "generate",
            "frequency": "monthly",
            "start_date": "2026-01-31",
            "vendor_id": vendor_id,
            "lines": [{"account_id": seed_accounts["6500"].id, "amount": 40}],
        },
    )
    assert r.status_code == 422, r.text

    js = (ROOT / "app/static/js/memorized.js").read_text(encoding="utf-8")
    mode = js[js.index("    modeHtml(r) {") : js.index("    statusHtml(r) {")]
    assert "if (r.mode === 'generate') {" in mode and ">Generated</span>" in mode
    title = mode[mode.index('title="') + len('title="') :]
    title = title[: title.index('"')]
    for words in ("Generate", "Enter All Due", "the server's cron", "on its own"):
        assert words in title, words
    assert "return r.mode === 'auto' ? 'Auto-enter' : 'Remind';" in mode
    assert "<td>${MemorizedPage.modeHtml(r)}</td>" in js
    assert "<td>${r.mode === 'auto' ? 'Auto-enter' : 'Remind'}</td>" not in js


def _today_is(frozen: date):
    """A date class whose today() is `frozen`, for a route module's `date`
    (the pattern tests/test_fiscal_year.py uses)."""

    class _Frozen(date):
        @classmethod
        def today(cls):
            return cls(frozen.year, frozen.month, frozen.day)

    return _Frozen


def test_ticking_active_again_names_the_end_date_the_schedule_has(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    """Round 3 wording note: W-81's 409 for ticking Active again on a
    recurring invoice past its end date quoted the end date the request
    sent, though the request is refused and rolled back. It names the end
    date the schedule has; an end date sent that is still too early is
    named as such, with the date it would resume on."""
    import app.routes.recurring as recurring_routes

    monkeypatch.setattr(recurring_routes, "date", _today_is(date(2026, 4, 15)))
    line = [{"description": "Monthly", "quantity": 1, "rate": 100}]
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "lines": line,
        },
    )
    assert r.status_code == 201, r.text
    url = f"/api/recurring/{r.json()['id']}"
    assert (
        client.post("/api/recurring/generate?as_of=2026-03-01").json()[
            "invoices_created"
        ]
        == 1
    )
    before = client.get(url).json()
    assert before["is_active"] is False and before["end_date"] == "2026-01-31"
    for sent in ("2026-01-15", "2026-04-30"):
        r = client.put(url, json={"is_active": True, "end_date": sent})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == (
            "This schedule has ended: its end date was 2026-01-31, and the end "
            f"date sent, {sent}, is before 2026-05-01, the next due date it would "
            "resume on, so nothing was changed; move or clear the end date first."
        )
        assert client.get(url).json() == before
    for body in ({"is_active": True}, {"is_active": True, "end_date": "2026-01-31"}):
        r = client.put(url, json=body)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == (
            "This schedule has ended: its end date was 2026-01-31; move or clear "
            "the end date first."
        )
    # paused with no end date, ticked Active with an end date already past
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "lines": line,
        },
    )
    url = f"/api/recurring/{r.json()['id']}"
    assert client.put(url, json={"is_active": False}).status_code == 200
    r = client.put(url, json={"is_active": True, "end_date": "2026-03-31"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This schedule would end as soon as it resumed: the end date sent, "
        "2026-03-31, is before 2026-05-01, the next due date it would resume on, "
        "so nothing was changed; move or clear the end date first."
    )
    got = client.get(url).json()
    assert got["end_date"] is None and got["is_active"] is False
    assert got["next_due"] == "2026-01-01"


def test_the_ended_sentence_says_what_to_change(client, db_session, seed_accounts):
    """Round 3 wording note: "Edit it and move the end" read loosely for a
    schedule that ended by its number of times. That one says to raise the
    number of times; one past its end date keeps its sentence, as does one
    past both."""
    vendor_id = _vendor(client)
    counted = _bill_schedule(client, vendor_id, seed_accounts, occurrences=2)
    dated = _bill_schedule(
        client, vendor_id, seed_accounts, name="Water", end_date="2026-02-28"
    )
    both = _bill_schedule(
        client,
        vendor_id,
        seed_accounts,
        name="Gas",
        end_date="2026-02-28",
        occurrences=2,
    )
    enter_due_memorized(db_session, as_of=date(2026, 12, 31))
    said = {}
    for mem in (counted, dated, both):
        r = client.post(f"/api/memorized/{mem['id']}/enter")
        assert r.status_code == 409, r.text
        said[mem["name"]] = r.json()["detail"]
    assert said["Electric bill"] == (
        "'Electric bill' has ended: both occurrences are entered. Edit it and "
        "raise the number of times to enter it again."
    )
    assert said["Water"] == (
        "'Water' has ended: its end date was 2026-02-28. Edit it and move the "
        "end to enter it again."
    )
    assert said["Gas"] == (
        "'Gas' has ended: its end date was 2026-02-28. Edit it and move the end "
        "to enter it again."
    )


def test_a_later_occurrence_is_not_the_next_one(
    client, db_session, seed_accounts, seed_customer
):
    """Round 3 wording note: Enter now for an occurrence after the
    schedule's next date answered "That occurrence was just entered", which
    is wrong for a date not yet reached. It says that occurrence is not the
    next one, names both dates, and posts nothing; an occurrence already
    passed keeps its sentence. A recurring invoice's Enter now the same."""
    mem = _bill_schedule(client, _vendor(client), seed_accounts, mode="remind")
    url = f"/api/memorized/{mem['id']}/enter"
    r = client.post(url, json={"occurrence": "2026-03-31"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "The 2026-03-31 occurrence is not the next one of 'Electric bill', so "
        "nothing was posted: its next date is 2026-01-31, with 0 entered."
    )
    assert _bills(db_session) == []
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert got["next_date"] == "2026-01-31" and got["entered_count"] == 0
    assert client.post(url, json={"occurrence": "2026-01-31"}).status_code == 200
    r = client.post(url, json={"occurrence": "2026-01-31"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("That occurrence was just entered")
    assert "'Electric bill' was due 2026-01-31 and its next date is now 2026-02-28" in (
        r.json()["detail"]
    )
    # one that has ended, asked for a later date
    once = _bill_schedule(
        client,
        _vendor(client, name="City Water"),
        seed_accounts,
        name="Water",
        occurrences=1,
    )
    assert client.post(f"/api/memorized/{once['id']}/enter").status_code == 200
    r = client.post(
        f"/api/memorized/{once['id']}/enter", json={"occurrence": "2026-05-31"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "The 2026-05-31 occurrence is not the next one of 'Water', so nothing was "
        "posted: it has ended, with 1 entered."
    )

    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    for query in ("occurrence=2026-03-01", "occurrence=2026-03-01&dated=2026-01-20"):
        r = client.post(f"/api/recurring/{rec_id}/generate?{query}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == (
            "The 2026-03-01 installment is not the next one of the schedule for "
            "Test Customer, so nothing was created: its next due date is "
            "2026-01-01."
        )
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 0


def test_the_docs_say_what_the_schedules_and_the_page_do_now():
    """W-84 to W-86 and the wording notes, in the changelog and the
    features doc: a recurring invoice keeps its start day and is listed as
    Generated, Enter All Due keeps going past an installment another window
    made, and the sentences for a later occurrence, a busy file, the end
    date and a count reached."""
    for doc in ("docs/features.md", "CHANGELOG.md"):
        text = _words(doc)
        for words in (
            "keeps the day of its start date",
            "the mode `generate`",
            "Generated",
            "when another window generates one of those installments first, it "
            "keeps going",
            "is not the next one",
            "busy saving another change",
            "the end date the schedule has",
            "says to raise the number of times",
        ):
            assert words in text, (doc, words)
    features = _words("docs/features.md")
    assert "the app generates what is due when it opens" not in features


def test_an_occurrence_before_the_start_was_never_one_of_its_dates(
    client, db_session, seed_accounts, seed_customer
):
    """Round 4 note: an occurrence before the schedule's start answered
    "That occurrence was just entered", though no such date was ever on the
    schedule. It says the date is before the schedule starts, names the
    start and the next date, and posts nothing; a recurring invoice's
    Generate the same."""
    mem = _bill_schedule(client, _vendor(client), seed_accounts, mode="remind")
    r = client.post(
        f"/api/memorized/{mem['id']}/enter", json={"occurrence": "2025-12-31"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "The 2025-12-31 occurrence is before 'Electric bill' starts (2026-01-31), "
        "so nothing was posted: its next date is 2026-01-31, with 0 entered."
    )
    assert _bills(db_session) == []

    rec_id = _recurring_invoice(client, seed_customer.id, start="2026-01-01")
    r = client.post(f"/api/recurring/{rec_id}/generate?occurrence=2025-12-01")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "The 2025-12-01 installment is before the schedule for Test Customer "
        "starts (2026-01-01), so nothing was created: its next due date is "
        "2026-01-01."
    )
    assert db_session.query(Invoice).filter_by(recurring_invoice_id=rec_id).count() == 0


# ---------------------------------------------------------------------------
# a bill line's Use tax box (construction) on a memorized bill
# ---------------------------------------------------------------------------


def test_a_memorized_bill_line_keeps_its_use_tax_box_into_each_bill(
    client, db_session, seed_accounts, seed_customer
):
    """Memorize Bill sends each line's Use tax box. The template keeps it
    (an edit that sends no lines too), and each bill entered from it accrues
    use tax on the ticked line as the bill it was memorized from did; one
    the job's rule would not tax."""
    from tests.test_construction_tax import _balance, _use_tax_setup

    job, vendor, payable = _use_tax_setup(
        client, db_session, seed_customer, treatment="charge_owner"
    )
    line = {"rate": 400, "cost_type": "equipment", "job_id": job["id"]}
    r = client.post(
        "/api/memorized",
        json={
            "kind": "bill",
            "name": "Lift rental",
            "mode": "remind",
            "frequency": "monthly",
            "start_date": "2026-07-04",
            "vendor_id": vendor["id"],
            "job_id": job["id"],
            "lines": [
                {**line, "description": "Lift", "use_tax": True},
                {**line, "description": "Crane"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    mem = r.json()
    assert [ln["use_tax"] for ln in mem["lines"]] == [True, None]
    r = client.put(f"/api/memorized/{mem['id']}", json={"name": "Lifts"})
    assert r.status_code == 200, r.text
    assert [ln["use_tax"] for ln in r.json()["lines"]] == [True, None]

    r = client.post(f"/api/memorized/{mem['id']}/enter")
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/bills/{r.json()['document_id']}").json()
    assert [ln["use_tax"] for ln in bill["lines"]] == [True, None]
    # 6.5% of the ticked 400 alone
    assert _balance(db_session, payable.id) == Decimal("26.00")


def test_a_memorized_bill_line_takes_no_line_id(client, seed_accounts):
    """A template is saved whole, so a line id (a bill's, or the template's
    own) is refused rather than ignored; the form leaves it out."""
    vendor_id = _vendor(client)
    mem = _bill_schedule(client, vendor_id, seed_accounts, mode="remind")
    line = {**mem["lines"][0], "amount": 400}
    sent = {k: line[k] for k in ("id", "account_id", "description", "amount")}
    r = client.put(f"/api/memorized/{mem['id']}", json={"lines": [sent]})
    assert r.status_code == 422, r.text
    del sent["id"]
    r = client.put(f"/api/memorized/{mem['id']}", json={"lines": [sent]})
    assert r.status_code == 200, r.text


def test_the_use_tax_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "memorized_use_tax.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()

    def cols():
        engine = create_engine(f"sqlite:///{db}")
        try:
            return {
                c["name"]
                for c in inspect(engine).get_columns("memorized_transaction_lines")
            }
        finally:
            engine.dispose()

    command.upgrade(cfg, "e2b6c4d8f0a3")
    assert "use_tax" in cols()
    command.downgrade(cfg, "b4d2e7a9c6f1")
    assert "use_tax" not in cols()
