"""Generic migration route + the Sage 50, Wave, Zoho Books, and GnuCash
dialects — each exercised through /api/migration/{source} with its own
format quirks."""

import io

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine


def _files(**named):
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/plain"))
        for name, text in named.items()
    ]


def _import(client, source, **named):
    return client.post(f"/api/migration/{source}/import", files=_files(**named))


def _journals(db_session, source_type):
    return (
        db_session.query(Transaction)
        .filter(Transaction.source_type == source_type)
        .all()
    )


def _assert_balanced(db_session, journals):
    for txn in journals:
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)


# ── Registry ─────────────────────────────────────────────────────────────


def test_sources_listing(client):
    keys = {s["key"] for s in client.get("/api/migration/sources").json()}
    assert keys == {"xero", "myob", "sage", "wave", "zoho", "gnucash"}


def test_unknown_source_400(client):
    resp = client.post(
        "/api/migration/quicken/dry-run", files=_files(**{"chart.csv": "x"})
    )
    assert resp.status_code == 400


# ── Sage 50: account-ID GL rows, mm/dd dates, Equity-gets closed ─────────

SAGE_COA = (
    "Account ID,Account Description,Account Type\n"
    "10200,Regular Checking Account,Cash\n"
    "39007,Owner Contributions,Equity-gets closed\n"
    "40000,Sales,Income\n"
)

SAGE_GL = (
    "Date,Reference,Jrnl,Account ID,Trans Description,Debit Amt,Credit Amt\n"
    '03/01/2026,DEP001,CRJ,10200,Deposit,"1,000.00",\n'
    '03/01/2026,DEP001,CRJ,40000,Deposit,,"1,000.00"\n'
)


def test_sage_import(client, db_session, seed_accounts):
    resp = _import(
        client, "sage", **{"chart.csv": SAGE_COA, "generalledger.csv": SAGE_GL}
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_accounts"] == 3
    checking = (
        db_session.query(Account)
        .filter(Account.name == "Regular Checking Account")
        .one()
    )
    assert checking.account_type == AccountType.ASSET
    equity = (
        db_session.query(Account).filter(Account.name == "Owner Contributions").one()
    )
    assert equity.account_type == AccountType.EQUITY
    journals = _journals(db_session, "sage_import")
    assert len(journals) == 1
    # mm/dd/yyyy: March 1st, not January 3rd
    assert journals[0].date.isoformat() == "2026-03-01"
    _assert_balanced(db_session, journals)


# ── Wave: keyword-mapped types + signed single-amount rows ───────────────

WAVE_COA = (
    "Account Name,Account Type\n"
    "Business Chequing,Cash and Bank\n"
    "Consulting Income,Income\n"
    "Software Subscriptions,Operating Expense\n"
)

WAVE_GL = (
    "Transaction ID,Transaction Date,Account Name,Transaction Line Description,Amount (One column)\n"
    "T-1,2026-02-10,Business Chequing,Client payment,500.00\n"
    "T-1,2026-02-10,Consulting Income,Client payment,-500.00\n"
    "T-2,2026-02-12,Software Subscriptions,IDE license,40.00\n"
    "T-2,2026-02-12,Business Chequing,IDE license,-40.00\n"
)


def test_wave_import_signed_amounts(client, db_session, seed_accounts):
    resp = _import(
        client, "wave", **{"accounts.csv": WAVE_COA, "transactions.csv": WAVE_GL}
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    journals = _journals(db_session, "wave_import")
    assert len(journals) == 2
    _assert_balanced(db_session, journals)
    chequing = (
        db_session.query(Account).filter(Account.name == "Business Chequing").one()
    )
    assert chequing.account_type == AccountType.ASSET


def test_wave_unmapped_type_flagged(client, seed_accounts):
    weird = WAVE_COA + "Mystery,Completely Unknown Bucket\n"
    resp = client.post(
        "/api/migration/wave/dry-run",
        files=_files(**{"accounts.csv": weird, "transactions.csv": WAVE_GL}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("Completely Unknown Bucket" in e for e in data["errors"])


# ── Zoho Books ───────────────────────────────────────────────────────────

ZOHO_COA = (
    "Account Name,Account Type\n"
    "Petty Cash,Cash\n"
    "Sales,Income\n"
    "Office Rent,Expense\n"
)

ZOHO_GL = (
    "Journal Number,Journal Date,Account,Description,Debit,Credit\n"
    "J-1,2026-04-01,Office Rent,April rent,900.00,\n"
    "J-1,2026-04-01,Petty Cash,April rent,,900.00\n"
)


def test_zoho_import(client, db_session, seed_accounts):
    resp = _import(client, "zoho", **{"chart.csv": ZOHO_COA, "journal.csv": ZOHO_GL})
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    journals = _journals(db_session, "zoho_import")
    assert len(journals) == 1
    _assert_balanced(db_session, journals)


# ── GnuCash: full account names, placeholders, signed splits ─────────────

GNUCASH_COA = (
    "Type,Full Account Name,Name,Account Code,Description,Placeholder\n"
    "ROOT,Root Account,Root Account,,,F\n"
    "ASSET,Assets,Assets,,,T\n"
    "BANK,Assets:Checking,Checking,,,F\n"
    "INCOME,Income:Consulting,Consulting,,,F\n"
    "EXPENSE,Expenses:Rent,Rent,,,F\n"
)

GNUCASH_GL = (
    "Date,Transaction ID,Description,Full Account Name,Amount Num.\n"
    "2026-05-01,tx001,May invoice,Assets:Checking,1200.00\n"
    "2026-05-01,tx001,May invoice,Income:Consulting,-1200.00\n"
    "2026-05-02,tx002,May rent,Expenses:Rent,800.00\n"
    "2026-05-02,tx002,May rent,Assets:Checking,-800.00\n"
)


def test_gnucash_import(client, db_session, seed_accounts):
    resp = _import(
        client,
        "gnucash",
        **{"accounts.csv": GNUCASH_COA, "transactions.csv": GNUCASH_GL},
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    # ROOT + placeholder rows skipped: 3 postable accounts
    assert data["imported_accounts"] == 3
    assert (
        db_session.query(Account).filter(Account.name == "Assets:Checking").one()
    ).account_type == AccountType.ASSET
    assert db_session.query(Account).filter(Account.name == "Assets").count() == 0
    journals = _journals(db_session, "gnucash_import")
    assert len(journals) == 2
    _assert_balanced(db_session, journals)
    # no TB export in GnuCash → warning, not error
    assert any("trial balance" in w.lower() for w in data["warnings"])


# ── Readiness metadata via the generic route ─────────────────────────────


def test_generic_import_records_chart_source(client, seed_accounts):
    _import(client, "zoho", **{"chart.csv": ZOHO_COA, "journal.csv": ZOHO_GL})
    status = client.get("/api/opening-balances/status").json()
    assert status["chart_setup_source"] == "zoho_import"
