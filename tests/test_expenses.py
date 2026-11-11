"""Expenses — one-step paid receipts (DR expense, CR bank/credit card)."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.transactions import Transaction


@pytest.fixture
def vendor(client):
    r = client.post("/api/vendors", json={"name": "Sweet Forest Cafe"})
    assert r.status_code == 201, r.text
    return r.json()


def _post(client, seed_accounts, **overrides):
    body = {
        "date": "2026-09-02",
        "expense_account_id": seed_accounts["6000"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "30.30",
        "reference": "rcpt 1187",
        "memo": "team lunch",
    }
    body.update(overrides)
    return client.post("/api/expenses", json=body)


def test_create_expense_posts_balanced_entry(client, db_session, seed_accounts, vendor):
    r = _post(client, seed_accounts, vendor_id=vendor["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payee"] == "Sweet Forest Cafe"
    assert body["vendor_id"] == vendor["id"]
    assert body["amount"] == 30.30
    assert body["expense_account_name"] == seed_accounts["6000"].name
    assert body["paid_from_account_name"] == seed_accounts["1000"].name
    assert body["memo"] == "team lunch"

    txn = db_session.query(Transaction).filter(Transaction.id == body["id"]).one()
    assert txn.source_type == "expense"
    assert txn.source_id == vendor["id"]
    assert txn.description == "Expense: Sweet Forest Cafe"
    debits = {ln.account_id: ln.debit for ln in txn.lines if ln.debit > 0}
    credits = {ln.account_id: ln.credit for ln in txn.lines if ln.credit > 0}
    assert debits == {seed_accounts["6000"].id: Decimal("30.30")}
    assert credits == {seed_accounts["1000"].id: Decimal("30.30")}


def test_expense_paid_from_credit_card(client, seed_accounts):
    r = _post(client, seed_accounts, paid_from_account_id=seed_accounts["2100"].id)
    assert r.status_code == 201, r.text
    assert r.json()["paid_from_account_name"] == seed_accounts["2100"].name


def test_expense_without_vendor_uses_payee(client, seed_accounts):
    r = _post(client, seed_accounts, payee="Corner Hardware", memo=None)
    assert r.status_code == 201, r.text
    assert r.json()["payee"] == "Corner Hardware"
    assert r.json()["vendor_id"] is None


def test_expense_rejects_bad_paid_from(client, seed_accounts):
    # Paying an expense "from" another expense account is nonsense.
    r = _post(client, seed_accounts, paid_from_account_id=seed_accounts["6100"].id)
    assert r.status_code == 400
    r = _post(client, seed_accounts, paid_from_account_id=999999)
    assert r.status_code == 404
    r = _post(client, seed_accounts, amount="0")
    assert r.status_code == 400
    r = _post(client, seed_accounts, vendor_id=999999)
    assert r.status_code == 404
    r = _post(client, seed_accounts, expense_account_id=999999)
    assert r.status_code == 404


def test_list_and_get_expenses(client, seed_accounts, vendor):
    assert client.get("/api/expenses").json() == []
    a = _post(client, seed_accounts, vendor_id=vendor["id"]).json()
    b = _post(client, seed_accounts, date="2026-09-03", amount="7.42").json()
    listed = client.get("/api/expenses").json()
    assert [x["id"] for x in listed] == [b["id"], a["id"]]
    assert client.get(f"/api/expenses/{a['id']}").json()["payee"] == "Sweet Forest Cafe"
    assert client.get("/api/expenses/999999").status_code == 404


def test_expense_respects_closing_date(client, seed_accounts):
    r = client.put("/api/settings", json={"closing_date": "2026-09-10"})
    assert r.status_code == 200, r.text
    r = _post(client, seed_accounts, date="2026-09-02")
    assert r.status_code == 403, r.text


def test_void_expense_posts_reversal_and_marks_status(
    client, db_session, seed_accounts, vendor
):
    body = _post(client, seed_accounts, vendor_id=vendor["id"]).json()
    assert body["status"] == "recorded"

    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"

    reversal = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "expense_void",
            Transaction.source_id == body["id"],
        )
        .one()
    )
    assert reversal.description == "VOID Expense: Sweet Forest Cafe"
    debits = {ln.account_id: ln.debit for ln in reversal.lines if ln.debit > 0}
    credits = {ln.account_id: ln.credit for ln in reversal.lines if ln.credit > 0}
    assert debits == {seed_accounts["1000"].id: Decimal("30.30")}
    assert credits == {seed_accounts["6000"].id: Decimal("30.30")}

    # The list shows it void, the reversal itself is not an expense row,
    # and a second void is refused.
    listed = client.get("/api/expenses").json()
    assert [(x["id"], x["status"]) for x in listed] == [(body["id"], "void")]
    assert client.get(f"/api/expenses/{body['id']}").json()["status"] == "void"
    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 400
    assert "already void" in r.json()["detail"]


def test_void_expense_respects_closing_date(client, seed_accounts):
    body = _post(client, seed_accounts, date="2026-01-15").json()
    r = client.put("/api/settings", json={"closing_date": "2026-06-30"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 403, r.text


# ── an expense across several accounts (#599) ────────────────────────────


def _split(client, seed_accounts, lines, **overrides):
    body = {
        "date": "2026-09-02",
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "100.00",
        "reference": "stmt 9",
        "lines": lines,
    }
    body.update(overrides)
    return client.post("/api/expenses", json=body)


def _three(seed_accounts):
    return [
        {
            "account_id": seed_accounts["6000"].id,
            "amount": "60.00",
            "description": "ads",
        },
        {
            "account_id": seed_accounts["6400"].id,
            "amount": "25.50",
            "description": "paper",
        },
        {"account_id": seed_accounts["6300"].id, "amount": "14.50"},
    ]


def test_three_lines_post_one_debit_each_and_one_credit(
    client, db_session, seed_accounts, vendor
):
    r = _split(client, seed_accounts, _three(seed_accounts), vendor_id=vendor["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == 100.0
    assert [ln["account_id"] for ln in body["lines"]] == [
        seed_accounts[n].id for n in ("6000", "6400", "6300")
    ]
    assert [ln["amount"] for ln in body["lines"]] == [60.0, 25.5, 14.5]
    assert body["lines"][1]["description"] == "paper"
    # the older top-level fields describe the first line
    assert body["expense_account_name"] == seed_accounts["6000"].name
    assert body["expense_account_id"] == seed_accounts["6000"].id

    txn = db_session.query(Transaction).filter(Transaction.id == body["id"]).one()
    debits = {ln.account_id: ln.debit for ln in txn.lines if ln.debit > 0}
    assert debits == {
        seed_accounts["6000"].id: Decimal("60.00"),
        seed_accounts["6400"].id: Decimal("25.50"),
        seed_accounts["6300"].id: Decimal("14.50"),
    }
    credits = [ln for ln in txn.lines if ln.credit > 0]
    assert [(c.account_id, c.credit) for c in credits] == [
        (seed_accounts["1000"].id, Decimal("100.00"))
    ]
    # a line without a memo takes the payee's, as the single line always did
    third = next(ln for ln in txn.lines if ln.account_id == seed_accounts["6300"].id)
    assert third.description == "Sweet Forest Cafe"


def test_the_amount_may_be_left_out_and_is_the_lines_sum(client, seed_accounts):
    r = _split(client, seed_accounts, _three(seed_accounts), amount=None)
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 100.0


def test_lines_that_do_not_add_up_are_refused(client, db_session, seed_accounts):
    r = _split(client, seed_accounts, _three(seed_accounts), amount="99.00")
    assert r.status_code == 422
    assert "add up to $100.00" in r.json()["detail"]
    assert "$99.00" in r.json()["detail"]
    assert client.get("/api/expenses").json() == []


def test_a_zero_or_negative_line_is_refused(client, seed_accounts):
    zero = _three(seed_accounts) + [
        {"account_id": seed_accounts["6500"].id, "amount": "0.00"}
    ]
    r = _split(client, seed_accounts, zero)
    assert r.status_code == 422
    assert "Line 4 is 0.00" in r.json()["detail"]
    neg = [
        {"account_id": seed_accounts["6000"].id, "amount": "110.00"},
        {"account_id": seed_accounts["6400"].id, "amount": "-10.00"},
    ]
    assert _split(client, seed_accounts, neg).status_code == 422
    assert _split(client, seed_accounts, []).status_code == 422


def test_lines_and_the_single_account_together_are_refused(client, seed_accounts):
    r = _split(
        client,
        seed_accounts,
        _three(seed_accounts),
        expense_account_id=seed_accounts["6000"].id,
    )
    assert r.status_code == 422
    assert client.get("/api/expenses").json() == []


def test_a_line_cannot_be_the_paid_from_account(client, seed_accounts):
    lines = _three(seed_accounts)
    lines[2]["account_id"] = seed_accounts["1000"].id
    assert _split(client, seed_accounts, lines).status_code == 400
    lines[2]["account_id"] = 999999
    assert _split(client, seed_accounts, lines).status_code == 404


def test_the_single_account_payload_is_one_line(client, seed_accounts):
    body = _post(client, seed_accounts).json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["account_id"] == seed_accounts["6000"].id
    assert body["lines"][0]["amount"] == 30.30
    assert body["lines"][0]["description"] == "team lunch"
    assert body["expense_account_name"] == seed_accounts["6000"].name


def test_each_line_carries_its_class_job_and_cost_code(
    client, db_session, seed_accounts, seed_customer
):
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Deck"}
    ).json()
    code = client.post(
        "/api/cost-codes", json={"code": "06", "name": "Wood", "cost_type": "material"}
    ).json()
    lines = [
        {
            "account_id": seed_accounts["6000"].id,
            "amount": "70.00",
            "job_id": job["id"],
            "cost_code_id": code["id"],
            "is_billable": True,
        },
        {"account_id": seed_accounts["6400"].id, "amount": "30.00"},
    ]
    r = _split(client, seed_accounts, lines)
    assert r.status_code == 201, r.text
    first, second = r.json()["lines"]
    assert first["job_id"] == job["id"] and first["cost_code_id"] == code["id"]
    assert first["cost_type"] == "material" and first["is_billable"] is True
    assert second["job_id"] is None and second["cost_code_id"] is None

    # job costing sees only the line that named the job
    summary = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert summary["total_costs"] == 70.0


def test_a_line_without_a_job_takes_the_expenses(client, seed_accounts, seed_customer):
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Deck"}
    ).json()
    lines = [
        {"account_id": seed_accounts["6000"].id, "amount": "70.00"},
        {"account_id": seed_accounts["6400"].id, "amount": "30.00"},
    ]
    r = _split(client, seed_accounts, lines, job_id=job["id"])
    assert [ln["job_id"] for ln in r.json()["lines"]] == [job["id"], job["id"]]
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["summary"]["total_costs"] == 100.0
    )


def test_a_split_expense_shows_in_reports_by_account(client, seed_accounts):
    assert _split(client, seed_accounts, _three(seed_accounts)).status_code == 201
    pl = client.get(
        "/api/reports/profit-loss?start_date=2026-09-01&end_date=2026-09-30"
    ).json()
    flat = str(pl)
    for name in ("Advertising & Marketing", "Office Supplies", "Insurance"):
        assert name in flat
    assert "60.0" in flat and "25.5" in flat and "14.5" in flat


def test_voiding_a_split_expense_reverses_every_line(client, db_session, seed_accounts):
    body = _split(client, seed_accounts, _three(seed_accounts)).json()
    r = client.post(f"/api/expenses/{body['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    reversal = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "expense_void",
            Transaction.source_id == body["id"],
        )
        .one()
    )
    credits = {ln.account_id: ln.credit for ln in reversal.lines if ln.credit > 0}
    assert credits == {
        seed_accounts["6000"].id: Decimal("60.00"),
        seed_accounts["6400"].id: Decimal("25.50"),
        seed_accounts["6300"].id: Decimal("14.50"),
    }
    debits = {ln.account_id: ln.debit for ln in reversal.lines if ln.debit > 0}
    assert debits == {seed_accounts["1000"].id: Decimal("100.00")}


# ── the form's line grid (#599), read from the page source ───────────────

JS_EXPENSES = (
    Path(__file__).resolve().parents[1] / "app/static/js/expenses.js"
).read_text(encoding="utf-8")


def test_the_expense_form_has_a_line_grid_with_a_running_total():
    form = JS_EXPENSES[JS_EXPENSES.index("async showForm() {") :]
    form = form[: form.index("\n    },")]
    # one line by default, a button for more, the paid amount and what is left
    assert 'id="expense-lines">${ExpensesPage.lineHtml()}' in form
    assert 'onclick="ExpensesPage.addLine()"' in form
    assert 'id="expense-paid-total"' in form and 'id="expense-left"' in form
    # the same per-line cells the bill grid shows
    for cell in (
        "CostCodes.headHtml()",
        "CostTypes.headHtml()",
        "Nonprofit.headHtml()",
    ):
        assert cell in form
    line = JS_EXPENSES[JS_EXPENSES.index("    lineHtml(line = null) {") :]
    line = line[: line.index("\n    },")]
    for cls in ("line-account", "line-desc", "line-job", "line-amt money"):
        assert cls in line
    assert "CostCodes.cellHtml('line-cost-code'" in line
    assert "ExpensesPage.fundCellsHtml(l)" in line
    # the save sends the lines and refuses ones that do not add up to the paid amount
    save = JS_EXPENSES[JS_EXPENSES.index("    async save(e) {") :]
    save = save[: save.index("\n    },")]
    assert "lines," in save and "line_order" not in save
    assert save.index("must add up to the amount paid") < save.index(
        "API.post('/expenses'"
    )
    # the scan fills the first line: the total goes through the one-line rule
    assert (
        "ExpensesPage.amountChanged();  // the scan fills the first line" in JS_EXPENSES
    )


def test_the_memo_is_the_one_typed_on_the_expense_not_a_lines(client, seed_accounts):
    """W-51 (2.31.0 gate): once lines carry their own memos (#599), the
    expense's memo must still be the one typed on the expense, and with none
    typed it is empty, not the payee the bank line shows in the register."""
    r = _split(
        client,
        seed_accounts,
        _three(seed_accounts),
        memo="Big Box run",
        payee="Big Box",
    )
    assert r.status_code == 201, r.text
    body = client.get(f"/api/expenses/{r.json()['id']}").json()
    assert body["memo"] == "Big Box run"
    assert body["payee"] == "Big Box"
    assert [ln["description"] for ln in body["lines"]][:2] == ["ads", "paper"]

    r = _split(client, seed_accounts, _three(seed_accounts), payee="Big Box")
    assert r.status_code == 201, r.text
    body = client.get(f"/api/expenses/{r.json()['id']}").json()
    assert body["memo"] == "" and body["payee"] == "Big Box"
    listed = next(
        e for e in client.get("/api/expenses").json() if e["id"] == body["id"]
    )
    assert listed["memo"] == ""


# ── the memo kept on its own (#648, from the 2.31.0 gate) ────────────────

ROOT = Path(__file__).resolve().parents[1]
MEMO_REVISION = "f3a9c7e1b5d4"
MEMO_BEFORE = "c9e5a7b3d1f2"  # memorized transactions (#605), which this one follows


def _memo_read_back(client, expense_id):
    """The memo the expense's own page and the list both show."""
    one = client.get(f"/api/expenses/{expense_id}").json()["memo"]
    listed = next(
        e for e in client.get("/api/expenses").json() if e["id"] == expense_id
    )
    assert listed["memo"] == one
    return one


def test_a_memo_typed_the_same_as_the_payee_comes_back_as_typed(
    client, seed_accounts, vendor
):
    r = _post(client, seed_accounts, payee="Corner Hardware", memo="Corner Hardware")
    assert r.status_code == 201, r.text
    assert r.json()["memo"] == "Corner Hardware"
    assert _memo_read_back(client, r.json()["id"]) == "Corner Hardware"

    # the same with a vendor and no payee: the memo reads like the vendor
    r = _post(client, seed_accounts, vendor_id=vendor["id"], memo="Sweet Forest Cafe")
    assert r.status_code == 201, r.text
    assert _memo_read_back(client, r.json()["id"]) == "Sweet Forest Cafe"


def test_a_vendor_with_a_different_payee_and_no_memo_has_none(
    client, db_session, seed_accounts, vendor
):
    r = _post(client, seed_accounts, vendor_id=vendor["id"], payee="Big Box", memo=None)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["memo"] == "" and body["vendor_id"] == vendor["id"]
    assert _memo_read_back(client, body["id"]) == ""
    # the bank line still carries the payee, which is what the register shows
    txn = db_session.query(Transaction).filter(Transaction.id == body["id"]).one()
    bank = next(ln for ln in txn.lines if ln.credit > 0)
    assert bank.description == "Big Box"
    assert txn.description == "Expense: Big Box"
    assert txn.memo == ""


def test_a_memo_with_a_vendor_and_no_payee_comes_back(client, seed_accounts, vendor):
    r = _post(client, seed_accounts, vendor_id=vendor["id"], memo="Blades")
    assert r.status_code == 201, r.text
    assert r.json()["payee"] == "Sweet Forest Cafe"
    assert _memo_read_back(client, r.json()["id"]) == "Blades"


def test_an_expense_saved_before_the_column_reads_its_memo_off_the_bank_line(
    client, db_session, seed_accounts, vendor
):
    """A row from before #648 has no stored memo: its bank line holds the
    memo, or with none typed the payee the entry's description carries,
    which is not a memo (and is not the vendor's name)."""
    from sqlalchemy import text

    with_memo = _post(client, seed_accounts, vendor_id=vendor["id"]).json()
    payee_only = _post(client, seed_accounts, payee="Corner Hardware", memo=None).json()
    vendor_and_payee = _post(
        client, seed_accounts, vendor_id=vendor["id"], payee="Big Box", memo=None
    ).json()
    ids = [with_memo["id"], payee_only["id"], vendor_and_payee["id"]]
    db_session.execute(
        text("UPDATE transactions SET memo = NULL WHERE id IN (:a, :b, :c)"),
        dict(zip("abc", ids)),
    )
    db_session.commit()
    assert [
        db_session.query(Transaction).filter(Transaction.id == i).one().memo
        for i in ids
    ] == [None, None, None]

    assert _memo_read_back(client, with_memo["id"]) == "team lunch"
    assert _memo_read_back(client, payee_only["id"]) == ""
    assert _memo_read_back(client, vendor_and_payee["id"]) == ""


def test_the_migration_adds_the_memo_column_and_drops_it(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "memo.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(MEMO_REVISION).down_revision == MEMO_BEFORE

    # books from before the column, with an expense in them
    command.upgrade(cfg, MEMO_BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        names = {c["name"] for c in inspect(conn).get_columns("transactions")}
        assert "memo" not in names
        conn.execute(
            text(
                "INSERT INTO transactions (id, date, description, source_type) "
                "VALUES (1, '2026-09-02', 'Expense: Big Box', 'expense')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, MEMO_REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("transactions")}
    assert cols["memo"]["nullable"] is True
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT memo, description FROM transactions WHERE id = 1")
        ).one()
    assert row == (None, "Expense: Big Box")
    engine.dispose()

    command.downgrade(cfg, MEMO_BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("transactions")}
    assert "memo" not in names and "description" in names
    with engine.connect() as conn:
        kept = conn.execute(
            text("SELECT description FROM transactions WHERE id = 1")
        ).scalar()
    assert kept == "Expense: Big Box"
    engine.dispose()
