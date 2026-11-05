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
from decimal import Decimal
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
    # the employee list's names and contact details come in (#711)
    assert qbo.classify_filename("Employees.xlsx") == "employees"
    # read nothing from: the ledger repeats the journal by account
    assert qbo.classify_filename("General_ledger.xlsx") == "skip"
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
    # a sub-account is named by its leaf and knows its parent; its full
    # Parent:Child path is what the Journal and the Trial Balance write, and
    # the statements join the parent's name to the leaf (NEW-203)
    assert by_name["Facebook"]["parent"] == "Advertising"
    assert by_name["Facebook"]["path"] == "Advertising:Facebook"
    assert by_name["Referral Fees"]["parent"] == "Commission Income"
    assert by_name["Referral Fees"]["path"] == "Commission Income:Referral Fees"
    assert not any(":" in a["name"] for a in accounts)
    # a parent with no activity of its own is a bare heading over its children
    assert by_name["Promotional"]["type"] == AccountType.EXPENSE
    assert by_name["Flyers"]["parent"] == "Promotional"
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
    already = {a["path"].lower() for a in parsed} & seeded
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
    facebook = db_session.query(Account).filter(Account.name == "Facebook").one()
    advertising = db_session.query(Account).filter(Account.name == "Advertising").one()
    assert facebook.parent_id == advertising.id
    assert facebook.account_number is None
    visa = db_session.query(Account).filter(Account.name == "Company Visa").one()
    assert (
        visa.bank_kind == "credit_card" and visa.account_type == AccountType.LIABILITY
    )
    postcards = db_session.query(Account).filter(Account.name == "Postcards").one()
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


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_GHOST = "Ghost Holdings:Mystery Account (777)"


def _payroll_files(parent, *, listed=True, ghost=None, packed="loose"):
    """A small export: the Balance Sheet lists Federal Unemployment (940)
    indented under ``parent`` (unless ``listed`` is False), the Trial
    Balance writes it "Payroll Liabilities:Federal Unemployment (940)" with
    a 100.00 credit, and the journal never posts to it, so its balance is
    an opening balance. ``ghost`` adds a Trial Balance row for an account
    whose parent is in neither report, with that credit. ``packed`` is
    "loose" (four workbooks) or "zip" (them in one zip)."""
    footer = "Tuesday, Nov 03, 2026 09:00:00 AM GMT-6 - Accrual Basis"
    child = [["               Federal Unemployment (940)", "=100.00"]] if listed else []
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
        *child,
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
    checking = 150 + (ghost or 0)
    ghost_row = [] if ghost is None else [[_GHOST, "", f"={ghost:.2f}"]]
    trial_balance = [
        ["Invented Payroll Co"],
        ["Trial Balance"],
        ["", "Debit", "Credit"],
        ["Payroll Checking", f"={checking:.2f}"],
        ["Payroll Liabilities:Federal Unemployment (940)", "", "=100.00"],
        *ghost_row,
        ["Payroll Service Fees", "", "=50.00"],
        ["TOTAL", f"={checking:.2f}", f"={checking:.2f}"],
        [""],
        [footer],
    ]
    books = [
        (name, _sheet(rows))
        for name, rows in (
            ("Balance_sheet.xlsx", balance_sheet),
            ("Profit_and_loss.xlsx", profit_and_loss),
            ("Journal.xlsx", journal),
            ("Trial_balance.xlsx", trial_balance),
        )
    ]
    if packed == "zip":
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            for name, data in books:
                archive.writestr(name, data)
        return lambda: [
            (
                "files",
                (
                    "Invented Payroll Co.zip",
                    io.BytesIO(zipped.getvalue()),
                    "application/zip",
                ),
            )
        ]
    return lambda: [("files", (n, io.BytesIO(d), _XLSX)) for n, d in books]


