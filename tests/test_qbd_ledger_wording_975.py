"""Every sentence that tells a QuickBooks Desktop user how to export
Transaction Detail by Account asks for Debit and Credit columns (#971,
#975): the unrecognized-layout refusal, the added-Account-column refusal,
the guide's quote of it, and the Import from Report CSV hint."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_added_account_column_refusal_asks_for_debit_and_credit(
    client, seed_accounts
):
    csv_text = (
        ',"Type","Date","Num","Name","Memo","Clr","Split","Account","Debit","Credit","Balance"\r\n'
        '"Checking",,,,,,,,,,,\r\n'
        ',"Deposit","1/16/2018",,"Google","Deposit","X","Product Revenue","Checking",4.18,,4.18\r\n'
        '"Total Checking",,,,,,,,,4.18,,4.18\r\n'
        '"TOTAL",,,,,,,,,4.18,,4.18\r\n'
    )
    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("tdba.csv", csv_text.encode("cp1252"), "text/csv")},
    )
    body = r.json()
    text = " ".join(body.get("errors", []))
    assert "added Account column" in text
    assert "without the Account column, with Debit and Credit ticked" in text
    assert "default columns" not in text


def test_the_guide_quotes_the_refusal_word_for_word():
    guide = (ROOT / "docs/migrate-from-quickbooks.md").read_text(encoding="utf-8")
    lines = guide.splitlines()
    first = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(
            "> This appears to be Transaction Detail by Account with an added Account column."
        )
    )
    block = []
    for line in lines[first:]:
        if not line.startswith("> "):
            break
        block.append(line[2:].strip())
    quote = " ".join(block)
    assert (
        "Export again without the Account column, with Debit and Credit ticked under "
        "Customize Report > Display; set Customize Report > Filters > Account > All "
        "accounts rather than adding an Account column." in quote
    )
    assert "Export again using the default columns" not in guide


def test_the_import_page_hint_names_debit_and_credit():
    js = (ROOT / "app/static/js/iif.js").read_text(encoding="utf-8")
    assert "Keep the report's default" not in js
    assert (
        "needs <strong>Debit</strong> and <strong>Credit</strong> ticked in place of Amount"
        in js
    )


def test_the_unrecognized_layout_refusal_scopes_the_debit_credit_advice(
    client, seed_accounts
):
    # #982: only the sales Transaction Detail and Transaction Detail by
    # Account need Debit and Credit; the others keep their default columns
    csv_text = ',"Type","Date","Num","Name","Memo","Clr","Split","Amount","Balance"\r\n"Checking",,,,,,,,,""\r\n,"Deposit","1/16/2018",,"Google","Deposit","X","Product Revenue",4.18,4.18\r\n'
    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("default.csv", csv_text.encode("cp1252"), "text/csv")},
    )
    text = " ".join(r.json().get("errors", []))
    assert "Unrecognized report layout" in text
    assert (
        "Transaction Detail (filtered to Sales Receipt or Invoice) and Transaction Detail by Account, "
        "each with Debit and Credit ticked under Customize Report > Display in place of Amount; "
        "Deposit Detail, Check Detail and Time by Name by Job detail with their default columns"
    ) in text
