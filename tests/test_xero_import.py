"""Xero import: dry-run contract (mutates nothing, blocks bad bundles)
and import execution; opening-balance wizard posting rules."""

import io

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine

COA_CSV = (
    "Code,Name,Type,Description\n"
    "090,Business Bank,Bank,Main account\n"
    "200,Sales,Revenue,\n"
    "400,Advertising,Expense,\n"
)

GL_CSV = (
    "Journal Number,Date,Account,Description,Reference,Debit,Credit\n"
    '1,01/03/2026,Business Bank,Invoice paid,INV-1,"1,150.00",\n'
    '1,01/03/2026,Sales,Invoice paid,INV-1,,"1,150.00"\n'
    "2,05/03/2026,Advertising,Ad spend,BILL-9,200.00,\n"
    "2,05/03/2026,Business Bank,Ad spend,BILL-9,,200.00\n"
)

TB_CSV = (
    "Account,Debit,Credit\n"
    "Business Bank,950.00,\n"
    "Sales,,1150.00\n"
    "Advertising,200.00,\n"
)


def _files(**named):
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/csv"))
        for name, text in named.items()
    ]


def test_dry_run_passes_and_mutates_nothing(client, db_session, seed_accounts):
    before = db_session.query(Account).count()
    resp = client.post(
        "/api/migration/xero/dry-run",
        files=_files(
            **{
                "ChartOfAccounts.csv": COA_CSV,
                "GeneralLedger.csv": GL_CSV,
                "TrialBalance.csv": TB_CSV,
            }
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data
    assert data["accounts"] == 3
    assert data["journals"] == 2
    assert db_session.query(Account).count() == before  # nothing written


def test_dry_run_flags_unbalanced_journal(client, seed_accounts):
    bad_gl = GL_CSV.replace('"1,150.00"\n', '"1,100.00"\n', 1)
    resp = client.post(
        "/api/migration/xero/dry-run",
        files=_files(**{"chart.csv": COA_CSV, "ledger.csv": bad_gl}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("unbalanced" in e.lower() for e in data["errors"])


def test_dry_run_flags_tb_mismatch(client, seed_accounts):
    bad_tb = TB_CSV.replace("950.00", "900.00")
    resp = client.post(
        "/api/migration/xero/dry-run",
        files=_files(
            **{"chart.csv": COA_CSV, "ledger.csv": GL_CSV, "trial.csv": bad_tb}
        ),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("mismatch" in e.lower() for e in data["errors"])


def test_dry_run_requires_gl_and_coa(client, seed_accounts):
    resp = client.post(
        "/api/migration/xero/dry-run", files=_files(**{"trial.csv": TB_CSV})
    )
    data = resp.json()
    assert data["ok"] is False
    assert len(data["errors"]) == 2


def test_import_creates_accounts_and_journals(client, db_session, seed_accounts):
    resp = client.post(
        "/api/migration/xero/import",
        files=_files(
            **{
                "chart.csv": COA_CSV,
                "ledger.csv": GL_CSV,
                "trial.csv": TB_CSV,
            }
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["imported_accounts"] == 3
    assert data["imported_journals"] == 2

    bank = db_session.query(Account).filter(Account.name == "Business Bank").one()
    assert bank.account_type == AccountType.ASSET

    journals = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "xero_import")
        .all()
    )
    assert len(journals) == 2
    for txn in journals:
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)

    # readiness metadata recorded for the opening-balance wizard
    status = client.get("/api/opening-balances/status").json()
    assert status["chart_setup_source"] == "xero_import"


def test_import_refuses_when_dry_run_fails(client, db_session, seed_accounts):
    before = db_session.query(Transaction).count()
    bad_gl = GL_CSV.replace('"1,150.00"\n', '"1,100.00"\n', 1)
    resp = client.post(
        "/api/migration/xero/import",
        files=_files(**{"chart.csv": COA_CSV, "ledger.csv": bad_gl}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert data["imported_journals"] == 0
    assert db_session.query(Transaction).count() == before


# ── Opening-balance wizard ───────────────────────────────────────────────


def _acct(db_session, name, acct_type):
    row = Account(name=name, account_type=acct_type)
    db_session.add(row)
    db_session.commit()
    return row


def test_opening_balances_post_balanced_entry(client, db_session, seed_accounts):
    checking = _acct(db_session, "OB Checking", AccountType.ASSET)
    loan = _acct(db_session, "OB Loan", AccountType.LIABILITY)
    equity = _acct(db_session, "OB Equity", AccountType.EQUITY)

    resp = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [
                {"account_id": checking.id, "amount": 5000},
                {"account_id": loan.id, "amount": 3000},
                {"account_id": equity.id, "amount": 2000},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .one()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert sum(ln.debit or 0 for ln in lines) == Decimal("5000.00")
    assert sum(ln.credit or 0 for ln in lines) == Decimal("5000.00")


def test_opening_balances_auto_balance(client, db_session, seed_accounts):
    checking = _acct(db_session, "OB2 Checking", AccountType.ASSET)
    equity = _acct(db_session, "OB2 Equity", AccountType.EQUITY)

    # Unbalanced without helper → 400
    unbalanced = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [{"account_id": checking.id, "amount": 750}],
        },
    )
    assert unbalanced.status_code == 400
    assert "unbalanced" in unbalanced.json()["detail"].lower()

    # With auto-balance to equity → posts, difference lands on equity
    resp = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [{"account_id": checking.id, "amount": 750}],
            "auto_balance_account_id": equity.id,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_balanced"] is True
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .order_by(Transaction.id.desc())
        .first()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert any(
        ln.account_id == equity.id and ln.credit == Decimal("750.00") for ln in lines
    )


def test_opening_balances_rejects_zero_only(client, db_session, seed_accounts):
    checking = _acct(db_session, "OB3 Checking", AccountType.ASSET)
    resp = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [{"account_id": checking.id, "amount": 0}],
        },
    )
    assert resp.status_code == 400


def test_negative_amount_inverts_side(client, db_session, seed_accounts):
    checking = _acct(db_session, "OB4 Checking", AccountType.ASSET)
    equity = _acct(db_session, "OB4 Equity", AccountType.EQUITY)
    # negative asset = credit side (e.g. overdrawn account)
    resp = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [{"account_id": checking.id, "amount": -100}],
            "auto_balance_account_id": equity.id,
        },
    )
    assert resp.status_code == 200, resp.text
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .order_by(Transaction.id.desc())
        .first()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert any(
        ln.account_id == checking.id and ln.credit == Decimal("100.00") for ln in lines
    )