def _opening_lands_on(db_session, child):
    opening = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .one()
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=opening.id).all()
    assert len(lines) == 2
    assert sum(ln.debit for ln in lines) == sum(ln.credit for ln in lines) == 100
    assert {ln.account_id for ln in lines if ln.credit == 100} == {child.id}


def _planned(warnings):
    return [w for w in warnings if "is on the trial balance but not in the chart" in w]


@pytest.mark.parametrize("packed", ["loose", "zip"])
@pytest.mark.parametrize(
    "parent", ["Payroll Liabilities", "Payroll Liabilities (2100)"]
)
def test_a_sub_account_the_two_files_spell_differently_carries_its_opening_balance(
    client, db_session, seed_accounts, parent, packed
):
    # GATE case A. The Balance Sheet lists the child indented under its
    # parent, the Trial Balance writes it Parent:Child with a "(940)"
    # suffix: one account. The import used to drop it and post a short
    # opening entry, answered as HTTP 500 (#690).
    files = _payroll_files(parent, packed=packed)
    data = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert data["ok"] is True, data["errors"]
    assert len(data["opening_balances"]) == 2
    assert _planned(data["warnings"]) == []  # the chart has it: nothing planned
    resp = client.post("/api/migration/qbo_export/import", files=files())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data["errors"]

    # named by its leaf under its parent (NEW-203), one account for both
    # spellings
    child = (
        db_session.query(Account)
        .filter(Account.name == "Federal Unemployment (940)")
        .one()
    )
    assert db_session.get(Account, child.parent_id).name == parent
    assert (
        db_session.query(Account)
        .filter(Account.name.like("%Federal Unemployment%"))
        .count()
        == 1
    )
    _opening_lands_on(db_session, child)


@pytest.mark.parametrize("packed", ["loose", "zip"])
def test_a_trial_balance_sub_account_the_reports_leave_off_is_planned_with_one_note(
    client, db_session, seed_accounts, packed
):
    # GATE case B (W-108): the Balance Sheet does not list the sub-account.
    # It is planned under its parent and said so, in the engine's words,
    # once; it used to be placed with no word.
    files = _payroll_files("Payroll Liabilities", listed=False, packed=packed)
    data = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert data["ok"] is True, data["errors"]
    assert _planned(data["warnings"]) == [
        "Account 'Payroll Liabilities:Federal Unemployment (940)' is on the trial "
        "balance but not in the chart files; it will be created as a sub-account "
        "of 'Payroll Liabilities' (liability) to carry its opening balance"
    ], data["warnings"]
    resp = client.post("/api/migration/qbo_export/import", files=files())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert len(_planned(data["warnings"])) == 1, data["warnings"]
    child = (
        db_session.query(Account)
        .filter(Account.name.like("%Federal Unemployment%"))
        .one()
    )
    assert child.name == "Federal Unemployment (940)"
    assert child.account_type == AccountType.LIABILITY
    assert db_session.get(Account, child.parent_id).name == "Payroll Liabilities"
    _opening_lands_on(db_session, child)


@pytest.mark.parametrize("packed", ["loose", "zip"])
def test_a_balance_on_an_account_with_no_known_parent_refuses_the_import(
    client, db_session, seed_accounts, packed
):
    # GATE case C (W-108): the parent is in neither report and the Trial
    # Balance carries a balance on it. It used to be typed from its
    # neighbours and made a top-level account; it is named and refused.
    files = _payroll_files("Payroll Liabilities", ghost=100, packed=packed)
    data = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert data["ok"] is False
    named = [e for e in data["errors"] if _GHOST in e]
    assert len(named) == 1 and "chart files do not list" in named[0], data["errors"]
    assert not any(_GHOST in w for w in data["warnings"]), data["warnings"]
    accounts = db_session.query(Account).count()
    resp = client.post("/api/migration/qbo_export/import", files=files())
    assert resp.status_code == 422, resp.text
    assert _GHOST in resp.json()["detail"]
    assert db_session.query(Account).count() == accounts
    assert db_session.query(Transaction).count() == 0


