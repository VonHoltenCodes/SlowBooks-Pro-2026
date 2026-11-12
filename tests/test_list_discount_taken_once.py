"""The invoices and bills lists read the discounts taken on a page once.

Since the early-payment discount (#664) each row of the two lists ran its
own query for the discount taken on it, a query a row that the query-count
test's slack hid until a cold settings cache tipped it over (#841's CI).
DocumentTerms.prefetch reads the page's discounts in one query; each row's
discount_taken is what the document's own GET says, and the offer on the
rows that have not taken theirs is unchanged.
"""

from tests.test_no_nplus1_in_list_endpoints import _count_selects
from tests.test_payment_terms_pass5_terms import (
    TWO_TEN,
    _account_id,
    _bill,
    _invoice,
    _receive,
    _term,
    _vendor,
)


def _touching(stmts, table):
    return [s for s in stmts if table in " ".join(str(s).split())]


def test_the_invoices_list_reads_the_discounts_taken_once(
    client, db_session, db_engine, seed_accounts, seed_customer
):
    _term(client, **TWO_TEN)
    invoices = [_invoice(client, seed_customer.id, "2% 10 Net 30") for _ in range(4)]
    acct = _account_id(db_session, "4900")
    r = _receive(client, seed_customer.id, invoices[1]["id"], "98.00", "2.00", acct)
    assert r.status_code == 201, r.text
    one = client.get(f"/api/invoices/{invoices[1]['id']}").json()
    assert one["discount_taken"]["amount"] == "2.00"

    with _count_selects(db_engine) as stmts:
        r = client.get("/api/invoices")
    assert r.status_code == 200, r.text
    assert len(_touching(stmts, "credit_memos")) == 1, [str(s) for s in stmts]
    rows = {row["id"]: row for row in r.json()}
    assert rows[invoices[1]["id"]]["discount_taken"] == one["discount_taken"]
    assert rows[invoices[1]["id"]]["discount_line"] is None
    for inv in (invoices[0], invoices[2], invoices[3]):
        assert rows[inv["id"]]["discount_taken"] is None
        assert rows[inv["id"]]["discount_line"] == "2% discount if paid by Oct 15, 2026"


def test_the_bills_list_reads_the_discounts_taken_once(
    client, db_session, db_engine, seed_accounts
):
    _term(client, **TWO_TEN)
    vendor = _vendor(client, terms="2% 10 Net 30")
    bills = [
        _bill(client, vendor["id"], "2% 10 Net 30", number=f"B-{i}") for i in range(3)
    ]
    acct = _account_id(db_session, "4900")
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-10-12",
            "amount": 490.0,
            "allocations": [
                {"bill_id": bills[2]["id"], "amount": 490.0, "discount": "10.00"}
            ],
            "discount_account_id": acct,
        },
    )
    assert r.status_code == 201, r.text
    one = client.get(f"/api/bills/{bills[2]['id']}").json()
    assert one["discount_taken"]["amount"] == "10.00"

    with _count_selects(db_engine) as stmts:
        r = client.get("/api/bills")
    assert r.status_code == 200, r.text
    assert len(_touching(stmts, "vendor_credits")) == 1, [str(s) for s in stmts]
    rows = {row["id"]: row for row in r.json()}
    assert rows[bills[2]["id"]]["discount_taken"] == one["discount_taken"]
    for bill in bills[:2]:
        assert rows[bill["id"]]["discount_taken"] is None
        assert (
            rows[bill["id"]]["discount_line"] == "2% discount if paid by Oct 15, 2026"
        )


def test_a_single_document_reads_as_before(
    client, db_session, seed_accounts, seed_customer
):
    """document_discount_taken (one document) and discounts_taken (a page)
    agree, with and without a discount taken."""
    from app.models.invoices import Invoice
    from app.services.payment_terms import discounts_taken, document_discount_taken

    _term(client, **TWO_TEN)
    invoices = [_invoice(client, seed_customer.id, "2% 10 Net 30") for _ in range(2)]
    acct = _account_id(db_session, "4900")
    assert (
        _receive(
            client, seed_customer.id, invoices[0]["id"], "98.00", "2.00", acct
        ).status_code
        == 201
    )
    docs = (
        db_session.query(Invoice)
        .filter(Invoice.id.in_([i["id"] for i in invoices]))
        .all()
    )
    page = discounts_taken(db_session, docs)
    assert set(page) == {invoices[0]["id"]}
    for doc in docs:
        assert document_discount_taken(db_session, doc) == page.get(doc.id)
    assert discounts_taken(db_session, []) == {}
