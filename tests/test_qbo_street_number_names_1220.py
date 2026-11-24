"""QuickBooks Online export: account names that start with a street number
in a company without account numbers (#1220).

A property company names its accounts for its addresses ("88 Larkspur Ln",
"88 Larkspur Ln:Roof", "88 Larkspur Ln-Escrow"). With account numbers off
in QuickBooks Online the leading number is part of the name, and the import
keeps the name whole: the company is numbered only when the files say so
(the Account List's Account # column, or the books' spelling of a
sub-account that only numbering gives), and a SlowBooks company with Use
account numbers off is read as unnumbered unless the files say otherwise.
The books here are invented; the street names are made up.
"""

import io
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.services import qbo_export_import as qbo

NUMBERED = Path(__file__).resolve().parent / "fixtures" / "qbo_export_numbered"
WILLOW = "57  N Willow Ct (deleted)"  # two spaces after the number, as written
GUTTERS = "88 Larkspur Ln:Gutters (deleted)"

BALANCE_SHEET = """Harrow Lane Properties,
Balance Sheet,
All Dates,
,
,Total
ASSETS,
   Current Assets,
      Bank Accounts,
         Operating Account,=1000.00
         88 Larkspur Ln-Escrow,=200.00
      Total Bank Accounts,=1200.00
   Total Current Assets,=1200.00
   Fixed Assets,
      88 Larkspur Ln,
         Roof,=5000.00
      Total 88 Larkspur Ln,=5000.00
      412 Quarry Rd,=7000.00
   Total Fixed Assets,=12000.00
TOTAL ASSETS,=13200.00
LIABILITIES AND EQUITY,
   Equity,
      Opening Balance Equity,=13200.00
   Total Equity,=13200.00
TOTAL LIABILITIES AND EQUITY,=13200.00
,
,
"Monday, Nov 23, 2026 09:00:00 AM GMT-6 - Accrual Basis",
"""

# (date, debit account, credit account, amount)
ENTRIES = [
    ("01/05/2026", "Operating Account", "Opening Balance Equity", "13200.00"),
    ("01/06/2026", "88 Larkspur Ln:Roof", "Operating Account", "5000.00"),
    ("01/07/2026", "412 Quarry Rd", "Operating Account", "7000.00"),
    ("01/08/2026", "88 Larkspur Ln-Escrow", "Operating Account", "200.00"),
    ("02/01/2026", GUTTERS, "Operating Account", "300.00"),
    ("02/02/2026", "Operating Account", GUTTERS, "300.00"),
    ("03/01/2026", WILLOW, "Operating Account", "150.00"),
    ("03/02/2026", "Operating Account", WILLOW, "150.00"),
]

TRIAL_BALANCE = """Harrow Lane Properties,,
Trial Balance,,
All Dates,,
,,
,Debit,Credit
Operating Account,=1000.00,
88 Larkspur Ln-Escrow,=200.00,
88 Larkspur Ln:Roof,=5000.00,
412 Quarry Rd,=7000.00,
Opening Balance Equity,,=13200.00
TOTAL,=13200.00,=13200.00
,,
,,
"Monday, Nov 23, 2026 09:00:00 AM GMT-6 - Accrual Basis",,
"""

# the General Ledger's headings in the chart's order, a sub-account's
# indented under its parent's; the two deleted accounts net to nothing and
# only the Journal and this report have them
GENERAL_LEDGER = """Harrow Lane Properties,,,,,,,,,
General Ledger,,,,,,,,,
All Dates,,,,,,,,,
,,,,,,,,,
,Date,Transaction Type,Num,Name,Memo/Description,Account,Debit,Credit,Balance
Operating Account,,,,,,,,,
,01/05/2026,Journal Entry,1,,Opening,Operating Account,=13200.00,,=13200.00
Total for Operating Account,,,,,,,=13200.00,,
88 Larkspur Ln-Escrow,,,,,,,,,
,01/08/2026,Journal Entry,4,,Escrow,88 Larkspur Ln-Escrow,=200.00,,=200.00
Total for 88 Larkspur Ln-Escrow,,,,,,,=200.00,,
88 Larkspur Ln,,,,,,,,,
   Roof,,,,,,,,,
,01/06/2026,Journal Entry,2,,Roof,88 Larkspur Ln:Roof,=5000.00,,=5000.00
   Total for Roof,,,,,,,=5000.00,,
   Gutters (deleted),,,,,,,,,
,02/01/2026,Journal Entry,5,,Gutters,88 Larkspur Ln:Gutters (deleted),=300.00,,=300.00
   Total for Gutters (deleted),,,,,,,=300.00,=300.00,
Total for 88 Larkspur Ln with sub-accounts,,,,,,,=5300.00,=300.00,
412 Quarry Rd,,,,,,,,,
,01/07/2026,Journal Entry,3,,Purchase,412 Quarry Rd,=7000.00,,=7000.00
Total for 412 Quarry Rd,,,,,,,=7000.00,,
57  N Willow Ct (deleted),,,,,,,,,
,03/01/2026,Journal Entry,7,,Deposit,57  N Willow Ct (deleted),=150.00,,=150.00
Total for 57  N Willow Ct (deleted),,,,,,,=150.00,=150.00,
Opening Balance Equity,,,,,,,,,
,01/05/2026,Journal Entry,1,,Opening,Opening Balance Equity,,=13200.00,=-13200.00
Total for Opening Balance Equity,,,,,,,,=13200.00,
,,,,,,,,,
"Monday, Nov 23, 2026 09:00:00 AM GMT-6 - Accrual Basis",,,,,,,,,
"""

