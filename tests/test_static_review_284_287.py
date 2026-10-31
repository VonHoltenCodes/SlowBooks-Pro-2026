"""Four findings from a static review of the source (#284–#287), each
reproduced here before it was fixed.

#284 — a bill payment that names no bank posted without a journal when
account 1000 was missing: the default lookup answered None and the
payment, its allocations and the bills' balances were committed anyway.
#285 — A/P aging as of a date included bills dated after it (the A/R side
filtered by date).
#286 — a manual journal balanced on sub-cent amounts could be stored a
cent out, since the ledger holds two decimals.
#287 — the 1099 summary and the form totals counted voided bill payments.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.bills import BillPayment
from app.models.transactions import Transaction, TransactionLine
from app.services import form_1099
from app.services.accounting import create_journal_entry
from app.services.safe_errors import DataProblem

TODAY = date.today().isoformat()


def _vendor(client, name="Pier Lumber", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, amount, when, accounts):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": when,
            "lines": [
                {
                    "description": "Work",
                    "account_id": accounts["6000"].id,
                    "quantity": 1,
                    "rate": str(amount),
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _payment(client, vendor_id, bill, amount, when, bank_id=None):
    body = {
        "vendor_id": vendor_id,
        "date": when,
        "amount": str(amount),
        "allocations": [{"bill_id": bill["id"], "amount": str(amount)}],
    }
    if bank_id is not None:
        body["pay_from_account_id"] = bank_id
    return client.post("/api/bill-payments", json=body)


# ── #284 ──────────────────────────────────────────────────────────────────


def test_a_payment_with_no_bank_and_no_1000_is_refused_whole(
    client, db_session, seed_accounts
):
    v = _vendor(client)
    bill = _bill(client, v["id"], "300.00", TODAY, seed_accounts)
    # a chart without 1000: the seeded account renumbered under the API's
    # control-account rules (the condition is a legacy or incomplete chart)
    checking = db_session.query(Account).filter(Account.account_number == "1000").one()
    taken = {n for (n,) in db_session.query(Account.account_number).all()}
    checking.account_number = next(
        n for n in ("1990", "1991", "1992") if n not in taken
    )
    db_session.commit()

    r = _payment(client, v["id"], bill, "300.00", TODAY)
    assert r.status_code == 409, r.text
    assert "1000" in r.text
    # nothing was written: no payment, no allocation, the bill still owed
    assert db_session.query(BillPayment).count() == 0
    assert client.get(f"/api/bills/{bill['id']}").json()["balance_due"] == "300.00"
    assert (
        db_session.query(Transaction).filter_by(source_type="bill_payment").count() == 0
    )

    # a bank named explicitly still pays, with its journal
    r = _payment(client, v["id"], bill, "300.00", TODAY, bank_id=checking.id)
    assert r.status_code == 201, r.text
    db_session.expire_all()
    assert db_session.query(BillPayment).one().transaction_id is not None
    assert client.get(f"/api/bills/{bill['id']}").json()["status"] == "paid"


def test_a_payment_with_no_bank_defaults_to_1000_with_its_journal(
    client, db_session, seed_accounts
):
    v = _vendor(client)
    bill = _bill(client, v["id"], "120.00", TODAY, seed_accounts)
    r = _payment(client, v["id"], bill, "120.00", TODAY)
    assert r.status_code == 201, r.text
    db_session.expire_all()
    txn_id = db_session.query(BillPayment).one().transaction_id
    assert txn_id is not None
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    assert {(ln.account_id, ln.debit, ln.credit) for ln in lines} == {
        (seed_accounts["2000"].id, Decimal("120.00"), Decimal("0.00")),
        (seed_accounts["1000"].id, Decimal("0.00"), Decimal("120.00")),
    }


# ── #285 ──────────────────────────────────────────────────────────────────


def test_ap_aging_as_of_a_date_leaves_out_bills_dated_after_it(client, seed_accounts):
    v = _vendor(client)
    _bill(client, v["id"], "100.00", "2026-01-10", seed_accounts)
    _bill(client, v["id"], "250.00", "2026-02-15", seed_accounts)
    jan = client.get(
        "/api/reports/ap-aging", params={"as_of_date": "2026-01-31"}
    ).json()
    assert float(jan["totals"]["total"]) == 100.0
    feb = client.get(
        "/api/reports/ap-aging", params={"as_of_date": "2026-02-28"}
    ).json()
    assert float(feb["totals"]["total"]) == 350.0


# ── #286 ──────────────────────────────────────────────────────────────────


def test_the_journal_api_takes_cents_only(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": TODAY,
            "description": "Sub-cent",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": "1.004",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["6100"].id,
                    "debit": "1.004",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["1000"].id,
                    "debit": "0",
                    "credit": "2.008",
                },
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "at most two decimal places" in r.text
    assert client.get("/api/journal").json() == []


def test_the_posting_service_balances_at_the_stored_precision(
    db_session, seed_accounts
):
    """1.004 + 1.004 balances 2.008 on paper and not in cents (1.00 + 1.00
    against 2.01): refused, nothing stored. Cent amounts post as they are."""
    with pytest.raises(DataProblem, match="out of balance"):
        create_journal_entry(
            db_session,
            date.today(),
            "Sub-cent",
            [
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": Decimal("1.004"),
                    "credit": 0,
                },
                {
                    "account_id": seed_accounts["6100"].id,
                    "debit": Decimal("1.004"),
                    "credit": 0,
                },
                {
                    "account_id": seed_accounts["1000"].id,
                    "debit": 0,
                    "credit": Decimal("2.008"),
                },
            ],
        )
    db_session.rollback()
    assert db_session.query(Transaction).count() == 0
    txn = create_journal_entry(
        db_session,
        date.today(),
        "Cents",
        [
            {
                "account_id": seed_accounts["6000"].id,
                "debit": Decimal("1.005"),
                "credit": 0,
            },
            {
                "account_id": seed_accounts["1000"].id,
                "debit": 0,
                "credit": Decimal("1.01"),
            },
        ],
    )
    db_session.commit()
    stored = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert sorted((ln.debit, ln.credit) for ln in stored) == [
        (Decimal("0.00"), Decimal("1.01")),
        (Decimal("1.01"), Decimal("0.00")),
    ]


# ── #287 ──────────────────────────────────────────────────────────────────


def test_a_voided_payment_is_not_money_paid_on_the_1099(
    client, db_session, seed_accounts
):
    v = _vendor(client, "Sign Painter", is_1099_vendor=True, vendor_1099_type="NEC")
    bill = _bill(client, v["id"], "2700.00", "2026-03-10", seed_accounts)
    paid = _payment(
        client, v["id"], bill, "2700.00", "2026-03-20", bank_id=seed_accounts["1000"].id
    )
    assert paid.status_code == 201, paid.text

    def summary():
        return client.get("/api/reports/1099-summary", params={"year": 2026}).json()

    s = summary()
    assert s["total"] == 2700.0 and s["vendors_above_threshold"] == 1
    assert form_1099._vendor_payment_totals(db_session, 2026)[v["id"]] == Decimal(
        "2700.00"
    )

    r = client.post(f"/api/bill-payments/{paid.json()['id']}/void")
    assert r.status_code == 200, r.text
    s = summary()
    assert s["total"] == 0.0 and s["vendors_above_threshold"] == 0
    assert [i["total_paid"] for i in s["items"]] == [0.0]
    db_session.expire_all()
    assert form_1099._vendor_payment_totals(db_session, 2026).get(v["id"]) in (
        None,
        Decimal("0.00"),
    )

    # a replacement payment counts, alone
    again = _payment(
        client, v["id"], bill, "2700.00", "2026-04-02", bank_id=seed_accounts["1000"].id
    )
    assert again.status_code == 201, again.text
    s = summary()
    assert s["total"] == 2700.0 and s["vendors_above_threshold"] == 1
    db_session.expire_all()
    assert form_1099._vendor_payment_totals(db_session, 2026)[v["id"]] == Decimal(
        "2700.00"
    )
