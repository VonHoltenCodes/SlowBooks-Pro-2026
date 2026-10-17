"""The Profit & Loss and Balance Sheet list their accounts in the chart's
order (#326, @TigerCoder27): by number, then name, as the Trial Balance and
General Ledger do, or by name on request; a sub-account right after its
parent either way, and an account with no number after the numbered ones.

The query grouped by account with no ORDER BY, so accounts added after the
seeded chart read in the order they were created (6000, 6200, 6600, 6900,
6250, 6410). The PDF and CSV carry the same order, and the pages offer it.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry
from tests.conftest import WEASYPRINT_AVAILABLE

MINE = {"Zeta Utilities", "Bank Fees", "Legal", "Automobile", "Fuel", "Aardvark Dues"}
PERIOD = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def _account(db, name, kind, number=None, parent=None):
    acct = Account(
        name=name,
        account_type=kind,
        account_number=number,
        balance=Decimal("0"),
        parent_id=parent.id if parent is not None else None,
    )
    db.add(acct)
    db.flush()
    return acct


def _post(db, debits, credit_to):
    lines = [
        {
            "account_id": a.id,
            "debit": Decimal("10"),
            "credit": Decimal("0"),
            "description": a.name,
        }
        for a in debits
    ]
    lines.append(
        {
            "account_id": credit_to.id,
            "debit": Decimal("0"),
            "credit": Decimal(10 * len(debits)),
            "description": "the other side",
        }
    )
    create_journal_entry(db, date(2026, 3, 1), "order test", lines)
    db.commit()


def _books(db):
    """Expense accounts made after the seeded chart, out of number order:
    a numbered parent with an unnumbered sub-account, and one top-level
    account with no number; each with a line."""
    by_number = {a.account_number: a for a in db.query(Account) if a.account_number}
    utilities = _account(db, "Zeta Utilities", AccountType.EXPENSE, "8900")
    fees = _account(db, "Bank Fees", AccountType.EXPENSE, "8200")
    legal = _account(db, "Legal", AccountType.EXPENSE, "8250")
    auto = _account(db, "Automobile", AccountType.EXPENSE, "8600")
    fuel = _account(db, "Fuel", AccountType.EXPENSE, None, parent=auto)
    dues = _account(db, "Aardvark Dues", AccountType.EXPENSE, None)
    _post(db, [utilities, fees, legal, auto, fuel, dues], by_number["1000"])
    return by_number


def _names(rows):
    return [r["account_name"] for r in rows if r["account_name"] in MINE]


def test_the_profit_and_loss_lists_accounts_by_number_then_name(
    client, db_session, seed_accounts
):
    _books(db_session)
    body = client.get("/api/reports/profit-loss", params=PERIOD).json()
    assert body["sort"] == "number"
    # the sub-account under its parent, the unnumbered one last
    assert _names(body["expenses"]) == [
        "Bank Fees",
        "Legal",
        "Automobile",
        "Fuel",
        "Zeta Utilities",
        "Aardvark Dues",
    ]
    numbers = [r["account_number"] for r in body["expenses"] if r["account_number"]]
    assert numbers == sorted(numbers)  # the Trial Balance's order


def test_by_name_on_request_keeps_a_sub_account_under_its_parent(
    client, db_session, seed_accounts
):
    _books(db_session)
    body = client.get(
        "/api/reports/profit-loss", params={**PERIOD, "sort": "name"}
    ).json()
    assert body["sort"] == "name"
    assert _names(body["expenses"]) == [
        "Aardvark Dues",
        "Automobile",
        "Fuel",
        "Bank Fees",
        "Legal",
        "Zeta Utilities",
    ]
    sideways = client.get(
        "/api/reports/profit-loss", params={**PERIOD, "sort": "sideways"}
    )
    assert sideways.status_code == 422


def test_the_balance_sheet_and_the_exports_carry_the_order(
    client, db_session, seed_accounts
):
    by_number = _books(db_session)
    deposits = _account(db_session, "Zed Deposits", AccountType.ASSET, "1950")
    petty = _account(db_session, "Petty Cash", AccountType.ASSET, "1150")
    _post(db_session, [deposits, petty], by_number["3000"])
    body = client.get(
        "/api/reports/balance-sheet", params={"as_of_date": "2026-12-31"}
    ).json()
    names = [r["account_name"] for r in body["assets"]]
    assert (
        names.index("Checking")
        < names.index("Petty Cash")
        < names.index("Zed Deposits")
    ), names
    by_name = client.get(
        "/api/reports/balance-sheet",
        params={"as_of_date": "2026-12-31", "sort": "name"},
    ).json()
    names = [r["account_name"] for r in by_name["assets"]]
    assert names == sorted(names, key=str.lower), names
    pl_csv = client.get(
        "/api/reports/profit-loss/csv", params={**PERIOD, "sort": "name"}
    ).text
    assert (
        pl_csv.index("Aardvark Dues")
        < pl_csv.index("Automobile")
        < pl_csv.index("Bank Fees")
    )
    bs_csv = client.get(
        "/api/reports/balance-sheet/csv", params={"as_of_date": "2026-12-31"}
    ).text
    assert bs_csv.index("Petty Cash") < bs_csv.index("Zed Deposits")
    # the PDF renders the same rows; a machine without WeasyPrint's native
    # stack (the Windows CI runner) skips only this check, not the test
    if WEASYPRINT_AVAILABLE:
        pdf = client.get(
            "/api/reports/balance-sheet/pdf",
            params={"as_of_date": "2026-12-31", "sort": "name"},
        )
        assert pdf.status_code == 200 and pdf.headers["content-type"].startswith(
            "application/pdf"
        )


def test_the_pages_offer_the_order_and_pass_it_on():
    js = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text(
        encoding="utf-8"
    )
    assert "report-sort-select" in js and "setParam('sort'" in js
    # the P&L and Balance Sheet fetches and their PDF / CSV buttons
    assert js.count("ReportsPage._sortQs(") >= 4
