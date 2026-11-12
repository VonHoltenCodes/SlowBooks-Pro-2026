"""Attachments on a new bill, expense or card charge before it is saved
(#796).

A file attached on the form before the first save is held (POST
/api/attachments/pending, a stored file of kind pending_attachment) and
named on the create call (`pending_attachments`): the document and its
paper trail arrive in the same step. A form abandoned without saving binds
nothing and its files age out after a day; a stale id is refused with a
sentence and nothing is posted; a pending file is the sign-in's that
uploaded it; the roles are the create's (read-only and accountant are
refused; a bookkeeper may)."""

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import _role_refusal, app
from app.models.attachments import Attachment
from app.models.bills import Bill
from app.models.contacts import Vendor
from app.models.stored_files import KIND_PENDING_ATTACHMENT, StoredFile
from app.models.transactions import Transaction
from app.models.users import User
from app.services import auth as auth_service
from app.services import pending_attachments

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
DAY = "2026-09-01"
USER_PW = "long-enough-pw-796"


def _pdf(name="receipt.pdf", data=b"%PDF-1.4 receipt 796"):
    return {"file": (name, io.BytesIO(data), "application/pdf")}


def _hold(client, **kw):
    r = client.post("/api/attachments/pending", files=_pdf(**kw))
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(name="vendor")
def vendor_fixture(db_session, seed_accounts):
    v = Vendor(
        name="Ridge Supply",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v


def _bill(client, vendor, seed_accounts, pending=None, **over):
    body = {
        "vendor_id": vendor.id,
        "date": DAY,
        "lines": [
            {
                "description": "Lumber",
                "quantity": 1,
                "rate": 40,
                "account_id": seed_accounts["6000"].id,
            }
        ],
    }
    if pending is not None:
        body["pending_attachments"] = pending
    body.update(over)
    return client.post("/api/bills", json=body)


def _expense(client, seed_accounts, pending=None, **over):
    body = {
        "date": DAY,
        "payee": "Ridge Supply",
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "40.00",
        "expense_account_id": seed_accounts["6000"].id,
    }
    if pending is not None:
        body["pending_attachments"] = pending
    body.update(over)
    return client.post("/api/expenses", json=body)


def _charge(client, seed_accounts, pending=None, **over):
    body = {
        "date": DAY,
        "payee": "Fuel",
        "amount": "40",
        "account_id": seed_accounts["6000"].id,
    }
    if pending is not None:
        body["pending_attachments"] = pending
    body.update(over)
    return client.post("/api/cc-charges", json=body)


def _attached(client, entity_type, entity_id):
    r = client.get(f"/api/attachments/{entity_type}/{entity_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _pending_rows(db_session):
    db_session.expire_all()
    return (
        db_session.query(StoredFile)
        .filter(StoredFile.kind == KIND_PENDING_ATTACHMENT)
        .all()
    )


# --- upload, then save binds -------------------------------------------------


def test_a_file_is_held_until_the_form_saves(client, db_session):
    held = _hold(client)
    assert len(held["id"]) == 32 and held["filename"] == "receipt.pdf"
    assert held["file_size"] == len(b"%PDF-1.4 receipt 796")
    assert held["mime_type"] == "application/pdf"
    expires = datetime.fromisoformat(held["expires_at"])
    left = expires - datetime.now(timezone.utc)
    assert timedelta(hours=23) < left <= timedelta(hours=24)
    # held, not attached to anything
    assert db_session.query(Attachment).count() == 0
    assert [r.token for r in _pending_rows(db_session)] == [held["id"]]


def test_saving_a_bill_attaches_its_files(client, db_session, vendor, seed_accounts):
    a = _hold(client, name="invoice.pdf", data=b"%PDF bill one")
    b = _hold(client, name="photo.pdf", data=b"%PDF bill two")
    r = _bill(client, vendor, seed_accounts, pending=[a["id"], b["id"]])
    assert r.status_code == 201, r.text
    bill_id = r.json()["id"]
    listed = _attached(client, "bill", bill_id)
    assert sorted(x["filename"] for x in listed) == ["invoice.pdf", "photo.pdf"]
    one = next(x for x in listed if x["filename"] == "invoice.pdf")
    down = client.get(f"/api/attachments/download/{one['id']}")
    assert down.status_code == 200 and down.content == b"%PDF bill one"
    # bound: no longer pending, and the same bytes (no copy)
    assert _pending_rows(db_session) == []
    assert db_session.query(StoredFile).count() == 2


def test_saving_an_expense_attaches_its_file(client, db_session, seed_accounts):
    held = _hold(client)
    r = _expense(client, seed_accounts, pending=[held["id"]])
    assert r.status_code == 201, r.text
    listed = _attached(client, "expense", r.json()["id"])
    assert [x["filename"] for x in listed] == ["receipt.pdf"]
    assert _pending_rows(db_session) == []


def test_saving_a_card_charge_attaches_its_file(client, db_session, seed_accounts):
    held = _hold(client)
    r = _charge(client, seed_accounts, pending=[held["id"]])
    assert r.status_code == 201, r.text
    charge_id = r.json()["transaction_id"]
    listed = _attached(client, "cc_charge", charge_id)
    assert [x["filename"] for x in listed] == ["receipt.pdf"]
    down = client.get(f"/api/attachments/download/{listed[0]['id']}")
    assert down.status_code == 200 and down.content == b"%PDF-1.4 receipt 796"
    assert _pending_rows(db_session) == []


def test_a_saved_card_charge_takes_a_file_too(client, seed_accounts):
    charge = _charge(client, seed_accounts).json()
    r = client.post(
        f"/api/attachments/cc_charge/{charge['transaction_id']}", files=_pdf()
    )
    assert r.status_code == 201, r.text
    assert r.json()["entity_type"] == "cc_charge"
    # an expense is a transaction, but not a card charge
    exp = _expense(client, seed_accounts).json()
    r = client.post(f"/api/attachments/cc_charge/{exp['id']}", files=_pdf())
    assert r.status_code == 404 and r.json()["detail"] == "Card charge not found"


def test_a_save_with_no_files_is_as_before(client, vendor, seed_accounts):
    for r in (
        _bill(client, vendor, seed_accounts),
        _expense(client, seed_accounts),
        _charge(client, seed_accounts),
        _bill(client, vendor, seed_accounts, pending=[], bill_number="B-2"),
    ):
        assert r.status_code == 201, r.text


def test_a_replaced_expense_takes_the_files_on_its_form(client, seed_accounts):
    old = _expense(client, seed_accounts).json()
    held = _hold(client)
    body = {
        "date": DAY,
        "payee": "Ridge Supply",
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "41.00",
        "expense_account_id": seed_accounts["6000"].id,
        "pending_attachments": [held["id"]],
    }
    r = client.post(f"/api/expenses/{old['id']}/replace", json=body)
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert [x["filename"] for x in _attached(client, "expense", new_id)] == [
        "receipt.pdf"
    ]
    assert _attached(client, "expense", old["id"]) == []


# --- abandon, discard, stale -------------------------------------------------


def _age(db_session, pending_id, hours):
    row = db_session.query(StoredFile).filter(StoredFile.token == pending_id).one()
    row.created_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db_session.commit()


def test_an_abandoned_form_binds_nothing_and_its_files_age_out(
    client, db_session, seed_accounts
):
    old = _hold(client, name="abandoned.pdf")
    _age(db_session, old["id"], 25)
    # an expired file is refused on a save, and nothing is posted
    before = db_session.query(Transaction).count()
    r = _expense(client, seed_accounts, pending=[old["id"]])
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(Transaction).count() == before
    # the next upload sweeps it
    fresh = _hold(client, name="fresh.pdf")
    assert [r.token for r in _pending_rows(db_session)] == [fresh["id"]]
    assert db_session.query(Attachment).count() == 0


def test_a_file_a_day_old_is_still_waiting(client, db_session, seed_accounts):
    held = _hold(client)
    _age(db_session, held["id"], 23)
    _hold(client, name="other.pdf")  # a sweep runs and keeps it
    r = _expense(client, seed_accounts, pending=[held["id"]])
    assert r.status_code == 201, r.text


def test_a_file_taken_off_the_form_is_gone(client, db_session, seed_accounts):
    held = _hold(client)
    r = client.delete(f"/api/attachments/pending/{held['id']}")
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    assert _pending_rows(db_session) == []
    # idempotent, and a nonsense id is no error either
    assert client.delete(f"/api/attachments/pending/{held['id']}").status_code == 200
    assert client.delete("/api/attachments/pending/nonsense").status_code == 200
    r = _charge(client, seed_accounts, pending=[held["id"]])
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.STALE


def test_a_stale_id_is_refused_and_nothing_is_posted(
    client, db_session, vendor, seed_accounts
):
    for bad in ("0" * 32, "not-an-id", ""):
        r = _bill(client, vendor, seed_accounts, pending=[bad])
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(Bill).count() == 0


def test_a_file_already_saved_with_a_document_is_not_bound_again(
    client, db_session, vendor, seed_accounts
):
    held = _hold(client)
    assert _expense(client, seed_accounts, pending=[held["id"]]).status_code == 201
    r = _bill(client, vendor, seed_accounts, pending=[held["id"]])
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.STALE
    db_session.expire_all()
    assert db_session.query(Bill).count() == 0
    assert db_session.query(Attachment).count() == 1


def test_a_refused_save_keeps_the_files_for_the_retry(
    client, db_session, vendor, seed_accounts
):
    held = _hold(client)
    r = _bill(client, vendor, seed_accounts, pending=[held["id"]], vendor_id=999999)
    assert r.status_code == 404
    assert [r.token for r in _pending_rows(db_session)] == [held["id"]]
    r = _bill(client, vendor, seed_accounts, pending=[held["id"]])
    assert r.status_code == 201, r.text
    assert len(_attached(client, "bill", r.json()["id"])) == 1


def test_one_id_named_twice_attaches_once(client, seed_accounts):
    held = _hold(client)
    r = _expense(client, seed_accounts, pending=[held["id"], held["id"]])
    assert r.status_code == 201, r.text
    assert len(_attached(client, "expense", r.json()["id"])) == 1


def test_a_form_takes_a_form_s_worth_of_files(client, seed_accounts):
    ids = [_hold(client, name=f"r{n}.pdf")["id"] for n in range(21)]
    r = _expense(client, seed_accounts, pending=ids)
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.TOO_MANY
    r = _expense(client, seed_accounts, pending=ids[:20])
    assert r.status_code == 201, r.text


def test_a_refused_file_says_so_as_before(client, db_session):
    r = client.post(
        "/api/attachments/pending",
        files={"file": ("tool.exe", io.BytesIO(b"MZ"), "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "File extension '.exe' not allowed"
    r = client.post(
        "/api/attachments/pending",
        files={"file": ("page.pdf", io.BytesIO(b"<html>"), "text/html")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "MIME type 'text/html' not allowed"
    assert _pending_rows(db_session) == []


# --- whose a pending file is, and the roles ----------------------------------


def _users(db_session):
    for role in ("readonly", "accountant", "bookkeeper"):
        db_session.add(
            User(
                username=role + "-796",
                display_name=role.title(),
                password_hash=auth_service.hash_password(USER_PW),
                role=role,
                is_active=True,
            )
        )
    db_session.commit()


def _signed_in(role):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": role + "-796", "password": USER_PW})
    assert r.status_code == 200, r.text
    return c


def test_read_only_and_accountant_cannot_hold_a_file(client, db_session):
    _users(db_session)
    for role in ("readonly", "accountant"):
        r = _signed_in(role).post("/api/attachments/pending", files=_pdf())
        assert r.status_code == 403, (role, r.text)
        # the sentence the role is refused every write with, as today
        assert r.json()["detail"] == _role_refusal(
            role, "POST", "/api/attachments/pending"
        )
    assert _pending_rows(db_session) == []


def test_a_bookkeeper_attaches_before_saving(client, db_session, seed_accounts):
    _users(db_session)
    keeper = _signed_in("bookkeeper")
    held = keeper.post("/api/attachments/pending", files=_pdf()).json()
    r = _charge(keeper, seed_accounts, pending=[held["id"]])
    assert r.status_code == 201, r.text
    assert len(_attached(keeper, "cc_charge", r.json()["transaction_id"])) == 1


def test_a_pending_file_is_the_sign_in_s_that_held_it(
    client, db_session, seed_accounts
):
    _users(db_session)
    held = _hold(client)  # the administrator's
    keeper = _signed_in("bookkeeper")
    r = _expense(keeper, seed_accounts, pending=[held["id"]])
    assert r.status_code == 400
    assert r.json()["detail"] == pending_attachments.STALE
    # nor can the bookkeeper discard it
    assert keeper.delete(f"/api/attachments/pending/{held['id']}").status_code == 200
    assert [r.token for r in _pending_rows(db_session)] == [held["id"]]
    # the administrator still can save with it
    assert _expense(client, seed_accounts, pending=[held["id"]]).status_code == 201


def test_an_api_token_attaches_before_saving(client, db_session, seed_accounts):
    r = client.post("/api/tokens", json={"label": "agent-796", "role": "bookkeeper"})
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    agent = TestClient(app)
    r = agent.post("/api/attachments/pending", files=_pdf(), headers=headers)
    assert r.status_code == 201, r.text
    pending_id = r.json()["id"]
    # the administrator's sign-in is not the token's
    r = _expense(client, seed_accounts, pending=[pending_id])
    assert r.status_code == 400
    body = {
        "date": DAY,
        "payee": "Fuel",
        "amount": "40",
        "account_id": seed_accounts["6000"].id,
        "pending_attachments": [pending_id],
    }
    r = agent.post("/api/cc-charges", json=body, headers=headers)
    assert r.status_code == 201, r.text
    listed = agent.get(
        f"/api/attachments/cc_charge/{r.json()['transaction_id']}", headers=headers
    ).json()
    assert [x["filename"] for x in listed] == ["receipt.pdf"]


# --- the window --------------------------------------------------------------


def test_the_new_document_forms_show_the_attachments_strip():
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "const PendingFiles = {" in utils
    assert "fetch('/api/attachments/pending', { method: 'POST', body: fd })" in utils
    for name, page in (
        ("bills.js", "BillsPage"),
        ("expenses.js", "ExpensesPage"),
        ("cc_charges.js", "CCChargesPage"),
    ):
        js = (JS / name).read_text(encoding="utf-8")
        assert "PendingFiles.stripHtml()" in js, name
        assert "pending_attachments: PendingFiles.ids()" in js, name
        assert "PendingFiles.saved();" in js, name
        assert "onClose: () => PendingFiles.discard()" in js, name
    cc = (JS / "cc_charges.js").read_text(encoding="utf-8")
    assert "API.get(`/attachments/cc_charge/${id}`)" in cc
    app_js = (JS / "app.js").read_text(encoding="utf-8")
    assert (
        "'CCChargesPage.uploadAttachment', 'CCChargesPage.deleteAttachment'" in app_js
    )