# the Account List report with account numbers off: no Account # column
ACCOUNT_LIST = """Harrow Lane Properties,,,,
Account List,,,,
,,,,
Account Name,Type,Detail type,Description,Total balance
Operating Account,Bank,Checking,,=1000.00
88 Larkspur Ln-Escrow,Bank,Savings,,=200.00
88 Larkspur Ln,Fixed Assets,Buildings,,=5000.00
88 Larkspur Ln:Roof,Fixed Assets,Buildings,,=5000.00
412 Quarry Rd,Fixed Assets,Buildings,,=7000.00
Opening Balance Equity,Equity,Opening Balance Equity,,=-13200.00
TOTAL,,,,=0.00
"""


def _unique(text: str) -> str:
    """The books with no two street accounts sharing a number."""
    return text.replace("88 Larkspur Ln-Escrow", "19 Fern Gate").replace(
        "88 Larkspur Ln", "31 Birch Way"
    )


def _journal(entries=ENTRIES) -> str:
    rows = [
        ",Harrow Lane Properties,,,,,,,",
        ",Journal,,,,,,,",
        ",All Dates,,,,,,,",
        ",,,,,,,,",
        ",Date,Transaction Type,Num,Name,Memo/Description,Account,Debit,Credit",
    ]
    for n, (date, debit, credit, amount) in enumerate(entries, start=1):
        rows += [
            f",{date},Journal Entry,{n},,Entry {n},{debit},={amount},",
            f",,,,,Entry {n},{credit},,={amount}",
            f",,,,,,,={amount},={amount}",
            ",,,,,,,,",
        ]
    return "\n".join(rows) + "\n"


def _upload(**files) -> list:
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/csv"))
        for name, text in files.items()
    ]


def _bundle(with_ledger=True, with_list=False) -> dict:
    files = {
        "Balance_sheet.csv": BALANCE_SHEET,
        "Journal.csv": _journal(),
        "Trial_balance.csv": TRIAL_BALANCE,
    }
    if with_ledger:
        files["General_ledger.csv"] = GENERAL_LEDGER
    if with_list:
        files["Harrow_Account_List.csv"] = ACCOUNT_LIST
    return files


def _dry_run(client, files):
    resp = client.post("/api/migration/qbo_export/dry-run", files=_upload(**files))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the decision ──────────────────────────────────────────────────────────


def test_street_numbers_shared_by_two_accounts_are_names():
    accounts, errors = qbo.parse_coa(BALANCE_SHEET)
    assert errors == []
    by_path = {a["path"]: a for a in accounts}
    assert all(a["code"] is None for a in accounts)
    assert {"88 Larkspur Ln", "88 Larkspur Ln-Escrow", "412 Quarry Rd"} <= set(by_path)
    roof = by_path["88 Larkspur Ln:Roof"]
    assert roof["name"] == "Roof" and roof["parent"] == "88 Larkspur Ln"
    # two live labels with one number and different names: not a number
    assert not qbo.uses_numbers(
        ["88 Larkspur Ln", "88 Larkspur Ln-Escrow", "412 Quarry Rd"]
    )
    # a deleted account's old number beside a live one's is not counted
    assert qbo.uses_numbers(["1000 Cash", "1010 Checking", "1010 Old Card (deleted)"])


def test_unique_street_numbers_are_names_when_the_books_spell_them_whole():
    # no two share a number, so the count alone would say "numbered"; the
    # Journal's "31 Birch Way:Roof" is the whole-name spelling of the
    # statements' "31 Birch Way" and "Roof", which numbering never writes
    sheet = _unique(BALANCE_SHEET)
    assert qbo.uses_numbers(["19 Fern Gate", "31 Birch Way", "412 Quarry Rd"])
    accounts, errors = qbo.parse_coa(sheet, also=("31 Birch Way:Roof",))
    assert errors == []
    assert all(a["code"] is None for a in accounts)
    paths = {a["path"] for a in accounts}
    assert {"31 Birch Way", "31 Birch Way:Roof", "19 Fern Gate"} <= paths


def test_an_account_list_without_numbers_says_the_company_has_none():
    sheet = _unique(BALANCE_SHEET)
    listed = _unique(ACCOUNT_LIST)
    accounts, errors = qbo.parse_coa(listed + "\n" + sheet)
    assert errors == []
    assert all(a["code"] is None for a in accounts)
    assert {"19 Fern Gate", "31 Birch Way:Roof"} <= {a["path"] for a in accounts}


