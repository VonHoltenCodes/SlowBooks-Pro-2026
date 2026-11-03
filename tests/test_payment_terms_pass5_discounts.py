"""Early-payment discounts, pass-5 skeptic fixes (#664/#665).

The discount work gained a late-discount path (POST .../discount), new money
and id fields, and a stored memo that must read the same as its journal
header. This file pins the pass-5 fixes: a zero discount is refused in a
sentence a person can act on; the new id fields refuse an out-of-range or
loose value instead of a 500; the stored memo takes the document's own face;
the journal's Void leaves a discount memo to its payment; each guard that had
no test gets one; and the payment view says per allocation whether a discount
can still be taken.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.audit import AuditLog
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.transactions import BASIS_CASH, Transaction, TransactionLine
from app.models.vendor_credits import VendorCredit
from tests.test_payment_terms_664 import (
    D,
    TWO_TEN,
    _account_id,
    _balanced,
    _bill,
    _gl,
    _invoice,
    _receive,
    _receive_one,
    _term,
    _vendor,
)


@pytest.fixture
def discounted(client, db_session, seed_accounts, seed_customer):
    """A customer and an invoice on a 2% 10 Net 30 term (local copy, so the
    fixture is not an imported-then-shadowed name)."""
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    return {"invoice": inv, "customer_id": seed_customer.id, "income": "4900"}


@pytest.fixture
def discounted_bill(client, seed_accounts):
    """A vendor and a bill on a 2% 10 Net 30 term."""
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    return _bill(client, vendor["id"], "2% 10 Net 30")


def _bookkeeper(client):
    """A second client signed in as a bookkeeper, not an administrator."""
    token = client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return keeper


def _short_pay(client, customer_id, invoice_id, amount="98.00", on="2026-10-12"):
    """Pay part of an invoice with no discount, so a late discount can go on
    the rest."""
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": on,
            "amount": amount,
            "allocations": [{"invoice_id": invoice_id, "amount": amount}],
        },
    ).json()


def _short_pay_bill(client, vendor_id, bill_id, amount=490.0, on="2026-10-12"):
    return client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": on,
            "amount": amount,
            "allocations": [{"bill_id": bill_id, "amount": amount}],
        },
    ).json()


# ── 1. a zero discount on /discount is refused before the account check ──────


@pytest.mark.parametrize("amount", ["0", "-0", "-1"])
def test_a_zero_discount_on_the_invoice_discount_route_is_refused(
    client, db_session, discounted, amount
):
    """A zero (or less) discount used to draw the account advice, which
    could not be followed: the account is required on this route."""
    inv = discounted["invoice"]
    pay = _short_pay(client, discounted["customer_id"], inv["id"])
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": amount,
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "A discount must be more than zero."
    assert db_session.query(CreditMemo).count() == 0


@pytest.mark.parametrize("amount", ["0", "-0", "-1"])
def test_a_zero_discount_on_the_bill_discount_route_is_refused(
    client, db_session, discounted_bill, amount
):
    pay = _short_pay_bill(client, discounted_bill["vendor_id"], discounted_bill["id"])
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": discounted_bill["id"],
            "discount": amount,
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "A discount must be more than zero."
    assert db_session.query(VendorCredit).count() == 0


# ── 2. the new id fields refuse an out-of-range or loose value, not a 500 ────

HUGE = 9223372036854775808  # 2**63, one past the signed 64-bit range


def test_an_out_of_range_discount_account_id_is_a_sentence_on_create(
    client, db_session, discounted
):
    """discount_account_id of 2**63 was a 500 (OverflowError on the account
    lookup); it is a 422 naming the field now."""
    inv = discounted["invoice"]
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "98.00", "2.00", HUGE
    )
    assert r.status_code == 422, r.text
    msg = r.json()["detail"][0]["message"]
    assert msg.startswith("Discount account must be") and "or less" in msg


def test_a_boolean_discount_account_id_is_refused_not_read_as_one(
    client, db_session, discounted
):
    """true was read as account 1; a strict id refuses it."""
    inv = discounted["invoice"]
    r = _receive_one(
        client, discounted["customer_id"], inv["id"], "98.00", "2.00", True
    )
    assert r.status_code == 422, r.text
    assert (
        r.json()["detail"][0]["message"] == "Discount account must be a whole number."
    )


def test_an_out_of_range_invoice_id_on_discount_is_a_sentence(
    client, db_session, discounted
):
    inv = discounted["invoice"]
    pay = _short_pay(client, discounted["customer_id"], inv["id"])
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": HUGE,
            "discount": "2.00",
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 422, r.text
    msg = r.json()["detail"][0]["message"]
    assert msg.startswith("Invoice must be") and "or less" in msg


def test_an_out_of_range_bill_id_on_discount_is_a_sentence(
    client, db_session, discounted_bill
):
    pay = _short_pay_bill(client, discounted_bill["vendor_id"], discounted_bill["id"])
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": HUGE,
            "discount": "10.00",
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 422, r.text
    msg = r.json()["detail"][0]["message"]
    assert msg.startswith("Bill must be") and "or less" in msg


def test_an_out_of_range_payment_id_in_the_discount_path_is_a_sentence(
    client, db_session
):
    """POST /api/payments/<2**63>/discount was a 500; it is a 422 now,
    matching how the branch bounds a path id."""
    r = client.post(
        f"/api/payments/{HUGE}/discount",
        json={"invoice_id": 1, "discount": "2.00", "discount_account_id": 1},
    )
    assert r.status_code == 422, r.text
    msg = r.json()["detail"][0]["message"]
    assert msg.startswith("Payment must be") and "or less" in msg


def test_an_out_of_range_bill_payment_id_in_the_discount_path_is_a_sentence(
    client, db_session
):
    r = client.post(
        f"/api/bill-payments/{HUGE}/discount",
        json={"bill_id": 1, "discount": "10.00", "discount_account_id": 1},
    )
    assert r.status_code == 422, r.text
    msg = r.json()["detail"][0]["message"]
    assert msg.startswith("Bill payment must be") and "or less" in msg


def test_a_string_discount_is_read_like_the_rest_of_the_apps_money(
    client, db_session, discounted
):
    """A discount sent as a string ("2.00") is taken, as PaymentAllocation's
    amount is: the money fields stay consistent (reported, not changed)."""
    inv = discounted["invoice"]
    r = _receive(
        client,
        discounted["customer_id"],
        inv["id"],
        "98.00",
        "2.00",
        _account_id(db_session, "4900"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["discounts"][0]["amount"] == "2.00"


# ── 3. the stored memo takes the document's own face, not "invoice" ──────────


def _nonprofit_pledge(client, customer_id, amount="100.00"):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "is_pledge": True,
            "lines": [{"description": "Gift", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_nonprofit_discount_memo_says_pledge_everywhere(
    client, db_session, seed_accounts, seed_customer
):
    """The header said "Pledge #N" while the stored memo said "invoice" — a
    flip could never fix the stored text. Memo, notes and the memo's own
    journal lines now read the document's face."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    _term(client, **TWO_TEN)
    pledge = _nonprofit_pledge(client, seed_customer.id)
    acct = _account_id(db_session, "4900")
    r = _receive(client, seed_customer.id, pledge["id"], "98.00", "2.00", acct)
    assert r.status_code == 201, r.text
    cm = (
        db_session.query(CreditMemo).filter_by(discount_payment_id=r.json()["id"]).one()
    )
    assert "on pledge " in cm.notes and "invoice" not in cm.notes.lower()
    txn = db_session.get(Transaction, cm.transaction_id)
    assert "Pledge #" in txn.description
    for ln in txn.lines:
        assert "invoice" not in (ln.description or "").lower()
        assert "pledge" in (ln.description or "").lower()


