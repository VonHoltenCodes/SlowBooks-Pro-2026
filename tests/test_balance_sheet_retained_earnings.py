"""Balance Sheet: Retained Earnings and Net Income as two lines (#708).

One computed line, "Net Income (current period)", carried every year's
net income to the as-of date. Now the years before the fiscal year the
as-of date falls in print as Retained Earnings and that fiscal year's to
the date as Net Income, as QuickBooks prints them: the JSON, the PDF and
the CSV, with the prior-year comparison too. Total equity does not move.
The fiscal year's first month comes from settings; a nonprofit keeps its
own words.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of


def _je(db, day, debit, credit, amount):
    create_journal_entry(
        db,
        day,
        "je",
        [
            {"account_id": debit.id, "debit": Decimal(amount), "credit": Decimal(0)},
            {"account_id": credit.id, "debit": Decimal(0), "credit": Decimal(amount)},
        ],
    )


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)
    cash, income = a["1000"], a["4000"]
    expense = next(acct for acct in a.values() if acct.account_type.value == "expense")
    # 2024: 1,000 in, 200 out; 2025: 500 in; 2026: 900 in, 100 out
    _je(db_session, date(2024, 3, 1), cash, income, "1000.00")
    _je(db_session, date(2024, 9, 1), expense, cash, "200.00")
    _je(db_session, date(2025, 8, 15), cash, income, "500.00")
    _je(db_session, date(2026, 2, 1), cash, income, "900.00")
    _je(db_session, date(2026, 4, 1), expense, cash, "100.00")
    db_session.commit()
    return a


def _bs(client, as_of, qs=""):
    r = client.get(f"/api/reports/balance-sheet?as_of_date={as_of}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _computed(d, key="amount"):
    return [
        (e["line"], e["account_name"], e[key])
        for e in d["equity"]
        if e["account_id"] is None
    ]


def test_the_years_before_are_retained_earnings_this_years_is_net_income(client, books):
    d = _bs(client, "2026-06-30")
    assert _computed(d) == [
        ("retained_earnings", "Retained Earnings", 1300.0),
        ("net_income", "Net Income", 800.0),
    ]
    # the same total equity the one line gave
    assert d["total_equity"] == 2100.0
    assert d["total_assets"] == d["total_liabilities"] + d["total_equity"]
    # the P&L for the fiscal year to the date is the Net Income line
    pl = client.get(
        "/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    assert pl["net_income"] == 800.0


def test_the_fiscal_year_comes_from_settings(client, books):
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    # FY2026 runs 2025-07-01 to 2026-06-30: the August 2025 income is this
    # fiscal year's
    d = _bs(client, "2026-06-30")
    assert _computed(d) == [
        ("retained_earnings", "Retained Earnings", 800.0),
        ("net_income", "Net Income", 1300.0),
    ]
    assert d["total_equity"] == 2100.0


def test_no_prior_years_no_retained_earnings_line(client, books):
    d = _bs(client, "2024-12-31")
    assert _computed(d) == [("net_income", "Net Income", 800.0)]


def test_the_first_day_of_a_fiscal_year_has_retained_earnings_only(client, books):
    d = _bs(client, "2026-01-01")
    assert _computed(d) == [("retained_earnings", "Retained Earnings", 1300.0)]


def test_the_prior_year_column_splits_the_same_way(client, books):
    d = _bs(client, "2026-06-30", "&compare=prior_year")
    rows = [
        (e["line"], e["amount"], e["prior_amount"])
        for e in d["equity"]
        if e["account_id"] is None
    ]
    # 2025-06-30: 800 before 2025, nothing in 2025 to the date
    assert rows == [
        ("retained_earnings", 1300.0, 800.0),
        ("net_income", 800.0, 0.0),
    ]
    assert d["prior"]["total_equity"] == 800.0
    tree = [r["label"] for r in d["tree"]["equity"] if r.get("account_id") is None]
    assert tree[-2:] == ["Retained Earnings", "Net Income"]


def test_the_csv_and_pdf_print_both_lines(client, books, monkeypatch):
    rows = rows_of(
        client.get("/api/reports/balance-sheet/csv?as_of_date=2026-06-30").text
    )
    flat = [[c.strip() for c in r] for r in rows]
    assert any("Retained Earnings" in r and "1300.00" in r for r in flat)
    assert any("Net Income" in r and "800.00" in r for r in flat)

    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get("/api/reports/balance-sheet/pdf?as_of_date=2026-06-30")
    by = {r["cells"][0].strip(): r for r in captured["s"][0]["rows"]}
    assert by["Retained Earnings"]["cells"][1] == "$1,300.00"
    assert by["Net Income"]["cells"][1] == "$800.00"


def test_a_nonprofit_keeps_its_words(client, books):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    d = _bs(client, "2026-06-30")
    assert _computed(d) == [
        ("retained_earnings", "Net Assets from Prior Years", 1300.0),
        ("net_income", "Change in Net Assets", 800.0),
    ]
