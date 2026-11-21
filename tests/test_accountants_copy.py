"""SlowBooks' own Accountant's Copy (#346): send, open, adjust, send back,
apply, and apply again. Two company files stand in for the two machines:
the owner's, and the accountant's install where the copy opens."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.accounts import Account
from app.models.transactions import Transaction
from app.services import accountants_copy as svc
from app.services import company_file
from app.services.accounting import create_journal_entry
from app.services.company_service import _init_company_db
from app.services.settings_service import get_setting_raw

pytestmark = pytest.mark.skipif(
    not company_file.available(), reason="sqlcipher3 is not installed here"
)

ROOT = Path(__file__).resolve().parents[1]


def _company(tmp_path, name):
    path = tmp_path / f"{name.lower().replace(' ', '-')}.db"
    url = "sqlite:///" + path.as_posix()
    _init_company_db(url, company_name=name)
    engine = create_engine(url)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return path, session, engine


def _accounts(db):
    return {a.account_number: a for a in db.query(Account).all() if a.account_number}


def _entry(db, accounts, day, amount, text, **kw):
    txn = create_journal_entry(
        db,
        day,
        text,
        [
            {
                "account_id": accounts["6300"].id,
                "debit": Decimal(amount),
                "credit": Decimal("0"),
            },
            {
                "account_id": accounts["2000"].id,
                "debit": Decimal("0"),
                "credit": Decimal(amount),
            },
        ],
        source_type="manual",
        **kw,
    )
    db.commit()
    return txn


def test_the_round_trip_send_adjust_send_back_apply_and_apply_again(tmp_path):
    owner_path, owner, owner_engine = _company(tmp_path, "Harbor Dist")
    accts = _accounts(owner)
    _entry(owner, accts, date(2026, 3, 5), "40.00", "Insurance paid")

    # --- send to accountant: dividing date March 31
    content, passphrase, meta = svc.make_copy(
        owner,
        date(2026, 3, 31),
        source_path=owner_path,
        source_key=None,
        company_name="Harbor Dist",
    )
    assert (
        meta["company_name"] == "Harbor Dist" and meta["dividing_date"] == "2026-03-31"
    )
    assert "token" not in meta and len(passphrase) >= 20
    assert content[:2] == b"PK"
    sent = owner.query(svc.AccountantsCopy).one()
    assert sent.copy_id == meta["copy_id"] and sent.applied == []

    # --- open accountant's copy on the other machine; the wrong passphrase is refused
    companies = tmp_path / "accountant-install"
    with pytest.raises(HTTPException) as refused:
        svc.open_copy(content, "not-the-passphrase", companies_root=companies)
    assert refused.value.status_code == 400
    assert not list(companies.glob("*.db"))
    opened = svc.open_copy(content, passphrase, companies_root=companies)
    assert opened["name"] == "Harbor Dist (accountant's copy)"
    copy_path = companies / opened["file"]
    assert copy_path.exists() and not company_file.is_encrypted_file(copy_path)
    copy_engine = create_engine("sqlite:///" + copy_path.as_posix())
    copy = sessionmaker(bind=copy_engine, autocommit=False, autoflush=False)()
    # the copy is one: it knows itself, the owner's sign-ins and secrets are gone
    m = svc.mode_of(copy)
    assert m and m["copy_id"] == meta["copy_id"] and m["dividing_date"] == "2026-03-31"
    assert get_setting_raw(copy, "auth_password_hash") is None
    assert get_setting_raw(copy, "company_name") == "Harbor Dist (accountant's copy)"
    assert copy.query(Transaction).count() == 1  # the owner's entry came along
    # the copy takes entries on or before the dividing date, not after
    with pytest.raises(HTTPException) as after:
        svc.check_entry_date(copy, date(2026, 4, 1))
    assert "on or before 2026-03-31" in after.value.detail
    svc.check_entry_date(copy, date(2026, 3, 31))
    assert svc.copy_gate("POST", "/api/journal") and svc.copy_gate(
        "GET", "/api/reports/profit-loss"
    )
    assert svc.copy_gate("POST", "/api/journal/7/void") and svc.copy_gate(
        "POST", "/api/attachments/journal_entry/7"
    )
    assert not svc.copy_gate("POST", "/api/invoices") and not svc.copy_gate(
        "PUT", "/api/settings"
    )

    # --- the accountant adjusts: two entries, one of them voided again
    caccts = _accounts(copy)
    aje = _entry(
        copy,
        caccts,
        date(2026, 3, 31),
        "120.00",
        "Accrue insurance",
        is_adjusting=True,
        posted_by="pat",
    )
    gone = _entry(copy, caccts, date(2026, 3, 30), "5.00", "Oops", posted_by="pat")
    from app.services.accounting import reversing_lines

    create_journal_entry(
        copy,
        gone.date,
        "VOID: Oops",
        reversing_lines(gone.lines),
        source_type="manual_void",
        source_id=gone.id,
    )
    copy.commit()

    # --- send changes back
    body = svc.changes(copy, accountant="Pat CPA")
    assert body["copy_id"] == meta["copy_id"] and body["accountant"] == "Pat CPA"
    refs = {e["ref"]: e for e in body["entries"]}
    assert set(refs) == {aje.id, gone.id}  # the owner's own entry is not a change
    assert refs[aje.id]["voided"] is False and refs[gone.id]["voided"] is True
    assert refs[aje.id]["lines"][0]["account_number"] == "6300"
    file_bytes = json.dumps(body).encode("utf-8")

    # --- the owner previews and applies
    seen = svc.preview(owner, file_bytes)
    assert seen["counts"] == {"new": 1, "applied": 0, "skipped": 1, "refused": 0}
    done = svc.apply(owner, file_bytes, posted_by="admin")
    assert done["counts"]["posted"] == 1
    posted = (
        owner.query(Transaction)
        .filter(Transaction.source_type == svc.SOURCE_TYPE)
        .one()
    )
    assert posted.is_adjusting is True and posted.posted_by == "accountant:Pat CPA"
    assert posted.reference == f"AC:{meta['copy_id'][:8]}:{aje.id}"
    assert posted.date == date(2026, 3, 31)
    assert {(ln.account_id, ln.debit, ln.credit) for ln in posted.lines} == {
        (accts["6300"].id, Decimal("120.00"), Decimal("0.00")),
        (accts["2000"].id, Decimal("0.00"), Decimal("120.00")),
    }
    # applied again: nothing posts twice
    again = svc.apply(owner, file_bytes)
    assert again["counts"]["posted"] == 0 and again["counts"]["applied"] == 1
    assert (
        owner.query(Transaction)
        .filter(Transaction.source_type == svc.SOURCE_TYPE)
        .count()
        == 1
    )

    # --- an altered file, and one from another copy, are refused whole
    tampered = dict(body)
    tampered["entries"] = [
        dict(e, description="Changed on the way") for e in body["entries"]
    ]
    with pytest.raises(HTTPException) as bad:
        svc.preview(owner, json.dumps(tampered).encode("utf-8"))
    assert "altered" in bad.value.detail
    foreign = dict(body, copy_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(HTTPException) as other:
        svc.preview(owner, json.dumps(foreign).encode("utf-8"))
    assert "did not make" in other.value.detail
    copy.close()
    copy_engine.dispose()
    owner.close()
    owner_engine.dispose()


def test_an_entry_dated_after_the_line_and_a_missing_account_are_refused_in_the_preview(
    tmp_path,
):
    owner_path, owner, owner_engine = _company(tmp_path, "Town of Elm")
    content, passphrase, meta = svc.make_copy(
        owner, date(2026, 6, 30), source_path=owner_path, company_name="Town of Elm"
    )
    companies = tmp_path / "accountant"
    opened = svc.open_copy(content, passphrase, companies_root=companies)
    copy_engine = create_engine("sqlite:///" + (companies / opened["file"]).as_posix())
    copy = sessionmaker(bind=copy_engine)()
    caccts = _accounts(copy)
    _entry(copy, caccts, date(2026, 6, 30), "10.00", "Fine")
    body = svc.changes(copy)
    # the file says one entry is after the line, and one names an account the owner lacks
    body["entries"][0]["date"] = "2026-07-01"
    body["entries"].append(
        dict(
            body["entries"][0],
            ref=999,
            date="2026-06-01",
            lines=[
                {
                    "account_number": "9999",
                    "account_name": "Nowhere",
                    "debit": "1.00",
                    "credit": "0.00",
                    "description": "",
                },
                {
                    "account_number": "2000",
                    "account_name": "Accounts Payable",
                    "debit": "0.00",
                    "credit": "1.00",
                    "description": "",
                },
            ],
        )
    )
    body["signature"] = svc._sign(
        svc.mode_of(copy)["token"], {k: v for k, v in body.items() if k != "signature"}
    )
    seen = svc.preview(owner, json.dumps(body).encode("utf-8"))
    assert [e["status"] for e in seen["entries"]] == ["refused", "refused"]
    assert "after the dividing date" in seen["entries"][0]["why"]
    assert "no account here for 9999 Nowhere" in seen["entries"][1]["why"]
    done = svc.apply(owner, json.dumps(body).encode("utf-8"))
    assert done["counts"]["posted"] == 0
    copy.close()
    copy_engine.dispose()
    owner.close()
    owner_engine.dispose()


def test_a_server_on_postgresql_cannot_make_a_copy(client):
    r = client.post(
        "/api/accountants-copy/make",
        json={"dividing_date": "2026-03-31", "company_name": "x"},
    )
    assert r.status_code in (400, 409)
    assert client.get("/api/accountants-copy/status").json()["is_copy"] is False


def test_the_pages_offer_the_copy_and_say_when_they_are_one():
    companies = (ROOT / "app/static/js/companies.js").read_text(encoding="utf-8")
    for call in (
        "CompaniesPage.showSendToAccountant()",
        "CompaniesPage.showOpenCopy()",
        "CompaniesPage.showApplyChanges()",
        "CompaniesPage.sendChanges()",
    ):
        assert call in companies, call
    assert "API.post('/accountants-copy/make'" in companies
    assert "fetch('/api/accountants-copy/open'" in companies
    assert "'/api/accountants-copy/changes/preview'" in companies
    assert "'/api/accountants-copy/changes/apply'" in companies
    assert "This is an accountant's copy of" in companies
    app = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "if (a.accountants_copy && !document.getElementById('ac-banner'))" in app
    assert "Send changes back</a> when they are in." in app


def test_an_applied_entry_is_a_journal_of_its_own_and_keeps_the_accountants_reference():
    """The owner's Journal Entries page lists what came back from the copy
    (it lists journals by their source, and the copy's is one), and an entry
    the accountant numbered (AJE-7) keeps that number when applied; one
    without a reference carries the copy and the entry it came from."""
    import inspect

    from app.routes.journal import _journal_sources

    assert svc.SOURCE_TYPE in _journal_sources()
    source = inspect.getsource(svc.apply)
    assert 'reference=(e.get("reference") or "").strip()' in source
    assert "AC:{seen['copy_id'][:8]}:{e['ref']}" in source
