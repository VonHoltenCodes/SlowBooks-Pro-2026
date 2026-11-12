"""Attachments before the first save on the rest of the new-document forms
(#881, the follow-up to #796): invoices (and invoice Quick Entry, which
posts the same create), estimates, sales receipts, purchase orders, vendor
credits, journal entries and register entries. Bill Quick Entry posts
POST /api/bills, which #796 already covers; its screen is checked below.

The mechanism and its rules are #796's, unchanged: a file is held (POST
/api/attachments/pending) and named on the create call
(`pending_attachments`); the save attaches it in the same step, a refused
save keeps it waiting for the retry, a file taken off the form (Cancel, x,
Escape) is gone, and a stale id refuses the save with nothing posted."""

import io
from pathlib import Path

import pytest

from app.models.attachments import Attachment
from app.models.contacts import Customer, Vendor
from app.models.estimates import Estimate
from app.models.invoices import Invoice
from app.models.purchase_orders import PurchaseOrder
from app.models.stored_files import KIND_PENDING_ATTACHMENT, StoredFile
from app.models.transactions import Transaction
from app.models.vendor_credits import VendorCredit
from app.routes.banking import TRANSFER_TAKES_NO_FILES
from app.services import pending_attachments

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
DAY = "2026-09-01"


def _pdf(name="receipt.pdf", data=b"%PDF-1.4 paper trail 881"):
    return {"file": (name, io.BytesIO(data), "application/pdf")}


