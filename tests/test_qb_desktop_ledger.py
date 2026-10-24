"""QuickBooks Desktop's Transaction Detail by Account comes across as journal
entries (2.23.4).

The report an accountant exports for "everything" (deposits, checks,
transfers, general journal entries, opening balances) is grouped by account:
a heading row per account, sub-accounts one column in, each transaction in
every account it touches with the other side in Split, and QuickBooks' own
Total row under each section. Import from Report CSV refused it as an
unrecognized layout. It now posts each transaction as one balanced journal
entry, after checking the whole file first, and refuses a file that fails
whole.

The fixture is invented books in the layout of a real export the importer
was built against (a small practice's year, every account matching the
totals QuickBooks printed); the real file stays private.
The sample company is Morden Bookkeeping, named for Kim Morden Bookkeeping,
who brought the report.
"""

import io
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.services.qb_ledger_import import SOURCE_TYPE

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "qb_desktop"
    / "Transaction_Detail_by_Account.csv"
)
ACCOUNTS = [
    ("Operating Checking 4321", AccountType.ASSET, None),
    ("Card Clearing", AccountType.ASSET, None),
    ("Owner Draw", AccountType.EQUITY, None),
    ("Retained Earnings", AccountType.EQUITY, None),
    ("Service Income", AccountType.INCOME, None),
    ("Card Fees", AccountType.EXPENSE, None),
    ("Office Expense", AccountType.EXPENSE, None),
    ("Software", AccountType.EXPENSE, "Office Expense"),
    ("Supplies", AccountType.EXPENSE, "Office Expense"),
    ("Rent", AccountType.EXPENSE, None),
]


def _chart(db):
    """The accounts the lists IIF would have made, sub-accounts under their
    parent."""
    have = {a.name.lower(): a for a in db.query(Account).all()}
    for name, kind, parent in ACCOUNTS:
        if parent is None and name.lower() in have:
            continue
        acct = Account(name=name, account_type=kind, balance=Decimal("0"))
        if parent:
            acct.parent_id = have[parent.lower()].id
        db.add(acct)
        db.flush()
        if parent is None:
            have[name.lower()] = acct
    db.commit()


def _upload(client, data: bytes):
    return client.post(
        "/api/csv/import/qb-report",
        files={
            "file": ("Transaction Detail by Account.csv", io.BytesIO(data), "text/csv")
        },
    ).json()


def _net(db, name, parent=None):
    q = db.query(Account).filter(Account.name == name)
    if parent:
        q = q.filter(
            Account.parent_id
            == db.query(Account.id).filter(Account.name == parent).scalar_subquery()
        )
    acct = q.one()
    lines = (
        db.query(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(
            Transaction.source_type == SOURCE_TYPE,
            TransactionLine.account_id == acct.id,
        )
        .all()
    )
    return sum((ln.debit or 0) - (ln.credit or 0) for ln in lines)


def test_the_report_comes_across_as_balanced_journals(
    client, db_session, seed_accounts
):
    _chart(db_session)
    data = _upload(client, FIXTURE.read_bytes())
    assert data["detected"] == "ledger"
    assert data["errors"] == [], data["errors"]
    # eight transactions: two same-day deposits told apart by name, and a
    # journal entry whose number QuickBooks printed on one line only
    assert data["journals"] == 8
    journals = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .all()
    )
    assert len(journals) == 8
    for txn in journals:
        assert sum(ln.debit or 0 for ln in txn.lines) == sum(
            ln.credit or 0 for ln in txn.lines
        )
    # each account posts what QuickBooks printed for it, sub-accounts included
    assert _net(db_session, "Operating Checking 4321") == Decimal("145.00")
    assert _net(db_session, "Card Clearing") == Decimal("175.00")
    assert _net(db_session, "Service Income") == Decimal("-620.00")
    assert _net(db_session, "Supplies", "Office Expense") == Decimal("45.00")
    assert _net(db_session, "Software", "Office Expense") == Decimal("30.00")
    assert _net(db_session, "Card Fees") == Decimal("25.00")
    check = next(t for t in journals if t.reference.startswith("Check 1001"))
    assert (
        check.date.isoformat() == "2024-01-08" and "Office Depot" in check.description
    )
    assert len(check.lines) == 3


