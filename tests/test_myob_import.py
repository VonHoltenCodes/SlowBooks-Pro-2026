"""MYOB import: AccountRight-style tab-separated exports — header-account
skipping, dd/mm/yyyy dates, ID No. journal grouping, number-only GL rows
resolved through the chart, accounting-negatives — same dry-run contract
as the Xero importer."""

import io

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine

# Tab-separated, like AccountRight's classic .TXT exports. Includes a
# non-postable Header row that must be skipped.
COA_TXT = (
    "Account Number\tAccount Name\tHeader\tAccount Type\tDescription\n"
    "1-0000\tAssets\tH\tAsset\t\n"
    "1-1100\tCheque Account\t\tBank\tMain operating account\n"
    "4-1000\tSales Income\t\tIncome\t\n"
    "6-2000\tAdvertising\t\tExpense\t\n"
)

# Journal lines grouped by ID No.; dd/mm/yyyy dates; one row carries the
# account NUMBER only; one amount uses accounting parentheses.
GL_TXT = (
    "ID No.\tDate\tMemo\tAccount Number\tAccount Name\tDebit Amount\tCredit Amount\n"
    'GJ000001\t03/01/2026\tInvoice paid\t1-1100\tCheque Account\t"1,650.00"\t\n'
    'GJ000001\t03/01/2026\tInvoice paid\t4-1000\t\t\t"1,650.00"\n'
    "GJ000002\t05/01/2026\tAd spend\t6-2000\tAdvertising\t220.00\t\n"
    "GJ000002\t05/01/2026\tAd spend\t1-1100\tCheque Account\t($220.00)\t\n"
)

TB_TXT = (
    "Account Name\tDebit\tCredit\n"
    'Cheque Account\t"1,430.00"\t\n'
    'Sales Income\t\t"1,650.00"\n'
    "Advertising\t220.00\t\n"
)


def _files(**named):
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/plain"))
        for name, text in named.items()
    ]


def test_dry_run_passes_and_mutates_nothing(client, db_session, seed_accounts):
    before = db_session.query(Account).count()
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(
            **{
                "AccountsList.TXT": COA_TXT,
                "GeneralJournal.TXT": GL_TXT,
                "TrialBalance.TXT": TB_TXT,
            }
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data
    # Header row skipped: 3 postable accounts, not 4
    assert data["accounts"] == 3
    assert data["journals"] == 2
    assert db_session.query(Account).count() == before


def test_number_only_gl_row_resolves_through_chart(client, seed_accounts):
    """The GJ000001 credit row carries only account number 4-1000 — it
    must resolve to Sales Income via the bundled chart."""
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(**{"accounts.txt": COA_TXT, "journal.txt": GL_TXT}),
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]


def test_unknown_account_number_flagged(client, seed_accounts):
    bad_gl = GL_TXT.replace("4-1000", "9-9999")
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(**{"accounts.txt": COA_TXT, "journal.txt": bad_gl}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("9-9999" in e for e in data["errors"])


def test_dry_run_flags_unbalanced_journal(client, seed_accounts):
    bad_gl = GL_TXT.replace('"1,650.00"\t\n', '"1,600.00"\t\n', 1)
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(**{"accounts.txt": COA_TXT, "journal.txt": bad_gl}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("unbalanced" in e.lower() for e in data["errors"])


def test_dry_run_flags_tb_mismatch(client, seed_accounts):
    bad_tb = TB_TXT.replace('"1,430.00"', '"1,400.00"')
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(
            **{"accounts.txt": COA_TXT, "journal.txt": GL_TXT, "trial.txt": bad_tb}
        ),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("mismatch" in e.lower() for e in data["errors"])


def test_import_creates_accounts_and_journals(client, db_session, seed_accounts):
    resp = client.post(
        "/api/migration/myob/import",
        files=_files(
            **{
                "accounts.txt": COA_TXT,
                "journal.txt": GL_TXT,
                "trial.txt": TB_TXT,
            }
        ),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_accounts"] == 3
    assert data["imported_journals"] == 2

    cheque = db_session.query(Account).filter(Account.name == "Cheque Account").one()
    assert cheque.account_type == AccountType.ASSET
    assert cheque.account_number == "1-1100"
    # Header account never created
    assert db_session.query(Account).filter(Account.name == "Assets").count() == 0

    journals = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "myob_import")
        .all()
    )
    assert len(journals) == 2
    for txn in journals:
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)
    # dd/mm/yyyy parsed as 3 January, not 1 March
    dates = sorted(t.date.isoformat() for t in journals)
    assert dates == ["2026-01-03", "2026-01-05"]

    status = client.get("/api/opening-balances/status").json()
    assert status["chart_setup_source"] == "myob_import"


