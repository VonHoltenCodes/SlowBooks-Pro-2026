"""Books Check and the QuickBooks Online import, after the 2.32.0 round-3
and round-4 gates (#914, #915, #919).

#914 (NEW-225): the import carries a company's open invoices and unapplied
payments over as documents with no entry of their own, on purpose, because
the imported Journal already posted them. Books Check's "No posted document
without its entry" row named every one of them and sent the user to re-save
documents that were right.

#915 (W-151): QuickBooks Online's Journal names a transaction's vendor once,
on its first row, and the A/P line carries no Name; the import posted the
entry without the vendor, so "A/P Aging = Accounts Payable" and "Vendor
balances = Accounts Payable" never tied on an imported book. A vendor-named
A/P entry now takes the vendor (the IIF import's shape, #897), a
customer-named A/R line the customer, unless the carried documents cover
that side."""

import io
from decimal import Decimal

from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services import control_accounts
from app.services.books_check import books_check
from tests.test_qbo_export_open_invoices import AGING, BUNDLE, FIXTURES, _files

AP_SECTION = (
    "         Accounts Payable,\n"
    "            Accounts Payable (A/P),=300.00\n"
    "         Total Accounts Payable,=300.00\n"
)
BILL = (
    ",02/01/2026,Bill,BN-1,{vendor},Flyers,Office Supplies,=300.00,\n"
    ",,,,,Flyers,Accounts Payable (A/P),,=300.00\n"
    ",,,,,,,=300.00,=300.00\n"
    ",,,,,,,,\n"
)


def _rows(db, as_of=None):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, as_of)["rows"]}


def _bundle_with_a_bill(vendor="Print Shop Co", with_aging=False):
    """The fixture bundle with Accounts Payable on the Balance Sheet and one
    Bill block in the Journal, its vendor on the first row only (as
    QuickBooks Online writes it); no Trial Balance, so nothing disagrees."""
    out = []
    for name in BUNDLE:
        if name == "Trial_balance.csv":
            continue
        text = (FIXTURES / name).read_text()
        if name == "Balance_sheet.csv":
            text = text.replace(
                "         Other Current Liabilities,\n",
                AP_SECTION + "         Other Current Liabilities,\n",
            )
        if name == "Journal.csv":
            text = text + BILL.format(vendor=vendor)
        out.append(("files", (name, io.BytesIO(text.encode()), "text/csv")))
    if with_aging:
        out.append(
            ("files", (AGING, io.BytesIO((FIXTURES / AGING).read_bytes()), "text/csv"))
        )
    return out


def _import(client, files):
    r = client.post("/api/migration/qbo_export/import", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data["errors"]
    assert "named_control_lines" not in data
    return data


# ── #914: the carried documents are not missing their entry ───────────────


def test_carried_invoices_and_payments_are_not_named_as_missing_their_entry(
    client, db_session, seed_accounts
):
    data = _import(client, _files())
    assert data["imported_open_invoices"] >= 1
    rows = _rows(db_session)
    row = rows["journal_entries"]
    assert row["status"] == "pass", row
    assert row["documents"] == []
    assert rows["ar_aging"]["status"] == "pass", rows["ar_aging"]
    assert rows["customer_balances"]["status"] == "pass", rows["customer_balances"]


def test_with_the_aging_given_the_journals_receivable_lines_stay_unnamed(
    client, db_session, seed_accounts
):
    """The carried invoices and payments are the receivable; naming the
    Journal's A/R lines as well would count the balance twice."""
    _import(client, _files())
    ar_id = control_accounts.find(db_session, "1100")
    named = (
        db_session.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Transaction.source_type == "qbo_export_import",
            TransactionLine.account_id == ar_id,
            TransactionLine.customer_id.isnot(None),
        )
        .count()
    )
    assert named == 0