def test_the_company_setting_off_keeps_names_whole_unless_the_files_say_numbers():
    sheet = _unique(BALANCE_SHEET)
    assert qbo.chart_numbered(sheet) is True  # the labels alone, as before
    assert qbo.chart_numbered(sheet, numbers_off=True) is False
    # a numbered company's own files win over the setting: the Account
    # List's Account # column, or the books' "1010 Operating:Operating
    # Checking" beside the statements' "Operating" and "1010 Operating
    # Checking"
    statements = (NUMBERED / "Balance_sheet.csv").read_text() + (
        NUMBERED / "Profit_and_loss.csv"
    ).read_text()
    listed = (NUMBERED / "Pinecrest_Account_List.csv").read_text()
    assert qbo.chart_numbered(listed + "\n" + statements, numbers_off=True) is True
    books = ("1010 Operating:Operating Checking", "Memberships:Family Memberships")
    assert qbo.chart_numbered(statements, books, numbers_off=True) is True


def test_the_general_ledger_keeps_a_headings_two_spaces():
    order = qbo.ledger_order(GENERAL_LEDGER, numbered=False)
    assert WILLOW in order
    assert "88 Larkspur Ln:Gutters (deleted)" in order
    assert "88 Larkspur Ln:Roof" in order


# ── the dry run and the import ────────────────────────────────────────────


def test_the_dry_run_places_both_deleted_accounts(client, seed_accounts):
    data = _dry_run(client, _bundle())
    assert data["ok"] is True, data["errors"]
    warnings = data["warnings"]
    assert any(
        f"{WILLOW!r} is not on the Balance Sheet" in w and "asset account" in w
        for w in warnings
    ), warnings
    # the Account List beside it changes nothing
    data = _dry_run(client, _bundle(with_list=True))
    assert data["ok"] is True, data["errors"]


def test_the_import_keeps_every_street_name_whole(client, db_session, seed_accounts):
    resp = client.post("/api/migration/qbo_export/import", files=_upload(**_bundle()))
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True, resp.json()["errors"]
    names = {a.name: a for a in db_session.query(Account).all()}
    for name in ("88 Larkspur Ln", "88 Larkspur Ln-Escrow", "412 Quarry Rd", WILLOW):
        assert name in names, sorted(names)
        assert names[name].account_number in (None, "")
    assert names["Roof"].parent_id == names["88 Larkspur Ln"].id
    assert names["Gutters (deleted)"].parent_id == names["88 Larkspur Ln"].id
    assert names["Gutters (deleted)"].is_active is False
    assert names[WILLOW].is_active is False
    assert names[WILLOW].account_type == AccountType.ASSET
    for split in ("Larkspur Ln", "Quarry Rd", "N Willow Ct (deleted)"):
        assert split not in names


def test_adding_the_account_first_clears_the_refusal(client, db_session, seed_accounts):
    # without the General Ledger nothing places the deleted account by: it
    # is named, for the person to add, whole and with its two spaces
    data = _dry_run(client, _bundle(with_ledger=False))
    assert data["ok"] is False
    assert any(
        WILLOW in e and "add it to the chart" in e for e in data["errors"]
    ), data["errors"]
    db_session.add(Account(name=WILLOW, account_type=AccountType.ASSET))
    db_session.commit()
    data = _dry_run(client, _bundle(with_ledger=False))
    assert data["ok"] is True, data["errors"]


def test_the_setting_off_reads_unique_street_numbers_as_names(
    client, db_session, seed_accounts
):
    # three street accounts with their own numbers and nothing else to go
    # by: with Use account numbers off in SlowBooks, the names stay whole
    entries = [e for e in ENTRIES if "deleted" not in e[1] + e[2]]
    files = {
        "Balance_sheet.csv": BALANCE_SHEET.replace(
            "88 Larkspur Ln-Escrow", "31 Birch Way"
        ).replace(
            "      88 Larkspur Ln,\n         Roof,=5000.00\n"
            "      Total 88 Larkspur Ln,=5000.00\n",
            "      88 Larkspur Ln,=5000.00\n",
        ),
        "Journal.csv": _journal(
            [
                (d, a.replace("88 Larkspur Ln-Escrow", "31 Birch Way"), c, n)
                for d, a, c, n in entries
            ]
        ).replace("88 Larkspur Ln:Roof", "88 Larkspur Ln"),
    }
    assert (
        client.put("/api/settings", json={"use_account_numbers": "false"}).status_code
        == 200
    )
    data = _dry_run(client, files)
    assert data["ok"] is True, data["errors"]
    resp = client.post("/api/migration/qbo_export/import", files=_upload(**files))
    assert resp.status_code == 200 and resp.json()["ok"] is True, resp.text
    names = {a.name for a in db_session.query(Account).all()}
    assert {"88 Larkspur Ln", "31 Birch Way", "412 Quarry Rd"} <= names
    assert not {"Larkspur Ln", "Birch Way", "Quarry Rd"} & names