@pytest.mark.parametrize("packed", ["loose", "zip"])
def test_a_zero_row_with_no_known_parent_is_typed_by_its_neighbours(
    client, db_session, seed_accounts, packed
):
    # the same account at 0.00: a zero row the reports leave off, placed by
    # its Trial Balance neighbour (2.27.0's rule), with its note
    files = _payroll_files("Payroll Liabilities", ghost=0, packed=packed)
    data = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert data["ok"] is True, data["errors"]
    assert any(
        _GHOST in w and "liability account" in w for w in data["warnings"]
    ), data["warnings"]
    resp = client.post("/api/migration/qbo_export/import", files=files())
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True, resp.json()["errors"]
    ghost = db_session.query(Account).filter(Account.name == _GHOST).one()
    assert ghost.account_type == AccountType.LIABILITY and ghost.parent_id is None


def test_the_statements_name_an_imported_sub_account_once(
    client, db_session, seed_accounts
):
    # NEW-203: the export named the sub-account "Payroll Liabilities:Federal
    # Unemployment (940)" and set its parent, so the statements, which join
    # the parents' names to the account's, printed the parent twice
    files = _payroll_files("Payroll Liabilities")
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    once = "Payroll Liabilities: Federal Unemployment (940)"
    tb = client.get(
        "/api/reports/trial-balance?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    paths = [row["account_path"] for row in tb["items"]]
    assert once in paths, paths
    assert not any("Payroll Liabilities: Payroll Liabilities" in p for p in paths)
    bs = client.get("/api/reports/balance-sheet?as_of_date=2026-12-31").json()
    rows = [row["account_path"] for row in bs["liabilities"]]
    assert once in rows, rows
    assert not any("Payroll Liabilities: Payroll Liabilities" in r for r in rows)


def test_a_leaf_name_under_another_parent_is_another_account(
    client, db_session, seed_accounts
):
    # the books already have a top-level "Federal Unemployment (940)": the
    # export's, under Payroll Liabilities, is a second account, and the
    # Trial Balance's "Payroll Liabilities:Federal Unemployment (940)" lands
    # on it, not on the top-level one (NEW-203)
    other = Account(name="Federal Unemployment (940)", account_type=AccountType.EXPENSE)
    db_session.add(other)
    db_session.commit()
    files = _payroll_files("Payroll Liabilities")
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    both = (
        db_session.query(Account)
        .filter(Account.name == "Federal Unemployment (940)")
        .all()
    )
    assert len(both) == 2
    child = next(a for a in both if a.id != other.id)
    assert db_session.get(Account, child.parent_id).name == "Payroll Liabilities"
    _opening_lands_on(db_session, child)


# ── the controls, the carried invoice at its total, the employees ─────────
# (#704, #711): a small export in the workbook layout, with an A/P account,
# an invoice of 1,240.00 with 500.00 paid on it, its A/R Aging Detail row,
# and the Employee Contact List.

_FOOTER = "Tuesday, Nov 03, 2026 09:00:00 AM GMT-6 - Accrual Basis"


def _controls_export(*, aging=True, journal_invoice=True):
    """The export as loose workbooks. ``aging`` adds the A/R Aging Detail
    report (invoice 1001 open for 740.00 of 1,240.00); ``journal_invoice``
    False numbers the Journal's invoice 999, so the Journal does not hold
    the report's invoice."""
    num = "1001" if journal_invoice else "999"
    balance_sheet = [
        ["Invented Landscaping Co"],
        ["Balance Sheet"],
        ["", "Total"],
        ["ASSETS"],
        ["   Current Assets"],
        ["      Bank Accounts"],
        ["         Checking", "=200.00"],
        ["      Total Bank Accounts", "=200.00"],
        ["      Accounts Receivable"],
        ["         Accounts Receivable (A/R)", "=740.00"],
        ["      Total Accounts Receivable", "=740.00"],
        ["TOTAL ASSETS", "=940.00"],
        ["LIABILITIES AND EQUITY"],
        ["   Liabilities"],
        ["      Current Liabilities"],
        ["         Accounts Payable"],
        ["            Accounts Payable (A/P)", "=300.00"],
        ["         Total Accounts Payable", "=300.00"],
        ["   Equity"],
        ["      Net Income", "=640.00"],
        ["TOTAL LIABILITIES AND EQUITY", "=940.00"],
        [""],
        [_FOOTER],
    ]
    profit_and_loss = [
        ["Invented Landscaping Co"],
        ["Profit and Loss"],
        ["", "Total"],
        ["Income"],
        ["   Installation", "=1240.00"],
        ["Total Income", "=1240.00"],
        ["Expenses"],
        ["   Job Materials", "=600.00"],
        ["Total Expenses", "=600.00"],
        ["Net Income", "=640.00"],
        [""],
        [_FOOTER],
    ]
    head = [
        "",
        "Date",
        "Transaction Type",
        "Num",
        "Name",
        "Memo/Description",
        "Account",
        "Debit",
        "Credit",
    ]
    journal = [
        ["", "Invented Landscaping Co"],
        ["", "Journal"],
        head,
        ["", "08/01/2026", "Expense", "", "Brookside", "Stone", "Job Materials"]
        + ["=300.00"],
        ["", "", "", "", "", "Stone", "Checking", "", "=300.00"],
        ["", "", "", "", "", "", "", "=300.00", "=300.00"],
        [""],
        ["", "08/06/2026", "Bill", "BN-1", "Brookside", "Plants", "Job Materials"]
        + ["=300.00"],
        ["", "", "", "", "", "Plants", "Accounts Payable (A/P)", "", "=300.00"],
        ["", "", "", "", "", "", "", "=300.00", "=300.00"],
        [""],
        ["", "08/14/2026", "Invoice", num, "Walt Okafor", "Install"]
        + ["Accounts Receivable (A/R)", "=1240.00"],
        ["", "", "", "", "", "Labor", "Installation", "", "=520.00"],
        ["", "", "", "", "", "Materials", "Installation", "", "=720.00"],
        ["", "", "", "", "", "", "", "=1240.00", "=1240.00"],
        [""],
        ["", "08/24/2026", "Payment", "2474", "Walt Okafor", "Payment"]
        + ["Checking", "=500.00"],
        ["", "", "", "", "", "Payment", "Accounts Receivable (A/R)", "", "=500.00"],
        ["", "", "", "", "", "", "", "=500.00", "=500.00"],
        [""],
    ]
    trial_balance = [
        ["Invented Landscaping Co"],
        ["Trial Balance"],
        ["", "Debit", "Credit"],
        ["Checking", "=200.00"],
        ["Accounts Receivable (A/R)", "=740.00"],
        ["Accounts Payable (A/P)", "", "=300.00"],
        ["Installation", "", "=1240.00"],
        ["Job Materials", "=600.00"],
        ["TOTAL", "=1540.00", "=1540.00"],
        [""],
        [_FOOTER],
    ]
    customers = [
        ["", "Invented Landscaping Co"],
        ["", "Customer Contact List"],
        ["", "Customer", "Phone Numbers", "Email", "Full Name", "Billing Address"],
        ["", "Walt Okafor", "Mobile: (815) 555-0182", "walt@example.com", "Walt"],
    ]
    vendors = [
        ["", "Invented Landscaping Co"],
        ["", "Vendor Contact List"],
        ["", "Vendor", "Phone Numbers", "Email", "Full Name", "Address"],
        ["", "Brookside", "Phone: (815) 555-0100", "orders@example.com", ""],
    ]
    employees = [
        ["", "Invented Landscaping Co"],
        ["", "Employee Contact List"],
        [""],
        ["", "Employee", "Phone Numbers", "Email", "Address"],
        ["", "Evan Pruitt", "Mobile: (815) 555-0191", "evan@example.com"]
        + ["312 Sycamore Street\nCedar Hollow IL 60999"],
        ["", "Luis Arredondo", "Mobile: (815) 555-0194", "luis@example.com"]
        + ["48 Prairie View Lane\nCedar Hollow IL 60999"],
        ["", "Dana Whitfield", "", "dana@example.com", ""],
    ]
    aging_report = [
        ["Invented Landscaping Co"],
        ["A/R Aging Detail Report"],
        ["As of October 31, 2026"],
        [""],
        ["", "Date", "Transaction type", "Num", "Customer full name", "Due date"]
        + ["Amount", "Open balance"],
        ["61 - 90 days past due"],
        ["", "08/14/2026", "Invoice", "1001", "Walt Okafor", "08/29/2026"]
        + ["1240", "740"],
        ["Total for 61 - 90 days past due", "", "", "", "", "", "=G7", "=H7"],
        ["TOTAL", "", "", "", "", "", "=G8", "=H8"],
    ]
    books = [
        ("Balance_sheet.xlsx", balance_sheet),
        ("Profit_and_loss.xlsx", profit_and_loss),
        ("Journal.xlsx", journal),
        ("Trial_balance.xlsx", trial_balance),
        ("Customers.xlsx", customers),
        ("Vendors.xlsx", vendors),
        ("Employees.xlsx", employees),
    ]
    if aging:
        books.append(("Invented_A_R_Aging_Detail_Report.xlsx", aging_report))
    made = [(name, _sheet(rows)) for name, rows in books]
    return lambda: [("files", (n, io.BytesIO(d), _XLSX)) for n, d in made]


def _account_named(client, *fragments):
    return [
        a
        for a in client.get("/api/accounts").json()
        if any(f in a["name"].lower() for f in fragments)
    ]


@pytest.mark.parametrize("aging", [True, False])
def test_the_payable_account_lands_on_the_ap_control(
    client, db_session, seed_accounts, aging
):
    # #704: QuickBooks' "Accounts Payable (A/P)" is 2000 here: its balance
    # lands on the control, which keeps its type, and no second account is
    # made beside it
    files = _controls_export(aging=aging)
    dry = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert any(
        "Accounts Payable (A/P) is 2000 Accounts Payable here" in w
        for w in dry["warnings"]
    ), dry["warnings"]
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    (payable,) = [
        a for a in _account_named(client, "payable") if Decimal(str(a["balance"]))
    ]
    assert payable["account_number"] == "2000" and payable["is_control"] is True
    assert payable["account_type"] == "liability"
    assert payable["detail_type"] == "accounts_payable"
    assert Decimal(str(payable["balance"])) == Decimal("300.00")
    assert (
        db_session.query(Account).filter_by(name="Accounts Payable (A/P)").count() == 0
    )
    # a bill entered now posts to the same account
    vendor = db_session.query(Vendor).filter(Vendor.name == "Brookside").one()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "date": "2026-11-04",
            "lines": [
                {
                    "description": "Mulch",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": 50,
                    "line_order": 0,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    (after,) = [
        a for a in _account_named(client, "payable") if Decimal(str(a["balance"]))
    ]
    assert after["id"] == payable["id"]
    assert Decimal(str(after["balance"])) == Decimal("350.00")


def test_no_empty_receivable_is_left_beside_1100(client, db_session, seed_accounts):
    # #704: the report carries all the Journal leaves on "Accounts
    # Receivable (A/R)", so that is 1100 here and no empty account is made
    files = _controls_export()
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    receivables = _account_named(client, "receivable")
    assert [(a["account_number"], Decimal(str(a["balance"]))) for a in receivables] == [
        ("1100", Decimal("740.00"))
    ]
    assert receivables[0]["is_control"] is True


def test_an_open_invoice_the_journal_holds_is_carried_at_its_total(
    client, db_session, seed_accounts
):
    # #711: invoice 1001 was 1,240.00 with 500.00 paid; it reads so, with
    # the payment applied, and A/R holds the 740.00 open, as before
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment

    files = _controls_export()
    dry = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert dry["open_invoices"] == 1
    assert any(
        "1 of the open invoices is carried at its total from the Journal" in w
        for w in dry["warnings"]
    ), dry["warnings"]
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    invoice = db_session.query(Invoice).filter(Invoice.invoice_number == "1001").one()
    assert invoice.total == Decimal("1240.00")
    assert invoice.amount_paid == Decimal("500.00")
    assert invoice.balance_due == Decimal("740.00")
    assert invoice.status == InvoiceStatus.PARTIAL
    assert invoice.date.isoformat() == "2026-08-14"
    (allocation,) = invoice.payment_allocations
    payment = allocation.payment
    assert allocation.amount == Decimal("500.00")
    assert payment.amount == Decimal("500.00")
    assert payment.date.isoformat() == "2026-08-24" and payment.reference == "2474"
    assert db_session.query(Payment).count() == 1
    shown = client.get(f"/api/invoices/{invoice.id}").json()
    assert Decimal(str(shown["total"])) == Decimal("1240.00")
    assert Decimal(str(shown["amount_paid"])) == Decimal("500.00")
    assert Decimal(str(shown["balance_due"])) == Decimal("740.00")
    (ar,) = _account_named(client, "receivable")
    assert Decimal(str(ar["balance"])) == Decimal("740.00")
    aging = client.get("/api/reports/ar-aging", params={"as_of_date": "2026-10-31"})
    assert Decimal(str(aging.json()["totals"]["total"])) == Decimal("740.00")
    # the pseudo-item stays on it and out of the pickers
    item = invoice.lines[0].item
    assert item.name == qbo.CARRY_ITEM and item.is_active is False
    picker = client.get("/api/items", params={"active_only": "true"}).json()
    assert qbo.CARRY_ITEM not in [i["name"] for i in picker]
    # a second upload carries nothing twice
    again = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert again["ok"] is True, again["errors"]
    assert again["imported_open_invoices"] == 0
    assert db_session.query(Payment).count() == 1


def test_without_the_journals_invoice_the_single_line_shape_stays(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice

    files = _controls_export(journal_invoice=False)
    dry = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert not any("carried at" in w for w in dry["warnings"]), dry["warnings"]
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    invoice = db_session.query(Invoice).filter(Invoice.invoice_number == "1001").one()
    assert invoice.total == Decimal("740.00") and invoice.amount_paid == 0
    (line,) = invoice.lines
    assert line.description == (
        "QuickBooks Online invoice 1001 of 08/14/2026 for 1,240.00, 740.00 of it open"
    )
    assert invoice.payment_allocations == []


def test_the_employee_list_comes_in_as_employees(client, db_session, seed_accounts):
    # #711: name, email and address; nothing about pay; one already on the
    # Employees list by name is left as it is
    from app.models.payroll import Employee

    db_session.add(Employee(first_name="Dana", last_name="Whitfield", is_active=True))
    db_session.commit()
    files = _controls_export(aging=False)
    dry = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert dry["employees"] == 2
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_employees"] == 2
    evan = db_session.query(Employee).filter(Employee.first_name == "Evan").one()
    assert evan.last_name == "Pruitt" and evan.email == "evan@example.com"
    assert evan.address1 == "312 Sycamore Street"
    assert (evan.city, evan.state, evan.zip) == ("Cedar Hollow", "IL", "60999")
    assert evan.pay_rate in (None, 0) and evan.ssn_last_four is None
    assert db_session.query(Employee).count() == 3
    again = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert again["imported_employees"] == 0
    assert db_session.query(Employee).count() == 3
