"""Account schema hardening: NULL balances and blank account numbers.

- accounts.balance is nullable in practice (legacy/imported rows); the
  response schema must coalesce NULL to 0 instead of 500ing on read.
- accounts.account_number is unique; a blank string must normalize to
  NULL or the second no-number account collides with the first. Since the
  2.17.3 exploratory test (W-L4) the form and the API refuse a blank or
  non-numeric number outright, with a sentence rather than a 500; an
  account an importer left without a number can still be renamed.
"""

from sqlalchemy import text

from app.models.accounts import Account, AccountType


def test_null_balance_account_reads_as_zero(client, db_session):
    account = Account(name="Legacy NULL balance", account_type=AccountType.EXPENSE)
    db_session.add(account)
    db_session.commit()
    db_session.execute(
        text("UPDATE accounts SET balance = NULL WHERE id = :id"),
        {"id": account.id},
    )
    db_session.commit()

    resp = client.get(f"/api/accounts/{account.id}")
    assert resp.status_code == 200
    assert resp.json()["balance"] == "0"


def test_blank_account_number_normalizes_to_null():
    from app.schemas.accounts import AccountCreate, AccountUpdate

    # the schema still reads a blank as "no number"...
    assert (
        AccountCreate(name="x", account_type="expense", account_number="  ")
    ).account_number is None
    assert AccountUpdate(account_number="").account_number is None


def test_a_new_account_without_a_number_is_refused_with_a_sentence(client):
    # ...and the route refuses it, rather than storing a second NULL or
    # reaching the unique constraint
    for blank in ("", "   "):
        r = client.post(
            "/api/accounts",
            json={
                "name": "No number",
                "account_type": "expense",
                "account_number": blank,
            },
        )
        assert r.status_code == 400, r.text
        assert "Give the account a number" in r.json()["detail"]


def test_removing_a_number_on_update_is_refused(client):
    created = client.post(
        "/api/accounts",
        json={
            "name": "Numbered",
            "account_type": "expense",
            "account_number": "9998",
        },
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    updated = client.put(f"/api/accounts/{account_id}", json={"account_number": ""})
    assert updated.status_code == 400
    assert client.get(f"/api/accounts/{account_id}").json()["account_number"] == "9998"


def test_an_imported_account_without_a_number_can_still_be_renamed(client, db_session):
    account = Account(name="From hledger", account_type=AccountType.EXPENSE)
    db_session.add(account)
    db_session.commit()
    r = client.put(
        f"/api/accounts/{account.id}",
        json={"name": "Renamed", "account_number": "", "account_type": "expense"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed" and r.json()["account_number"] is None
