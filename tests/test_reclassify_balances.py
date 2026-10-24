"""Reclassify and the stored account balances (#490).

The ledger reports read the lines, so the P&L and General Ledger showed a
reclassified line on its new account while the Chart of Accounts Balance
column, the accounts CSV export and the AI tools read `Account.balance`,
which the move never touched: the amount stayed on the old account and the
new one showed nothing. The move now carries the balance with the line, a
move back restores it, and books already affected are repaired by
recomputing every stored balance from the lines, at start and on request.
"""

from decimal import Decimal

from sqlalchemy import update

from app.models.accounts import Account
from app.models.transactions import TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_BOOKKEEPER, ROLE_READONLY
from app.services import ai_tools
from app.services.accounting import rebuild_stored_balances
from tests.test_reclassify import CPA_PW, _login_as, _mk_user, _ok

YEAR = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def _expense_account(client, number, name):
    return _ok(
        client.post(
            "/api/accounts",
            json={"name": name, "account_number": number, "account_type": "expense"},
        )
    )["id"]


def _balances(client) -> dict[int, Decimal]:
    return {
        a["id"]: Decimal(str(a["balance"])) for a in _ok(client.get("/api/accounts"))
    }


def _pl_expenses(client) -> dict[int, Decimal]:
    pl = _ok(client.get("/api/reports/profit-loss", params=YEAR))
    return {
        e["account_id"]: Decimal(str(e["amount"]))
        for e in pl["expenses"] + pl["other_expense"]
    }


def _line_ids_on(db, account_id):
    return [
        ln.id
        for ln in db.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .order_by(TransactionLine.id)
        .all()
    ]


def _move(client, line_ids, account_id):
    return _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": line_ids, "account_id": account_id, "dry_run": False},
        )
    )


def _csv_balance(client, name) -> str:
    r = client.get("/api/csv/export/accounts")
    assert r.status_code == 200, r.text
    rows = r.text.splitlines()
    (row,) = [r for r in rows if f",{name}," in r]
    return row.split(",")[4]


def test_the_issues_repro_the_chart_of_accounts_follows_the_move(
    client, db_session, seed_accounts
):
    """Steps 1-5 of #490: after the move the stored balances read as the
    P&L does, on every surface that reads them."""
    interest = _expense_account(client, "6971", "Interest Expense")
    internet = _expense_account(client, "6972", "Internet")
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-04-03",
                "amount": "-32.15",
                "category_account_id": interest,
                "payee": "Comcast",
                "description": "April service",
            },
        )
    )
    before = _balances(client)
    assert (before[interest], before[internet]) == (Decimal("32.15"), Decimal("0"))

    listed = _ok(
        client.get("/api/reclassify/lines", params={"account_id": interest, **YEAR})
    )
    ids = [ln["id"] for ln in listed["lines"] if ln["movable"]]
    assert len(ids) == 1
    result = _move(client, ids, internet)
    assert result["count"] == 1

    after = _balances(client)
    assert (after[interest], after[internet]) == (Decimal("0"), Decimal("32.15"))
    # the bank side is untouched
    assert after[seed_accounts["1000"].id] == before[seed_accounts["1000"].id]
    # ... and agrees with the P&L, which reads the lines
    pl = _pl_expenses(client)
    assert pl.get(internet) == Decimal("32.15") and interest not in pl
    # the accounts export and the AI tool read the same stored total
    assert _csv_balance(client, "Internet") == "32.15"
    assert _csv_balance(client, "Interest Expense") == "0.00"
    db_session.expire_all()
    assert ai_tools.get_account_balance(db_session, internet)["balance"] == 32.15
    assert ai_tools.get_account_balance(db_session, interest)["balance"] == 0.0


def test_moving_the_lines_back_restores_the_balances(client, db_session, seed_accounts):
    interest = _expense_account(client, "6971", "Interest Expense")
    internet = _expense_account(client, "6972", "Internet")
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-04-03",
                "amount": "-32.15",
                "category_account_id": interest,
                "payee": "Comcast",
            },
        )
    )
    ids = _line_ids_on(db_session, interest)
    _move(client, ids, internet)
    assert _balances(client)[internet] == Decimal("32.15")
    # the undo is the same move the other way
    db_session.expire_all()
    _move(client, _line_ids_on(db_session, internet), interest)
    after = _balances(client)
    assert (after[interest], after[internet]) == (Decimal("32.15"), Decimal("0"))