def test_a_business_discount_memo_still_says_invoice(client, db_session, discounted):
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    r = _receive(client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct)
    cm = (
        db_session.query(CreditMemo).filter_by(discount_payment_id=r.json()["id"]).one()
    )
    assert "on invoice " in cm.notes


# ── 4. the journal's Void leaves a discount memo to its payment ──────────────


def test_the_journal_void_refuses_a_discount_memo_and_the_payment_void_reverses_it_once(
    client, db_session, discounted
):
    """A discount memo's posting is voided with its payment, never through
    POST /api/journal/{txn}/void: that once reversed it a second time and
    left A/R $2 high. Main's #661 guard covers it; this pins that."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = _receive(client, discounted["customer_id"], inv["id"], "98.00", "2.00", acct)
    cm = (
        db_session.query(CreditMemo)
        .filter_by(discount_payment_id=pay.json()["id"])
        .one()
    )

    r = client.post(f"/api/journal/{cm.transaction_id}/void")
    assert r.status_code == 400, r.text
    assert "credit memo" in r.json()["detail"]

    assert client.post(f"/api/payments/{pay.json()['id']}/void").status_code == 200
    db_session.expire_all()
    assert db_session.get(CreditMemo, cm.id).status == CreditMemoStatus.VOID
    reversals = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "credit_memo_void",
            Transaction.source_id == cm.id,
        )
        .count()
    )
    assert reversals == 1
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert after["balance_due"] == "100.00"
    assert _gl(db_session, _account_id(db_session, "1100")) == D("100.00")
    _balanced(db_session)


def test_the_journal_void_refuses_a_vendor_credit_discount_on_the_bill_side(
    client, db_session, discounted_bill
):
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": discounted_bill["vendor_id"],
            "date": "2026-10-12",
            "amount": 490.0,
            "allocations": [
                {"bill_id": discounted_bill["id"], "amount": 490.0, "discount": "10.00"}
            ],
            "discount_account_id": acct,
        },
    ).json()
    vc = (
        db_session.query(VendorCredit)
        .filter_by(discount_bill_payment_id=pay["id"])
        .one()
    )
    r = client.post(f"/api/journal/{vc.transaction_id}/void")
    assert r.status_code == 400, r.text
    assert "vendor credit" in r.json()["detail"]
    assert client.post(f"/api/bill-payments/{pay['id']}/void").status_code == 200
    reversals = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "vendor_credit_void",
            Transaction.source_id == vc.id,
        )
        .count()
    )
    assert reversals == 1
    assert _gl(db_session, _account_id(db_session, "2000")) == D("-500.00")
    _balanced(db_session)


# ── 5. guards that had no test ───────────────────────────────────────────────


def test_a_bookkeepers_override_on_the_invoice_discount_route_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    """may_override on payments.add_discount: only one of the four override
    endpoints was tested."""
    inv = _invoice(client, seed_customer.id, "Net 30")
    acct = _account_id(db_session, "4900")
    pay = _short_pay(client, seed_customer.id, inv["id"], amount="10.00")
    keeper = _bookkeeper(client)
    r = keeper.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": "90.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 403, r.text
    assert "Only an administrator can take a discount" in r.json()["detail"]
    assert db_session.query(CreditMemo).count() == 0


def test_a_bookkeepers_override_on_bill_create_is_refused(
    client, db_session, seed_accounts
):
    vendor = _vendor(client, terms="Net 30")
    bill = _bill(client, vendor["id"], "Net 30", amount="100.00")
    acct = _account_id(db_session, "4900")
    keeper = _bookkeeper(client)
    r = keeper.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 10.0,
            "allocations": [
                {"bill_id": bill["id"], "amount": 10.0, "discount": "90.00"}
            ],
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 403, r.text
    assert "Only an administrator can take a discount" in r.json()["detail"]
    assert db_session.query(VendorCredit).count() == 0


def test_a_bookkeepers_override_on_the_bill_discount_route_is_refused(
    client, db_session, seed_accounts
):
    vendor = _vendor(client, terms="Net 30")
    bill = _bill(client, vendor["id"], "Net 30", amount="100.00")
    acct = _account_id(db_session, "4900")
    pay = _short_pay_bill(client, vendor["id"], bill["id"], amount=10.0)
    keeper = _bookkeeper(client)
    r = keeper.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": bill["id"],
            "discount": "90.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 403, r.text
    assert "Only an administrator can take a discount" in r.json()["detail"]
    assert db_session.query(VendorCredit).count() == 0


def test_a_late_discount_over_the_balance_is_refused_and_nothing_posts(
    client, db_session, discounted
):
    """The late-discount cap at the balance in payments.add_discount: an
    administrator's $50 override on a $2 balance is 400, and no memo posts."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = _short_pay(client, discounted["customer_id"], inv["id"])  # $2 left
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": "50.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400, r.text
    assert "is more than the $2.00 still due" in r.json()["detail"]
    assert db_session.query(CreditMemo).count() == 0


