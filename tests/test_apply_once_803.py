"""Receive Payment and the invoice's Apply Credit never apply a row twice
(#803, the same guarantee #747 gave the credit memo and vendor credit
Apply dialogs in 2.31.11).

Where one request carries several invoices (Receive Payment's Save,
`POST /api/payments`, and an unapplied payment's Apply,
`POST /api/payments/{id}/apply`), the server applies them in one
transaction: a refusal on the second of three rows leaves the first as it
was, and the sentence names the refused row and says nothing was applied.
Where the page sends one request per row (a credit memo applied from
Receive Payment's Apply Credit, and the invoice view's Apply Credit from
several credits), the page checks every row first, stops at the first
refusal, says what was applied before it and opens the dialog again on
what remains, so a second Apply sends only what is left. Driven through
tests/js/apply_once_803_probe.js with a fake DOM and a fake server."""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def customer(db_session, seed_accounts):
    c = Customer(name="Customer A", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, amount):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-11-01",
            "tax_rate": 0,
            "lines": [{"description": "Work", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert client.post(f"/api/invoices/{inv['id']}/send").status_code == 200
    return inv


def _paid(client, inv):
    got = client.get(f"/api/invoices/{inv['id']}").json()
    return Decimal(got["amount_paid"]), Decimal(got["balance_due"])


def _pay(client, cid, amount, rows, key=None):
    return client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-11-09",
            "amount": amount,
            "allocations": [{"invoice_id": i, "amount": a} for i, a in rows],
        },
        headers={"Idempotency-Key": key} if key else {},
    )


def _three(client, cid):
    # the second owes $20, so $25 on it is refused after the first is taken
    return (
        _invoice(client, cid, "30.00"),
        _invoice(client, cid, "20.00"),
        _invoice(client, cid, "30.00"),
    )


def test_a_payment_refused_on_its_second_row_records_nothing_and_says_so(
    client, db_session, customer
):
    a, b, c = _three(client, customer)
    txns = db_session.query(Transaction).count()
    r = _pay(
        client,
        customer,
        "65.00",
        [(a["id"], "30.00"), (b["id"], "25.00"), (c["id"], "10.00")],
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"$25.00 is more than the $20.00 still due on invoice {b['invoice_number']}. "
        "Nothing was recorded: the payment and its invoices are as they were."
    )
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.query(PaymentAllocation).count() == 0
    assert db_session.query(Transaction).count() == txns
    # the first row, taken before the refusal, was rolled back with it
    assert _paid(client, a) == (Decimal("0"), Decimal("30.00"))
    assert _paid(client, b) == (Decimal("0"), Decimal("20.00"))
    assert _paid(client, c) == (Decimal("0"), Decimal("30.00"))


def test_the_payment_sent_again_corrected_applies_each_row_once(
    client, db_session, customer
):
    """Save keeps its Idempotency-Key after a refusal: the refused request
    reserved nothing, so the corrected one under the same key is booked
    once, and sending it a third time replays it."""
    a, b, c = _three(client, customer)
    key = "rp-803-retry"
    r = _pay(
        client,
        customer,
        "60.00",
        [(a["id"], "30.00"), (b["id"], "25.00"), (c["id"], "5.00")],
        key,
    )
    assert r.status_code == 400, r.text
    rows = [(a["id"], "30.00"), (b["id"], "20.00"), (c["id"], "10.00")]
    r = _pay(client, customer, "60.00", rows, key)
    assert r.status_code == 201, r.text
    again = _pay(client, customer, "60.00", rows, key)
    assert again.status_code == 201 and again.headers.get("X-Replayed") == "true"
    db_session.expire_all()
    assert db_session.query(Payment).count() == 1
    assert sorted(
        (x.invoice_id, Decimal(str(x.amount)))
        for x in db_session.query(PaymentAllocation)
    ) == sorted(
        [
            (a["id"], Decimal("30.00")),
            (b["id"], Decimal("20.00")),
            (c["id"], Decimal("10.00")),
        ]
    )
    assert _paid(client, a) == (Decimal("30.00"), Decimal("0.00"))
    assert _paid(client, b) == (Decimal("20.00"), Decimal("0.00"))
    assert _paid(client, c) == (Decimal("10.00"), Decimal("20.00"))


def test_a_one_row_refusal_keeps_its_own_sentence(client, customer):
    a = _invoice(client, customer, "20.00")
    r = _pay(client, customer, "25.00", [(a["id"], "25.00")])
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"$25.00 is more than the $20.00 still due on invoice {a['invoice_number']}."
    )


def _apply(client, payment_id, rows):
    return client.post(
        f"/api/payments/{payment_id}/apply",
        json={"allocations": [{"invoice_id": i, "amount": a} for i, a in rows]},
    )