def test_a_second_upload_posts_nothing_twice(client, db_session, seed_accounts):
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    again = _upload(client, FIXTURE.read_bytes())
    assert again["journals"] == 0 and again["duplicates_skipped"] == 8
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .count()
        == 8
    )


def test_accounts_not_in_the_chart_refuse_the_file_whole(
    client, db_session, seed_accounts
):
    data = _upload(client, FIXTURE.read_bytes())
    assert data["journals"] == 0
    assert any(
        "not in the chart" in e and "Lists to IIF Files" in e for e in data["errors"]
    ), data["errors"]
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .count()
        == 0
    )


def test_a_report_without_account_headings_says_which_report_to_export(
    client, db_session, seed_accounts
):
    _chart(db_session)
    text = FIXTURE.read_bytes().decode("cp1252")
    rows = [
        ln
        for ln in text.split("\r\n")
        if not (
            ln.startswith(",")
            and ln.split(",")[1].strip()
            and not ln.split(",")[5].strip()
        )
    ]
    data = _upload(client, "\r\n".join(rows).encode("cp1252"))
    assert data["journals"] == 0
    assert any("Transaction Detail by Account" in e for e in data["errors"]), data[
        "errors"
    ]


def test_a_section_that_does_not_add_up_refuses_the_file(
    client, db_session, seed_accounts
):
    _chart(db_session)
    text = (
        FIXTURE.read_bytes()
        .decode("cp1252")
        .replace(
            ',Total Rent,,,,,,,,,,,,,,,,,,,,"1,000.00"',
            ',Total Rent,,,,,,,,,,,,,,,,,,,,"1,100.00"',
        )
    )
    assert "1,100.00" in text
    data = _upload(client, text.encode("cp1252"))
    assert data["journals"] == 0
    assert any(
        "Rent" in e and "cut short or edited" in e for e in data["errors"]
    ), data["errors"]
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .count()
        == 0
    )


def _two_sections_report(second_rent_total: str = "327.96") -> bytes:
    """A report that lists Rent as two separate sections, each with its own
    Total row (a real 2019 export did this), one label column before Type."""

    def row(ttype, date, num, name, memo, split, debit, credit):
        return f",{ttype},{date},{num},{name},{memo},{split},{debit},{credit},"

    def total(text, debit, credit):
        return f"{text},,,,,,,{debit},{credit},"

    lines = [
        ",Type,Date,Num,Name,Memo,Split,Debit,Credit,Balance",
        "Operating Checking 4321,,,,,,,,,",
        row("Check", "01/05/2026", "101", "Landlord", "Jan rent", "Rent", "", "150.00"),
        row("Check", "02/05/2026", "102", "Landlord", "Feb rent", "Rent", "", "207.96"),
        row("Check", "02/20/2026", "103", "Landlord", "Late fee", "Rent", "", "120.00"),
        total("Total Operating Checking 4321", "0.00", "477.96"),
        "Rent,,,,,,,,,",
        row(
            "Check",
            "01/05/2026",
            "101",
            "Landlord",
            "Jan rent",
            "Operating Checking 4321",
            "150.00",
            "",
        ),
        total("Total Rent", "150.00", "0.00"),
        "Rent,,,,,,,,,",
        row(
            "Check",
            "02/05/2026",
            "102",
            "Landlord",
            "Feb rent",
            "Operating Checking 4321",
            "207.96",
            "",
        ),
        row(
            "Check",
            "02/20/2026",
            "103",
            "Landlord",
            "Late fee",
            "Operating Checking 4321",
            "120.00",
            "",
        ),
        total("Total Rent", second_rent_total, "0.00"),
        total("TOTAL", "477.96", "477.96"),
    ]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def test_an_account_listed_as_two_sections_is_checked_section_by_section(
    client, db_session, seed_accounts
):
    _chart(db_session)
    data = _upload(client, _two_sections_report())
    assert data["errors"] == [], data["errors"]
    assert data["journals"] == 3
    assert _net(db_session, "Rent") == Decimal("477.96")
    assert _net(db_session, "Operating Checking 4321") == Decimal("-477.96")


