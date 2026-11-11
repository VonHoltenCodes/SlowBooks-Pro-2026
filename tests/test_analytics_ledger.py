"""Analytics reads the ledger (issue #281).

Expenses by category summed bill lines on paid bills, and the revenue trend
paid invoices: a company that reconciles through a bank feed and records
the rest as journal entries saw an empty doughnut and an AI brief with
nothing in it. Both read the ledger now, as the Profit & Loss does — and so
do the four KPI cards, the PDF's and the AI brief's headline figures, on
one basis (2.22.3 gate, NEW-73 / W-2: the margin divided paid-invoice
revenue into ledger expenses). Categories carry the account's name
(NEW-74), the AI action says its figures are the ledger's (NEW-77 / W-3),
and the PDF heads the trend "Revenue (ledger)" (NEW-76 / W-1).
"""

import csv
import io
from datetime import date, timedelta
from pathlib import Path

from app.services import ai_tools
from app.services.ai_actions import ACTIONS, _format_user_prompt
from app.services.ai_service import build_insights_prompt
from app.services.analytics import AnalyticsEngine, account_label

ROOT = Path(__file__).resolve().parents[1]


def _this_month():
    return date.today().replace(day=1).isoformat()


def _today():
    # today as the code under test reads it, at the moment the test runs: a
    # module constant, taken at import, failed seven tests when a CI run
    # crossed midnight UTC (#773)
    return date.today().isoformat()


def _period():
    return {"start_date": _this_month(), "end_date": _today()}


