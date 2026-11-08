"""Make Deposits' destination settings (#734, #735).

A company that banks everything through one checking account picked it on
Make Deposits every time; a company with a dozen bank accounts deposits into
two of them. "Default deposit account" (default_deposit_account_id) is the
bank account Make Deposits opens on; "Deposit into these accounts"
(deposit_account_ids) narrows the destination list. Both are company
settings, bank accounts from the chart only, and neither moves a deposit
already saved.
"""

import pytest

from app.models.contacts import Customer


@pytest.fixture
def acme(db_session):
    c = Customer(name="Acme Diner", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, **fields):
    return client.put("/api/settings", json=fields)


def _deposit_into(client, acme, account_id, amount=125.0):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": acme,
            "date": "2026-11-02",
            "amount": amount,
            "method": "Check",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    pending = client.get("/api/deposits/pending").json()
    assert pending, "the payment is not waiting to be deposited"
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": account_id,
            "date": "2026-11-03",
            "reference": "SLIP-1",
            "line_ids": [p["transaction_line_id"] for p in pending],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["transaction_id"]


# --- #734: Default deposit account -----------------------------------------


def test_the_default_deposit_account_is_blank_until_chosen(client, seed_accounts):
    assert _settings(client)["default_deposit_account_id"] == ""


def test_a_bank_account_saves_as_the_default_and_blank_clears_it(client, seed_accounts):
    savings = seed_accounts["1010"].id
    r = _put(client, default_deposit_account_id=str(savings))
    assert r.status_code == 200, r.text
    assert _settings(client)["default_deposit_account_id"] == str(savings)
    # a number from the API is stored the same way
    r = _put(client, default_deposit_account_id=savings)
    assert r.status_code == 200, r.text
    assert _settings(client)["default_deposit_account_id"] == str(savings)
    r = _put(client, default_deposit_account_id="")
    assert r.status_code == 200, r.text
    assert _settings(client)["default_deposit_account_id"] == ""


@pytest.mark.parametrize("number", ["1200", "1100", "2000", "4000"])
def test_an_account_that_is_not_a_bank_is_refused_as_the_default(
    client, seed_accounts, number
):
    acct = seed_accounts.get(number)
    if acct is None:
        pytest.skip(f"no {number} in the seeded chart")
    r = _put(client, default_deposit_account_id=str(acct.id))
    assert r.status_code == 422, r.text
    assert "must be a bank account" in r.json()["detail"]
    assert _settings(client)["default_deposit_account_id"] == ""


@pytest.mark.parametrize("bad", ["99999", "abc", "1.5"])
def test_an_id_not_in_the_chart_is_refused_as_the_default(client, seed_accounts, bad):
    r = _put(client, default_deposit_account_id=bad)
    assert r.status_code == 422, r.text
    assert _settings(client)["default_deposit_account_id"] == ""


def test_an_inactive_bank_account_is_refused_as_the_default(
    client, seed_accounts, db_session
):
    savings = seed_accounts["1010"]
    savings.is_active = False
    db_session.commit()
    r = _put(client, default_deposit_account_id=str(savings.id))
    assert r.status_code == 422, r.text
    assert "inactive" in r.json()["detail"]


def test_a_refused_default_saves_nothing_else(client, seed_accounts):
    before = _settings(client)["invoice_notes"]
    r = _put(
        client,
        invoice_notes="Changed",
        default_deposit_account_id=str(seed_accounts["1200"].id),
    )
    assert r.status_code == 422, r.text
    assert _settings(client)["invoice_notes"] == before


def test_a_saved_deposit_keeps_its_account_when_the_default_is_another(
    client, seed_accounts, acme
):
    checking = seed_accounts["1000"].id
    savings = seed_accounts["1010"].id
    dep = _deposit_into(client, acme, checking)
    r = _put(client, default_deposit_account_id=str(savings))
    assert r.status_code == 200, r.text
    assert _settings(client)["default_deposit_account_id"] == str(savings)
    r = client.put(f"/api/deposits/{dep}", json={"reference": "SLIP-1A"})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/deposits/{dep}").json()
    assert after["reference"] == "SLIP-1A"
    assert after["account_name"] == "Checking"