def test_import_refuses_when_dry_run_fails(client, db_session, seed_accounts):
    before = db_session.query(Transaction).count()
    bad_gl = GL_TXT.replace('"1,650.00"\t\n', '"1,600.00"\t\n', 1)
    resp = client.post(
        "/api/migration/myob/import",
        files=_files(**{"accounts.txt": COA_TXT, "journal.txt": bad_gl}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert data["imported_journals"] == 0
    assert db_session.query(Transaction).count() == before


# ── Behaviors discovered validating against MYOB's real Clearwater
# sample exports (see PR #36) ────────────────────────────────────────────

CW_COA = (
    "Account Number\tAccount Name\tHeader\tBalance\tAccount Type\n"
    "11100\tGeneral Cheque Account 1\t\t$0.00\tBank\n"
    "31000\tHistorical Balancing\t\t$0.00\tEquity\n"
    "41000\tSales\t\t$0.00\tIncome\n"
    "63100\tWages & Salaries\t\t$0.00\tExpense\n"
    "65100\tWages & Salaries\t\t$0.00\tExpense\n"
)

# "EP" reused across two dates (electronic payments) — must split into
# two journals, not merge into one.
CW_GL = (
    "Journal Number\tDate\tMemo\tAccount Number\tDebit Amount\tCredit Amount\n"
    "EP\t5/07/2010\tPaycheque\t65100\t$500.00\t\n"
    "EP\t5/07/2010\tPaycheque\t11100\t\t$500.00\n"
    "EP\t19/07/2010\tPaycheque\t63100\t$400.00\t\n"
    "EP\t19/07/2010\tPaycheque\t11100\t\t$400.00\n"
    "16\t20/07/2010\tCash sale\t11100\t$1,000.00\t\n"
    "16\t20/07/2010\tCash sale\t41000\t\t$1,000.00\n"
)

# Report-style trial balance: address preamble, period/YTD columns, and
# opening balances (cheque +2000 offset by Historical Balancing -2000)
# that are NOT in the journals.
CW_TB = (
    "\n"
    "Clearwater Pty Ltd\n"
    "25 Spring Street\n"
    "\n"
    "Trial Balance\n"
    "June 2011\n"
    "\tAccount\tDebit\tCredit\tYTD Debit\tYTD Credit\n"
    "\n"
    '\tGeneral Cheque Account 1\t$0.00\t\t"$2,100.00"\t\n'
    '\tHistorical Balancing\t$0.00\t\t\t"$2,000.00"\n'
    '\tSales\t\t$0.00\t\t"$1,000.00"\n'
    "\tWages & Salaries\t$0.00\t\t$500.00\t\n"
    "\tWages & Salaries\t$0.00\t\t$400.00\t\n"
    '\tTotal:\t\t\t"$3,000.00"\t"$3,000.00"\n'
)


def test_clearwater_shapes_end_to_end(client, db_session, seed_accounts):
    resp = client.post(
        "/api/migration/myob/import",
        files=_files(
            **{"accounts.txt": CW_COA, "journal.txt": CW_GL, "trial.txt": CW_TB}
        ),
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]

    # EP split by date + the numbered journal = 3, not 2
    journals = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "myob_import")
        .all()
    )
    assert len(journals) == 3

    # duplicate names disambiguated into two real accounts
    wages = {
        a.name
        for a in db_session.query(Account)
        .filter(Account.name.like("Wages & Salaries (%"))
        .all()
    }
    # (the seeded chart's own plain "Wages & Salaries" also exists)
    assert wages == {"Wages & Salaries (63100)", "Wages & Salaries (65100)"}

    # opening balances synthesized from the TB residuals (net zero)
    assert any(o["amount"] == 2000.0 for o in data["opening_balances"])
    ob = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .one()
    )
    # dated before the earliest journal
    assert ob.date.isoformat() == "2010-07-04"

    # post-import: cheque account nets to the TB's YTD figure
    cheque = (
        db_session.query(Account)
        .filter(Account.name == "General Cheque Account 1")
        .one()
    )
    lines = db_session.query(TransactionLine).filter(
        TransactionLine.account_id == cheque.id
    )
    net = sum(ln.debit or 0 for ln in lines) - sum(ln.credit or 0 for ln in lines)
    from decimal import Decimal

    assert net == Decimal("2100.00")


def test_clearwater_multi_journal_files_accumulate(client, db_session, seed_accounts):
    """Per-type journal exports upload together — the second file's rows
    must accumulate, not overwrite the first."""
    header = "Journal Number\tDate\tMemo\tAccount Number\tDebit Amount\tCredit Amount\n"
    sales = header + (
        "16\t20/07/2010\tCash sale\t11100\t$1,000.00\t\n"
        "16\t20/07/2010\tCash sale\t41000\t\t$1,000.00\n"
    )
    disb = header + (
        "EP\t5/07/2010\tPaycheque\t65100\t$500.00\t\n"
        "EP\t5/07/2010\tPaycheque\t11100\t\t$500.00\n"
    )
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(
            **{
                "accounts.txt": CW_COA,
                "journal-sales.txt": sales,
                "journal-disbursements.txt": disb,
            }
        ),
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["journals"] == 2


def test_residuals_that_do_not_net_to_zero_stay_errors(client, seed_accounts):
    bad_tb = CW_TB.replace('"$2,000.00"', '"$1,500.00"')  # breaks the net
    resp = client.post(
        "/api/migration/myob/dry-run",
        files=_files(
            **{"accounts.txt": CW_COA, "journal.txt": CW_GL, "trial.txt": bad_tb}
        ),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("mismatch" in e.lower() for e in data["errors"])
