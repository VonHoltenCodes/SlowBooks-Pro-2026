"""Migrating from QuickBooks Online without the Intuit API: the Export data
bundle QuickBooks Online hands every user (Settings → Export data), as the
zip, as its workbooks, or as those saved to CSV. The fixtures in
tests/fixtures/qbo_export/ are invented books in the bundle's exact layout
(a leading blank column on the journal and the lists, the title rows, every
figure as formula text, Parent:Child sub-accounts, a totals row and a blank
row after every journal block, a memo-only row, the cash-basis footer).

The real bundle this was built against is a user's business and stays
private; nothing of it is in the tree. The sample company is Sutehk Realty
Group, named for @sutehk, whose two hours in Intuit's developer portal are
why this route exists."""

import io
import zipfile
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services import qbo_export_import as qbo

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
BUNDLE = [
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "General_ledger.csv",
    "Trial_balance.csv",
    "Customers.csv",
    "Vendors.csv",
    "Employees.csv",
]


def _csv_files(names=BUNDLE):
    return [
        ("files", (name, io.BytesIO((FIXTURES / name).read_bytes()), "text/csv"))
        for name in names
    ]


def _workbook(csv_path: Path) -> bytes:
    """The fixture as the .xlsx QuickBooks Online writes: one sheet, cells as
    the CSV has them (figures stay formula text)."""
    import csv

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            sheet.append([cell if cell != "" else None for cell in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _journals(db_session):
    return (
        db_session.query(Transaction)
        .filter(Transaction.source_type == qbo.SOURCE_TYPE)
        .order_by(Transaction.date, Transaction.id)
        .all()
    )


# ── classification ─────────────────────────────────────────────────────────


def test_the_bundles_files_are_told_apart_by_name():
    assert qbo.classify_filename("Balance_sheet.xlsx") == "coa"
    assert qbo.classify_filename("Profit_and_loss.xlsx") == "coa"
    assert qbo.classify_filename("Journal.xlsx") == "gl"
    assert qbo.classify_filename("Trial_balance.xlsx") == "tb"
    assert qbo.classify_filename("Customers.xlsx") == "customers"
    assert qbo.classify_filename("Vendors.xlsx") == "vendors"
    # read nothing from: the ledger repeats the journal by account, and
    # the employee list is payroll's, which the export does not carry
    assert qbo.classify_filename("General_ledger.xlsx") == "skip"
    assert qbo.classify_filename("Employees.xlsx") == "skip"
    assert qbo.classify_filename("holiday_photos.jpg") is None


# ── the chart, from the balance sheet and the P&L ─────────────────────────


def test_the_chart_comes_from_the_two_reports_typed_and_nested():
    text = (FIXTURES / "Balance_sheet.csv").read_text() + (
        FIXTURES / "Profit_and_loss.csv"
    ).read_text()
    accounts, errors = qbo.parse_coa(text)
    assert errors == []
    by_name = {a["name"]: a for a in accounts}
    assert by_name["Operating Account"]["type"] == AccountType.ASSET
    assert by_name["Operating Account"]["bank_kind"] == "bank"
    assert by_name["Company Visa"]["type"] == AccountType.LIABILITY
    assert by_name["Company Visa"]["bank_kind"] == "credit_card"
    assert by_name["Escrow Held"]["type"] == AccountType.LIABILITY
    assert by_name["Opening Balance Equity"]["type"] == AccountType.EQUITY
    assert by_name["Commission Income"]["type"] == AccountType.INCOME
    assert by_name["Office Supplies"]["type"] == AccountType.EXPENSE
    # a sub-account keeps QuickBooks' Parent:Child name and knows its parent
    assert by_name["Advertising:Facebook"]["parent"] == "Advertising"
    assert by_name["Commission Income:Referral Fees"]["parent"] == "Commission Income"
    # a parent with no activity of its own is a bare heading over its children
    assert by_name["Promotional"]["type"] == AccountType.EXPENSE
    assert by_name["Promotional:Flyers"]["parent"] == "Promotional"
    # report arithmetic is not an account
    for total in ("Net Income", "Gross Profit", "Net Operating Income", "TOTAL ASSETS"):
        assert total not in by_name
    assert all(a["code"] is None for a in accounts)  # QuickBooks Online has no numbers
    assert (
        len(accounts) == 14
    )  # the reports alone; the hidden sub-account comes from the books


# ── the journal ────────────────────────────────────────────────────────────


def test_every_journal_block_is_one_balanced_group():
    groups, errors = qbo.parse_gl((FIXTURES / "Journal.csv").read_text())
    assert errors == []
    assert len(groups) == 19
    for group in groups:
        assert sum(r["debit"] - r["credit"] for r in group) == 0
    first = groups[0]
    assert first[0]["date"].isoformat() == "2026-01-05"
    assert first[0]["account"] == "Operating Account"
    assert first[0]["debit"] == 1000 and first[1]["credit"] == 1000
    # the memo-only row and the item at 0.00 with no account inside the
    # invoice are not lines, and the totals row is not a line
    invoice = groups[1]
    assert [r["account"] for r in invoice] == [
        "Accounts Receivable (A/R)",
        "Commission Income",
    ]
    assert invoice[0]["description"].startswith("Invoice 1001")
    assert "Dana Reyes" in invoice[0]["description"]
    # three transfers on two days with no number still get distinct references
    refs = [g[0]["journal"] for g in groups]
    assert len(refs) == len(set(refs))


# ── the trial balance and its basis ────────────────────────────────────────


def test_the_trial_balance_reads_and_names_its_basis():
    text = (FIXTURES / "Trial_balance.csv").read_text()
    balances, errors = qbo.parse_tb(text)
    assert errors == []
    # names as written; the engine keys them as it keys the journal (#690)
    assert balances["Operating Account"] == 4010
    assert balances["Company Visa"] == -120
    assert "total" not in balances
    assert qbo.trial_balance_basis(text) == "cash"
    assert (
        qbo.trial_balance_basis(text.replace("Cash Basis", "Accrual Basis"))
        == "accrual"
    )


# ── through the route ──────────────────────────────────────────────────────


def test_the_bundle_dry_runs_with_the_cash_basis_named_and_nothing_written(
    client, db_session, seed_accounts
):
    resp = client.post("/api/migration/qbo_export/dry-run", files=_csv_files())
    data = resp.json()
    assert resp.status_code == 200, data
    assert data["ok"] is True, data["errors"]
    assert data["accounts"] == 15 and data["journals"] == 19
    assert data["customers"] == 2 and data["vendors"] == 4
    assert any("cash basis" in w.lower() for w in data["warnings"]), data["warnings"]
    assert data["opening_balances"] == []
    assert _journals(db_session) == []
    # without the A/R Aging Detail report: no counts for it, and a note says
    # what the Journal leaves on receivables and how to carry it over
    assert "open_invoices" not in data and "unapplied_payments" not in data
    assert any(
        "Journal leaves 525.00 on Accounts Receivable (A/R)" in w
        and "A/R Aging Detail" in w
        for w in data["warnings"]
    ), data["warnings"]


def test_the_bundle_imports_the_chart_the_journals_and_the_contacts(
    client, db_session, seed_accounts
):
    # names the seeded chart already has (Undeposited Funds, and whatever
    # else the seed shares with the export) are used, not doubled
    parsed, _ = qbo.parse_coa(
        (FIXTURES / "Balance_sheet.csv").read_text()
        + (FIXTURES / "Profit_and_loss.csv").read_text()
    )
    seeded = {a.name.lower() for a in db_session.query(Account).all()}
    already = {a["name"].lower() for a in parsed} & seeded
    assert "undeposited funds" in already
    resp = client.post("/api/migration/qbo_export/import", files=_csv_files())
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_accounts"] == 15 - len(already)
    assert (
        db_session.query(Account).filter(Account.name == "Undeposited Funds").count()
        == 1
    )
    assert data["imported_journals"] == 19
    assert data["imported_customers"] == 2 and data["imported_vendors"] == 4
    facebook = (
        db_session.query(Account).filter(Account.name == "Advertising:Facebook").one()
    )
    advertising = db_session.query(Account).filter(Account.name == "Advertising").one()
    assert facebook.parent_id == advertising.id
    assert facebook.account_number is None
    visa = db_session.query(Account).filter(Account.name == "Company Visa").one()
    assert (
        visa.bank_kind == "credit_card" and visa.account_type == AccountType.LIABILITY
    )
    postcards = (
        db_session.query(Account).filter(Account.name == "Advertising:Postcards").one()
    )
    assert (
        postcards.parent_id == advertising.id
    )  # hidden by the P&L, typed by its parent
    assert postcards.account_type == AccountType.EXPENSE
    journals = _journals(db_session)
    assert len(journals) == 19
    for txn in journals:
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)
    assert journals[0].date.isoformat() == "2026-01-05"
    assert journals[0].reference
    # the lists: names as QuickBooks has them, with what the list carried
    dana = db_session.query(Customer).filter(Customer.name == "Dana Reyes - CUST").one()
    assert dana.email == "dana@example.com"
    assert (
        db_session.query(Vendor).filter(Vendor.name == "Print Shop Co").one().email
        == "orders@example.com"
    )
    # the chart setup remembers where it came from
    from app.services.settings_service import get_setting_raw

    assert get_setting_raw(db_session, "chart_setup_source") == qbo.SOURCE_TYPE


def test_a_second_import_posts_nothing_twice(client, db_session, seed_accounts):
    first = client.post("/api/migration/qbo_export/import", files=_csv_files()).json()
    assert first["ok"] and first["imported_journals"] == 19
    again = client.post("/api/migration/qbo_export/import", files=_csv_files()).json()
    assert again["ok"], again["errors"]
    assert again["imported_journals"] == 0 and again["duplicate_journals"] == 19
    assert again["imported_accounts"] == 0
    assert again["imported_customers"] == 0 and again["imported_vendors"] == 0
    assert len(_journals(db_session)) == 19


def test_an_accrual_trial_balance_is_verified_against_the_journal(
    client, db_session, seed_accounts
):
    # the same Trial Balance on the accrual basis: A/R carries the open
    # invoices less the payments not applied (525.00), as the Journal does
    files = _csv_files([n for n in BUNDLE if n != "Trial_balance.csv"])
    accrual = (FIXTURES / "Trial_balance_accrual.csv").read_text()
    assert "Accrual Basis" in accrual
    files.append(
        ("files", ("Trial_balance.csv", io.BytesIO(accrual.encode()), "text/csv"))
    )
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert not any("cash basis" in w.lower() for w in data["warnings"]), data[
        "warnings"
    ]
    assert data["opening_balances"] == []


def test_the_workbooks_and_the_zip_read_the_same_as_the_csvs(
    client, db_session, seed_accounts
):
    # the bundle as QuickBooks Online hands it: one zip of .xlsx workbooks
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as archive:
        for name in BUNDLE:
            archive.writestr(name.replace(".csv", ".xlsx"), _workbook(FIXTURES / name))
        archive.writestr("__MACOSX/._Journal.xlsx", b"\x00")  # a Mac's stray
    data = client.post(
        "/api/migration/qbo_export/dry-run",
        files=[
            (
                "files",
                (
                    "Sutehk Realty Group Oct 14, 2026.zip",
                    io.BytesIO(zipped.getvalue()),
                    "application/zip",
                ),
            )
        ],
    ).json()
    assert data["ok"] is True, data["errors"]
    assert data["accounts"] == 15 and data["journals"] == 19
    # and the workbooks loose, without the zip
    files = [
        (
            "files",
            (
                name.replace(".csv", ".xlsx"),
                io.BytesIO(_workbook(FIXTURES / name)),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )
        for name in BUNDLE
    ]
    data = client.post("/api/migration/qbo_export/import", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_journals"] == 19 and data["imported_accounts"] == 13


def test_a_zip_that_is_not_one_is_refused(client, seed_accounts):
    resp = client.post(
        "/api/migration/qbo_export/dry-run",
        files=[("files", ("export.zip", io.BytesIO(b"not a zip"), "application/zip"))],
    )
    assert resp.status_code == 400 and "not a zip" in resp.json()["detail"]


def test_a_bundle_without_the_journal_says_so(client, seed_accounts):
    data = client.post(
        "/api/migration/qbo_export/dry-run",
        files=_csv_files(
            ["Balance_sheet.csv", "Profit_and_loss.csv", "Trial_balance.csv"]
        ),
    ).json()
    assert data["ok"] is False
    assert any("journal" in e.lower() for e in data["errors"]), data["errors"]


def test_the_source_is_listed(client):
    sources = client.get("/api/migration/sources").json()
    assert {"key": "qbo_export", "label": "QuickBooks Online export"} in sources


def test_an_account_the_reports_hide_is_typed_by_its_trial_balance_neighbours(
    client, seed_accounts
):
    # QuickBooks Online leaves a zero-balance row off both reports; a
    # sub-account takes its parent's type, and a top-level one the type of
    # the account listed beside it in the Trial Balance, with a note
    journal = (
        (FIXTURES / "Journal.csv")
        .read_text()
        .replace("Advertising:Postcards", "Mystery Fee")
    )
    tb = (
        (FIXTURES / "Trial_balance.csv")
        .read_text()
        .replace("Advertising:Postcards", "Mystery Fee")
    )
    files = _csv_files(
        [n for n in BUNDLE if n not in ("Journal.csv", "Trial_balance.csv")]
    )
    files.append(("files", ("Journal.csv", io.BytesIO(journal.encode()), "text/csv")))
    files.append(("files", ("Trial_balance.csv", io.BytesIO(tb.encode()), "text/csv")))
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert any(
        "Mystery Fee" in w and "expense account" in w for w in data["warnings"]
    ), data["warnings"]
    # with no Trial Balance there is nothing to place it by: named, for the person
    files = _csv_files(
        [n for n in BUNDLE if n not in ("Journal.csv", "Trial_balance.csv")]
    )
    files.append(("files", ("Journal.csv", io.BytesIO(journal.encode()), "text/csv")))
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is False
    assert any(
        "Mystery Fee" in e and "add it to the chart" in e for e in data["errors"]
    ), data["errors"]


def test_the_page_takes_a_zip_or_a_workbook():
    js = (Path(__file__).resolve().parents[1] / "app/static/js/migration.js").read_text(
        encoding="utf-8"
    )
    assert 'accept=".csv,.txt,.xlsx,.zip"' in js
    assert "upload the Export data zip as downloaded" in js


def _sheet(rows) -> bytes:
    """A small report workbook, cells as QuickBooks Online writes them."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append([cell if cell != "" else None for cell in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


@pytest.mark.parametrize(
    "parent", ["Payroll Liabilities", "Payroll Liabilities (2100)"]
)
def test_a_sub_account_the_two_files_spell_differently_carries_its_opening_balance(
    client, db_session, seed_accounts, parent
):
    # The Balance Sheet lists the child indented under its parent, the Trial
    # Balance writes it Parent:Child with a "(940)" suffix; the journal never
    # posts to it, so its balance is an opening balance. The import used to
    # drop it and post a short opening entry, answered as HTTP 500 (#690).
    footer = "Tuesday, Nov 03, 2026 09:00:00 AM GMT-6 - Accrual Basis"
    balance_sheet = [
        ["Invented Payroll Co"],
        ["Balance Sheet"],
        ["", "Total"],
        ["ASSETS"],
        ["   Current Assets"],
        ["      Bank Accounts"],
        ["         Payroll Checking", "=150.00"],
        ["      Total Bank Accounts", "=150.00"],
        ["LIABILITIES AND EQUITY"],
        ["   Liabilities"],
        ["      Current Liabilities"],
        ["         Other Current Liabilities"],
        [f"            {parent}"],
        ["               Federal Unemployment (940)", "=100.00"],
        [f"            Total {parent}", "=100.00"],
        ["   Equity"],
        ["      Net Income", "=50.00"],
        [""],
        [footer],
    ]
    profit_and_loss = [
        ["Invented Payroll Co"],
        ["Profit and Loss"],
        ["", "Total"],
        ["Income"],
        ["   Payroll Service Fees", "=50.00"],
        ["Total Income", "=50.00"],
        ["Net Income", "=50.00"],
        [""],
        [footer],
    ]
    journal = [
        ["", "Invented Payroll Co"],
        ["", "Journal"],
        [
            "",
            "Date",
            "Transaction Type",
            "Num",
            "Name",
            "Memo/Description",
            "Account",
            "Debit",
            "Credit",
        ],
        ["", "03/02/2026", "Deposit", "", "", "Fee", "Payroll Checking", "=50.00"],
        ["", "", "", "", "", "Fee", "Payroll Service Fees", "", "=50.00"],
        ["", "", "", "", "", "", "", "=50.00", "=50.00"],
        [""],
    ]
    trial_balance = [
        ["Invented Payroll Co"],
        ["Trial Balance"],
        ["", "Debit", "Credit"],
        ["Payroll Checking", "=150.00"],
        ["Payroll Liabilities:Federal Unemployment (940)", "", "=100.00"],
        ["Payroll Service Fees", "", "=50.00"],
        ["TOTAL", "=150.00", "=150.00"],
        [""],
        [footer],
    ]
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    files = [
        ("files", (name, io.BytesIO(_sheet(rows)), xlsx))
        for name, rows in (
            ("Balance_sheet.xlsx", balance_sheet),
            ("Profit_and_loss.xlsx", profit_and_loss),
            ("Journal.xlsx", journal),
            ("Trial_balance.xlsx", trial_balance),
        )
    ]
    data = client.post("/api/migration/qbo_export/dry-run", files=files).json()
    assert data["ok"] is True, data["errors"]
    assert len(data["opening_balances"]) == 2
    for _, (_, fh, _) in files:
        fh.seek(0)
    resp = client.post("/api/migration/qbo_export/import", files=files)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data["errors"]

    child = (
        db_session.query(Account)
        .filter(Account.name == f"{parent}:Federal Unemployment (940)")
        .one()
    )
    assert child.parent_id is not None
    assert db_session.get(Account, child.parent_id).name.startswith(
        "Payroll Liabilities"
    )
    # one account, not a second one for the Trial Balance's spelling
    assert (
        db_session.query(Account)
        .filter(Account.name.like("%Federal Unemployment%"))
        .count()
        == 1
    )
    opening = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .one()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=opening.id).all()
    assert len(lines) == 2
    assert sum(ln.debit for ln in lines) == sum(ln.credit for ln in lines) == 100
    assert {ln.account_id for ln in lines if ln.credit == 100} == {child.id}
