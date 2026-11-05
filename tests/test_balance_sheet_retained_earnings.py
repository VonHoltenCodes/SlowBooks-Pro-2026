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


# Two lines called Retained Earnings (#708, macbase1): with the posted 3200
# Retained Earnings holding a balance, the computed line of earlier years'
# net income is named "Net Income, prior years"; with none it stays
# Retained Earnings (the tests above). Its key does not change.


@pytest.fixture
def posted_re(db_session, books):
    """An opening balance posted to 3200 Retained Earnings in 2025."""
    _je(db_session, date(2025, 1, 1), books["1000"], books["3200"], "250.00")
    db_session.commit()
    return books


def _equity_names(d):
    return [
        (
            e["line"] if e["account_id"] is None else e["account_number"],
            e["account_name"],
            e["amount"],
        )
        for e in d["equity"]
    ]


def test_beside_a_posted_retained_earnings_the_line_is_prior_years(client, posted_re):
    d = _bs(client, "2026-06-30")
    names = _equity_names(d)
    assert ("3200", "Retained Earnings", 250.0) in names
    assert _computed(d) == [
        ("retained_earnings", "Net Income, prior years", 1300.0),
        ("net_income", "Net Income", 800.0),
    ]
    # one line, and one only, says Retained Earnings
    assert [n for _k, n, _a in names].count("Retained Earnings") == 1
    assert d["total_equity"] == 2350.0
    assert d["total_assets"] == d["total_liabilities"] + d["total_equity"]
    tree = [r["label"] for r in d["tree"]["equity"]]
    assert "Net Income, prior years" in tree
    assert sum("Retained Earnings" in label for label in tree) == 1


def test_the_prior_year_column_keeps_one_name(client, posted_re):
    """3200 holds a balance at 2025-12-31 and none a year earlier: the
    computed line is still one row, named for the sheet."""
    d = _bs(client, "2025-12-31", "&compare=prior_year")
    rows = [
        (e["line"], e["account_name"], e["amount"], e["prior_amount"])
        for e in d["equity"]
        if e["account_id"] is None
    ]
    # 2025-12-31: 800 from 2024 retained, 500 of 2025; 2024-12-31: all of
    # 2024's 800 is that year's net income
    assert rows == [
        ("retained_earnings", "Net Income, prior years", 800.0, 0.0),
        ("net_income", "Net Income", 500.0, 800.0),
    ]


def test_the_csv_and_pdf_print_the_new_name(client, posted_re, monkeypatch):
    rows = rows_of(
        client.get("/api/reports/balance-sheet/csv?as_of_date=2026-06-30").text
    )
    flat = [[c.strip() for c in r] for r in rows]
    assert any("Net Income, prior years" in r and "1300.00" in r for r in flat)
    assert any(any("Retained Earnings" in c for c in r) and "250.00" in r for r in flat)
    assert sum(any(c.endswith("Retained Earnings") for c in r) for r in flat) == 1

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
    assert by["Net Income, prior years"]["cells"][1] == "$1,300.00"
    assert by["Net Income"]["cells"][1] == "$800.00"
    assert "Retained Earnings" not in by
    assert sum(label.endswith("Retained Earnings") for label in by) == 1


def test_with_no_posted_balance_it_stays_retained_earnings(client, books):
    d = _bs(client, "2026-06-30")
    assert _computed(d)[0] == ("retained_earnings", "Retained Earnings", 1300.0)
    assert not any(e["account_number"] == "3200" for e in d["equity"] if e["amount"])


def test_a_nonprofit_keeps_its_words_beside_a_posted_balance(client, posted_re):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    d = _bs(client, "2026-06-30")
    assert _computed(d) == [
        ("retained_earnings", "Net Assets from Prior Years", 1300.0),
        ("net_income", "Change in Net Assets", 800.0),
    ]