def _hold(client, **kw):
    r = client.post("/api/attachments/pending", files=_pdf(**kw))
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture(name="books")
def books_fixture(db_session, seed_accounts):
    customer = Customer(name="Lakeside Studio", is_active=True)
    vendor = Vendor(
        name="Ridge Supply",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add_all([customer, vendor])
    db_session.commit()
    return {
        "customer": customer.id,
        "vendor": vendor.id,
        "checking": seed_accounts["1000"].id,
        "savings": seed_accounts["1010"].id,
        "expense": seed_accounts["6000"].id,
        "income": seed_accounts["4000"].id,
        "undeposited": seed_accounts["1200"].id,
    }


def _sale_lines():
    return [{"description": "Design work", "quantity": 1, "rate": 40}]


def _purchase_lines(books):
    return [
        {
            "description": "Lumber",
            "quantity": 1,
            "rate": 40,
            "account_id": books["expense"],
        }
    ]


# Each document type: its create call's path and body, its record type for
# the attachments, how a saved one's id is read from the answer, a body the
# create refuses (to show the files wait for the retry), and the table a
# refused save must leave empty of new rows.
def _invoice(books):
    return {"customer_id": books["customer"], "date": DAY, "lines": _sale_lines()}


def _estimate(books):
    return {"customer_id": books["customer"], "date": DAY, "lines": _sale_lines()}


def _sales_receipt(books):
    return {
        "customer_id": books["customer"],
        "date": DAY,
        "method": "cash",
        "deposit_to_account_id": books["undeposited"],
        "lines": _sale_lines(),
    }


def _purchase_order(books):
    lines = [{"description": "Lumber", "quantity": 1, "rate": 40}]
    return {"vendor_id": books["vendor"], "date": DAY, "lines": lines}


def _vendor_credit(books):
    return {"vendor_id": books["vendor"], "date": DAY, "lines": _purchase_lines(books)}


def _journal(books):
    return {
        "date": DAY,
        "description": "Owner paid for the sign",
        "lines": [
            {"account_id": books["expense"], "debit": "40", "credit": "0"},
            {"account_id": books["checking"], "debit": "0", "credit": "40"},
        ],
    }


def _register(books):
    return {
        "account_id": books["checking"],
        "date": DAY,
        "amount": "-40",
        "payee": "Ridge Supply",
        "category_account_id": books["expense"],
    }


DOCUMENTS = {
    "invoice": (
        "/api/invoices",
        _invoice,
        "invoice",
        lambda body: body["id"],
        {"customer_id": 999999},
        Invoice,
    ),
    "estimate": (
        "/api/estimates",
        _estimate,
        "estimate",
        lambda body: body["id"],
        {"customer_id": 999999},
        Estimate,
    ),
    "sales_receipt": (
        "/api/sales-receipts",
        _sales_receipt,
        "invoice",
        lambda body: body["invoice"]["id"],
        {"deposit_to_account_id": 999999},
        Invoice,
    ),
    "purchase_order": (
        "/api/purchase-orders",
        _purchase_order,
        "purchase_order",
        lambda body: body["id"],
        {"vendor_id": 999999},
        PurchaseOrder,
    ),
    "vendor_credit": (
        "/api/vendor-credits",
        _vendor_credit,
        "vendor_credit",
        lambda body: body["id"],
        {"vendor_id": 999999},
        VendorCredit,
    ),
    "journal_entry": (
        "/api/journal",
        _journal,
        "journal_entry",
        lambda body: body["id"],
        {"lines": []},
        Transaction,
    ),
    "register_entry": (
        "/api/banking/transactions",
        _register,
        "bank_entry",
        lambda body: body["id"],
        {"category_account_id": 999999},
        Transaction,
    ),
}


def _create(client, books, kind, pending=None, **over):
    path, body_of, *_ = DOCUMENTS[kind]
    body = body_of(books)
    if pending is not None:
        body["pending_attachments"] = pending
    body.update(over)
    return client.post(path, json=body)


def _attached(client, entity_type, entity_id):
    r = client.get(f"/api/attachments/{entity_type}/{entity_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _pending_tokens(db_session):
    db_session.expire_all()
    return [
        r.token
        for r in db_session.query(StoredFile)
        .filter(StoredFile.kind == KIND_PENDING_ATTACHMENT)
        .all()
    ]


@pytest.mark.parametrize("kind", sorted(DOCUMENTS))
def test_the_save_attaches_the_files_held_for_it(client, db_session, books, kind):
    first = _hold(client, name="receipt.pdf", data=b"%PDF first 881")
    second = _hold(client, name="photo.pdf", data=b"%PDF second 881")
    r = _create(client, books, kind, pending=[first, second])
    assert r.status_code == 201, r.text
    _, _, entity_type, id_of, _, _ = DOCUMENTS[kind]
    listed = _attached(client, entity_type, id_of(r.json()))
    assert sorted(a["filename"] for a in listed) == ["photo.pdf", "receipt.pdf"]
    one = next(a for a in listed if a["filename"] == "receipt.pdf")
    down = client.get(f"/api/attachments/download/{one['id']}")
    assert down.status_code == 200 and down.content == b"%PDF first 881"
    # no longer waiting, and the same bytes (no copy)
    assert _pending_tokens(db_session) == []
    assert db_session.query(StoredFile).count() == 2


@pytest.mark.parametrize("kind", sorted(DOCUMENTS))
def test_a_save_with_no_files_is_as_before(client, books, kind):
    assert _create(client, books, kind).status_code == 201
    assert _create(client, books, kind, pending=[]).status_code == 201


@pytest.mark.parametrize("kind", sorted(DOCUMENTS))
def test_cancel_discards_and_the_save_then_refuses_the_id(
    client, db_session, books, kind
):
    held = _hold(client)
    # Cancel, x or Escape: the form takes each file off (PendingFiles.discard)
    r = client.delete(f"/api/attachments/pending/{held}")
    assert r.status_code == 200, r.text
    assert _pending_tokens(db_session) == []
    table = DOCUMENTS[kind][5]
    before = db_session.query(table).count()
    r = _create(client, books, kind, pending=[held])
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(table).count() == before
    assert db_session.query(Attachment).count() == 0


@pytest.mark.parametrize("kind", sorted(DOCUMENTS))
def test_a_refused_save_keeps_the_files_for_the_retry(client, db_session, books, kind):
    held = _hold(client)
    _, _, entity_type, id_of, refused, _ = DOCUMENTS[kind]
    r = _create(client, books, kind, pending=[held], **refused)
    assert 400 <= r.status_code < 500, r.text
    assert _pending_tokens(db_session) == [held]
    assert db_session.query(Attachment).count() == 0
    r = _create(client, books, kind, pending=[held])
    assert r.status_code == 201, r.text
    assert len(_attached(client, entity_type, id_of(r.json()))) == 1
    assert _pending_tokens(db_session) == []


@pytest.mark.parametrize("kind", sorted(DOCUMENTS))
def test_a_stale_id_is_refused_and_nothing_is_posted(client, db_session, books, kind):
    table = DOCUMENTS[kind][5]
    before = db_session.query(table).count()
    for bad in ("0" * 32, "not-an-id"):
        r = _create(client, books, kind, pending=[bad])
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(table).count() == before


def test_a_file_saved_with_one_document_is_not_bound_to_another(
    client, db_session, books
):
    held = _hold(client)
    assert _create(client, books, "estimate", pending=[held]).status_code == 201
    r = _create(client, books, "purchase_order", pending=[held])
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(PurchaseOrder).count() == 0
    assert db_session.query(Attachment).count() == 1


def test_a_sales_receipt_whose_payment_fails_keeps_its_files(
    client, db_session, books, monkeypatch
):
    """The receipt's invoice is written before its payment; a payment that
    fails voids that invoice, and the files stay on the form."""
    from app.routes import sales_receipts

    def refuse(*a, **k):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="payment refused")

    monkeypatch.setattr(sales_receipts, "create_payment", refuse)
    held = _hold(client)
    r = _create(client, books, "sales_receipt", pending=[held])
    assert r.status_code == 400, r.text
    assert _pending_tokens(db_session) == [held]
    assert db_session.query(Attachment).count() == 0


def test_a_transfer_from_the_register_takes_no_files(client, db_session, books):
    held = _hold(client)
    before = db_session.query(Transaction).count()
    r = _create(
        client,
        books,
        "register_entry",
        pending=[held],
        category_account_id=books["savings"],
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TRANSFER_TAKES_NO_FILES
    db_session.expire_all()
    assert db_session.query(Transaction).count() == before
    assert _pending_tokens(db_session) == [held]
    # without files the transfer posts as before
    r = _create(client, books, "register_entry", category_account_id=books["savings"])
    assert r.status_code == 201, r.text


def test_a_replaced_vendor_credit_takes_the_files_on_its_form(client, books):
    old = _create(client, books, "vendor_credit").json()
    held = _hold(client)
    body = _vendor_credit(books)
    body["pending_attachments"] = [held]
    r = client.post(f"/api/vendor-credits/{old['id']}/replace", json=body)
    assert r.status_code in (200, 201), r.text
    new_id = r.json()["id"]
    assert [a["filename"] for a in _attached(client, "vendor_credit", new_id)] == [
        "receipt.pdf"
    ]
    assert _attached(client, "vendor_credit", old["id"]) == []


# --- the window --------------------------------------------------------------

# (file, create call): each new-document form shows the strip, sends the
# ids on its create, and clears them once the create has answered.
FORMS = (
    ("invoices.js", "API.post('/invoices'"),
    ("estimates.js", "API.post('/estimates'"),
    ("sales_receipts.js", "API.post('/sales-receipts'"),
    ("purchase_orders.js", "API.post('/purchase-orders'"),
    ("vendor_credits.js", "API.post('/vendor-credits'"),
    ("journal.js", "API.post('/journal'"),
    ("banking.js", "API.post('/banking/transactions'"),
    ("app.js", "API.post('/invoices'"),
    ("bill_quick_entry.js", "API.post('/bills'"),
)


@pytest.mark.parametrize("name,create", FORMS)
def test_each_new_document_form_shows_the_strip(name, create):
    js = (JS / name).read_text(encoding="utf-8")
    assert "PendingFiles.reset();" in js, name
    assert "PendingFiles.stripHtml()" in js, name
    assert (
        "pending_attachments: PendingFiles.ids()" in js
        or "pending_attachments = PendingFiles.ids()" in js
    ), name
    assert js.index("PendingFiles.saved();") > js.index(create), name


def test_a_dialog_form_discards_on_close_and_a_page_form_on_leaving():
    for name in (
        "invoices.js",
        "estimates.js",
        "sales_receipts.js",
        "purchase_orders.js",
        "vendor_credits.js",
        "journal.js",
        "banking.js",
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert "onClose: () => PendingFiles.discard()" in js, name
    app_js = (JS / "app.js").read_text(encoding="utf-8")
    for route in ("'/quick-entry':", "'/bill-entry':"):
        line = next(ln for ln in app_js.splitlines() if route in ln)
        assert "mount: () => () => PendingFiles.discard()" in line, route


def test_an_existing_document_keeps_its_own_attachments_panel():
    # the strip is on a new document only
    for name, guard in (
        ("invoices.js", "${id ? '' : PendingFiles.stripHtml()}"),
        ("estimates.js", "${id ? '' : PendingFiles.stripHtml()}"),
        ("purchase_orders.js", "${id ? '' : PendingFiles.stripHtml()}"),
        ("banking.js", "${entry ? '' : PendingFiles.stripHtml()}"),
        (
            "journal.js",
            "${entry || (App.isAccountant && App.isAccountant()) ? '' : "
            "PendingFiles.stripHtml()}",
        ),
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert guard in js, name
    # and a saved one's view lists its files, under its own record type
    for name, entity in (
        ("estimates.js", "estimate"),
        ("purchase_orders.js", "purchase_order"),
        ("vendor_credits.js", "vendor_credit"),
        ("sales_receipts.js", "invoice"),
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert f"InvoicesPage.attachmentsPanelHtml('{entity}'" in js, name
        assert f"InvoicesPage.loadAttachments('{entity}'" in js, name
