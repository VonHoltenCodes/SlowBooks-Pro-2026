"""QuickBooks Online export with account numbers switched on (#767).

The statements put each account's number on its own row ("4050
Memberships", then "Family Memberships" indented under it); the Journal,
the Trial Balance and the General Ledger write the account's own number in
front of the whole path and leave the parents' off ("Memberships:Family
Memberships", "1010 Operating:Operating Checking", "2201 Long-term loans:Bank
loan"). The fixtures in tests/fixtures/qbo_export_numbered/ are invented
books in that layout; tests/fixtures/qbo_export/ stays the company without
numbers, and reads as it did before.
"""

import io
from pathlib import Path

from app.models.accounts import Account, AccountType, DetailType
from app.services import qbo_export_import as qbo

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbo_export_numbered"
PLAIN = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
BUNDLE = [
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "General_ledger.csv",
    "Trial_balance.csv",
]
LIST = "Pinecrest_Account_List.csv"


def _files(names=BUNDLE):
    return [
        ("files", (name, io.BytesIO((FIXTURES / name).read_bytes()), "text/csv"))
        for name in names
    ]


def _statements(where=FIXTURES):
    return (where / "Balance_sheet.csv").read_text() + (
        where / "Profit_and_loss.csv"
    ).read_text()


def _dry_run(client, names=BUNDLE):
    resp = client.post("/api/migration/qbo_export/dry-run", files=_files(names))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _import(client, names=BUNDLE):
    resp = client.post("/api/migration/qbo_export/import", files=_files(names))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the chart ──────────────────────────────────────────────────────────────


def test_the_statements_numbers_are_the_accounts_and_the_paths_are_the_journals():
    accounts, errors = qbo.parse_coa(_statements())
    assert errors == []
    by = {a["name"]: a for a in accounts}
    # a numbered child under a parent with no number
    assert by["Operating Checking"]["code"] == "1010"
    assert by["Operating Checking"]["path"] == "1010 Operating:Operating Checking"
    assert by["Operating Checking"]["parent"] == "Operating"
    assert by["Operating"]["code"] is None and by["Operating"]["path"] == "Operating"
    # a child with no number under a numbered parent: the parent's number
    # is left off the path, as the Journal writes it
    assert by["Family Memberships"]["code"] is None
    assert by["Family Memberships"]["path"] == "Memberships:Family Memberships"
    assert by["Family Memberships"]["parent"] == "4050 Memberships"
    assert by["Memberships"]["code"] == "4050"
    # a numbered child under a numbered parent: its own number in front
    assert by["Bank loan"]["code"] == "2201"
    assert by["Bank loan"]["path"] == "2201 Long-term loans:Bank loan"
    assert by["Discounts Given"]["path"] == "4351 Store Sales:Discounts Given"
    assert by["Discounts Given"]["parent"] == "4350 Store Sales"
    assert by["Company Visa"]["code"] == "2150"
    assert by["Company Visa"]["bank_kind"] == "credit_card"
    assert by["Bank loan"]["detail_type"] == DetailType.LONG_TERM_LIABILITY
    assert not any(":" in a["name"] for a in accounts)
    # every spelling the Trial Balance writes is a path here
    tb_names = qbo._tb_names((FIXTURES / "Trial_balance.csv").read_text())
    paths = {a["path"] for a in accounts}
    assert set(tb_names) <= paths, set(tb_names) - paths
    # a number SlowBooks keeps for a control account is left off: the name
    # stays, the path keeps the books' spelling, and one note says which
    assert by["Cash Box"]["code"] is None and by["Cash Box"]["path"] == "1000 Cash Box"
    assert by["Long-term loans"]["code"] is None
    assert by["Long-term loans"]["path"] == "2200 Long-term loans"
    notes = [n for n in qbo._COA_NOTES if "control accounts" in n]
    assert len(notes) == 1, qbo._COA_NOTES
    assert "1000 (Cash Box; Checking here)" in notes[0]
    assert "2200 (Long-term loans; Sales Tax Payable here)" in notes[0]
    assert "Number them in Chart of Accounts" in notes[0]