def test_a_closed_period_keeps_a_discounts_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    """recompute_discount_cash_lines skips a discount on or before the
    closing date, so a later line edit can't rewrite its cash lines inside
    the closed period. A taxed invoice, so the discount posts cash lines."""
    from app.models.invoices import Invoice
    from app.models.transactions import READ_ALL
    from app.services import cash_basis

    def _cash_ids():
        return sorted(
            ln.id
            for ln in db_session.query(TransactionLine)
            .filter(
                TransactionLine.transaction_id == cm.transaction_id,
                TransactionLine.basis == BASIS_CASH,
            )
            .execution_options(**cash_basis.reading(READ_ALL))
        )

    _term(client, **TWO_TEN)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-05",
            "terms": "2% 10 Net 30",
            "tax_rate": "0.07",
            "lines": [
                {"description": "Svc", "quantity": 1, "rate": "500", "is_taxable": True}
            ],
        },
    ).json()
    acct = _account_id(db_session, "4900")
    assert (
        _receive_one(
            client, seed_customer.id, inv["id"], "524.30", "10.70", acct
        ).status_code
        == 201
    )
    cm = (
        db_session.query(CreditMemo)
        .filter(CreditMemo.discount_payment_id.isnot(None))
        .one()
    )
    before = _cash_ids()
    assert before  # the discount posted cash lines

    assert (
        client.put("/api/settings", json={"closing_date": "2026-12-31"}).status_code
        == 200
    )
    invoice = db_session.get(Invoice, inv["id"])
    n = cash_basis.recompute_discount_cash_lines(db_session, invoice, invoice=True)
    assert n == 0  # skipped — the discount is inside the closed period
    assert _cash_ids() == before  # the same rows, not deleted and rewritten


