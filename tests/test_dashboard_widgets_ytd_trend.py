"""Tests for the pnl_ytd and balance_sheet_trend dashboard widgets.

pnl_ytd: year-to-date income/expenses/net from the ledger, plus the
cumulative net by month within the year (current month month-to-date).

balance_sheet_trend: assets / liabilities / equity at each of the last
12 month-ends. Mirrors /api/reports/balance-sheet semantics — in
particular, current net income (never closed into equity in this app)
must fold into equity so the series balances (A = L + E) at every point.
"""

from datetime import date
from decimal import Decimal

from app.models.accounts import AccountType


def _month_back(i):
    """The (year, month) i months before today — same back-counting the
    monthly_revenue widget uses, so tests are stable across year ends."""
    today = date.today()
    year, month = today.year, today.month - i
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def _post(db_session, *, debit_account_id, credit_account_id, amount, when):
    """One balanced journal entry: debit one account, credit another."""
    from app.models.transactions import Transaction, TransactionLine

    txn = Transaction(
        date=when,
        reference="TEST",
        description="dashboard widget test entry",
        source_type="journal",
    )
    db_session.add(txn)
    db_session.flush()
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=debit_account_id,
                debit=Decimal(str(amount)),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=credit_account_id,
                debit=Decimal("0"),
                credit=Decimal(str(amount)),
            ),
        ]
    )
    db_session.commit()
    return txn


def _accounts_of_type(seed_accounts, acct_type):
    return [a for a in seed_accounts.values() if a.account_type == acct_type]


def _pick(seed_accounts, acct_type):
    return _accounts_of_type(seed_accounts, acct_type)[0].id


# ---------------------------------------------------------------------------
# pnl_ytd
# ---------------------------------------------------------------------------


def test_pnl_ytd_totals_and_cumulative(client, db_session, seed_accounts):
    """Income and expenses posted this year surface as YTD totals, and the
    cumulative series runs month by month up to the current month."""
    income_id = _pick(seed_accounts, AccountType.INCOME)
    expense_id = _pick(seed_accounts, AccountType.EXPENSE)
    asset_id = _pick(seed_accounts, AccountType.ASSET)

    # +1000 income this month, −400 expense this month.
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=income_id,
        amount="1000.00",
        when=date.today(),
    )
    _post(
        db_session,
        debit_account_id=expense_id,
        credit_account_id=asset_id,
        amount="400.00",
        when=date.today(),
    )

    resp = client.get("/api/dashboard/data?ids=pnl_ytd")
    assert resp.status_code == 200, resp.text
    d = resp.json()["pnl_ytd"]

    assert d["income"] == 1000.00
    assert d["expenses"] == 400.00
    assert d["net"] == 600.00
    # "year" is the fiscal-year label (#444); a January start is the calendar year
    assert d["year"] == str(date.today().year)
    # One slot per month of the year so far, ending with the current month.
    assert len(d["months"]) == date.today().month
    assert d["months"][-1]["cumulative"] == 600.00
    assert d["months"][-1]["net"] == 600.00


def test_pnl_ytd_ignores_last_years_activity(client, db_session, seed_accounts):
    """Only the current calendar year counts — last year's net must not
    leak into the YTD totals."""
    income_id = _pick(seed_accounts, AccountType.INCOME)
    asset_id = _pick(seed_accounts, AccountType.ASSET)

    year, month = _month_back(13)  # ~last year, guaranteed before Jan 1
    if (year, month) >= (date.today().year, 1):
        return  # pragma: no cover — calendar edge, nothing to prove
    from datetime import date as _date

    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=income_id,
        amount="9999.00",
        when=_date(year, month, 15),
    )

    resp = client.get("/api/dashboard/data?ids=pnl_ytd")
    d = resp.json()["pnl_ytd"]
    assert d["income"] == 0.0
    assert d["net"] == 0.0


# ---------------------------------------------------------------------------
# balance_sheet_trend
# ---------------------------------------------------------------------------


