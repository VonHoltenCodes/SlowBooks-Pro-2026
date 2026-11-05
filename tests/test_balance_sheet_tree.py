"""Sub-accounts beneath their parents on the Balance Sheet (#565).

The P&L has printed a parent's family together since #416; the Balance
Sheet printed every account flat inside its #390 group. Now each group's
rows (and the equity side's) come from family_rows: the parent as a
heading, the sub-accounts indented beneath it, the parent's own postings
as "Direct postings to …", "Total …" at the heading's level — on the
screen (the API's `tree` per group), in the PDF and in the CSV, with and
without the prior-year comparison. A parent gathers only the sub-accounts
that print in its own group; one placed in another group by its detail
type prints there, flat, by its full path. No figure or subtotal moves,
and a flat chart prints exactly as before.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry
from app.services.ledger_exports import rows_of

AS_OF = "2026-12-31"


def _je(db, day, lines):
    create_journal_entry(
        db,
        day,
        "je",
        [
            {"account_id": a.id, "debit": Decimal(str(dr)), "credit": Decimal(str(cr))}
            for a, dr, cr in lines
        ],
    )


@pytest.fixture
def books(db_session, seed_accounts):
    a = dict(seed_accounts)

    def add(key, number, name, atype, detail, parent=None):
        acct = Account(
            account_number=number,
            name=name,
            account_type=atype,
            detail_type=detail,
            parent_id=a[parent].id if parent else None,
        )
        db_session.add(acct)
        db_session.flush()
        a[key] = acct

    A, L, E = AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY
    # a Current Assets family: a parent with its own postings and two subs
    add("deposits", "1250", "Deposits", A, "other_current_asset")
    add("dep_a", "1251", "Deposit A", A, "other_current_asset", "deposits")
    add("dep_b", "1252", "Deposit B", A, "other_current_asset", "deposits")
    # a sub-account typed into another group: prints flat in Other Assets
    add("dep_long", "1253", "Long-term deposit", A, "other_asset", "deposits")
    # a parent with no postings of its own still heads its family
    add("loans", "2600", "Loans", L, "long_term_liability")
    add("loan_bank", "2610", "Bank loan", L, "long_term_liability", "loans")
    # an equity family
    add("partners", "3300", "Partners", E, "equity")
    add("partner_a", "3310", "Partner A", E, "equity", "partners")
    add("partner_b", "3320", "Partner B", E, "equity", "partners")
    cash = a["1000"]
    last = date(2025, 6, 1)
    _je(db_session, last, [(cash, 1000, 0), (a["partner_a"], 0, 1000)])
    _je(db_session, last, [(a["dep_a"], 300, 0), (cash, 0, 300)])
    _je(db_session, last, [(a["deposits"], 50, 0), (cash, 0, 50)])
    this = date(2026, 3, 1)
    _je(db_session, this, [(cash, 2000, 0), (a["partner_b"], 0, 2000)])
    _je(db_session, this, [(cash, 500, 0), (a["partners"], 0, 500)])
    _je(db_session, this, [(a["dep_a"], 100, 0), (cash, 0, 100)])
    _je(db_session, this, [(a["dep_b"], 200, 0), (cash, 0, 200)])
    _je(db_session, this, [(a["deposits"], 25, 0), (cash, 0, 25)])
    _je(db_session, this, [(a["dep_long"], 700, 0), (cash, 0, 700)])
    _je(db_session, this, [(cash, 4000, 0), (a["loan_bank"], 0, 4000)])
    _je(db_session, this, [(cash, 900, 0), (a["4000"], 0, 900)])
    db_session.commit()
    return a


def _bs(client, qs=""):
    r = client.get(f"/api/reports/balance-sheet?as_of_date={AS_OF}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _group(d, side, heading):
    return next(g for g in d["groups"][side] if g["heading"] == heading)


def _shape(rows, key="amount"):
    return [(r["kind"], r["depth"], r["label"], r.get(key)) for r in rows]


def _flat_total_holds(rows, total, key="amount"):
    """Every line printed once: the account and direct rows add to the
    group's total, and so do the top level's lines and family totals."""
    lines = sum(
        Decimal(str(r[key])) for r in rows if r["kind"] in ("account", "direct")
    )
    top = sum(
        Decimal(str(r[key]))
        for r in rows
        if r["depth"] == 0 and r["kind"] in ("account", "total")
    )
    assert lines == top == Decimal(str(total)).quantize(Decimal("0.01"))


def test_a_current_assets_family_prints_beneath_its_parent(client, books):
    d = _bs(client)
    current = _group(d, "assets", "Current Assets")
    assert _shape(current["tree"]) == [
        ("account", 0, "1000 - Checking", 7025.0),
        ("heading", 0, "1250 - Deposits", None),
        ("account", 1, "1251 - Deposit A", 400.0),
        ("account", 1, "1252 - Deposit B", 200.0),
        ("direct", 1, "Direct postings to Deposits", 75.0),
        ("total", 0, "Total 1250 - Deposits", 675.0),
    ]
    # the direct row is the parent's own account, for the drill-down
    direct = next(r for r in current["tree"] if r["kind"] == "direct")
    assert direct["account_id"] == books["deposits"].id
    _flat_total_holds(current["tree"], d["total_current_assets"])
    # the flat list and the subtotal are what they were
    assert [i["account_number"] for i in current["lines"]] == [
        "1000",
        "1250",
        "1251",
        "1252",
    ]
    assert d["total_current_assets"] == 7025 + 675


def test_a_sub_account_typed_into_another_group_prints_there_flat(client, books):
    d = _bs(client)
    other = _group(d, "assets", "Other Assets")
    # by its full path, the way the flat statement named it
    assert _shape(other["tree"]) == [
        ("account", 0, "1253 - Deposits: Long-term deposit", 700.0)
    ]
    # and the parent's family total leaves it out
    total = next(
        r for r in _group(d, "assets", "Current Assets")["tree"] if r["kind"] == "total"
    )
    assert total["amount"] == 675.0
    assert d["total_other_assets"] == 700
    assert d["total_assets"] == 7025 + 675 + 700


def test_a_parent_with_no_postings_heads_its_liability_family(client, books):
    d = _bs(client)
    assert _shape(_group(d, "liabilities", "Long Term Liabilities")["tree"]) == [
        ("heading", 0, "2600 - Loans", None),
        ("account", 1, "2610 - Bank loan", 4000.0),
        ("total", 0, "Total 2600 - Loans", 4000.0),
    ]
    assert d["total_long_term_liabilities"] == 4000


def test_the_equity_family_prints_with_net_income_last(client, books):
    d = _bs(client)
    assert _shape(d["tree"]["equity"]) == [
        ("heading", 0, "3300 - Partners", None),
        ("account", 1, "3310 - Partner A", 1000.0),
        ("account", 1, "3320 - Partner B", 2000.0),
        ("direct", 1, "Direct postings to Partners", 500.0),
        ("total", 0, "Total 3300 - Partners", 3500.0),
        ("account", 0, "Net Income", 900.0),
    ]
    assert d["tree"]["equity"][-1]["account_id"] is None
    _flat_total_holds(d["tree"]["equity"], d["total_equity"])
    assert d["total_equity"] == 3500 + 900
    assert d["total_assets"] == round(d["total_liabilities"] + d["total_equity"], 2)


def test_the_prior_year_gives_each_family_total_every_column(client, books):
    d = _bs(client, "&compare=prior_year")
    current = _group(d, "assets", "Current Assets")["tree"]
    assert _shape(current, "prior_amount") == [
        ("account", 0, "1000 - Checking", 650.0),
        ("heading", 0, "1250 - Deposits", None),
        ("account", 1, "1251 - Deposit A", 300.0),
        ("account", 1, "1252 - Deposit B", 0.0),
        ("direct", 1, "Direct postings to Deposits", 50.0),
        ("total", 0, "Total 1250 - Deposits", 350.0),
    ]
    total = next(r for r in current if r["kind"] == "total")
    assert (total["amount"], total["prior_amount"]) == (675.0, 350.0)
    _flat_total_holds(current, d["total_current_assets"])
    _flat_total_holds(current, d["prior"]["total_current_assets"], "prior_amount")
    equity = d["tree"]["equity"]
    assert _shape(equity, "prior_amount")[-2:] == [
        ("total", 0, "Total 3300 - Partners", 1000.0),
        ("account", 0, "Net Income", 0.0),
    ]
    _flat_total_holds(equity, d["prior"]["total_equity"], "prior_amount")


def test_the_pdf_indents_the_families_inside_their_groups(client, books, monkeypatch):
    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?as_of_date={AS_OF}")
    rows = captured["s"][0]["rows"]
    by = {r["cells"][0].strip(): r for r in rows}
    # the group heading at 1, a family's heading at 2, its lines at 3
    assert by["Current Assets"]["style"] == "group"
    assert by["1250 - Deposits"] == {
        "cells": ["  1250 - Deposits", ""],
        "style": "group",
        "indent": 2,
    }
    assert by["1251 - Deposit A"]["indent"] == 3
    assert by["Direct postings to Deposits"]["cells"][1] == "$75.00"
    assert by["Total 1250 - Deposits"]["style"] == "subtotal"
    assert by["Total 1250 - Deposits"]["indent"] == 2
    assert by["Total 1250 - Deposits"]["cells"][1] == "$675.00"
    assert by["Total Current Assets"]["cells"][1] == "$7,700.00"
    # the child placed in another group, flat, by its path
    assert by["1253 - Deposits: Long-term deposit"]["indent"] == 2
    # equity is one section: its family's heading at 1
    assert by["3300 - Partners"]["indent"] == 1
    assert by["3310 - Partner A"]["indent"] == 2
    assert by["Total 3300 - Partners"]["cells"][1] == "$3,500.00"
    words = [r["cells"][0].strip() for r in rows]
    assert (
        words.index("Total 3300 - Partners")
        < words.index("Net Income")
        < words.index("Total Equity")
    )
    assert "1250 - Deposits: Deposit A" not in by  # the path is the indent now


def test_the_pdf_with_prior_year_gives_a_family_total_every_column(
    client, books, monkeypatch
):
    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?as_of_date={AS_OF}&compare=prior_year")
    section = captured["s"][0]
    assert section["columns"] == ["", "Current", "Prior Year", "$ Change", "% Change"]
    by = {r["cells"][0].strip(): r for r in section["rows"]}
    assert by["Total 1250 - Deposits"]["cells"] == [
        "  Total 1250 - Deposits",
        "$675.00",
        "$350.00",
        "$325.00",
        "92.9%",
    ]
    assert by["1250 - Deposits"]["cells"] == ["  1250 - Deposits", "", "", "", ""]


def test_the_csv_carries_the_families_as_the_screen_does(client, books):
    rows = rows_of(
        client.get(f"/api/reports/balance-sheet/csv?as_of_date={AS_OF}").text
    )
    assert rows[0] == ["Section", "Account number", "Account name", "Amount", "Type"]
    current = [r for r in rows if r[0] == "Current Assets"]
    assert [(r[1], r[2], r[3], r[4]) for r in current] == [
        ("1000", "Checking", "7025.00", "Assets"),
        ("1250", "Deposits", "", "Assets"),
        ("1251", "  Deposits: Deposit A", "400.00", "Assets"),
        ("1252", "  Deposits: Deposit B", "200.00", "Assets"),
        ("1250", "  Direct postings to Deposits", "75.00", "Assets"),
        ("", "Total 1250 - Deposits", "675.00", "Assets"),
        ("", "Total Current Assets", "7700.00", "Assets"),
    ]
    other = [r for r in rows if r[0] == "Other Assets"]
    assert [(r[1], r[2], r[3]) for r in other] == [
        ("1253", "Deposits: Long-term deposit", "700.00"),
        ("", "Total Other Assets", "700.00"),
    ]
    equity = [r for r in rows if r[0] == "Equity"]
    assert [(r[1], r[2], r[3], r[4]) for r in equity] == [
        ("3300", "Partners", "", "Equity"),
        ("3310", "  Partners: Partner A", "1000.00", "Equity"),
        ("3320", "  Partners: Partner B", "2000.00", "Equity"),
        ("3300", "  Direct postings to Partners", "500.00", "Equity"),
        ("", "Total 3300 - Partners", "3500.00", "Equity"),
        ("", "Net Income", "900.00", "Equity"),
        ("", "Total Equity", "4400.00", "Equity"),
    ]


def test_the_csv_with_prior_year_keeps_the_columns(client, books):
    rows = rows_of(
        client.get(
            f"/api/reports/balance-sheet/csv?as_of_date={AS_OF}&compare=prior_year"
        ).text
    )
    assert rows[0] == [
        "Section",
        "Account number",
        "Account name",
        "Current",
        "Prior year",
        "$ Change",
        "% Change",
        "Type",
    ]
    total = next(r for r in rows if r[2] == "Total 1250 - Deposits")
    assert total == [
        "Current Assets",
        "",
        "Total 1250 - Deposits",
        "675.00",
        "350.00",
        "325.00",
        "92.9%",
        "Assets",
    ]
    heading = next(r for r in rows if r[1] == "1250" and r[2] == "Deposits")
    assert heading[3:7] == ["", "", "", ""]


def test_the_accountants_package_prints_the_families(client, books, monkeypatch):
    """The statements pack builds its Balance Sheet from _bs_section."""
    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(
        "/api/reports/financial-statements/pdf"
        f"?start_date=2026-01-01&end_date={AS_OF}"
    )
    bs = next(s for s in captured["s"] if s["title"] == "Balance Sheet")
    words = [r["cells"][0].strip() for r in bs["rows"]]
    assert "Direct postings to Deposits" in words
    assert words.index("1250 - Deposits") < words.index("Total 1250 - Deposits")


@pytest.fixture
def flat_books(db_session, seed_accounts):
    """The seeded chart alone: no sub-accounts."""
    a = seed_accounts
    cash = a["1000"]
    _je(db_session, date(2026, 3, 1), [(cash, 5000, 0), (a["2400"], 0, 5000)])
    _je(db_session, date(2026, 3, 1), [(a["1600"], 3000, 0), (cash, 0, 3000)])
    _je(db_session, date(2026, 4, 1), [(cash, 800, 0), (a["4000"], 0, 800)])
    db_session.commit()
    return a


def test_a_flat_chart_prints_exactly_as_before(client, flat_books, monkeypatch):
    d = _bs(client, "&compare=prior_year")
    for side in ("assets", "liabilities"):
        for g in d["groups"][side]:
            # the tree is the flat lines, one row each, nothing added
            assert [
                (r["kind"], r["depth"], r["label"], r["amount"], r["prior_amount"])
                for r in g["tree"]
            ] == [
                ("account", 0, i["account_label"], i["amount"], i["prior_amount"])
                for i in g["lines"]
            ]
    assert [(r["kind"], r["depth"], r["label"]) for r in d["tree"]["equity"]] == [
        ("account", 0, i["account_label"]) for i in d["equity"]
    ]
    rows = rows_of(
        client.get(f"/api/reports/balance-sheet/csv?as_of_date={AS_OF}").text
    )
    # no family row, no indent: the lines, each group's subtotal, the totals
    assert all(not r[2].startswith(" ") for r in rows)
    assert [r for r in rows[1:] if r[2].startswith("Total ")] == [
        r for r in rows[1:] if r[1] == "" and r[2] != "Net Income"
    ][:-1]
    names = [r[2] for r in rows[1:]]
    assert names == [
        "Checking",
        "Total Current Assets",
        "Vehicles",
        "Total Fixed Assets",
        "Total Assets",
        "Loan Payable",
        "Total Long Term Liabilities",
        "Total Liabilities",
        "Net Income",
        "Total Equity",
        "Liabilities + Equity",
    ]

    from app.routes.reports import financial

    captured = {}
    monkeypatch.setattr(
        financial,
        "_pdf_response",
        lambda sections, db, filename, landscape=False: captured.update(s=sections)
        or {},
    )
    client.get(f"/api/reports/balance-sheet/pdf?as_of_date={AS_OF}")
    rows = captured["s"][0]["rows"]
    assert [(r["cells"][0].strip(), r.get("style"), r.get("indent")) for r in rows] == [
        ("Assets", "subtotal", None),
        ("Current Assets", "group", 1),
        ("1000 - Checking", None, 2),
        ("Total Current Assets", "subtotal", 1),
        ("Fixed Assets", "group", 1),
        ("1600 - Vehicles", None, 2),
        ("Total Fixed Assets", "subtotal", 1),
        ("Total Assets", "subtotal", None),
        ("Liabilities", "subtotal", None),
        ("Long Term Liabilities", "group", 1),
        ("2400 - Loan Payable", None, 2),
        ("Total Long Term Liabilities", "subtotal", 1),
        ("Total Liabilities", "subtotal", None),
        ("Equity", "subtotal", None),
        ("Net Income", None, 1),
        ("Total Equity", "subtotal", None),
        ("Liabilities + Equity", "grand-total", None),
    ]