def test_a_discount_on_the_last_day_is_taken_and_the_next_day_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    """The pay_date > by boundary: a payment dated on the discount's last
    day (Oct 15 for a 2% 10 term dated Oct 5) is taken; the next day is not,
    without an override. No existing test pays on the date itself."""
    _term(client, **TWO_TEN)
    acct = _account_id(db_session, "4900")
    on_time = _invoice(client, seed_customer.id, "2% 10 Net 30")
    late = _invoice(client, seed_customer.id, "2% 10 Net 30")
    r = _receive_one(
        client,
        seed_customer.id,
        on_time["id"],
        "98.00",
        "2.00",
        acct,
        date="2026-10-15",
    )
    assert r.status_code == 201, r.text
    r = _receive_one(
        client,
        seed_customer.id,
        late["id"],
        "98.00",
        "2.00",
        acct,
        date="2026-10-16",
    )
    assert r.status_code == 400, r.text
    assert "had to be taken by Oct 15, 2026" in r.json()["detail"]


def test_a_late_discount_before_the_invoices_date_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    """The /discount path had no before-the-document test (only create did).
    A payment dated before the invoice can't post its discount in that
    earlier month."""
    _term(client, **TWO_TEN)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30", on="2026-10-28")
    acct = _account_id(db_session, "4900")
    pay = _short_pay(client, seed_customer.id, inv["id"], on="2026-08-01")
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": "2.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400, r.text
    assert "dated Aug 1, 2026, before invoice" in r.json()["detail"]
    assert db_session.query(CreditMemo).count() == 0


def test_a_late_bill_discount_before_the_bills_date_is_refused(
    client, db_session, seed_accounts
):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    bill = _bill(client, vendor["id"], "2% 10 Net 30", on="2026-10-28", amount="100.00")
    acct = _account_id(db_session, "4900")
    pay = _short_pay_bill(
        client, vendor["id"], bill["id"], amount=90.0, on="2026-08-01"
    )
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": bill["id"],
            "discount": "2.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 400, r.text
    assert "dated Aug 1, 2026, before bill" in r.json()["detail"]
    assert db_session.query(VendorCredit).count() == 0


def _override_rows(db_session, table, record_id):
    return [
        row
        for row in db_session.query(AuditLog)
        .filter(AuditLog.table_name == table, AuditLog.record_id == record_id)
        .all()
        if (row.new_values or {}).get("discount_override")
    ]