def test_balance_sheet_trend_balances_every_month(client, db_session, seed_accounts):
    """The core invariant: assets = liabilities + equity at every month end,
    because current net income folds into equity."""
    asset_id = _pick(seed_accounts, AccountType.ASSET)
    liab_id = _pick(seed_accounts, AccountType.LIABILITY)
    equity_id = _pick(seed_accounts, AccountType.EQUITY)
    income_id = _pick(seed_accounts, AccountType.INCOME)
    expense_id = _pick(seed_accounts, AccountType.EXPENSE)

    today = date.today()
    # Owner contributes 5000 cash, borrows 2000, earns 1200, spends 300.
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=equity_id,
        amount="5000.00",
        when=date(today.year, 1, 10),
    )
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=liab_id,
        amount="2000.00",
        when=date(today.year, 1, 15),
    )
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=income_id,
        amount="1200.00",
        when=date(today.year, max(1, today.month - 1), 5) if today.month > 1 else today,
    )
    _post(
        db_session,
        debit_account_id=expense_id,
        credit_account_id=asset_id,
        amount="300.00",
        when=today,
    )

    resp = client.get("/api/dashboard/data?ids=balance_sheet_trend")
    assert resp.status_code == 200, resp.text
    d = resp.json()["balance_sheet_trend"]

    assert len(d["months"]) == 12
    for m in d["months"]:
        assert m["assets"] == m["liabilities"] + m["equity"], (
            f"{m['month']} {m['year']}: A={m['assets']} != L+E="
            f"{m['liabilities'] + m['equity']} — net income fold is broken"
        )

    last = d["months"][-1]
    assert last["assets"] == 7900.00  # 5000 + 2000 + 1200 − 300
    assert last["liabilities"] == 2000.00
    assert last["equity"] == 5900.00  # 5000 + net income 900


def test_balance_sheet_trend_equity_includes_net_income_only(
    client, db_session, seed_accounts
):
    """With no explicit equity activity, equity is exactly the cumulative
    net income — the case that breaks naive implementations."""
    asset_id = _pick(seed_accounts, AccountType.ASSET)
    income_id = _pick(seed_accounts, AccountType.INCOME)
    expense_id = _pick(seed_accounts, AccountType.EXPENSE)

    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=income_id,
        amount="500.00",
        when=date.today(),
    )
    _post(
        db_session,
        debit_account_id=expense_id,
        credit_account_id=asset_id,
        amount="100.00",
        when=date.today(),
    )

    resp = client.get("/api/dashboard/data?ids=balance_sheet_trend")
    d = resp.json()["balance_sheet_trend"]
    last = d["months"][-1]
    assert last["liabilities"] == 0.0
    assert last["equity"] == 400.00  # net income only
    assert last["assets"] == 400.00


def test_new_widgets_are_opt_in(client, db_session, seed_accounts):
    """The new cards join the catalog (so Customize can add them) but must
    NOT be on the default layout — the standard overview is unchanged."""
    resp = client.get("/api/dashboard/widgets")
    assert resp.status_code == 200
    body = resp.json()
    ids = [w["id"] for w in body["widgets"]]
    assert "pnl_ytd" in ids
    assert "balance_sheet_trend" in ids
    assert "pnl_ytd" not in body["default_order"]
    assert "balance_sheet_trend" not in body["default_order"]


def test_balance_sheet_trend_matches_report_as_of_today(
    client, db_session, seed_accounts
):
    """The trend's latest point must agree with the balance-sheet report
    for the same date — one source of truth for the accounting."""
    asset_id = _pick(seed_accounts, AccountType.ASSET)
    liab_id = _pick(seed_accounts, AccountType.LIABILITY)
    equity_id = _pick(seed_accounts, AccountType.EQUITY)
    income_id = _pick(seed_accounts, AccountType.INCOME)

    today = date.today()
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=equity_id,
        amount="3000.00",
        when=date(today.year, 1, 5),
    )
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=liab_id,
        amount="700.00",
        when=today,
    )
    _post(
        db_session,
        debit_account_id=asset_id,
        credit_account_id=income_id,
        amount="250.00",
        when=today,
    )

    trend = client.get("/api/dashboard/data?ids=balance_sheet_trend").json()[
        "balance_sheet_trend"
    ]["months"][-1]
    report = client.get("/api/reports/balance-sheet").json()

    assert trend["assets"] == report["total_assets"]
    assert trend["liabilities"] == report["total_liabilities"]
    assert trend["equity"] == report["total_equity"]


def test_the_trends_current_month_stops_at_today(db_session, seed_accounts):
    """The last point is month-to-date: a post-dated entry later this month is
    not a balance anyone holds yet, and the card's footer says 'to date'."""
    from datetime import date

    from app.services.dashboard_widgets import balance_sheet_trend

    out = balance_sheet_trend(db_session)
    assert len(out["months"]) == 12
    assert out["months"][-1]["as_of"] == date.today().isoformat()
    # earlier points are true month-ends, strictly increasing
    ends = [m["as_of"] for m in out["months"]]
    assert ends == sorted(ends) and len(set(ends)) == 12
