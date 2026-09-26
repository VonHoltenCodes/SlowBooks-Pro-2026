"""The bank register shows a bill payment's check number and keeps its own
address.

Exploratory 2.17.3 (skytech, W-L6): check 1050 on the Sign Supply bill
payment was missing from the register's REF # — the number lives on the
bill payment, not on its journal entry. And the register kept the
#/banking hash, so a refresh lost the page."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.services.ofx_import import import_transactions

JS_DIR = Path(__file__).resolve().parents[1] / "app/static/js"
BANKING_JS = (JS_DIR / "banking.js").read_text(encoding="utf-8")
DASHBOARD_JS = (JS_DIR / "dashboard.js").read_text(encoding="utf-8")


@pytest.fixture
def paid_bill(client, seed_accounts):
    vendor = client.post("/api/vendors", json={"name": "Sign Supply"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "SS-778",
            "date": "2026-09-02",
            "lines": [
                {
                    "description": "Vinyl",
                    "quantity": 1,
                    "rate": 450,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-09-05",
            "amount": 450,
            "method": "check",
            "check_number": "1050",
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill.json()["id"], "amount": 450}],
        },
    )
    assert pay.status_code == 201, pay.text
    return pay.json()


def _register(client, seed_accounts):
    return client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()


def test_the_register_shows_the_bill_payments_check_number(
    client, seed_accounts, paid_bill
):
    [row] = _register(client, seed_accounts)["entries"]
    assert row["source_type"] == "bill_payment"
    assert row["reference"] == "1050"
    assert row["payee"] == "Sign Supply"


def test_the_void_shows_it_too(client, seed_accounts, paid_bill):
    r = client.post(f"/api/bill-payments/{paid_bill['id']}/void")
    assert r.status_code == 200, r.text
    rows = _register(client, seed_accounts)["entries"]
    assert [(x["source_type"], x["reference"]) for x in rows] == [
        ("bill_payment", "1050"),
        ("bill_payment_void", "1050"),
    ]


def test_the_reconciliation_shows_it(client, seed_accounts, paid_bill):
    recon = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": "2026-09-30",
            "statement_balance": "-450",
        },
    ).json()
    [row] = client.get(
        f"/api/banking/reconciliations/{recon['id']}/transactions"
    ).json()["transactions"]
    assert row["check_number"] == "1050"


def test_the_match_dialog_shows_it(client, db_session, seed_accounts, paid_bill):
    feed = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    ).json()
    # 20 days off: too far to auto-match, near enough to offer
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "c1050",
                "date": date(2026, 9, 25),
                "amount": Decimal("-450"),
                "payee": "CHECK 1050",
                "memo": "",
            }
        ],
    )
    bt = db_session.query(BankTransaction).one()
    [cand] = client.get(f"/api/banking/transactions/{bt.id}/candidates").json()
    assert cand["reference"] == "1050"


def test_a_register_is_a_route_of_its_own():
    # the overview's cards (and the dashboard's) move the address bar
    assert "onclick=\"BankingPage.go('#/banking/${a.account_id}')\"" in BANKING_JS
    assert "onclick=\"BankingPage.go('#/banking/${b.id}')\"" in DASHBOARD_JS
    go = re.search(r"\n    go\(hash\) \{.*?\n    \},\n", BANKING_JS, re.S)
    assert go and "location.hash = hash" in go.group(0)
    # nothing in the banking pages renders a register without its address
    assert not re.search(r"App\.navigate\(['`]#/banking", BANKING_JS)