def test_a_remainder_applied_to_three_invoices_refused_on_the_second_applies_none(
    client, db_session, customer
):
    a, b, c = _three(client, customer)
    pay = _pay(client, customer, "65.00", [])
    assert pay.status_code == 201, pay.text
    pay = pay.json()
    r = _apply(
        client,
        pay["id"],
        [(a["id"], "30.00"), (b["id"], "25.00"), (c["id"], "10.00")],
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"$25.00 is more than the $20.00 still due on invoice {b['invoice_number']}. "
        "Nothing was applied."
    )
    db_session.expire_all()
    assert db_session.query(PaymentAllocation).count() == 0
    assert _paid(client, a) == (Decimal("0"), Decimal("30.00"))
    got = client.get(f"/api/payments/{pay['id']}").json()
    assert Decimal(got["unapplied"]) == Decimal("65.00")

    # the Apply sent again, corrected: each row once, the rest still a credit
    r = _apply(
        client,
        pay["id"],
        [(a["id"], "30.00"), (b["id"], "20.00"), (c["id"], "10.00")],
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.query(PaymentAllocation).count() == 3
    assert _paid(client, a) == (Decimal("30.00"), Decimal("0.00"))
    assert _paid(client, b) == (Decimal("20.00"), Decimal("0.00"))
    assert _paid(client, c) == (Decimal("10.00"), Decimal("20.00"))
    got = client.get(f"/api/payments/{pay['id']}").json()
    assert Decimal(got["unapplied"]) == Decimal("5.00")


def test_a_void_second_invoice_refuses_the_remainders_apply_whole(
    client, db_session, customer
):
    a, b, c = _three(client, customer)
    assert client.post(f"/api/invoices/{b['id']}/void").status_code == 200
    pay = _pay(client, customer, "50.00", []).json()
    r = _apply(
        client,
        pay["id"],
        [(a["id"], "30.00"), (b["id"], "10.00"), (c["id"], "10.00")],
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"Invoice {b['invoice_number']} is void. Nothing was applied."
    )
    db_session.expire_all()
    assert db_session.query(PaymentAllocation).count() == 0
    assert _paid(client, a) == (Decimal("0"), Decimal("30.00"))


@pytest.fixture(scope="module")
def probe():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "apply_once_803_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_receive_payments_apply_credit_stops_at_a_refusal_and_says_what_was_applied(
    probe,
):
    got = probe["receiveRefused"]
    # the first row applied, the second sent once and refused, the third
    # never sent
    assert got["sent"] == [
        ["/credit-memos/1/apply", 21, 3],
        ["/credit-memos/1/apply", 22, 3],
    ]
    assert got["toast"] == [
        "Invoice #1022: Something on the server refused this row. $3.00 to "
        "invoice #1021 was applied before it; the dialog shows what remains now.",
        "error",
    ]
    # the dialog opened again on the memo and invoices as they are now
    assert got["open"] is True
    assert got["memoLeft"] == 7 and got["balances"] == [27, 30, 30]
    assert [row[:2] for row in got["reopenedWith"]] == [
        ["1021", "27"],
        ["1022", "30"],
        ["1023", "30"],
    ]


def test_receive_payments_apply_credit_sent_again_applies_only_what_is_left(probe):
    got = probe["receiveRetry"]
    assert got["sent"] == [
        ["/credit-memos/1/apply", 22, 3],
        ["/credit-memos/1/apply", 23, 4],
    ]
    assert got["toast"] == ["Credit applied", "ok"]
    # invoice 1021 took $3 once, not twice
    assert got["memoLeft"] == 0 and got["balances"] == [27, 27, 26]


def test_receive_payments_apply_credit_checks_every_row_before_sending(probe):
    assert probe["receiveOverBalance"] == {
        "posts": 0,
        "toast": [
            "Invoice #1022: $30.01 is more than its $30.00 balance. Nothing was applied.",
            "error",
        ],
    }
    assert probe["receiveOverCredit"] == {
        "posts": 0,
        "toast": [
            "$12.00 is more than the $10.00 the credit has. Nothing was applied.",
            "error",
        ],
    }


def test_the_invoices_apply_credit_stops_at_a_refusal_and_says_what_was_applied(
    probe,
):
    assert [row[:2] for row in probe["invoiceDialog"]] == [
        ["1", "10.00"],
        ["2", "10.00"],
        ["3", "10.00"],
    ]
    got = probe["invoiceRefused"]
    assert got["sent"] == [
        ["/credit-memos/1/apply", 21, 10],
        ["/credit-memos/2/apply", 21, 10],
    ]
    assert got["toast"] == [
        "Credit memo CM-0002 of 2026-09-02: Something on the server refused this "
        "row. $10.00 from Credit memo CM-0001 of 2026-09-01 was applied before "
        "it; the dialog shows what remains now.",
        "error",
    ]
    assert got["open"] is True and got["refreshed"] == 0
    assert got["memosLeft"] == [0, 10, 10] and got["balance"] == 20
    # the spent memo is gone from the dialog; the other two fill what is due
    assert got["reopenedWith"] == [["2", "10.00"], ["3", "10.00"]]


def test_the_invoices_apply_credit_sent_again_applies_only_what_is_left(probe):
    got = probe["invoiceRetry"]
    assert got["sent"] == [
        ["/credit-memos/2/apply", 21, 10],
        ["/credit-memos/3/apply", 21, 10],
    ]
    assert got["toast"] == ["Credit applied", "ok"] and got["refreshed"] == 1
    assert got["memosLeft"] == [0, 0, 0] and got["balance"] == 0


def test_the_invoices_apply_credit_refused_on_the_first_row_says_nothing_was_applied(
    probe,
):
    got = probe["invoiceRefusedFirst"]
    assert got["sent"] == [["/credit-memos/1/apply", 21, 10]]
    assert got["toast"] == [
        "Credit memo CM-0001 of 2026-09-01: Something on the server refused this "
        "row. Nothing was applied.",
        "error",
    ]


def test_the_invoices_apply_credit_checks_every_row_before_sending(probe):
    assert probe["invoiceOverCredit"] == {
        "posts": 0,
        "toast": [
            "Credit memo CM-0002 of 2026-09-02: $10.01 is more than the $10.00 it "
            "has left. Nothing was applied.",
            "error",
        ],
    }