def _journal(client, seed_accounts, dr, cr, amount, when=None, text="Entry"):
    if when is None:
        when = _today()
    r = client.post(
        "/api/journal",
        json={
            "date": when,
            "description": text,
            "lines": [
                {
                    "account_id": seed_accounts[dr].id,
                    "debit": str(amount),
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts[cr].id,
                    "debit": "0",
                    "credit": str(amount),
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dashboard(client):
    r = client.get("/api/analytics/dashboard", params=_period())
    assert r.status_code == 200, r.text
    return r.json()


def _expenses(client):
    return _dashboard(client)["expenses_by_category"]


def _label(seed_accounts, number):
    return f"{number} - {seed_accounts[number].name}"


def test_the_label_is_the_profit_and_losss():
    assert (
        account_label("6100", "Auto & Truck Expense") == "6100 - Auto & Truck Expense"
    )
    assert account_label(None, "Postage") == "Postage"
    assert account_label("6100", "") == "6100"
    assert account_label(None, None) == "Uncategorized"


def test_a_journal_entry_and_a_register_entry_count_as_expenses(client, seed_accounts):
    """The reporter's company: no Bills, a bank feed and journal entries."""
    assert _expenses(client) == {}
    # the owner paid the hosting personally: expense against equity
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    # a categorised register entry: money out of Checking to an expense account
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": _today(),
            "amount": "-120.00",
            "category_account_id": seed_accounts["6100"].id,
            "payee": "Corner Hardware",
        },
    )
    assert r.status_code == 201, r.text
    got = _expenses(client)
    # named as the P&L names them, not by number alone (NEW-74)
    assert got == {
        _label(seed_accounts, "6000"): 48.0,
        _label(seed_accounts, "6100"): 120.0,
    }, got


def test_an_unpaid_bill_counts_when_dated_and_a_void_nets_out(client, seed_accounts):
    """Accrual, as the P&L: a bill is an expense when it is dated, paid or
    not; an expense voided is gone."""
    vendor = client.post("/api/vendors", json={"name": "Pier Lumber"})
    assert vendor.status_code == 201, vendor.text
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.json()["id"],
            "date": _today(),
            "lines": [
                {
                    "description": "Lumber",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": "300.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    ads, auto = _label(seed_accounts, "6000"), _label(seed_accounts, "6100")
    assert _expenses(client)[ads] == 300.0  # unpaid, and counted
    ex = client.post(
        "/api/expenses",
        json={
            "date": _today(),
            "payee": "Corner Hardware",
            "expense_account_id": seed_accounts["6100"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "42.50",
        },
    )
    assert ex.status_code == 201, ex.text
    assert _expenses(client)[auto] == 42.5
    assert client.post(f"/api/expenses/{ex.json()['id']}/void").status_code == 200
    assert auto not in _expenses(client)  # the reversal nets it to zero


def test_the_revenue_trend_reads_income_postings(client, seed_accounts):
    """Income booked by a journal entry (a deposit a bank-fed company
    records straight to the ledger) shows in the trend's month."""
    key = date.today().strftime("%Y-%m")
    before = client.get("/api/analytics/dashboard").json()["revenue_trend"]
    assert before[key] == 0.0
    _journal(
        client, seed_accounts, "1000", "4000", "2500.00", text="Deposit, consulting"
    )
    after = client.get("/api/analytics/dashboard").json()["revenue_trend"]
    assert after[key] == 2500.0
    assert len(after) == 12 and list(after)[-1] == key


def test_revenue_by_customer_is_still_paid_invoices(client, seed_accounts):
    """Ledger lines carry no customer: that table keeps its paid basis, and
    the page says so (tests/test_analytics_basis.py pins the labels)."""
    _journal(client, seed_accounts, "1000", "4000", "2500.00")
    assert _dashboard(client)["revenue_by_customer"] == {}


def test_the_headline_figures_are_on_one_basis(client, seed_accounts):
    """The gate's ledger company: $2,500 of journal-entry income, $468 of
    expenses, no invoices. The P&L has net income $2,032 and a margin of
    81.3%; the dashboard's totals say the same (NEW-73 / W-2: they read
    "Revenue $0, Net income $-468, Margin 0.0%")."""
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    _journal(client, seed_accounts, "6100", "1000", "420.00", text="Truck repair")
    _journal(client, seed_accounts, "1000", "4000", "2500.00", text="Deposit")
    dash = _dashboard(client)
    assert dash["totals"] == {
        "basis": "ledger",
        "revenue": 2500.0,
        "expenses": 468.0,
        "net_income": 2032.0,
        "margin_pct": 81.3,
    }
    assert dash["revenue_by_customer"] == {}  # the paid basis, its own section
    pl = client.get("/api/reports/profit-loss", params=_period()).json()
    assert float(pl["total_income"]) == 2500.0
    assert float(pl["net_income"]) == 2032.0


def test_no_revenue_means_no_margin(client, seed_accounts):
    """Expenses and nothing to divide them by: the margin is not 0.0%, it is
    absent (the page and the PDF show a dash)."""
    _journal(client, seed_accounts, "6000", "3000", "48.00")
    totals = _dashboard(client)["totals"]
    assert totals["revenue"] == 0.0 and totals["net_income"] == -48.0
    assert totals["margin_pct"] is None


def test_the_ai_brief_reads_the_same_totals(client, seed_accounts):
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    _journal(client, seed_accounts, "6100", "1000", "420.00", text="Truck repair")
    _journal(client, seed_accounts, "1000", "4000", "2500.00", text="Deposit")
    dash = _dashboard(client)
    dash["period"] = {"name": "month", "start": _this_month(), "end": _today()}
    prompt = build_insights_prompt(dash, "Ledger Scratch")
    assert (
        "=== KEY METRICS (the ledger, as the Profit & Loss counts them) ===" in prompt
    )
    assert "Revenue:     $2,500" in prompt
    assert "Expenses:    $468" in prompt
    assert "Net income:  $2,032" in prompt
    assert "Margin:      81.3%" in prompt
    # the categories are named, so the model can name them
    assert f"  {_label(seed_accounts, '6100')}: $420" in prompt
    assert "(invoices and sales receipts paid in full)" in prompt
    assert "can total less than Revenue" in prompt
    # and with no revenue the margin says so instead of 0.0%
    empty = build_insights_prompt(
        {
            "totals": {
                "revenue": 0.0,
                "expenses": 48.0,
                "net_income": -48.0,
                "margin_pct": None,
            }
        }
    )
    assert "Margin:      n/a (no revenue in the period)" in empty


def test_the_ai_tool_answers_from_the_same_ledger(client, db_session, seed_accounts):
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    _journal(
        client, seed_accounts, "6100", "3000", "12.00", when="2024-01-15", text="Old"
    )
    got = ai_tools.get_expenses_by_category(db_session, _this_month(), _today())
    assert got["total"] == 48.0
    assert [r["category"] for r in got["results"]] == [_label(seed_accounts, "6000")]
    everything = ai_tools.get_expenses_by_category(db_session)
    assert everything["total"] == 60.0  # no dates: all time, as before
    # the engine's own rows, shared by both
    rows = AnalyticsEngine(db_session).expenses_by_account(
        date.fromisoformat(_this_month()), date.today()
    )
    assert [(n, a) for n, _, a in rows] == [("6000", 48.0)]


def test_the_ai_action_says_its_figures_are_the_ledgers():
    """The dropdown's "Expenses by category" framed its data as vendor bills
    and sat under Vendors & Bills (NEW-77 / W-3)."""
    spec = ACTIONS["expenses_by_category"]
    assert spec.category == "Financial Reports"
    assert spec.framing.startswith(
        "Expenses for the period by account, from the ledger"
    )
    assert (
        "journal entries" in spec.framing and "Not vendor bills alone" in spec.framing
    )
    prompt = _format_user_prompt(
        spec, {"results": [], "total": 0}, date(2026, 10, 1), date(2026, 10, 12)
    )
    assert prompt.startswith(spec.framing)
    assert "Vendor bill expenses" not in prompt


def test_the_action_catalogue_lists_it_under_financial_reports(client):
    groups = client.get("/api/analytics/ai-actions").json()["groups"]
    by = {g["category"]: [a["key"] for a in g["actions"]] for g in groups}
    assert "expenses_by_category" in by["Financial Reports"]
    assert "expenses_by_category" not in by.get("Vendors & Bills", [])


def test_the_csv_carries_the_totals_and_the_named_categories(client, seed_accounts):
    _journal(client, seed_accounts, "6000", "3000", "48.00")
    _journal(client, seed_accounts, "1000", "4000", "2500.00")
    r = client.get("/api/analytics/export.csv", params=_period())
    assert r.status_code == 200, r.text
    rows = [
        row
        for row in csv.reader(io.StringIO(r.content.decode("utf-8-sig")))
        if row and not row[0].startswith("#")
    ]
    assert ["totals", "revenue", "ledger", "2500.00"] in rows
    assert ["totals", "expenses", "ledger", "48.00"] in rows
    assert ["totals", "net_income", "ledger", "2452.00"] in rows
    assert ["totals", "margin_pct", "ledger", "98.1"] in rows
    assert ["expenses_by_category", _label(seed_accounts, "6000"), "", "48.00"] in rows


def test_the_table_is_the_doughnuts_legend():
    """Chart.js's legend clipped "6000 - Advertising & Marketing" at the
    card's edge once the categories were named, so it is off; the table
    under the doughnut carries each slice's swatch, from the same palette in
    the same order, by theme variable so a theme switch recolours it."""
    js = (ROOT / "app" / "static" / "js" / "analytics.js").read_text(encoding="utf-8")
    assert "legend: { display: false }" in js
    assert (
        'class="chart-swatch" style="background:var(--chart-${palette[i % palette.length]})"'
        in js
    )
    assert "const palette = this.EXPENSE_PALETTE.map((name) => chartColor(name));" in js
    assert "const palette = this.EXPENSE_PALETTE;" in js
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert ".chart-swatch {" in css
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in css


def test_a_net_credit_account_has_no_slice_and_no_swatch(client, seed_accounts):
    """A refund larger than the spending nets an expense account below zero.
    Chart.js sizes a doughnut's arcs by absolute value, so the ring drew it
    as spending (NEW-78). It keeps its row and its place in the totals; the
    ring and the swatches are the accounts with net spending."""
    _journal(client, seed_accounts, "6000", "3000", "48.00", text="Owner paid hosting")
    _journal(client, seed_accounts, "6100", "1000", "120.00", text="Truck repair")
    _journal(client, seed_accounts, "1000", "6100", "200.00", text="Refund")
    dash = _dashboard(client)
    assert dash["expenses_by_category"] == {
        _label(seed_accounts, "6000"): 48.0,
        _label(seed_accounts, "6100"): -80.0,
    }
    assert dash["totals"]["expenses"] == -32.0
    js = (ROOT / "app" / "static" / "js" / "analytics.js").read_text(encoding="utf-8")
    assert ".filter(([, a]) => Number(a) > 0)" in js  # _expenseSlices
    assert (
        "const entries = this._expenseSlices(this.state.data.expenses_by_category);"
        in js
    )
    assert "const sliced = this._expenseSlices(byCategory).map(([c]) => c);" in js
    assert "return i < 0" in js  # a row off the ring has no swatch


def test_no_paid_sales_in_thirty_days_means_no_dso(
    client, seed_accounts, seed_customer
):
    """$125k open and nothing paid in the last 30 days read "DSO 0.0 — lower
    is better" (NEW-80 / W-4). There is no collection period to measure:
    the dashboard says None, the page and the PDF a dash, the brief n/a."""
    cid = seed_customer.id
    old = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": (date.today() - timedelta(days=60)).isoformat(),
            "lines": [{"description": "work", "quantity": 1, "rate": "500.00"}],
        },
    )
    assert old.status_code == 201, old.text
    assert client.post(f"/api/invoices/{old.json()['id']}/send").status_code in (
        200,
        201,
    )
    dash = client.get("/api/analytics/dashboard").json()
    assert dash["dso"] is None
    assert sum(sum(b.values()) for b in dash["ar_aging"].values()) == 500.0
    prompt = build_insights_prompt(dash)
    assert (
        "DSO:         n/a (no invoice or sales receipt dated in the last 30 days is paid in full)"
        in prompt
    )
    r = client.get("/api/analytics/export.csv")
    rows = [row for row in csv.reader(io.StringIO(r.content.decode("utf-8-sig")))]
    assert ["dso", "days", "", ""] in rows
    # a sale paid this month: a number again
    paid = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": _today(),
            "lines": [{"description": "work", "quantity": 1, "rate": "250.00"}],
        },
    )
    assert paid.status_code == 201, paid.text
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": _today(),
            "amount": "250.00",
            "allocations": [{"invoice_id": paid.json()["id"], "amount": "250.00"}],
        },
    )
    assert pay.status_code == 201, pay.text
    assert (
        client.get("/api/analytics/dashboard").json()["dso"] == 60.0
    )  # 500 / 250 × 30
    js = (ROOT / "app" / "static" / "js" / "analytics.js").read_text(encoding="utf-8")
    assert "const dso = data.dso == null ? null : Number(data.dso);" in js
    assert 'const dsoText = dso == null ? "&mdash;" : dso.toFixed(1);' in js
    assert "no collection period to measure" in js
    # the words say what the figure counts: invoices and sales receipts DATED in
    # the window and paid in full — a payment received today on an older invoice
    # is not in it (NEW-82)
    assert (
        "No invoice or sales receipt dated in the last 30 days has been paid in full"
        in js
    )
    assert (
        "invoices and sales receipts dated in the last 30 days and paid in full) × 30"
        in js
    )