def test_a_company_without_numbers_reads_as_it_did():
    accounts, errors = qbo.parse_coa(_statements(PLAIN))
    assert errors == []
    assert all(a["code"] is None for a in accounts)
    paths = {a["path"] for a in accounts}
    assert {"Advertising:Facebook", "Operating Account", "Promotional:Flyers"} <= paths
    assert not qbo._COA_NOTES
    # one account named for a year is a name, not a number
    gala = _statements(PLAIN).replace(
        "   Office Supplies,=400.00", "   2024 Gala,=400.00"
    )
    accounts, errors = qbo.parse_coa(gala)
    assert errors == []
    by = {a["name"]: a for a in accounts}
    assert by["2024 Gala"]["code"] is None and by["2024 Gala"]["path"] == "2024 Gala"
    # three numbered labels, or one beside the books' numbered path, say
    # the company numbers its accounts
    assert not qbo.uses_numbers(["2024 Gala", "Rent", "Office Supplies"])
    assert qbo.uses_numbers(["1000 Cash", "1010 Checking", "2000 Visa"])
    assert qbo.uses_numbers(["1010 Checking"], ["1010 Operating:Checking"])
    assert not qbo.uses_numbers(["1010 Checking"], ["Operating:Checking"])


def test_the_general_ledger_lists_every_account_in_the_books_spelling():
    order = qbo.ledger_order((FIXTURES / "General_ledger.csv").read_text())
    assert order[:4] == (
        "1000 Cash Box",
        "Operating",
        "1010 Operating:Operating Checking",
        "1011 Operating:Operating Savings",
    )
    # the deleted card, between the liabilities it sat among
    assert (
        order.index("2150 Company Visa")
        < order.index("2101 Old Store Card (deleted)")
        < order.index("2200 Long-term loans")
    )
    assert "Memberships:Family Memberships" in order
    assert "4351 Store Sales:Discounts Given" in order
    assert "Office expenses:Interest paid" in order
    assert not any(n.lower().startswith("total") for n in order)
    # the sample without numbers puts its headings in the Date column
    plain = qbo.ledger_order((PLAIN / "General_ledger.csv").read_text())
    assert plain and plain[0] == "Operating Account"
    assert all(" " != n[:1] for n in plain)


def test_a_statement_from_the_reports_page_is_named_once():
    flat = (
        "\n".join(
            line.lstrip(" ")
            for line in (FIXTURES / "Balance_sheet.csv").read_text().splitlines()
        )
        + "\n"
    )
    accounts, errors = qbo.parse_coa(
        flat + (FIXTURES / "Profit_and_loss.csv").read_text()
    )
    assert len(errors) == 1, errors
    assert "Balance Sheet" in errors[0] and "no indentation" in errors[0]
    assert "Export data" in errors[0]
    # the Profit and Loss beside it still reads
    assert {"Memberships", "Rent", "Family Memberships"} <= {
        a["name"] for a in accounts
    }


# ── through the route ──────────────────────────────────────────────────────


def test_the_dry_run_finds_every_journal_account_and_places_the_deleted_card(
    client, seed_accounts
):
    data = _dry_run(client)
    assert data["ok"] is True, data["errors"]
    assert data["journals"] == 11
    assert data["opening_balances"] == []  # the Trial Balance agrees with the Journal
    warnings = data["warnings"]
    assert any(
        "'2101 Old Store Card (deleted)' is not on the Balance Sheet" in w
        and "General Ledger" in w
        and "liability account" in w
        for w in warnings
    ), warnings
    assert any(
        "1 account QuickBooks Online had deleted" in w and "inactive" in w
        for w in warnings
    ), warnings
    assert any(
        "control accounts" in w
        and "2200 (Long-term loans; Sales Tax Payable here)" in w
        for w in warnings
    ), warnings
    # 1010 is the seeded chart's Savings account: the number is left off, said once
    assert any(
        "books already use" in w and "1010 (Operating Checking)" in w for w in warnings
    ), warnings
    # without the General Ledger the deleted card has nothing to place it
    # by: named for the person, as before
    data = _dry_run(client, [n for n in BUNDLE if n != "General_ledger.csv"])
    assert data["ok"] is False
    assert any(
        "2101 Old Store Card (deleted)" in e and "add it to the chart" in e
        for e in data["errors"]
    ), data["errors"]