def test_one_of_two_same_named_sections_that_does_not_add_up_still_refuses(
    client, db_session, seed_accounts
):
    _chart(db_session)
    data = _upload(client, _two_sections_report(second_rent_total="300.00"))
    assert data["journals"] == 0
    cut_short = [e for e in data["errors"] if "cut short or edited" in e]
    assert len(cut_short) == 1, data["errors"]
    # that section's own figures, not the two sections added together
    assert "Rent" in cut_short[0]
    assert "327.96" in cut_short[0] and "300.00" in cut_short[0]
    assert "477.96" not in cut_short[0]
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .count()
        == 0
    )


def test_the_page_counts_the_journal_entries():
    js = (Path(__file__).resolve().parents[1] / "app/static/js/iif.js").read_text(
        encoding="utf-8"
    )
    assert "journals: 'Journal entries'" in js
    assert (
        "'checks', 'journals', 'time_entries', 'posted', 'splits']" in js
    )  # time joined the count in 2.23.4


def test_the_entries_list_on_journal_entries_with_their_word(
    client, db_session, seed_accounts
):
    """The page's own call, GET /api/journal with no filter, lists them
    (2.23.4 gate, NEW-122), and their Type reads in words (W-3)."""
    _chart(db_session)
    _upload(client, FIXTURE.read_bytes())
    listed = [
        e for e in client.get("/api/journal").json() if e["source_type"] == SOURCE_TYPE
    ]
    assert len(listed) == 8
    utils = (Path(__file__).resolve().parents[1] / "app/static/js/utils.js").read_text(
        encoding="utf-8"
    )
    assert "qb_ledger_import: 'QuickBooks Desktop import'" in utils
    js = (Path(__file__).resolve().parents[1] / "app/static/js/iif.js").read_text(
        encoding="utf-8"
    )
    assert (
        "(result.checks || 0) + (result.journals || 0)" in js
    )  # the toast counts them (NEW-123)
    assert (
        "Transaction Detail\n" in js
        or "Transaction Detail" in js.split("Import from Report CSV")[1][:1500]
    )


def test_the_lists_iif_makes_bank_and_card_accounts_registers(
    client, db_session, seed_accounts
):
    """A lists IIF's BANK and CCARD accounts come in with their bank kind, so
    Banking lists them (2.23.4 gate, W-4)."""
    iif = (
        "!ACCNT\tNAME\tACCNTTYPE\r\n"
        "ACCNT\tOperating Checking 4321\tBANK\r\n"
        "ACCNT\tCompany Card\tCCARD\r\n"
        "ACCNT\tRent Paid\tEXP\r\n"
    )
    r = client.post(
        "/api/iif/import",
        files={"file": ("lists.IIF", io.BytesIO(iif.encode()), "text/plain")},
    )
    assert r.status_code == 200, r.text
    kinds = {
        a.name: a.bank_kind
        for a in db_session.query(Account).filter(
            Account.name.in_(["Operating Checking 4321", "Company Card", "Rent Paid"])
        )
    }
    assert kinds == {
        "Operating Checking 4321": "bank",
        "Company Card": "credit_card",
        "Rent Paid": None,
    }
    banks = {a["name"] for a in client.get("/api/accounts", params={"bank": 1}).json()}
    assert {"Operating Checking 4321", "Company Card"} <= banks


def test_a_lists_iifs_credit_cards_are_liabilities(client, db_session, seed_accounts):
    """QuickBooks for Mac's sample lists IIF: its CCARD accounts came in as
    expenses (no CCARD in the type map), and round 2's register flag then put
    them on Banking as cards (2.23.4 gate, NEW-125). They are liabilities."""
    sample = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "sample_qbmac_opening_balances.iif"
    )
    r = client.post(
        "/api/iif/import",
        files={"file": (sample.name, io.BytesIO(sample.read_bytes()), "text/plain")},
    )
    assert r.status_code == 200, r.text
    cards = db_session.query(Account).filter(Account.bank_kind == "credit_card").all()
    assert cards and all(a.account_type == AccountType.LIABILITY for a in cards), [
        (a.name, a.account_type) for a in cards
    ]
    assert (
        db_session.query(Account)
        .filter(Account.name.like("QBCC%"), Account.account_type == AccountType.EXPENSE)
        .count()
        == 0
    )


def test_a_journal_line_without_an_account_number_reads_cleanly():
    js = (Path(__file__).resolve().parents[1] / "app/static/js/journal.js").read_text(
        encoding="utf-8"
    )
    assert (
        "${l.account_number ? `${escapeHtml(l.account_number)} - ` : ''}" in js
    )  # NEW-126