def test_a_local_invoice_without_its_entry_is_still_named(
    client, db_session, seed_accounts
):
    _import(client, _files())
    customer = db_session.query(Customer).first()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer.id,
            "date": "2026-10-20",
            "due_date": "2026-11-19",
            "lines": [{"description": "Consulting", "quantity": 1, "rate": 125.0}],
        },
    )
    assert r.status_code in (200, 201), r.text
    invoice_id = r.json()["id"]
    from app.models.invoices import Invoice, InvoiceStatus

    # posted (sent) with its entry gone: the row names it, and only it
    inv = db_session.get(Invoice, invoice_id)
    inv.status = InvoiceStatus.SENT
    inv.transaction_id = None
    db_session.commit()
    row = _rows(db_session)["journal_entries"]
    assert row["status"] == "difference", row
    assert [(d["type"], d["id"]) for d in row["documents"]] == [("invoice", invoice_id)]


# ── #915: a vendor-named A/P entry is the vendor's ────────────────────────


def test_a_bill_blocks_payable_entry_takes_its_vendor_and_the_aging_ties(
    client, db_session, seed_accounts
):
    _import(client, _bundle_with_a_bill())
    vendor = db_session.query(Vendor).filter(Vendor.name == "Print Shop Co").one()
    txns = (
        db_session.query(Transaction).filter(Transaction.vendor_id == vendor.id).all()
    )
    assert len(txns) == 1 and txns[0].source_type == "qbo_export_import"

    r = client.get("/api/reports/ap-aging?as_of_date=2026-10-15")
    assert r.status_code == 200, r.text
    row = next(i for i in r.json()["items"] if i["vendor_id"] == vendor.id)
    assert row["total"] == 300.0
    from app.services.contact_balances import vendor_balance

    assert vendor_balance(db_session, vendor.id) == Decimal("300.00")

    rows = _rows(db_session)
    for key in ("ap_aging", "vendor_balances", "journal_entries"):
        assert rows[key]["status"] == "pass", rows[key]


def test_a_customer_named_receivable_line_on_the_control_takes_its_customer(
    client, db_session, seed_accounts
):
    """A Journal line on the Accounts Receivable control that names a
    customer is the customer's, unless the A/R Aging Detail carried the
    open invoices (then the documents are the receivable and the line stays
    unnamed, or the balance would count twice)."""
    from app.services import qbo_export_import as qbo

    customer = Customer(name="Dana Reyes - CUST", is_active=True)
    vendor = Vendor(name="Print Shop Co", is_active=True)
    db_session.add_all([customer, vendor])
    db_session.commit()
    a = seed_accounts

    def entry(debit_acct, credit_acct):
        txn = Transaction(
            date=__import__("datetime").date(2026, 9, 1),
            description="imported",
            source_type="qbo_export_import",
        )
        db_session.add(txn)
        db_session.flush()
        db_session.add_all(
            [
                TransactionLine(
                    transaction_id=txn.id, account_id=debit_acct, debit=100, credit=0
                ),
                TransactionLine(
                    transaction_id=txn.id, account_id=credit_acct, debit=0, credit=100
                ),
            ]
        )
        db_session.commit()
        return txn

    ar, ap, income, expense = a["1100"].id, a["2000"].id, a["4000"].id, a["6000"].id
    receivable = entry(ar, income)
    payable = entry(expense, ap)
    named = [
        {
            "kind": "receivable",
            "transaction_id": receivable.id,
            "name": "Dana Reyes - CUST",
        },
        {"kind": "payable", "transaction_id": payable.id, "name": "print shop co"},
    ]
    # the aging given: the receivable stays unnamed, the payable is named
    assert qbo._name_control_lines(db_session, named, {"given": True}) == 1
    db_session.expire_all()
    lines = [ln for ln in receivable.lines if ln.account_id == ar]
    assert lines and all(ln.customer_id is None for ln in lines)
    assert payable.vendor_id == vendor.id
    # no aging: the receivable line is the customer's
    assert qbo._name_control_lines(db_session, named, {"given": False}) == 1
    db_session.expire_all()
    assert all(
        ln.customer_id == customer.id for ln in receivable.lines if ln.account_id == ar
    )
    # a second pass names nothing again
    assert qbo._name_control_lines(db_session, named, {"given": False}) == 0