def test_the_trend_card_has_its_table():
    """A point at the chart's edge or top could not carry its figure and the
    card had no table (NEW-81 / W-5): it has one now, as the PDF does."""
    js = (ROOT / "app" / "static" / "js" / "analytics.js").read_text(encoding="utf-8")
    assert "${this._trendTable(data.revenue_trend)}" in js
    assert (
        '<th scope="col">Month</th><th scope="col" class="amount">Revenue (ledger)</th>'
        in js
    )


def test_a_payment_today_on_an_older_invoice_is_not_in_the_dso(
    client, seed_accounts, seed_customer
):
    """NEW-82: the denominator is invoices DATED in the last 30 days and paid
    in full, so paying a 45-day-old invoice today leaves the DSO absent, and
    the words say so rather than "nothing was paid"."""
    cid = seed_customer.id
    old = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": (date.today() - timedelta(days=45)).isoformat(),
            "lines": [{"description": "work", "quantity": 1, "rate": "500.00"}],
        },
    )
    assert old.status_code == 201, old.text
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": _today(),
            "amount": "500.00",
            "allocations": [{"invoice_id": old.json()["id"], "amount": "500.00"}],
        },
    )
    assert pay.status_code == 201, pay.text
    dash = client.get("/api/analytics/dashboard").json()
    assert dash["dso"] is None
    assert "is paid in full" in build_insights_prompt(dash)
