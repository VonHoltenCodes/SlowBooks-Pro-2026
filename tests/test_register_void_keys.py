"""The register tells a voided payment from an unrelated posting (#448).

A `<type>_void` reversal keys its `source_id` by one of two conventions:
the voided TRANSACTION's id (an expense, a deposit) or the DOCUMENT's id (a
payment, a bill payment), whose posting is a different transaction. The
register marked a posting void by matching `source_id` against the
transaction id, which missed a voided payment received straight to the bank
and could flag an unrelated posting whose transaction id equalled the
document id. app/services/voids.py now knows the difference.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.bills import BillPayment
from app.models.contacts import Customer
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine

TODAY = date.today().isoformat()


# ── helpers ─────────────────────────────────────────────────────────────────


def _rows(client, account_id):
    """The register's rows for an account, keyed by transaction id."""
    r = client.get(f"/api/banking/check-register?account_id={account_id}")
    assert r.status_code == 200, r.text
    return {row["transaction_id"]: row for row in r.json()["entries"]}


def _customer(db_session, name="Acme Diner"):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _receive_to_bank(client, cid, amount, bank_id, when="2026-09-10"):
    """A customer payment received straight into a bank account (not UF)."""
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": when,
            "amount": amount,
            "method": "Check",
            "allocations": [],
            "deposit_to_account_id": bank_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _vendor(client, name="Pier Lumber"):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, amount, accounts, when=TODAY):
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


def _bill_payment(client, vendor_id, bill, amount, bank_id, when=TODAY):
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": when,
            "amount": str(amount),
            "pay_from_account_id": bank_id,
            "allocations": [{"bill_id": bill["id"], "amount": str(amount)}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pending(client):
    r = client.get("/api/deposits/pending")
    assert r.status_code == 200, r.text
    return r.json()


# ── the fix ───────────────────────────────────────────────────────────────


def test_a_voided_payment_received_to_the_bank_is_marked_void(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"].id
    cid = _customer(db_session)
    pid = _receive_to_bank(client, cid, "100.00", bank)
    from app.database import SessionLocal  # the test's transaction

    with SessionLocal() as s:
        ptxn = s.get(Payment, pid).transaction_id

    assert _rows(client, bank)[ptxn]["voided"] is False
    assert client.post(f"/api/payments/{pid}/void").status_code == 200
    assert _rows(client, bank)[ptxn]["voided"] is True


def test_a_voided_bill_payment_is_marked_void(client, seed_accounts):
    bank = seed_accounts["1000"].id
    v = _vendor(client)
    bill = _bill(client, v["id"], "200.00", seed_accounts)
    bp = _bill_payment(client, v["id"], bill, "200.00", bank)
    from app.database import SessionLocal  # the test's transaction

    with SessionLocal() as s:
        bptxn = s.get(BillPayment, bp["id"]).transaction_id

    assert _rows(client, bank)[bptxn]["voided"] is False
    assert client.post(f"/api/bill-payments/{bp['id']}/void").status_code == 200
    assert _rows(client, bank)[bptxn]["voided"] is True


def test_an_unrelated_posting_whose_id_equals_the_payment_id_is_not_void(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"].id
    ar = seed_accounts["1100"].id
    cid = _customer(db_session)
    # A document id no autoincremented row in this fresh DB will reach, so
    # the collision is between a real payment's id and a real posting's id.
    doc_id = 90001

    # An unrelated bank posting whose TRANSACTION id equals the document id.
    unrelated = Transaction(
        id=doc_id,
        date=date(2026, 9, 1),
        source_type="bank_entry",
        description="unrelated deposit",
    )
    db_session.add(unrelated)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=doc_id,
                account_id=bank,
                debit=Decimal("5.00"),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=doc_id,
                account_id=ar,
                debit=Decimal("0"),
                credit=Decimal("5.00"),
            ),
        ]
    )

    # A real payment received straight to the bank, whose document id equals
    # that unrelated posting's transaction id.
    posting = Transaction(
        date=date(2026, 9, 2), source_type="payment", description="Payment from Acme"
    )
    db_session.add(posting)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=posting.id,
                account_id=bank,
                debit=Decimal("100.00"),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=posting.id,
                account_id=ar,
                debit=Decimal("0"),
                credit=Decimal("100.00"),
            ),
        ]
    )
    db_session.add(
        Payment(
            id=doc_id,
            customer_id=cid,
            date=date(2026, 9, 2),
            amount=Decimal("100.00"),
            method="Check",
            deposit_to_account_id=bank,
            transaction_id=posting.id,
            is_voided=False,
        )
    )
    db_session.commit()
    posting_id = posting.id

    # The void reversal stores source_id = Payment.id = doc_id.
    assert client.post(f"/api/payments/{doc_id}/void").status_code == 200
    rows = _rows(client, bank)
    assert rows[doc_id]["voided"] is False  # the unrelated posting
    assert rows[posting_id]["voided"] is True  # the payment's own posting


def test_an_expense_void_and_a_deposit_void_still_mark_their_postings(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"].id

    # An expense IS its own transaction (transaction-keyed void).
    v = _vendor(client, "Sweet Forest Cafe")
    e = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "vendor_id": v["id"],
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": bank,
            "amount": "30.30",
            "reference": "rcpt 1187",
            "memo": "team lunch",
        },
    ).json()
    assert client.post(f"/api/expenses/{e['id']}/void").status_code == 200

    # A deposit posts DR bank / CR UF; its void keys by the deposit's own id.
    cid = _customer(db_session, "Diner Two")
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-05",
            "amount": "40.00",
            "method": "Check",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    waiting = _pending(client)
    assert waiting, "the payment should be waiting to deposit"
    dep = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": bank,
            "date": "2026-09-06",
            "total": 40.0,
            "reference": "DEP-1",
            "line_ids": [waiting[0]["transaction_line_id"]],
        },
    )
    assert dep.status_code == 200, dep.text
    dep_txn = dep.json()["transaction_id"]
    assert client.post(f"/api/deposits/{dep_txn}/void").status_code == 200

    rows = _rows(client, bank)
    assert rows[e["id"]]["voided"] is True
    assert rows[dep_txn]["voided"] is True


# ── exhaustiveness: no void type may fall through silently ──────────────────


def test_every_void_source_type_in_app_is_classified():
    """Grep app/ for every "<type>_void" source string (as
    tests/test_keyboard_paths.py greps the JS) and assert each is in one of
    the two sets, so the next void type cannot be missed by the register."""
    from app.services.voids import DOCUMENT_KEYED_VOIDS, TRANSACTION_KEYED_VOIDS

    app_dir = Path(__file__).resolve().parents[1] / "app"
    known = set(TRANSACTION_KEYED_VOIDS) | set(DOCUMENT_KEYED_VOIDS)
    literal = re.compile(r"""['"]([a-z][a-z0-9_]*_void)['"]""")
    found = set()
    for py in app_dir.rglob("*.py"):
        for match in literal.finditer(py.read_text(encoding="utf-8")):
            found.add(match.group(1))

    assert found, "expected to find some _void source types in app/"
    missing = found - known
    assert not missing, f"unclassified *_void source types in app/: {sorted(missing)}"