def test_the_import_numbers_the_accounts_retires_the_deleted_card_and_doubles_nothing(
    client, db_session, seed_accounts
):
    data = _import(client)
    assert data["ok"] is True, data["errors"]
    assert data["imported_journals"] == 11

    def acct(name):
        return db_session.query(Account).filter(Account.name == name).one()

    checking = acct("Operating Checking")
    assert checking.account_number is None  # 1010 is the seeded Savings account's
    assert checking.parent.name == "Operating" and checking.bank_kind == "bank"
    assert acct("Operating Savings").account_number == "1011"
    loan = acct("Bank loan")
    assert loan.account_number == "2201"
    assert loan.parent.name == "Long-term loans"
    assert loan.detail_type == "long_term_liability"
    assert acct("Long-term loans").account_number is None  # 2200 is Sales Tax Payable's
    assert acct("Memberships").account_number == "4050"
    family = acct("Family Memberships")
    assert family.parent.name == "Memberships" and family.account_number is None
    discounts = acct("Discounts Given")
    assert discounts.account_number == "4351" and discounts.parent.name == "Store Sales"
    card = acct("Old Store Card (deleted)")
    assert card.account_number == "2101"
    assert card.account_type == AccountType.LIABILITY and card.is_active is False
    # the statements name the imported accounts by their paths
    tb = client.get(
        "/api/reports/trial-balance?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    paths = [row["account_path"] for row in tb["items"]]
    assert "Operating: Operating Checking" in paths, paths
    assert "Long-term loans: Bank loan" in paths, paths
    # the same export again: every account found, nothing doubled
    again = _import(client)
    assert again["ok"] is True, again["errors"]
    assert again["imported_accounts"] == 0
    assert again["imported_journals"] == 0 and again["duplicate_journals"] == 11
    assert not any("books already use" in w for w in again["warnings"]), again[
        "warnings"
    ]
    names = [a.name for a in db_session.query(Account).all()]
    for name in ("Cash Box", "Operating Checking", "Memberships", "Long-term loans"):
        assert names.count(name) == 1, (name, names.count(name))


def test_the_account_list_report_is_the_chart_with_the_statements_filling_in(
    client, db_session, seed_accounts
):
    accounts, errors = qbo.parse_coa((FIXTURES / LIST).read_text())
    assert errors == []
    by = {a["name"]: a for a in accounts}
    assert by["Prepaid rent"]["type"] == AccountType.ASSET
    assert by["Prepaid rent"]["detail_type"] == DetailType.OTHER_CURRENT_ASSET
    assert by["Prepaid rent"]["description"] == "Rent paid ahead"
    assert by["Company Visa"]["bank_kind"] == "credit_card"
    assert by["Company Visa"]["code"] == "2150"
    assert by["Cash Box"]["description"] == "Petty cash in the studio"
    # the parent "Operating" is listed after its children and still found
    assert by["Operating Checking"]["path"] == "1010 Operating:Operating Checking"
    assert by["Operating Checking"]["parent"] == "Operating"
    assert by["Bank loan"]["parent"] == "2200 Long-term loans"
    assert by["Long-term loans"]["code"] is None  # the control number, left off
    # alone with the Journal, the Ledger and the Trial Balance: a chart
    data = _dry_run(
        client, [LIST, "Journal.csv", "General_ledger.csv", "Trial_balance.csv"]
    )
    assert data["ok"] is True, data["errors"]
    # with the statements too: one chart, the list's words first, and the
    # deleted card from the ledger
    data = _import(client, BUNDLE + [LIST])
    assert data["ok"] is True, data["errors"]
    cash = db_session.query(Account).filter(Account.name == "Cash Box").one()
    assert cash.description == "Petty cash in the studio"
    assert db_session.query(Account).filter(Account.name == "Prepaid rent").count() == 1
    assert db_session.query(Account).filter(Account.name == "Cash Box").count() == 1
    card = (
        db_session.query(Account)
        .filter(Account.name == "Old Store Card (deleted)")
        .one()
    )
    assert card.is_active is False
    # a type the import does not know is named, by account
    text = (
        (FIXTURES / LIST)
        .read_text()
        .replace(",Prepaid rent,Other Current Assets,", ",Prepaid rent,Mystery,")
    )
    _, errors = qbo.parse_coa(text)
    assert any("Prepaid rent" in e and "Mystery" in e for e in errors), errors