def test_a_bill_create_override_is_logged_against_the_acting_user(
    client, db_session, discounted_bill
):
    """Only the invoice create path asserted the override audit row."""
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": discounted_bill["vendor_id"],
            "date": "2026-11-20",  # past the discount window
            "amount": 490.0,
            "allocations": [
                {"bill_id": discounted_bill["id"], "amount": 490.0, "discount": "10.00"}
            ],
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    ).json()
    rows = _override_rows(db_session, "bill_payments", pay["id"])
    assert rows
    note = pay["discount_override_note"] or ""
    assert rows[0].username and f"by {rows[0].username}" in note


def test_a_late_override_on_discount_is_logged_against_the_acting_user(
    client, db_session, discounted
):
    """The /discount override path had no audit assertion."""
    inv = discounted["invoice"]
    acct = _account_id(db_session, "4900")
    pay = _short_pay(client, discounted["customer_id"], inv["id"], on="2026-10-30")
    r = client.post(
        f"/api/payments/{pay['id']}/discount",
        json={
            "invoice_id": inv["id"],
            "discount": "2.00",
            "discount_account_id": acct,
            "allow_discount_override": True,
        },
    )
    assert r.status_code == 200, r.text
    rows = _override_rows(db_session, "payments", pay["id"])
    assert rows
    note = r.json()["discount_override_note"] or ""
    assert rows[0].username and f"by {rows[0].username}" in note


def test_a_locked_claim_on_the_bill_discount_route_is_a_sentence(
    client, db_session, discounted_bill, monkeypatch
):
    """The bill side shares payments._claim; its "locked" branch had no
    test (the invoice side's did)."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Query

    pay = _short_pay_bill(client, discounted_bill["vendor_id"], discounted_bill["id"])

    def locked(self, *a, **kw):
        raise OperationalError(
            "UPDATE bill_payments", {}, Exception("database is locked")
        )

    monkeypatch.setattr(Query, "update", locked)
    r = client.post(
        f"/api/bill-payments/{pay['id']}/discount",
        json={
            "bill_id": discounted_bill["id"],
            "discount": "10.00",
            "discount_account_id": _account_id(db_session, "4900"),
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "Another discount on this payment is being saved; try again in a moment."
    )


# ── 6. the allocation view says whether a late discount can be taken ─────────


def test_can_take_discount_is_true_only_where_a_late_discount_would_go_through(
    client, db_session, seed_accounts, seed_customer
):
    """can_take_discount: true for an undiscounted allocation whose terms
    offer one; false where a discount was already taken, and false for money
    applied later from a remainder (the button is offered on true only)."""
    _term(client, **TWO_TEN)
    offers = _invoice(client, seed_customer.id, "2% 10 Net 30")
    taken = _invoice(client, seed_customer.id, "2% 10 Net 30")
    remainder = _invoice(client, seed_customer.id, "2% 10 Net 30")
    acct = _account_id(db_session, "4900")

    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-12",
            "amount": "200.00",
            "allocations": [
                {"invoice_id": offers["id"], "amount": "50.00"},
                {"invoice_id": taken["id"], "amount": "98.00", "discount": "2.00"},
            ],
            "discount_account_id": acct,
        },
    ).json()
    # a $50 remainder, applied to the third invoice (from_remainder)
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": remainder["id"], "amount": "50.00"}]},
    )
    assert r.status_code == 200, r.text

    view = client.get(f"/api/payments/{pay['id']}").json()
    flags = {a["invoice_id"]: a["can_take_discount"] for a in view["allocations"]}
    assert flags[offers["id"]] is True
    assert flags[taken["id"]] is False
    assert flags[remainder["id"]] is False


def test_can_take_discount_on_a_bill_payment(client, db_session, seed_accounts):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    offers = _bill(client, vendor["id"], "2% 10 Net 30", amount="500.00", number="B-A")
    taken = _bill(client, vendor["id"], "2% 10 Net 30", amount="500.00", number="B-B")
    acct = _account_id(db_session, "4900")
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 540.0,
            "allocations": [
                {"bill_id": offers["id"], "amount": 50.0},
                {"bill_id": taken["id"], "amount": 490.0, "discount": "10.00"},
            ],
            "discount_account_id": acct,
        },
    ).json()
    view = client.get(f"/api/bill-payments/{pay['id']}").json()
    flags = {a["bill_id"]: a["can_take_discount"] for a in view["allocations"]}
    assert flags[offers["id"]] is True
    assert flags[taken["id"]] is False
