"""Reports on a cash basis, step 1 (#483, docs/design/cash-basis.md).

Every ledger line carries a basis; a payment writes the cash recognition
of what it collected into its own entry; the P&L and Balance Sheet read
one basis. The invariant every scenario proves, in the cash view:

    net income = change in cash + change in other assets
                 − change in liabilities − change in equity (ex net income)

with A/R and A/P at zero, and the accrual reading unchanged from before.
"""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.models.transactions import (
    BASIS_ACCRUAL,
    BASIS_BOTH,
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ALL,
    Transaction,
    TransactionLine,
)

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
REVISION = "e1a2b3c4d5f6"
BEFORE = "c8e4a2f1b3d5"
INDEX_REVISION = "f2b3c4d5e6a7"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _account_id(client, number):
    return next(
        a["id"]
        for a in client.get("/api/accounts").json()
        if a["account_number"] == number
    )


def _vendor(client, name="Harbor Supply"):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _item(client, name, income_number):
    r = client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "service",
            "rate": 1,
            "income_account_id": _account_id(client, income_number),
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _invoice(client, customer_id, date, lines, tax_rate=0):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": date,
            "tax_rate": tax_rate,
            "lines": [
                {"description": f"line {i}", "quantity": 1, "line_order": i, **ln}
                for i, ln in enumerate(lines)
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _payment(client, customer_id, date, amount, allocations=()):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": date,
            "amount": float(amount),
            "method": "check",
            "allocations": [
                {"invoice_id": inv_id, "amount": float(amt)}
                for inv_id, amt in allocations
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, date, lines):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": date,
            "lines": [
                {"description": f"line {i}", "quantity": 1, "line_order": i, **ln}
                for i, ln in enumerate(lines)
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill_payment(client, vendor_id, date, amount, allocations=()):
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": date,
            "amount": float(amount),
            "method": "check",
            "allocations": [
                {"bill_id": bill_id, "amount": float(amt)}
                for bill_id, amt in allocations
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pl(client, start, end, basis=None, **extra):
    qs = f"start_date={start}&end_date={end}"
    if basis:
        qs += f"&basis={basis}"
    for k, v in extra.items():
        qs += f"&{k}={v}"
    r = client.get(f"/api/reports/profit-loss?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _bs(client, as_of, basis=None):
    qs = f"as_of_date={as_of}"
    if basis:
        qs += f"&basis={basis}"
    r = client.get(f"/api/reports/balance-sheet?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _amount(report, number, sections=("income", "cogs", "expenses")):
    """One account's figure on a report, 0 when it has no line."""
    for key in sections:
        for line in report.get(key) or []:
            if line.get("account_number") == number:
                return D(str(line["amount"]))
    return D("0")


def _bs_amount(bs, number):
    return _amount(bs, number, ("assets", "liabilities", "equity"))


def _cash(bs):
    """Bank and card balances plus Undeposited Funds: what a cash-basis
    book calls cash."""
    total = D("0")
    for line in bs["assets"]:
        if line.get("detail_type") == "bank" or line.get("account_number") == "1200":
            total += D(str(line["amount"]))
    for line in bs["liabilities"]:
        if line.get("detail_type") == "credit_card":
            total -= D(str(line["amount"]))
    return total


def _day_before(iso):
    from datetime import date, timedelta

    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _assert_cash_invariant(client, start, end):
    """The cash view balances, carries no A/R or A/P, and its net income is
    the change in cash plus the named non-cash movements."""
    pl = _pl(client, start, end, "cash")
    after = _bs(client, end, "cash")
    before = _bs(client, _day_before(start), "cash")
    for bs in (after, before):
        assert round(bs["total_assets"], 2) == round(
            bs["total_liabilities"] + bs["total_equity"], 2
        ), bs
        assert _bs_amount(bs, "1100") == 0, "A/R on a cash-basis Balance Sheet"
        assert _bs_amount(bs, "2000") == 0, "A/P on a cash-basis Balance Sheet"

    def delta(side, keep):
        tot = D("0")
        for b, sign in ((after, 1), (before, -1)):
            for line in b[side]:
                if keep(line):
                    tot += sign * D(str(line["amount"]))
        return tot

    def is_cash(ln):
        return (
            ln.get("detail_type") in ("bank", "credit_card")
            or ln.get("account_number") == "1200"
        )

    d_cash = _cash(after) - _cash(before)
    d_other_assets = delta("assets", lambda ln: not is_cash(ln))
    d_liabilities = delta("liabilities", lambda ln: not is_cash(ln))
    d_equity = delta("equity", lambda ln: ln.get("account_id") is not None)
    expected = d_cash + d_other_assets - d_liabilities - d_equity
    assert D(str(pl["net_income"])) == expected, (pl["net_income"], expected)
    return pl, after, before


def _each_entry_balances_on_every_basis(db_session):
    for txn in db_session.query(Transaction).all():
        lines = (
            db_session.query(TransactionLine)
            .filter(TransactionLine.transaction_id == txn.id)
            .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
            .all()
        )
        for basis in (None, BASIS_ACCRUAL, BASIS_CASH):
            chosen = [ln for ln in lines if basis is None or ln.basis == basis]
            out = sum((D(str(ln.debit)) - D(str(ln.credit)) for ln in chosen), D("0"))
            assert out == 0, (txn.id, txn.description, basis, out)


# ---------------------------------------------------------------------------
# invoice → partial payment → payment
# ---------------------------------------------------------------------------


def test_invoice_paid_in_two_parts_recognizes_each_part_when_it_lands(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    labor = _item(client, "Labor", "4100")
    inv = _invoice(
        client,
        cust,
        "2026-03-01",
        [{"rate": 1000}, {"rate": 500, "item_id": labor["id"]}],
        tax_rate=0.08,
    )
    assert D(str(inv["total"])) == D("1620.00")
    _payment(client, cust, "2026-03-10", 810, [(inv["id"], 810)])
    _payment(client, cust, "2026-04-05", 810, [(inv["id"], 810)])

    # March on the cash basis: half of everything, dated the payment
    march, bs_march, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(march, "4000") == D("500.00")
    assert _amount(march, "4100") == D("250.00")
    assert D(str(march["net_income"])) == D("750.00")
    assert march["basis"] == "cash"
    assert _bs_amount(bs_march, "2200") == D("60.00")  # tax collected, unpaid
    assert _cash(bs_march) == D("810.00")
    # April: the other half
    april, _, _ = _assert_cash_invariant(client, "2026-04-01", "2026-04-30")
    assert _amount(april, "4000") == D("500.00")
    assert D(str(april["net_income"])) == D("750.00")

    # The accrual reading is the invoice's, on its date, as it always was
    accrual = _pl(client, "2026-03-01", "2026-03-31", "accrual")
    assert D(str(accrual["net_income"])) == D("1500.00")
    assert accrual == {**_pl(client, "2026-03-01", "2026-03-31"), "basis": "accrual"}
    assert _pl(client, "2026-03-01", "2026-03-31")["basis"] == "accrual"
    assert D(str(_pl(client, "2026-04-01", "2026-04-30", "accrual")["net_income"])) == 0
    bs = _bs(client, "2026-03-31")
    assert _bs_amount(bs, "1100") == D("810.00")
    assert _bs_amount(bs, "2200") == D("120.00")
    _each_entry_balances_on_every_basis(db_session)

    # the cash lines sit on the payment's entry, carrying the invoice's
    # accounts, and the stored balance is the accrual one
    pay_txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "payment")
        .order_by(Transaction.id)
        .first()
    )
    cash_lines = [
        ln
        for ln in db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == pay_txn.id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
        if ln.basis == BASIS_CASH
    ]
    by_account = {
        ln.account_id: D(str(ln.credit)) - D(str(ln.debit)) for ln in cash_lines
    }
    assert by_account[seed_accounts["4000"].id] == D("500.00")
    assert by_account[seed_accounts["4100"].id] == D("250.00")
    assert by_account[seed_accounts["2200"].id] == D("60.00")
    assert by_account[seed_accounts["1100"].id] == D("-810.00")
    db_session.expire_all()
    assert D(str(seed_accounts["4000"].balance)) == D("1000.00")
    # the default reading of the payment's entry hides the cash lines
    assert all(
        ln.basis == BASIS_BOTH
        for ln in db_session.query(TransactionLine).filter(
            TransactionLine.transaction_id == pay_txn.id
        )
    )


def test_a_discount_line_is_prorated_as_a_debit():
    """A negative line (a discount item's) keeps its side through the
    proration, and the shares add up to the amount applied exactly."""
    from app.services import cash_basis

    lines = [
        {"account_id": 1, "amount": D("200.00")},
        {"account_id": 2, "amount": D("-50.00")},
        {"account_id": 3, "amount": D("0.03")},
    ]
    shares = cash_basis.prorate(lines, D("75.01"), D("150.03"))
    assert sum((s["amount"] for s in shares), D("0")) == D("75.01")
    by = {s["account_id"]: s["amount"] for s in shares}
    assert by[2] < 0 and by[1] > 0
    assert cash_basis.prorate(lines, D("0"), D("150.03")) == []
    debit = cash_basis._cash_line({"account_id": 2, "amount": D("-25.00")}, "m")
    assert debit["debit"] == D("25.00") and debit["credit"] == 0
    assert debit["basis"] == "cash"


# ---------------------------------------------------------------------------
# bill → payment
# ---------------------------------------------------------------------------


def test_bill_paid_in_part_recognizes_the_expense_when_paid(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    a6000 = _account_id(client, "6000")
    a6100 = _account_id(client, "6100")
    bill = _bill(
        client,
        vendor["id"],
        "2026-05-05",
        [{"rate": 175, "account_id": a6000}, {"rate": 25, "account_id": a6100}],
    )
    assert D(str(bill["total"])) == D("200.00")
    _bill_payment(client, vendor["id"], "2026-05-25", 100, [(bill["id"], 100)])

    may, bs_may, _ = _assert_cash_invariant(client, "2026-05-01", "2026-05-31")
    assert _amount(may, "6000") == D("87.50")
    assert _amount(may, "6100") == D("12.50")
    assert D(str(may["net_income"])) == D("-100.00")
    assert _cash(bs_may) == D("-100.00")
    assert _bs_amount(bs_may, "2000") == 0
    # accrual: the whole bill on its date, A/P half open
    assert D(str(_pl(client, "2026-05-01", "2026-05-31")["net_income"])) == D("-200.00")
    assert _bs_amount(_bs(client, "2026-05-31"), "2000") == D("100.00")

    _bill_payment(client, vendor["id"], "2026-06-03", 100, [(bill["id"], 100)])
    june, _, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert _amount(june, "6000") == D("87.50")
    _each_entry_balances_on_every_basis(db_session)


def test_a_bill_payment_remainder_is_unapplied_cash_expense(
    client, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    _bill_payment(client, vendor["id"], "2026-05-25", 40)
    may, _, _ = _assert_cash_invariant(client, "2026-05-01", "2026-05-31")
    assert _amount(may, "6990") == D("40.00")
    assert D(str(_pl(client, "2026-05-01", "2026-05-31")["net_income"])) == 0


# ---------------------------------------------------------------------------
# void after payment
# ---------------------------------------------------------------------------


def test_voiding_the_payment_takes_the_cash_income_back(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-07-01", [{"rate": 300}])
    pay = _payment(client, cust, "2026-07-02", 300, [(inv["id"], 300)])
    assert D(str(_pl(client, "2026-07-01", "2026-07-31", "cash")["net_income"])) == 300

    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    july, bs, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert D(str(july["net_income"])) == 0
    assert _cash(bs) == 0
    # the accrual reading still has the invoice, open again
    assert D(str(_pl(client, "2026-07-01", "2026-07-31")["net_income"])) == 300
    assert _bs_amount(_bs(client, "2026-07-31"), "1100") == D("300.00")

    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text
    assert D(str(_pl(client, "2026-07-01", "2026-07-31")["net_income"])) == 0
    assert D(str(_pl(client, "2026-07-01", "2026-07-31", "cash")["net_income"])) == 0
    _each_entry_balances_on_every_basis(db_session)
    # the void copied every line of the payment's entry, basis and all
    void_txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "payment_void")
        .one()
    )
    bases = sorted(
        ln.basis
        for ln in db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == void_txn.id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
    )
    assert bases == ["both", "both", "cash", "cash"]


# ---------------------------------------------------------------------------
# prepayment: unapplied cash, then applied
# ---------------------------------------------------------------------------


def test_a_prepayment_is_unapplied_cash_income_until_it_is_applied(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    pay = _payment(client, cust, "2026-06-01", 400)
    june, bs, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert _amount(june, "4950") == D("400.00")
    assert D(str(june["net_income"])) == D("400.00")
    assert _bs_amount(bs, "1100") == 0
    # accrual: a credit on A/R, no income
    assert D(str(_pl(client, "2026-06-01", "2026-06-30")["net_income"])) == 0
    assert _bs_amount(_bs(client, "2026-06-30"), "1100") == D("-400.00")

    inv = _invoice(client, cust, "2026-06-10", [{"rate": 400}])
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": inv["id"], "amount": 400}]},
    )
    assert r.status_code == 200, r.text
    june, _, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert _amount(june, "4950") == 0
    assert _amount(june, "4000") == D("400.00")
    assert D(str(june["net_income"])) == D("400.00")
    # moved inside the payment's own entry: no new transaction
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "payment_apply")
        .count()
        == 0
    )
    _each_entry_balances_on_every_basis(db_session)

    # voiding the payment undoes the lot
    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    june, _, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert D(str(june["net_income"])) == 0


# ---------------------------------------------------------------------------
# prior-year compare, the Balance Sheet, the switch
# ---------------------------------------------------------------------------


def test_prior_year_compare_runs_both_years_on_the_cash_basis(
    client, seed_accounts, seed_customer
):
    cust = seed_customer.id
    old = _invoice(client, cust, "2025-02-01", [{"rate": 900}])
    _payment(client, cust, "2025-02-20", 500, [(old["id"], 500)])
    new = _invoice(client, cust, "2026-02-01", [{"rate": 700}])
    _payment(client, cust, "2026-02-15", 700, [(new["id"], 700)])

    pl = _pl(client, "2026-01-01", "2026-12-31", "cash", compare="prior_year")
    assert D(str(pl["net_income"])) == D("700.00")
    assert D(str(pl["prior"]["net_income"])) == D("500.00")
    accrual = _pl(client, "2026-01-01", "2026-12-31", compare="prior_year")
    assert D(str(accrual["prior"]["net_income"])) == D("900.00")

    bs = _bs(client, "2026-12-31", "cash")
    assert bs["basis"] == "cash"
    assert _bs_amount(bs, "1100") == 0
    assert _cash(bs) == D("1200.00")
    r = client.get(
        "/api/reports/balance-sheet?as_of_date=2026-12-31&basis=cash&compare=prior_year"
    )
    assert r.status_code == 200
    assert D(str(r.json()["prior"]["total_assets"])) == D("500.00")
    assert _bs_amount(_bs(client, "2026-12-31"), "1100") == D("400.00")


def test_the_company_default_and_the_parameter(client, seed_accounts, seed_customer):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-08-01", [{"rate": 120}])
    _payment(client, cust, "2026-08-20", 50, [(inv["id"], 50)])

    plain = _pl(client, "2026-08-01", "2026-08-31")
    assert plain["basis"] == "accrual" and D(str(plain["net_income"])) == 120
    r = client.put("/api/settings", json={"report_basis": "cash"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["report_basis"] == "cash"
    by_default = _pl(client, "2026-08-01", "2026-08-31")
    assert by_default["basis"] == "cash" and D(str(by_default["net_income"])) == 50
    assert _bs(client, "2026-08-31")["basis"] == "cash"
    # the parameter wins over the default, either way
    assert _pl(client, "2026-08-01", "2026-08-31", "accrual") == plain
    client.put("/api/settings", json={"report_basis": "accrual"})
    assert _pl(client, "2026-08-01", "2026-08-31", "cash") == by_default

    r = client.get(
        "/api/reports/profit-loss?start_date=2026-08-01&end_date=2026-08-31&basis=modified"
    )
    assert r.status_code == 422
    assert "accrual, cash" in r.json()["detail"]
    r = client.put("/api/settings", json={"report_basis": "modified"})
    assert r.status_code in (400, 422), r.text

    # the CSV and the PDF route say so
    r = client.get(
        "/api/reports/profit-loss/csv?start_date=2026-08-01&end_date=2026-08-31&basis=cash"
    )
    assert r.status_code == 200
    assert "(cash basis)" in r.text and "50.00" in r.text
    r = client.get("/api/reports/balance-sheet/csv?as_of_date=2026-08-31&basis=cash")
    assert r.status_code == 200 and "(cash basis)" in r.text
    r = client.get("/api/reports/balance-sheet/csv?as_of_date=2026-08-31")
    assert "(cash basis)" not in r.text


def test_the_month_columns_follow_the_basis(client, seed_accounts, seed_customer):
    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-01-15", [{"rate": 300}])
    _payment(client, cust, "2026-02-10", 300, [(inv["id"], 300)])
    data = _pl(client, "2026-01-01", "2026-03-31", "cash", columns="month")
    assert data["basis"] == "cash"
    assert [round(c["net_income"], 2) for c in data["columns"]] == [0, 300, 0]
    data = _pl(client, "2026-01-01", "2026-03-31", columns="month")
    assert [round(c["net_income"], 2) for c in data["columns"]] == [300, 0, 0]


# ---------------------------------------------------------------------------
# the backfill on a file written without the column
# ---------------------------------------------------------------------------


def test_the_backfill_tags_and_writes_once(
    client, db_session, seed_accounts, seed_customer
):
    from app.services import cash_basis

    cust = seed_customer.id
    vendor = _vendor(client)
    labor = _item(client, "Labor", "4100")
    inv = _invoice(
        client,
        cust,
        "2026-03-01",
        [{"rate": 1000}, {"rate": 500, "item_id": labor["id"]}],
        0.08,
    )
    _payment(client, cust, "2026-03-10", 810, [(inv["id"], 810)])
    _payment(client, cust, "2026-03-12", 100)  # a prepayment
    other = _invoice(client, cust, "2026-03-15", [{"rate": 50}])
    voided = _payment(client, cust, "2026-03-16", 50, [(other["id"], 50)])
    client.post(f"/api/payments/{voided['id']}/void")
    bill = _bill(
        client,
        vendor["id"],
        "2026-03-05",
        [{"rate": 175, "account_id": _account_id(client, "6000")}],
    )
    _bill_payment(client, vendor["id"], "2026-03-20", 75, [(bill["id"], 75)])

    expected_pl = _pl(client, "2026-03-01", "2026-03-31", "cash")
    expected_bs = _bs(client, "2026-03-31", "cash")
    assert cash_basis.pending_backfill(db_session) is False

    # the file as a build before #483 left it: no cash lines, every line both
    db_session.execute(text("DELETE FROM transaction_lines WHERE basis = 'cash'"))
    db_session.execute(text("UPDATE transaction_lines SET basis = 'both'"))
    db_session.commit()
    assert cash_basis.pending_backfill(db_session) is True
    assert _pl(client, "2026-03-01", "2026-03-31", "cash") != expected_pl

    done = cash_basis.backfill(db_session)
    assert done["payments"] == 2 and done["bill_payments"] == 1 and done["tagged"] > 0
    assert cash_basis.pending_backfill(db_session) is False
    assert _pl(client, "2026-03-01", "2026-03-31", "cash") == expected_pl
    assert _bs(client, "2026-03-31", "cash") == expected_bs
    _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    _each_entry_balances_on_every_basis(db_session)

    # a second run writes nothing
    assert cash_basis.backfill(db_session) == {
        "tagged": 0,
        "payments": 0,
        "bill_payments": 0,
        "failed": 0,
    }
    assert _pl(client, "2026-03-01", "2026-03-31", "cash") == expected_pl


def test_the_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "basis.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == BEFORE
    command.upgrade(cfg, INDEX_REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("transaction_lines")}
    assert "basis" in cols and cols["basis"]["nullable"] is False
    assert "both" in str(cols["basis"]["default"])
    # the backfill's lookup by source is indexed (review: 189 s for 8,000)
    assert script.get_revision(INDEX_REVISION).down_revision == REVISION
    indexes = {i["name"]: i for i in inspect(engine).get_indexes("transactions")}
    assert indexes["ix_transactions_source"]["column_names"] == [
        "source_type",
        "source_id",
    ]
    engine.dispose()
    command.downgrade(cfg, BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    assert "ix_transactions_source" not in {
        i["name"] for i in inspect(engine).get_indexes("transactions")
    }
    assert "basis" not in {
        c["name"] for c in inspect(engine).get_columns("transaction_lines")
    }
    engine.dispose()


def test_the_books_invariants_scenario_holds_on_the_cash_basis(
    client, db_session, seed_accounts, seed_customer
):
    """The cross-feature scenario of test_books_balance_invariants, read on
    the cash basis: balanced, no A/R or A/P, net income the change in cash
    plus the tax collected."""
    from app.models.contacts import Vendor
    from tests.test_books_balance_invariants import _build_scenario

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)
    _assert_cash_invariant(client, "2026-01-01", "2026-12-31")
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# review (Trent, #572): backfill isolation and batching, delete guards, the
# A/R note, the accountant's package, importers
# ---------------------------------------------------------------------------


def _strip_cash_lines(db_session):
    """The file as a build before #483 left it."""
    db_session.execute(text("DELETE FROM transaction_lines WHERE basis = 'cash'"))
    db_session.execute(text("UPDATE transaction_lines SET basis = 'both'"))
    db_session.commit()


def test_a_broken_payment_is_skipped_and_named_not_fatal(
    client, db_session, seed_accounts, seed_customer, monkeypatch, caplog
):
    from app.models.payments import Payment
    from app.services import cash_basis

    cust = seed_customer.id
    ids = []
    for day in ("2026-03-10", "2026-03-11", "2026-03-12"):
        inv = _invoice(client, cust, "2026-03-01", [{"rate": 100}])
        ids.append(_payment(client, cust, day, 100, [(inv["id"], 100)])["id"])
    _strip_cash_lines(db_session)
    broken = db_session.get(Payment, ids[1])
    broken_entry = broken.transaction_id

    real = cash_basis.append_cash_lines

    def flaky(db, txn, lines):
        real(db, txn, lines)  # the half-written rows must go with the savepoint
        if txn.id == broken_entry:
            raise RuntimeError("deliberately broken payment")
        return len(lines)

    monkeypatch.setattr(cash_basis, "append_cash_lines", flaky)
    with caplog.at_level("ERROR", logger=cash_basis.log.name):
        done = cash_basis.backfill(db_session)

    assert done["payments"] == 2 and done["failed"] == 1
    assert any(f"payment {broken.id}" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]
    cash_lines = (
        db_session.query(TransactionLine.transaction_id)
        .filter(TransactionLine.basis == BASIS_CASH)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .distinct()
        .all()
    )
    assert broken_entry not in {t for (t,) in cash_lines}
    assert len(cash_lines) == 2

    # fixed, the next run picks the payment up
    monkeypatch.setattr(cash_basis, "append_cash_lines", real)
    again = cash_basis.backfill(db_session)
    assert (again["payments"], again["failed"]) == (1, 0)
    _each_entry_balances_on_every_basis(db_session)


def test_the_backfill_commits_as_it_goes(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    from app.services import cash_basis

    cust = seed_customer.id
    for day in range(10, 15):
        inv = _invoice(client, cust, "2026-03-01", [{"rate": 10}])
        _payment(client, cust, f"2026-03-{day}", 10, [(inv["id"], 10)])
    _strip_cash_lines(db_session)

    commits = []
    real_commit = db_session.commit
    monkeypatch.setattr(
        db_session, "commit", lambda: (commits.append(1), real_commit())[1]
    )
    monkeypatch.setattr(cash_basis, "BACKFILL_BATCH", 2)
    done = cash_basis.backfill(db_session)
    assert done["payments"] == 5
    # the tags, two batches of two, and the closing commit
    assert len(commits) >= 4


def test_an_account_with_only_cash_lines_cannot_be_deleted(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    _payment(client, cust, "2026-03-12", 80)  # a prepayment: 4950 gets cash lines only
    unapplied = _account_id(client, "4950")
    on_accrual = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.account_id == unapplied)
        .count()
    )
    assert on_accrual == 0  # the default reading never sees them

    r = client.delete(f"/api/accounts/{unapplied}")
    assert r.status_code == 409, r.text
    assert "posted transaction line" in r.json()["detail"]
    assert _account_id(client, "4950") == unapplied
    _assert_cash_invariant(client, "2026-03-01", "2026-03-31")


def test_a_job_with_only_cash_lines_is_in_use(client, db_session, seed_accounts):
    from datetime import date

    from app.models.contacts import Customer
    from app.models.jobs import Job

    cust = Customer(name="Cash Only Co", is_active=True)
    db_session.add(cust)
    db_session.flush()
    job = Job(name="Cash only job", customer_id=cust.id)
    db_session.add(job)
    db_session.flush()
    txn = Transaction(date=date(2026, 3, 1), description="cash lines only")
    db_session.add(txn)
    db_session.flush()
    bank = _account_id(client, "1000")
    income = _account_id(client, "4000")
    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=bank,
                debit=5,
                credit=0,
                job_id=job.id,
                basis=BASIS_CASH,
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=income,
                debit=0,
                credit=5,
                job_id=job.id,
                basis=BASIS_CASH,
            ),
        ]
    )
    db_session.commit()
    r = client.delete(f"/api/jobs/{job.id}")
    assert r.status_code == 400, r.text


def _ar_journal(client, accounts, day, amount):
    """History that came in as a journal entry: A/R against income. Written
    as such an entry was saved before 2.32 (or as an import brings one): the
    journal form refuses a line on A/R since #897."""
    from datetime import date as _date
    from decimal import Decimal as _D

    from sqlalchemy.orm import object_session

    from app.services.accounting import create_journal_entry

    db = object_session(accounts["1100"])
    amount = _D(str(amount))
    create_journal_entry(
        db,
        _date.fromisoformat(day),
        "Opening A/R",
        [
            {"account_id": accounts["1100"].id, "debit": amount, "credit": _D("0")},
            {"account_id": accounts["4000"].id, "debit": _D("0"), "credit": amount},
        ],
        source_type="manual",
    )
    db.commit()


def test_the_cash_report_says_when_receivables_are_not_zero(
    client, db_session, seed_accounts, seed_customer
):
    from app.routes.reports.financial import _bs_section, _pl_section

    # a clean cash book carries no note
    inv = _invoice(client, seed_customer.id, "2026-03-01", [{"rate": 100}])
    _payment(client, seed_customer.id, "2026-03-05", 100, [(inv["id"], 100)])
    assert _bs(client, "2026-03-31", "cash")["basis_note"] is None

    _ar_journal(client, seed_accounts, "2026-02-01", "250.00")
    bs = _bs(client, "2026-03-31", "cash")
    assert "not zero" in bs["basis_note"]
    assert _bs(client, "2026-03-31", "accrual")["basis_note"] is None
    pl = _pl(client, "2026-03-01", "2026-03-31", "cash")
    assert "journal entries" in pl["basis_note"]
    assert _pl(client, "2026-03-01", "2026-03-31", "accrual")["basis_note"] is None

    # the PDF sections carry it, and the page prints it
    assert _bs_section(bs)["note"] == bs["basis_note"]
    assert _pl_section(pl)["note"] == pl["basis_note"]
    from app.services.pdf_service import _render

    html = _render(
        "report_pdf.html",
        {},
        sections=[_bs_section(bs)],
        paper_size="letter",
        landscape=False,
        generated_on="2026-03-31",
    )
    assert "not zero on this cash-basis report" in html


def test_the_accountants_package_follows_the_company_basis(
    client, db_session, seed_accounts, seed_customer, monkeypatch
):
    import io
    import zipfile

    from app.routes.reports import financial

    seen = []
    for name in ("_pl_section", "_bs_section"):
        real = getattr(financial, name)
        monkeypatch.setattr(
            financial,
            name,
            lambda data, *a, _real=real, **k: (
                seen.append(data.get("basis")),
                _real(data, *a, **k),
            )[1],
        )

    def package():
        r = client.get(
            "/api/reports/accountants-package?start_date=2026-01-01&end_date=2026-12-31"
        )
        assert r.status_code == 200, r.text
        return zipfile.ZipFile(io.BytesIO(r.content)).read("README.txt").decode()

    readme = package()
    assert "Report basis: accrual" in readme
    assert set(seen) <= {"accrual"}

    seen.clear()
    assert client.put("/api/settings", json={"report_basis": "cash"}).status_code == 200
    readme = package()
    assert "Report basis: cash" in readme
    assert "cash basis" in readme
    assert seen and set(seen) == {"cash"}
    manifest = client.get(
        "/api/reports/accountants-package/manifest?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    assert manifest["basis"] == "cash"


def test_an_iif_import_gives_its_payments_cash_lines(client, db_session, seed_accounts):
    from app.models.payments import Payment
    from app.services import cash_basis
    from app.services.iif_import import import_all

    iif = (
        "!CUST\tNAME\nCUST\tBob Jones\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t03/01/2026\tAccounts Receivable\tBob Jones\t100.00\tI-1\n"
        "SPL\tINVOICE\t03/01/2026\tService Income\tBob Jones\t-100.00\tI-1\n"
        "ENDTRNS\n"
        "TRNS\tPAYMENT\t04/03/2026\tUndeposited Funds\tBob Jones\t100.00\tP-1\n"
        "SPL\tPAYMENT\t04/03/2026\tAccounts Receivable\tBob Jones\t-100.00\tP-1\n"
        "ENDTRNS\n"
    )
    result = import_all(db_session, iif)
    assert result["errors"] == [] and result["payments"] == 1
    assert db_session.query(Payment).count() == 1
    # no restart: the cash lines are already there
    assert cash_basis.pending_backfill(db_session) is False
    april = _pl(client, "2026-04-01", "2026-04-30", "cash")
    assert D(str(april["total_income"])) == D("100.00"), april["income"]
    assert _pl(client, "2026-03-01", "2026-03-31", "cash")["total_income"] == 0
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# clearing-on online payments, and QuickBooks Online postings
# ---------------------------------------------------------------------------


def _cash_credit_lines(db_session, txn_id):
    return [
        ln
        for ln in db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
        if ln.basis == BASIS_CASH and ln.credit
    ]


def test_an_online_payment_with_clearing_on_writes_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    from datetime import date

    from app.models.payments import Payment
    from app.services.payments.recorder import record_provider_payment
    from app.services.settings_service import set_setting

    set_setting(db_session, "stripe_clearing_enabled", "true")
    db_session.commit()
    inv = _invoice(client, seed_customer.id, "2026-03-01", [{"rate": 100}])
    status = record_provider_payment(
        db_session, "stripe", "Stripe", inv["id"], "cs_clear", D("100.00"), D("3.20")
    )
    assert status == "payment_recorded"
    payment = db_session.query(Payment).filter_by(reference="cs_clear").one()
    cash_credits = _cash_credit_lines(db_session, payment.transaction_id)
    assert sum(D(str(ln.credit)) for ln in cash_credits) == D("100.00")

    today = date.today().isoformat()
    pl, bs, _ = _assert_cash_invariant(client, today, today)
    assert _amount(pl, "4000") == D("100.00")
    assert _bs_amount(bs, "1100") == 0
    _each_entry_balances_on_every_basis(db_session)


def _make_ledger_posted(db_session, invoice_id, qbo_type, qbo_id):
    """Dress an invoice written here as one the QuickBooks Online ledger
    import posted: its entry becomes a qbo_ledger one (every line `both`),
    the invoice keeps no entry of its own, and the mappings exist."""
    from app.models.invoices import Invoice
    from app.models.qbo_mapping import QBOMapping

    invoice = db_session.get(Invoice, invoice_id)
    txn = db_session.get(Transaction, invoice.transaction_id)
    txn.source_type = "qbo_ledger"
    txn.source_id = None
    for ln in (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn.id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
    ):
        ln.basis = BASIS_BOTH
    invoice.transaction_id = None
    entity = "sales_receipt" if qbo_type == "Sales Receipt" else "invoice"
    db_session.add(
        QBOMapping(entity_type=entity, slowbooks_id=invoice.id, qbo_id=qbo_id)
    )
    db_session.add(
        QBOMapping(
            entity_type="ledger", slowbooks_id=txn.id, qbo_id=f"{qbo_type}:{qbo_id}"
        )
    )
    db_session.commit()
    return txn


def test_a_qbo_ledger_invoice_counts_once_on_cash(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, "2026-03-01", [{"rate": 100}])
    _make_ledger_posted(db_session, inv["id"], "Invoice", "9001")
    _payment(client, seed_customer.id, "2026-04-05", 100, [(inv["id"], 100)])

    # income counts once, on the posting's date; nothing lands on 4950
    pl, bs, _ = _assert_cash_invariant(client, "2026-03-01", "2026-04-30")
    assert D(str(pl["net_income"])) == D("100.00")
    assert _amount(pl, "4000") == D("100.00")
    assert _amount(pl, "4950") == 0
    assert _bs_amount(bs, "1100") == 0
    accrual = _pl(client, "2026-03-01", "2026-04-30", "accrual")
    assert D(str(accrual["net_income"])) == D("100.00")
    _each_entry_balances_on_every_basis(db_session)


def test_an_adopted_qbo_receipt_payment_gets_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice
    from app.models.payments import Payment

    inv = _invoice(client, seed_customer.id, "2026-03-01", [{"rate": 100}])
    pay = _payment(client, seed_customer.id, "2026-03-01", 100, [(inv["id"], 100)])
    # the import posted the receipt and its payment together, no entries of ours
    payment = db_session.get(Payment, pay["id"])
    for ln in (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == payment.transaction_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
    ):
        db_session.delete(ln)
    db_session.flush()
    db_session.delete(db_session.get(Transaction, payment.transaction_id))
    payment.transaction_id = None
    db_session.commit()
    _make_ledger_posted(db_session, inv["id"], "Sales Receipt", "9002")

    # an edit that changes the date makes the receipt ours
    r = client.put(f"/api/invoices/{inv['id']}", json={"date": "2026-03-02"})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    payment = db_session.get(Payment, pay["id"])
    assert payment.transaction_id is not None
    cash_credits = _cash_credit_lines(db_session, payment.transaction_id)
    assert sum(D(str(ln.credit)) for ln in cash_credits) == D("100.00")
    assert db_session.get(Invoice, inv["id"]).transaction_id is not None
    # the income went to the invoice's own account, not unapplied cash
    pl, bs, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(pl, "4000") == D("100.00")
    assert _amount(pl, "4950") == 0
    assert _bs_amount(bs, "1100") == 0
    _each_entry_balances_on_every_basis(db_session)
