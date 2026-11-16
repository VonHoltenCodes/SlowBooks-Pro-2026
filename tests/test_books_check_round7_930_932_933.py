"""Books Check after the 2.32.0 round-7 gate (#930, #932, #933).

#930 (W-155): the customer and vendor balance rows counted every open
document whatever its date against a ledger and an aging as of today, so an
invoice dated for next month read as a difference, and the party it listed
had no name. Both sides now read as of the check's date, and the party is
named from its record.

#932 (W-157): a pay run's number is its pay date, not "Pay run <date>",
which the type label then doubled.

#933 (W-158): a document's label and reason go through the company's words
once, in Books Check itself, with the article made to fit ("a pledge", not
"an pledge"), so the JSON, the window, the PDF and the CSV agree."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import _doc_words, _words, books_check
from app.services.terminology import Terms


def _rows(db):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, None)["rows"]}


def _all_pass(rows):
    return [k for k, r in rows.items() if r["status"] == "difference"]


def _invoice(client, customer_id, when, rate=300.0):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": when.isoformat(),
            "due_date": (when + timedelta(days=30)).isoformat(),
            "lines": [{"description": "Consulting", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _bill(client, vendor_id, when, account_id, amount=400.0):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": when.isoformat(),
            "due_date": (when + timedelta(days=30)).isoformat(),
            "lines": [
                {"account_id": account_id, "amount": amount, "description": "Plants"}
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _entry(
    db, when, debit_acct, credit_acct, amount, *, vendor_id=None, customer_id=None
):
    txn = Transaction(
        date=when,
        description="imported",
        source_type="qbo_export_import",
        vendor_id=vendor_id,
    )
    db.add(txn)
    db.flush()
    db.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=debit_acct,
                debit=amount,
                credit=0,
                customer_id=customer_id,
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=credit_acct,
                debit=0,
                credit=amount,
                customer_id=customer_id,
            ),
        ]
    )
    db.commit()
    return txn


# ── #930 ───────────────────────────────────────────────────────────────────


def test_an_invoice_dated_ahead_is_no_difference(client, db_session, seed_accounts):
    customer = Customer(name="Dockside Marina", is_active=True)
    db_session.add(customer)
    db_session.commit()
    _invoice(client, customer.id, date.today() + timedelta(days=10))
    rows = _rows(db_session)
    assert _all_pass(rows) == [], {k: rows[k]["sentence"] for k in _all_pass(rows)}
    assert rows["customer_balances"]["status"] == "pass"


def test_a_bill_dated_ahead_is_no_difference(client, db_session, seed_accounts):
    a = seed_accounts
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _bill(client, vendor.id, date.today() + timedelta(days=10), a["6000"].id)
    rows = _rows(db_session)
    assert _all_pass(rows) == [], {k: rows[k]["sentence"] for k in _all_pass(rows)}


def test_a_named_entry_dated_ahead_is_no_difference(client, db_session, seed_accounts):
    a = seed_accounts
    vendor = Vendor(name="Coastline Framing", is_active=True)
    customer = Customer(name="Walt Okafor", is_active=True)
    db_session.add_all([vendor, customer])
    db_session.commit()
    ahead = date.today() + timedelta(days=5)
    _entry(db_session, ahead, a["6000"].id, a["2000"].id, 485, vendor_id=vendor.id)
    _entry(db_session, ahead, a["1100"].id, a["4000"].id, 395, customer_id=customer.id)
    rows = _rows(db_session)
    assert _all_pass(rows) == [], {k: rows[k]["sentence"] for k in _all_pass(rows)}


def test_a_customer_whose_balance_drifted_is_named(client, db_session, seed_accounts):
    from app.models.invoices import Invoice, InvoiceStatus

    customer = Customer(name="Oakview Terrace HOA", is_active=True)
    db_session.add(customer)
    db_session.commit()
    inv_id = _invoice(client, customer.id, date.today() - timedelta(days=5))
    inv = db_session.get(Invoice, inv_id)
    inv.status = InvoiceStatus.SENT
    db_session.commit()
    # a balance moved by hand: the row names the customer and the invoice
    inv.balance_due = Decimal(str(inv.balance_due)) + Decimal("50")
    db_session.commit()
    row = _rows(db_session)["customer_balances"]
    assert row["status"] == "difference", row
    # the drifted invoice is named; the customer's balance and their aging
    # row both read the moved balance due, so no customer row is listed, and
    # every document listed carries a name (no bare "Customer")
    assert [
        (d["type"], d["number"]) for d in row["documents"] if d["type"] == "invoice"
    ] == [("invoice", "1001")]
    assert all(d["number"] for d in row["documents"]), row["documents"]


# ── #932 ───────────────────────────────────────────────────────────────────


def test_a_pay_runs_number_is_its_date():
    text = (
        Path(__file__).resolve().parents[1] / "app/services/books_check.py"
    ).read_text()
    assert 'f"Pay run {' not in text
    assert text.count("pay_date.isoformat()") >= 3


# ── #933 ───────────────────────────────────────────────────────────────────


def test_the_article_fits_the_companys_word():
    np = Terms("nonprofit")
    assert (
        _words(np, "an invoice whose balance due drifted")
        == "a pledge whose balance due drifted"
    )
    assert (
        _words(np, "move it to an invoice or a credit memo")
        == "move it to a pledge or a credit memo"
    )
    assert _words(np, "that names no customer") == "that names no donor"
    assert (
        _words(np, "an entry posted straight; an IIF import")
        == "an entry posted straight; an IIF import"
    )
    assert (
        _words(Terms("business"), "an invoice; a customer") == "an invoice; a customer"
    )
    doc = _doc_words(
        np, {"type": "invoice", "type_label": "Invoice", "why": "an invoice"}
    )
    assert doc["type_label"] == "Pledge" and doc["why"] == "a pledge"


def test_a_nonprofit_reads_one_way_in_the_json_and_the_csv(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    a = seed_accounts
    # a nameless entry on A/R: the stray the rows name
    txn = Transaction(date=date(2026, 9, 1), description="stray", source_type="manual")
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id, account_id=a["1100"].id, debit=80, credit=0
            ),
            TransactionLine(
                transaction_id=txn.id, account_id=a["4000"].id, debit=0, credit=80
            ),
        ]
    )
    db_session.commit()
    rows = _rows(db_session)
    row = rows["ar_aging"]
    assert row["status"] == "difference", row
    assert "an pledge" not in row["sentence"] and "a pledge" in row["sentence"], row[
        "sentence"
    ]
    doc = next(d for d in row["documents"] if d["type"] == "journal")
    assert "names no donor" in doc["why"] and "a pledge" in doc["why"], doc["why"]
    assert "an pledge" not in doc["why"] and "customer" not in doc["why"]
    csv_text = client.get("/api/reports/books-check/csv").text
    assert "an pledge" not in csv_text and "names no donor" in csv_text
    assert "Invoice" not in csv_text
