"""The journal's void route reverses journal entries of their own only (#661).

A document's posting is reversed by voiding the document, which writes its
reversal under the document's own id (an invoice's void is `invoice_void`
with the invoice's id). The route's already-reversed test looks for a
reversal under the transaction's id, so it saw a voided invoice's posting
as unreversed, and POST /api/journal/{id}/void took the invoice's income
and A/R off the books a second time. The Journal page never offers Void on
a document's posting; the route now refuses one, with a sentence saying
where to void it. Imported checks and late fees, which have no document of
their own, still void here (tests/test_possible_duplicates.py and
tests/test_late_fee_job_and_void.py)."""

from decimal import Decimal

from app.models.transactions import Transaction, TransactionLine


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-04-01",
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [{"description": "Consulting", "quantity": "1", "rate": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _posting(db_session, source_type, source_id):
    return (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == source_type,
            Transaction.source_id == source_id,
        )
        .one()
    )


def _balance(db_session, account_id):
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .all()
    )
    return sum(((ln.debit or 0) - (ln.credit or 0) for ln in lines), Decimal("0"))


def test_a_voided_invoices_posting_is_not_reversed_again(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    db_session.expire_all()
    posting = _posting(db_session, "invoice", inv["id"])
    ar = seed_accounts["1100"].id
    before = (db_session.query(Transaction).count(), _balance(db_session, ar))
    assert before[1] == Decimal("0")

    r = client.post(f"/api/journal/{posting.id}/void")

    assert r.status_code == 400, r.text
    number = inv["invoice_number"]
    assert r.json()["detail"] == (
        f"This is invoice {number}'s posting, not a journal entry of its own. "
        f"Void invoice {number} itself; that reverses this posting with it."
    )
    db_session.expire_all()
    assert (db_session.query(Transaction).count(), _balance(db_session, ar)) == before


def test_an_open_invoices_posting_is_refused_too(
    client, db_session, seed_accounts, seed_customer
):
    # Reversed on its own, the posting would leave an open invoice whose
    # A/R the ledger no longer carries.
    inv = _invoice(client, seed_customer.id)
    db_session.expire_all()
    posting = _posting(db_session, "invoice", inv["id"])
    count = db_session.query(Transaction).count()

    r = client.post(f"/api/journal/{posting.id}/void")

    assert r.status_code == 400, r.text
    assert f"Void invoice {inv['invoice_number']} itself" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.query(Transaction).count() == count
    assert client.get(f"/api/invoices/{inv['id']}").json()["status"] != "void"


def test_a_journal_entry_of_its_own_still_voids(client, db_session, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-04-02",
            "description": "Owner contribution",
            "lines": [
                {"account_id": seed_accounts["1000"].id, "debit": "250"},
                {"account_id": seed_accounts["3000"].id, "credit": "250"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    entry = r.json()

    assert client.post(f"/api/journal/{entry['id']}/void").status_code == 200
    db_session.expire_all()
    _posting(db_session, "manual_void", entry["id"])


def test_a_payments_ref_is_said_as_a_ref_not_as_its_number(
    client, db_session, seed_accounts, seed_customer
):
    """2.31.2 gate, R2-2: a payment's posting carries the customer's check
    number, not the payment's own; the refusal named it "payment 771" as an
    invoice's is "invoice 1001". It says the Ref # now."""
    inv = _invoice(client, seed_customer.id)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-02",
            "amount": "100.00",
            "reference": "771",
            "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    db_session.expire_all()
    posting = _posting(db_session, "payment", r.json()["id"])
    assert posting.reference == "771"

    refused = client.post(f"/api/journal/{posting.id}/void")

    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == (
        "This is a payment's posting (Ref # 771), not a journal entry of its "
        "own. Void the payment itself; that reverses this posting with it."
    )