def test_a_vendor_on_no_list_stays_nameless_and_is_still_named_by_books_check(
    client, db_session, seed_accounts
):
    _import(client, _bundle_with_a_bill(vendor="Nobody Supply Inc"))
    assert (
        db_session.query(Vendor).filter(Vendor.name == "Nobody Supply Inc").count() == 0
    )
    ap_id = control_accounts.find(db_session, "2000")
    entry = (
        db_session.query(Transaction)
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Transaction.source_type == "qbo_export_import",
            TransactionLine.account_id == ap_id,
        )
        .one()
    )
    assert entry.vendor_id is None
    row = _rows(db_session)["ap_aging"]
    assert row["status"] == "difference", row
    assert entry.id in [d["id"] for d in row["documents"]]


def test_a_second_import_of_the_same_bundle_posts_and_names_nothing_twice(
    client, db_session, seed_accounts
):
    _import(client, _bundle_with_a_bill())
    again = _import(client, _bundle_with_a_bill())
    assert again["imported_journals"] == 0
    vendor = db_session.query(Vendor).filter(Vendor.name == "Print Shop Co").one()
    assert (
        db_session.query(Transaction).filter(Transaction.vendor_id == vendor.id).count()
        == 1
    )


# ── #919: the A/R rows on an imported book, and a word ────────────────────


def _sent_invoice_without_its_entry(client, db_session, amount=111.0):
    from app.models.invoices import Invoice, InvoiceStatus

    customer = db_session.query(Customer).first()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer.id,
            "date": "2026-10-20",
            "due_date": "2026-11-19",
            "lines": [{"description": "Consulting", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code in (200, 201), r.text
    inv = db_session.get(Invoice, r.json()["id"])
    inv.status = InvoiceStatus.SENT
    db_session.flush()
    # the invoice stays and its entry goes (direct writes, as the gate did):
    # A/R is now off by the invoice's amount, for a reason that is not the
    # import's
    tid = inv.transaction_id
    inv.transaction_id = None
    db_session.flush()
    if tid is not None:
        db_session.query(TransactionLine).filter(
            TransactionLine.transaction_id == tid
        ).delete()
        db_session.query(Transaction).filter(Transaction.id == tid).delete()
    db_session.commit()
    return inv.id


def test_the_receivable_rows_leave_out_the_imports_covered_lines(
    client, db_session, seed_accounts
):
    """With the A/R Aging Detail given, the carried documents are the
    receivable and the Journal's A/R lines stay unnamed (#915). Once A/R is
    off for another reason, the A/R rows name that reason, not the import's
    own lines ("move it to an invoice" would count the balance twice)."""
    _import(client, _files())
    _sent_invoice_without_its_entry(client, db_session)
    rows = _rows(db_session)
    for key in ("ar_aging", "customer_balances"):
        row = rows[key]
        assert row["status"] == "difference", row
        imported = (
            db_session.query(Transaction.id)
            .filter(Transaction.source_type == "qbo_export_import")
            .all()
        )
        imported_ids = {t for (t,) in imported}
        assert not any(
            d["type"] == "journal" and d["id"] in imported_ids for d in row["documents"]
        ), row["documents"][:3]
        assert not any("names no customer" in d["why"] for d in row["documents"])


def test_a_nameless_import_line_is_still_named_on_a_book_with_no_carried_documents(
    client, db_session, seed_accounts
):
    """No A/R Aging Detail, no carried documents: an import line on the
    control that names nobody is the stray it always was."""
    from datetime import date

    from app.services.qbo_export_import import has_carried_documents

    assert has_carried_documents(db_session) is False
    a = seed_accounts
    txn = Transaction(
        date=date(2026, 9, 1), description="imported", source_type="qbo_export_import"
    )
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id, account_id=a["1100"].id, debit=40, credit=0
            ),
            TransactionLine(
                transaction_id=txn.id, account_id=a["4000"].id, debit=0, credit=40
            ),
        ]
    )
    db_session.commit()
    row = _rows(db_session)["ar_aging"]
    assert row["status"] == "difference", row
    assert any(
        d["type"] == "journal" and d["id"] == txn.id for d in row["documents"]
    ), (txn.id, row["documents"])


def test_the_payable_sentence_says_an_entry():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1] / "app/services/books_check.py"
    ).read_text()
    assert 'or a "\n        "entry posted' not in text
    assert text.count('or an "\n        "entry posted straight to the account') >= 2
