"""An operator can shrink a chart of accounts they did not choose (#139).

Reported by tresero, arriving from hledger with their own chart. A new
company is seeded with 57 accounts and, before this, **none of them could be
removed or hidden**:

  * `delete_account` refused anything carrying `is_system`, and all 57 carry
    it — so delete was refused for every seeded account, forever, even on a
    company with no transactions;
  * the Chart of Accounts page had no deactivate control at all, so the
    "Deactivate it instead" advice in our own delete error was something the
    interface could not do.

That is the same wrong flag 2.10.2 found hiding the Edit button, in a second
place. The gate is the control-account registry: sixteen numbers the posting
code resolves literally and a document that cannot find one is #119. Those
can be renamed, never removed. The other forty-two are ordinary accounts.
"""

import pytest

from app.models.accounts import Account
from app.services import control_accounts


def _seeded_ordinary(db):
    """A seeded account that is NOT a control account — the case that was
    refused for the wrong reason."""
    acct = (
        db.query(Account)
        .filter(Account.is_system.is_(True))
        .filter(Account.account_number.notin_(list(control_accounts.CONTROL_ACCOUNTS)))
        .first()
    )
    assert acct is not None, "no seeded non-control account to test with"
    return acct


def test_a_seeded_account_with_no_history_can_be_deleted(
    client, db_session, seed_accounts
):
    acct = _seeded_ordinary(db_session)
    assert acct.is_system is True, "the point of this test is that is_system is set"
    number = acct.account_number

    r = client.delete(f"/api/accounts/{acct.id}")
    assert r.status_code == 200, r.text
    assert (
        db_session.query(Account).filter(Account.account_number == number).first()
        is None
    )


def test_a_control_account_is_refused_and_told_why(client, db_session, seed_accounts):
    ar = seed_accounts["1100"]
    r = client.delete(f"/api/accounts/{ar.id}")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "1100" in detail and "control account" in detail
    # It must say what it IS good for, and what you can do instead.
    assert "rename" in detail.lower()
    assert db_session.query(Account).filter(Account.id == ar.id).first() is not None


@pytest.mark.parametrize("number", sorted(control_accounts.CONTROL_ACCOUNTS))
def test_every_control_number_is_protected(client, db_session, seed_accounts, number):
    """The registry is the gate, so every number in it must be refused —
    not just the famous ones."""
    acct = db_session.query(Account).filter(Account.account_number == number).first()
    if acct is None:
        pytest.skip(f"{number} is created on demand, not seeded")
    assert client.delete(f"/api/accounts/{acct.id}").status_code == 400


def test_an_account_with_postings_is_refused_with_its_count(
    client, db_session, seed_accounts
):
    """Unchanged behaviour, re-pinned on an ordinary account: history anchors
    to the account, so it can be hidden but never removed."""
    acct = _seeded_ordinary(db_session)
    vendor = client.post("/api/vendors", json={"name": "Probe Supply"}).json()
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-05-01",
            "vendor_id": vendor["id"],
            "expense_account_id": acct.id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "42.00",
        },
    )
    assert r.status_code == 201, r.text

    r = client.delete(f"/api/accounts/{acct.id}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "posted transaction line" in detail
    assert "Deactivate it instead" in detail


def test_an_account_in_use_elsewhere_names_where(client, db_session, seed_accounts):
    """A reference that is not a posting — an item's income account. The old
    message said only 'referenced by other records', which does not tell the
    operator where to go and undo it."""
    # a seeded income account: an item's account is never a bank account (#1038)
    acct = (
        db_session.query(Account)
        .filter(Account.is_system.is_(True))
        .filter(Account.account_number.notin_(list(control_accounts.CONTROL_ACCOUNTS)))
        .filter(Account.account_type == "income")
        .first()
    )
    assert acct is not None, "no seeded income account to test with"
    r = client.post(
        "/api/items",
        json={
            "name": "Widget",
            "item_type": "service",
            "rate": "10.00",
            "income_account_id": acct.id,
        },
    )
    assert r.status_code in (200, 201), r.text

    r = client.delete(f"/api/accounts/{acct.id}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "items" in detail, detail
    assert "deactivate" in detail.lower()


def test_the_reference_scan_is_derived_from_the_schema_not_a_list():
    """Thirty-five columns across seventeen models reference accounts.id and
    the count grows with every feature — vendor credits added one this week.
    A hand-kept list would be wrong within a release, and the symptom of it
    being wrong is a foreign-key violation surfacing as a 500."""
    from app.database import Base

    referencing = {
        table.name
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "accounts"
    }
    # A sample from opposite ends of the app; the scan walks all of them.
    for expected in ("items", "transaction_lines", "vendor_credit_lines"):
        assert expected in referencing


def test_deactivating_hides_an_account_without_deleting_it(
    client, db_session, seed_accounts
):
    """The answer for anything that cannot be deleted, including all sixteen
    control accounts. It was supported by the API and had no control on the
    page, which is why the delete error's advice was unfollowable."""
    acct = _seeded_ordinary(db_session)
    r = client.put(f"/api/accounts/{acct.id}", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False

    db_session.expire_all()
    assert db_session.query(Account).filter(Account.id == acct.id).first() is not None

    r = client.put(f"/api/accounts/{acct.id}", json={"is_active": True})
    assert r.status_code == 200 and r.json()["is_active"] is True


def test_a_control_account_can_be_deactivated_and_renamed(
    client, db_session, seed_accounts
):
    """What the reporter is actually offered for the sixteen: they stay, but
    they can carry his names and drop out of the pickers."""
    ar = seed_accounts["1100"]
    assert (
        client.put(f"/api/accounts/{ar.id}", json={"name": "Trade debtors"}).status_code
        == 200
    )
    assert (
        client.put(f"/api/accounts/{ar.id}", json={"is_active": False}).status_code
        == 200
    )
    db_session.expire_all()
    got = db_session.query(Account).filter(Account.id == ar.id).first()
    assert got.name == "Trade debtors" and got.account_number == "1100"


def test_the_page_offers_deactivate_reactivate_and_delete():
    """The gap was never only the rule — there was no control on the page.
    Guarded at the source for the same reason as the clipboard and CSS
    checks: the defect lives in code no Python test can execute."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/app.js").read_text(
        encoding="utf-8"
    )
    assert "setAccountActive(" in js
    assert "deleteAccount(" in js
    # These assert the call EXISTS, which is what let the 2.12.0 gate's HIGH
    # through: the Delete handler was in the source and the markup around it
    # did not parse, so the button never fired. A test of a string is not a
    # test of a document — see tests/test_onclick_attributes_parse.py.
    assert (
        "JSON.stringify"
        not in js.split("async renderAccounts() {")[1].split("saveAccount")[0]
    ), "a stringified value in the accounts row markup again"
    assert "API.del(" in js, "API exposes del(), not delete()"
    # A deactivated account must stay reachable, or deactivating is one-way.
    assert "_showInactiveAccounts" in js
    assert "toggleInactiveAccounts" in js
    # Never offer Delete on a control account; the server refuses it anyway.
    assert "a.is_control ? '' :" in js
