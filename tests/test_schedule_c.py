"""Schedule C (2.17.3 exploratory test, skytech W-H2).

Gross income was tested with `"Line 1" in line`, which also matches Lines
10, 13, 15, 17 and 18 — Office Supplies (Line 18) was counted as income and
left out of expenses, overstating net profit by twice its amount. The line
table was written for a chart this app never seeds (Rent landed on Supplies,
Repairs on Travel, Utilities on Rent), wages / payroll tax / depreciation
fell through to "other expenses", and the seeded COGS accounts fell through
to Line 1 — cost of goods sold added to gross receipts.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
from app.services.accounting import create_journal_entry

EXPENSES = [
    a["account_number"] for a in CHART_OF_ACCOUNTS if a["account_type"] == "expense"
]
COGS = [a["account_number"] for a in CHART_OF_ACCOUNTS if a["account_type"] == "cogs"]

RECEIPTS = Decimal("1500.00")  # 1,000 service + 500 product
RETURNS = Decimal("50.00")


def _post(db, accts, day, debit_number, credit_number, amount, memo="x"):
    create_journal_entry(
        db,
        day,
        memo,
        [
            {"account_id": accts[debit_number].id, "debit": amount, "credit": 0},
            {"account_id": accts[credit_number].id, "debit": 0, "credit": amount},
        ],
    )


@pytest.fixture
def year_of_books(db_session, seed_accounts):
    """Sales, a return, and a distinct amount on every seeded expense account."""
    d = date(2026, 5, 1)
    _post(db_session, seed_accounts, d, "1000", "4000", Decimal("1000.00"))
    _post(db_session, seed_accounts, d, "1000", "4100", Decimal("500.00"))
    # a return of goods: the credit memo debits the income account
    _post(db_session, seed_accounts, d, "4100", "1000", RETURNS)
    spent = {}
    for i, number in enumerate(EXPENSES):
        amount = Decimal("10.00") + Decimal(i)
        _post(db_session, seed_accounts, d, number, "1000", amount)
        spent[number] = amount
    db_session.commit()
    return spent


def _schedule(client):
    r = client.get("/api/tax/schedule-c?start_date=2026-01-01&end_date=2026-12-31")
    assert r.status_code == 200, r.text
    return r.json()


def _line_of(data, number):
    for line in data["lines"]:
        for acct in line["accounts"]:
            if acct["account_number"] == number:
                return line
    raise AssertionError(f"{number} is on no line")


def _net_income(client):
    return client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31"
    ).json()["net_income"]


def test_every_seeded_expense_account_lands_in_total_expenses(client, year_of_books):
    data = _schedule(client)
    assert Decimal(str(data["total_expenses"])) == sum(year_of_books.values())
    for number in EXPENSES:
        line = _line_of(data, number)
        assert line["part"] == "expense", (number, line["line"])
    # gross income is receipts less returns: no expense line is counted as
    # income however its number starts (Lines 10, 13, 15, 17, 18)
    assert Decimal(str(data["gross_income"])) == RECEIPTS - RETURNS
    assert data["net_profit"] == pytest.approx(_net_income(client))


@pytest.mark.parametrize(
    "number, line_id",
    [
        ("4000", "1"),
        ("4100", "1"),
        ("6000", "8"),
        ("6100", "9"),
        ("6110", "26"),
        ("6120", "23"),
        ("6130", "15"),
        ("6150", "14"),
        ("6200", "27a"),
        ("6300", "15"),
        ("6400", "18"),
        ("6500", "20b"),
        ("6600", "21"),
        ("6700", "25"),
        ("6800", "22"),
        ("6810", "13"),
        ("6900", "25"),
        ("6950", "27a"),
    ],
)
def test_the_seeded_chart_lands_on_the_right_lines(
    client, year_of_books, number, line_id
):
    assert _line_of(_schedule(client), number)["line_id"] == line_id


def test_cost_of_goods_sold_is_line_4_and_reduces_gross_income(
    client, db_session, seed_accounts, year_of_books
):
    d = date(2026, 6, 1)
    for number in COGS:
        _post(db_session, seed_accounts, d, number, "1000", Decimal("25.00"))
    db_session.commit()
    cogs = Decimal("25.00") * len(COGS)
    data = _schedule(client)
    for number in COGS:
        assert _line_of(data, number)["line_id"] == "4", number
    assert Decimal(str(data["cost_of_goods_sold"])) == cogs
    assert Decimal(str(data["gross_receipts"])) == RECEIPTS - RETURNS
    # Line 7 gross income = receipts - returns - COGS (+ other income)
    assert Decimal(str(data["gross_income"])) == RECEIPTS - RETURNS - cogs
    assert Decimal(str(data["total_expenses"])) == sum(year_of_books.values())
    assert data["net_profit"] == pytest.approx(_net_income(client))


def test_a_user_mapping_still_wins(client, seed_accounts, year_of_books):
    for number, tax_line in (
        ("6950", "Line 18"),
        ("6200", "Schedule C, Line 10"),
        ("6960", "Charitable (review)"),
    ):
        r = client.post(
            "/api/tax/mappings",
            json={"account_id": seed_accounts[number].id, "tax_line": tax_line},
        )
        assert r.status_code == 201, r.text
    data = _schedule(client)
    assert _line_of(data, "6950")["line_id"] == "18"
    assert _line_of(data, "6400")["line_id"] == "18"  # joins the default
    assert _line_of(data, "6200")["line_id"] == "10"
    # a label that names no line keeps its label and still counts as expense
    odd = _line_of(data, "6960")
    assert odd["line"] == "Charitable (review)" and odd["part"] == "expense"
    assert Decimal(str(data["total_expenses"])) == sum(year_of_books.values())
    assert Decimal(str(data["gross_income"])) == RECEIPTS - RETURNS


def test_lines_come_in_form_order(client, year_of_books):
    ids = [ln["line_id"] for ln in _schedule(client)["lines"]]
    assert ids.index("1") < ids.index("8") < ids.index("9") < ids.index("13")
    assert ids.index("13") < ids.index("18") < ids.index("20b") < ids.index("27a")


def test_the_csv_carries_the_same_totals(client, year_of_books):
    r = client.get("/api/tax/schedule-c/csv?start_date=2026-01-01&end_date=2026-12-31")
    assert r.status_code == 200, r.text
    rows = {ln.split(",")[0]: ln.split(",")[-1] for ln in r.text.splitlines() if ln}
    assert rows["GROSS INCOME (LINE 7)"] == f"{RECEIPTS - RETURNS:.2f}"
    assert rows["TOTAL EXPENSES (LINE 28)"] == f"{sum(year_of_books.values()):.2f}"
    assert rows["Line 18 - Office expense"] == f"{year_of_books['6400']:.2f}"


def test_a_seeded_number_on_an_account_of_another_kind_follows_its_kind(
    client, db_session
):
    """An imported chart can use 5000 for an expense and 6400 for income;
    the seeded chart's line applies only to an account of the seeded kind."""
    from app.models.accounts import Account, AccountType

    accts = {
        "1000": Account(
            account_number="1000", name="Bank", account_type=AccountType.ASSET
        ),
        "5000": Account(
            account_number="5000", name="Advertising", account_type=AccountType.EXPENSE
        ),
        "6400": Account(
            account_number="6400", name="Rental Income", account_type=AccountType.INCOME
        ),
    }
    db_session.add_all(accts.values())
    db_session.flush()
    _post(db_session, accts, date(2026, 5, 1), "5000", "1000", Decimal("40.00"))
    _post(db_session, accts, date(2026, 5, 1), "1000", "6400", Decimal("300.00"))
    db_session.commit()
    data = _schedule(client)
    assert _line_of(data, "5000")["line_id"] == "27a"
    assert _line_of(data, "6400")["line_id"] == "1"
    assert Decimal(str(data["cost_of_goods_sold"])) == Decimal("0")
    assert Decimal(str(data["total_expenses"])) == Decimal("40.00")
    assert Decimal(str(data["gross_income"])) == Decimal("300.00")