def test_a_dry_run_moves_no_balance(client, db_session, seed_accounts):
    interest = _expense_account(client, "6971", "Interest Expense")
    internet = _expense_account(client, "6972", "Internet")
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-04-03",
                "amount": "-32.15",
                "category_account_id": interest,
                "payee": "Comcast",
            },
        )
    )
    ids = _line_ids_on(db_session, interest)
    plan = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "account_id": internet, "dry_run": True},
        )
    )
    assert plan["count"] == 1 and plan["dry_run"] is True
    after = _balances(client)
    assert (after[interest], after[internet]) == (Decimal("32.15"), Decimal("0"))


def test_a_journal_line_moves_its_balance_in_each_accounts_own_direction(
    client, db_session, seed_accounts
):
    """A hand-typed journal's line, moved from an expense account onto an
    income account: 40.00 of expense leaves 6900 and lands as -40.00 of
    income on 4000 (a debit on a credit-normal account), the P&L's reading
    of the same line."""
    accounts = seed_accounts
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-03-10",
                "description": "Refund posted as an expense",
                "lines": [
                    {
                        "account_id": accounts["6900"].id,
                        "debit": "40.00",
                        "credit": "0",
                    },
                    {
                        "account_id": accounts["2000"].id,
                        "debit": "0",
                        "credit": "40.00",
                    },
                ],
            },
        )
    )
    before = _balances(client)
    assert before[accounts["6900"].id] == Decimal("40.00")
    ids = _line_ids_on(db_session, accounts["6900"].id)
    _move(client, ids, accounts["4000"].id)
    after = _balances(client)
    assert after[accounts["6900"].id] == Decimal("0")
    assert after[accounts["4000"].id] == before[accounts["4000"].id] - Decimal("40.00")
    assert after[accounts["2000"].id] == before[accounts["2000"].id]
    pl = _ok(client.get("/api/reports/profit-loss", params=YEAR))
    income = {i["account_id"]: Decimal(str(i["amount"])) for i in pl["income"]}
    assert income.get(accounts["4000"].id) == Decimal("-40.00")
    # the stored totals agree with the ledger: the repair finds nothing
    db_session.expire_all()
    assert rebuild_stored_balances(db_session) == []


def _skew(db, account_id, to):
    db.execute(update(Account).where(Account.id == account_id).values(balance=to))
    db.commit()
    db.expire_all()


def test_the_rebuild_puts_a_skewed_balance_back(client, db_session, seed_accounts):
    """A book a pre-fix reclassify touched: the amount still stored on the
    account the line left, nothing on the one it went to. The repair reads
    the lines and names both corrections; a second run finds nothing."""
    interest = _expense_account(client, "6971", "Interest Expense")
    internet = _expense_account(client, "6972", "Internet")
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-04-03",
                "amount": "-32.15",
                "category_account_id": internet,
                "payee": "Comcast",
            },
        )
    )
    _skew(db_session, interest, Decimal("32.15"))
    _skew(db_session, internet, Decimal("0"))
    skewed = _balances(client)
    assert (skewed[interest], skewed[internet]) == (Decimal("32.15"), Decimal("0"))

    r = _ok(client.post("/api/accounts/rebuild-balances"))
    assert r["count"] == 2
    by_id = {c["account_id"]: c for c in r["corrected"]}
    assert by_id[interest] == {
        "account_id": interest,
        "account_number": "6971",
        "name": "Interest Expense",
        "was": "32.15",
        "now": "0.00",
    }
    assert by_id[internet]["was"] == "0.00" and by_id[internet]["now"] == "32.15"
    fixed = _balances(client)
    assert (fixed[interest], fixed[internet]) == (Decimal("0"), Decimal("32.15"))
    # idempotent
    assert _ok(client.post("/api/accounts/rebuild-balances")) == {
        "count": 0,
        "corrected": [],
    }


def test_the_rebuild_is_an_administrators_call(client, db_session, seed_accounts):
    _mk_user(db_session, "books", CPA_PW, ROLE_BOOKKEEPER)
    _mk_user(db_session, "cpa", CPA_PW, ROLE_ACCOUNTANT)
    _mk_user(db_session, "viewer", CPA_PW, ROLE_READONLY)
    for who in ("books", "cpa", "viewer"):
        _login_as(client, who, CPA_PW)
        assert client.post("/api/accounts/rebuild-balances").status_code == 403, who


def test_the_repair_runs_when_the_server_starts(monkeypatch, lifespan_client):
    """Books already touched are repaired on upgrade: the start-up runs the
    rebuild. The fixture has started once already; entering the client
    again runs the start-up once more, now with the recorder in place
    (`client` skips the lifespan altogether)."""
    calls = []
    import app.services.accounting as accounting

    monkeypatch.setattr(
        accounting,
        "rebuild_stored_balances",
        lambda db: calls.append(db) or [],
    )
    with lifespan_client as c:
        assert c.get("/api/accounts").status_code == 200
    assert len(calls) == 1
