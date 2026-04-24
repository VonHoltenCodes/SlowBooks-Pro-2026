"""Sanity checks: the harness itself works before we start writing real tests."""


def test_chart_of_accounts_seeds(db_session, seed_accounts):
    from app.models.accounts import Account
    assert db_session.query(Account).count() == len(seed_accounts)
    # Accounts Receivable is the anchor for most posting tests.
    ar = next(a for a in seed_accounts.values() if a.account_number == "1100")
    assert ar.name == "Accounts Receivable"


def test_client_can_fetch_empty_list(client, seed_accounts):
    # Any GET that requires DB access proves the override is wired up.
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
