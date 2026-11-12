"""Reports on a cash basis, step 3 (#483, docs/design/cash-basis.md): editing
a paid invoice or bill rewrites its payments' cash lines, and a journal entry
chooses the basis it counts on."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.models.payments import Payment
from app.models.transactions import (
    BASIS_ACCRUAL,
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ALL,
    TransactionLine,
)
from tests.test_cash_basis import (
    _account_id,
    _amount,
    _assert_cash_invariant,
    _bill,
    _bill_payment,
    _bs,
    _bs_amount,
    _each_entry_balances_on_every_basis,
    _invoice,
    _item,
    _make_ledger_posted,
    _payment,
    _pl,
    _vendor,
)

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
BEFORE = "c6e8f0a2d4b6"
REVISION = "f4c6e8a0b2d4"


def _edit_lines(client, invoice_id, lines):
    r = client.put(
        f"/api/invoices/{invoice_id}",
        json={
            "lines": [
                {"description": f"line {i}", "quantity": 1, "line_order": i, **ln}
                for i, ln in enumerate(lines)
            ]
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _cash_lines(db_session, txn_id):
    db_session.expire_all()
    return [
        ln
        for ln in db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
        if ln.basis == BASIS_CASH
    ]


# ---------------------------------------------------------------------------
# editing a paid document
# ---------------------------------------------------------------------------


def test_editing_a_part_paid_invoice_moves_the_paid_share(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    labor = _item(client, "Labor", "4100")
    inv = _invoice(
        client,
        cust,
        "2026-03-01",
        [{"rate": 1000}, {"rate": 500, "item_id": labor["id"]}],
    )
    _payment(client, cust, "2026-03-10", 750, [(inv["id"], 750)])
    march, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(march, "4000") == D("500.00")
    assert _amount(march, "4100") == D("250.00")

    # the amounts swap accounts
    _edit_lines(
        client, inv["id"], [{"rate": 500}, {"rate": 1000, "item_id": labor["id"]}]
    )

    march, bs, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(march, "4000") == D("250.00")
    assert _amount(march, "4100") == D("500.00")
    assert D(str(march["net_income"])) == D("750.00")
    assert _bs_amount(bs, "1100") == 0
    # the accrual view is the edited invoice, A/R open for what is unpaid
    accrual = _pl(client, "2026-03-01", "2026-03-31", "accrual")
    assert _amount(accrual, "4000") == D("500.00")
    assert _amount(accrual, "4100") == D("1000.00")
    assert _bs_amount(_bs(client, "2026-03-31"), "1100") == D("750.00")
    _each_entry_balances_on_every_basis(db_session)

    # a second payment after the edit prorates over the new lines, and a
    # further edit rewrites both payments
    _payment(client, cust, "2026-04-05", 750, [(inv["id"], 750)])
    _edit_lines(client, inv["id"], [{"rate": 1500}])
    march, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(march, "4000") == D("750.00")
    assert _amount(march, "4100") == 0
    april, _, _ = _assert_cash_invariant(client, "2026-04-01", "2026-04-30")
    assert _amount(april, "4000") == D("750.00")
    _each_entry_balances_on_every_basis(db_session)


def test_editing_a_part_paid_bill_moves_the_paid_share(
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
    _bill_payment(client, vendor["id"], "2026-05-25", 100, [(bill["id"], 100)])

    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {"description": "a", "quantity": 1, "rate": 25, "account_id": a6000},
                {"description": "b", "quantity": 1, "rate": 175, "account_id": a6100},
            ]
        },
    )
    assert r.status_code == 200, r.text

    may, bs, _ = _assert_cash_invariant(client, "2026-05-01", "2026-05-31")
    assert _amount(may, "6000") == D("12.50")
    assert _amount(may, "6100") == D("87.50")
    assert D(str(may["net_income"])) == D("-100.00")
    assert _bs_amount(bs, "2000") == 0
    accrual = _pl(client, "2026-05-01", "2026-05-31", "accrual")
    assert _amount(accrual, "6000") == D("25.00")
    assert _amount(accrual, "6100") == D("175.00")
    _each_entry_balances_on_every_basis(db_session)


def test_a_payment_under_the_closing_date_keeps_its_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    labor = _item(client, "Labor", "4100")
    # paid in March ahead of the invoice, applied when the invoice is made
    pay = _payment(client, cust, "2026-03-10", 600)
    inv = _invoice(
        client,
        cust,
        "2026-04-10",
        [{"rate": 400}, {"rate": 200, "item_id": labor["id"]}],
    )
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": inv["id"], "amount": 600}]},
    )
    assert r.status_code == 200, r.text
    r = client.put("/api/settings", json={"closing_date": "2026-03-31"})
    assert r.status_code == 200, r.text

    _edit_lines(
        client, inv["id"], [{"rate": 100}, {"rate": 500, "item_id": labor["id"]}]
    )

    # March is closed: the payment's lines stay as they were
    march, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert _amount(march, "4000") == D("400.00")
    assert _amount(march, "4100") == D("200.00")
    # the accrual view is the edited invoice
    accrual = _pl(client, "2026-04-01", "2026-04-30", "accrual")
    assert _amount(accrual, "4000") == D("100.00")
    _each_entry_balances_on_every_basis(db_session)


def test_voiding_the_payment_after_an_edit_reverses_cleanly(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    labor = _item(client, "Labor", "4100")
    inv = _invoice(
        client,
        cust,
        "2026-03-01",
        [{"rate": 1000}, {"rate": 500, "item_id": labor["id"]}],
    )
    pay = _payment(client, cust, "2026-03-10", 750, [(inv["id"], 750)])
    _edit_lines(
        client, inv["id"], [{"rate": 500}, {"rate": 1000, "item_id": labor["id"]}]
    )

    r = client.post(f"/api/payments/{pay['id']}/void")
    assert r.status_code == 200, r.text
    march, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert D(str(march["net_income"])) == 0
    assert _amount(march, "4100") == 0
    assert D(str(_pl(client, "2026-03-01", "2026-03-31")["net_income"])) == D("1500.00")
    _each_entry_balances_on_every_basis(db_session)


def test_an_adopted_qbo_invoice_recognizes_its_income_on_payments_that_have_other_lines(
    client, db_session, seed_accounts, seed_customer
):
    """Known limit of step 1: a ledger-posted invoice's income counted once,
    on the posting date (`both`). Edited, it becomes an accrual invoice, and
    the payments on it must recognize it, or its income would vanish from
    the cash view, here on a payment that already carried another invoice's
    cash lines."""
    cust = seed_customer.id
    ledger = _invoice(client, cust, "2026-03-01", [{"rate": 100}])
    _make_ledger_posted(db_session, ledger["id"], "Invoice", "9101")
    plain = _invoice(client, cust, "2026-03-02", [{"rate": 50}])
    pay = _payment(
        client, cust, "2026-04-05", 150, [(ledger["id"], 100), (plain["id"], 50)]
    )
    pl, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-04-30")
    assert D(str(pl["net_income"])) == D("150.00")

    _edit_lines(client, ledger["id"], [{"rate": 150}])

    # March (the posting, now reversed and replaced by an accrual invoice)
    # holds no cash income; April holds both invoices' paid parts
    march, _, _ = _assert_cash_invariant(client, "2026-03-01", "2026-03-31")
    assert D(str(march["net_income"])) == 0
    april, bs, _ = _assert_cash_invariant(client, "2026-04-01", "2026-04-30")
    assert _amount(april, "4000") == D("150.00")
    assert _amount(april, "4950") == 0
    assert _bs_amount(bs, "1100") == 0
    whole = _pl(client, "2026-03-01", "2026-04-30", "cash")
    assert D(str(whole["net_income"])) == D("150.00")
    accrual = _pl(client, "2026-03-01", "2026-04-30", "accrual")
    assert D(str(accrual["net_income"])) == D("200.00")
    payment = db_session.get(Payment, pay["id"])
    linked = {
        ln.recognizes_invoice_id
        for ln in _cash_lines(db_session, payment.transaction_id)
        if ln.recognizes_invoice_id
    }
    assert linked == {ledger["id"], plain["id"]}
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# lines named by their document, and the older ones that are not
# ---------------------------------------------------------------------------


def test_cash_lines_name_their_document_and_the_backfill_stays_idempotent(
    client, db_session, seed_accounts, seed_customer
):
    from app.services import cash_basis

    cust = seed_customer.id
    inv = _invoice(client, cust, "2026-03-01", [{"rate": 100}])
    pay = _payment(client, cust, "2026-03-02", 100, [(inv["id"], 100)])
    txn_id = db_session.get(Payment, pay["id"]).transaction_id
    named = [ln for ln in _cash_lines(db_session, txn_id) if ln.recognizes_invoice_id]
    assert [ln.recognizes_invoice_id for ln in named] == [inv["id"]]

    # a file whose payments carry unnamed cash lines: backfill writes nothing
    # more, and an edit still finds the lines of a payment that paid one
    # document in full
    db_session.execute(
        text("UPDATE transaction_lines SET recognizes_invoice_id = NULL")
    )
    db_session.commit()
    assert cash_basis.backfill(db_session)["payments"] == 0
    _edit_lines(client, inv["id"], [{"rate": 100, "description": "edited"}])
    cash = _pl(client, "2026-03-01", "2026-03-31", "cash")
    assert _amount(cash, "4000") == D("100.00")
    assert len(_cash_lines(db_session, txn_id)) == 2  # not doubled
    _assert_cash_invariant(client, "2026-03-01", "2026-03-31")


def test_unnamed_cash_lines_of_a_payment_on_two_documents_are_left_alone(
    client, db_session, seed_accounts, seed_customer
):
    cust = seed_customer.id
    one = _invoice(client, cust, "2026-03-01", [{"rate": 100}])
    two = _invoice(client, cust, "2026-03-01", [{"rate": 50}])
    pay = _payment(client, cust, "2026-03-02", 150, [(one["id"], 100), (two["id"], 50)])
    db_session.execute(
        text("UPDATE transaction_lines SET recognizes_invoice_id = NULL")
    )
    db_session.commit()
    txn_id = db_session.get(Payment, pay["id"]).transaction_id
    before = sorted(
        (ln.account_id, ln.credit, ln.debit) for ln in _cash_lines(db_session, txn_id)
    )

    _edit_lines(client, one["id"], [{"rate": 120}])

    after = sorted(
        (ln.account_id, ln.credit, ln.debit) for ln in _cash_lines(db_session, txn_id)
    )
    assert after == before
    _assert_cash_invariant(client, "2026-03-01", "2026-03-31")


# ---------------------------------------------------------------------------
# the journal entry's basis
# ---------------------------------------------------------------------------


def _journal(client, accounts, basis=None, day="2026-02-01", amount="500.00"):
    body = {
        "date": day,
        "description": "Opening A/R",
        "lines": [
            {"account_id": accounts["1100"].id, "debit": amount, "credit": "0"},
            {"account_id": accounts["4000"].id, "debit": "0", "credit": amount},
        ],
    }
    if basis is not None:
        body["basis"] = basis
    return client.post("/api/journal", json=body)


def _tb_total(client, as_of):
    r = client.get(f"/api/reports/trial-balance?start_date=2026-01-01&end_date={as_of}")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_journal_entry_counts_on_the_basis_it_chooses(
    client, db_session, seed_accounts, seed_customer
):
    for basis, accrual, cash in (
        (None, "500.00", "500.00"),
        ("both", "500.00", "500.00"),
        ("accrual", "500.00", "0"),
        ("cash", "0", "500.00"),
    ):
        db_session.execute(text("DELETE FROM transaction_lines"))
        db_session.execute(text("DELETE FROM transactions"))
        db_session.commit()
        r = _journal(client, seed_accounts, basis)
        assert r.status_code == 201, r.text
        assert r.json()["basis"] == (basis or "both")
        assert client.get(f"/api/journal/{r.json()['id']}").json()["basis"] == (
            basis or "both"
        )
        a = _pl(client, "2026-02-01", "2026-02-28", "accrual")
        c = _pl(client, "2026-02-01", "2026-02-28", "cash")
        assert _amount(a, "4000") == D(accrual), basis
        assert _amount(c, "4000") == D(cash), basis
        assert _bs_amount(_bs(client, "2026-02-28", "accrual"), "1100") == D(accrual)
        assert _bs_amount(_bs(client, "2026-02-28", "cash"), "1100") == D(cash)
        # the Trial Balance reads accrual until it takes a basis
        tb = _tb_total(client, "2026-02-28")
        assert D(str(tb["total_debit"])) == D(accrual), basis
        _each_entry_balances_on_every_basis(db_session)


def test_an_unknown_journal_basis_is_refused(client, seed_accounts):
    r = _journal(client, seed_accounts, "sometimes")
    assert r.status_code == 422


def test_editing_and_voiding_a_journal_entry_carry_its_basis(
    client, db_session, seed_accounts, seed_customer
):
    entry = _journal(client, seed_accounts, "cash").json()
    listed = [
        e
        for e in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
        if e["id"] == entry["id"]
    ]
    assert listed[0]["basis"] == "cash" and len(listed[0]["lines"]) == 2

    body = {
        "date": "2026-02-01",
        "description": "Opening A/R, corrected",
        "lines": [
            {"account_id": seed_accounts["1100"].id, "debit": "700.00", "credit": "0"},
            {"account_id": seed_accounts["4000"].id, "debit": "0", "credit": "700.00"},
        ],
    }
    # left out, the edit keeps the entry's basis
    r = client.put(f"/api/journal/{entry['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "cash" and r.json()["total_debit"] == 700
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "cash"), "4000") == D(
        "700.00"
    )
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "accrual"), "4000") == 0

    # a new choice moves it
    r = client.put(f"/api/journal/{entry['id']}", json={**body, "basis": "accrual"})
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "accrual"
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "cash"), "4000") == 0
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "accrual"), "4000") == D(
        "700.00"
    )

    r = client.post(f"/api/journal/{entry['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["basis"] == "accrual"
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "accrual"), "4000") == 0
    assert _amount(_pl(client, "2026-02-01", "2026-02-28", "cash"), "4000") == 0
    bases = {
        ln.basis
        for ln in db_session.query(TransactionLine)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    }
    assert bases == {BASIS_ACCRUAL}
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def test_the_migration_adds_the_document_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "links.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(REVISION).down_revision == BEFORE
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"]: c for c in inspect(engine).get_columns("transaction_lines")}
    for name in ("recognizes_invoice_id", "recognizes_bill_id"):
        assert name in cols and cols[name]["nullable"] is True
    indexed = {i["name"] for i in inspect(engine).get_indexes("transaction_lines")}
    assert "ix_transaction_lines_recognizes_invoice_id" in indexed
    engine.dispose()
    command.downgrade(cfg, BEFORE)
    engine = create_engine(f"sqlite:///{db}")
    names = {c["name"] for c in inspect(engine).get_columns("transaction_lines")}
    assert "recognizes_invoice_id" not in names and "basis" in names
    engine.dispose()
