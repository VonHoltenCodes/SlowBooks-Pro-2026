"""QBO account pages and Banking identities survive an accounts import."""

from types import SimpleNamespace

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_import


def _qbo_account(number, name, account_type="Expense"):
    return SimpleNamespace(
        Id=str(number),
        Name=name,
        FullyQualifiedName=name,
        AccountType=account_type,
        Active=True,
        SyncToken="0",
    )


def _mock_accounts(monkeypatch, rows, inactive=()):
    from quickbooks.objects.account import Account as QBOAccount

    calls = []

    def all_page(cls, *, qb, start_position, max_results):
        calls.append((start_position, max_results))
        return rows[start_position - 1 : start_position - 1 + max_results]

    monkeypatch.setattr(QBOAccount, "all", classmethod(all_page))
    monkeypatch.setattr(
        QBOAccount, "query", classmethod(
            lambda cls, select, *, qb: list(inactive) if "Active = false" in select else []
        ),
    )
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    return calls


def test_qbo_query_reads_later_pages(monkeypatch):
    rows = [_qbo_account(i, f"Account {i:03d}") for i in range(1, 103)]
    calls = _mock_accounts(monkeypatch, rows)

    from quickbooks.objects.account import Account as QBOAccount

    found = qbo_import._all_qbo_objects(QBOAccount, object())

    assert [row.Id for row in found] == [row.Id for row in rows]
    assert calls == [(1, 100), (101, 100)]


def test_import_accounts_exposes_banks_in_banking(db_session, monkeypatch):
    rows = [
        _qbo_account(101, "Late Checking", "Bank"),
        _qbo_account(102, "Late Visa", "Credit Card"),
    ]
    _mock_accounts(monkeypatch, rows)

    result = qbo_import.import_accounts(db_session)
    db_session.commit()

    assert result == {"imported": 2, "errors": []}
    assert db_session.query(QBOMapping).filter_by(entity_type="account").count() == 2
    bank = db_session.query(Account).filter_by(name="Late Checking").one()
    card = db_session.query(Account).filter_by(name="Late Visa").one()
    assert (bank.account_type, bank.bank_kind) == (AccountType.ASSET, "bank")
    assert (card.account_type, card.bank_kind) == (
        AccountType.LIABILITY,
        "credit_card",
    )
    assert {feed.account_id for feed in db_session.query(BankAccount).all()} == {
        bank.id,
        card.id,
    }
    assert {
        row.name
        for row in db_session.query(Account)
        .filter(Account.bank_kind.isnot(None))
        .all()
    } == {
        "Late Checking",
        "Late Visa",
    }


def test_reimport_repairs_previously_mapped_bank_without_duplicate_feed(
    db_session, monkeypatch
):
    account = Account(name="Existing Checking", account_type=AccountType.ASSET)
    db_session.add(account)
    db_session.flush()
    db_session.add(
        QBOMapping(entity_type="account", slowbooks_id=account.id, qbo_id="7")
    )
    db_session.flush()
    rows = [_qbo_account(7, "Existing Checking", "Bank")]
    _mock_accounts(monkeypatch, rows)

    first = qbo_import.import_accounts(db_session)
    second = qbo_import.import_accounts(db_session)
    db_session.flush()

    assert first == second == {"imported": 0, "errors": []}
    assert account.bank_kind == "bank"
    assert db_session.query(BankAccount).filter_by(account_id=account.id).count() == 1
    assert db_session.query(QBOMapping).filter_by(entity_type="account").count() == 1


def test_existing_name_match_gets_bank_identity(db_session, monkeypatch):
    account = Account(name="Existing Savings", account_type=AccountType.ASSET)
    db_session.add(account)
    db_session.flush()
    _mock_accounts(monkeypatch, [_qbo_account(9, account.name, "Bank")])

    result = qbo_import.import_accounts(db_session)
    db_session.flush()

    assert result == {"imported": 0, "errors": []}
    assert account.bank_kind == "bank"
    assert db_session.query(BankAccount).filter_by(account_id=account.id).count() == 1
    assert db_session.query(QBOMapping).filter_by(qbo_id="9").one().slowbooks_id == account.id


def test_imports_inactive_historical_account_without_bank_feed(db_session, monkeypatch):
    old = _qbo_account(123, "Old Bank (deleted)", "Bank")
    old.Active = False
    _mock_accounts(monkeypatch, [], inactive=[old])

    result = qbo_import.import_accounts(db_session)
    db_session.flush()

    assert result == {"imported": 1, "errors": []}
    account = db_session.query(Account).filter_by(name=old.Name).one()
    assert account.account_type == AccountType.ASSET
    assert account.is_active is False
    assert db_session.query(BankAccount).filter_by(account_id=account.id).count() == 0
    assert db_session.query(QBOMapping).filter_by(qbo_id="123").one().slowbooks_id == account.id
