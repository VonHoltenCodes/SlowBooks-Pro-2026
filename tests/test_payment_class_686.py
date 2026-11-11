"""A class on customer payments and bill payments (#686).

A payment posted without a class, so its bank and A/R or A/P lines could
not be put on a division. The payment now names one (as a transfer does,
#676): it goes on the payment's own posted lines and on the early-payment
discount taken with it, an unknown class is 404 and an archived one 400,
and the cash-basis lines keep reading the documents' own classes.
"""

import re
from pathlib import Path

from app.models.bills import BillPayment
from app.models.credit_memos import CreditMemo
from app.models.payments import Payment
from app.models.transactions import BASIS_CASH, Transaction
from app.models.vendor_credits import VendorCredit
from tests.test_payment_terms_664 import (
    TWO_TEN,
    _account_id,
    _balanced,
    _bill,
    _invoice,
    _term,
    _vendor,
)

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
REVISION = "f6a9d3c27e51"


def _class(client, name="North Crew"):
    r = client.post("/api/classes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _receive(client, customer_id, invoice_id, amount="100.00", **extra):
    return client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-10-12",
            "amount": amount,
            "deposit_to_account_id": extra.pop("deposit_to_account_id", None),
            "allocations": [
                {
                    "invoice_id": invoice_id,
                    "amount": amount,
                    **extra.pop("alloc", {}),
                }
            ],
            **extra,
        },
    )


def _pay_bill(client, bill, amount=500.0, **extra):
    return client.post(
        "/api/bill-payments",
        json={
            "vendor_id": bill["vendor_id"],
            "date": "2026-10-12",
            "amount": amount,
            "allocations": [
                {"bill_id": bill["id"], "amount": amount, **extra.pop("alloc", {})}
            ],
            **extra,
        },
    )


def _every(txn):
    from sqlalchemy.orm import object_session

    from app.services.accounting import all_lines

    return all_lines(object_session(txn), txn.id)


def _accrual(txn):
    return [ln for ln in _every(txn) if ln.basis != BASIS_CASH]


def _cash(txn):
    """The entry's cash-basis lines (#483), which the session hides."""
    return [ln for ln in _every(txn) if ln.basis == BASIS_CASH]


# ── customer payments ───────────────────────────────────────────────────


def test_a_payment_with_a_class_posts_its_lines_in_it(
    client, db_session, seed_accounts, seed_customer
):
    cls = _class(client)
    inv = _invoice(client, seed_customer.id, "Net 30")
    bank = seed_accounts["1000"].id
    r = _receive(
        client,
        seed_customer.id,
        inv["id"],
        class_id=cls["id"],
        deposit_to_account_id=bank,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["class_id"] == cls["id"] and body["class_name"] == "North Crew"
    pay = db_session.get(Payment, body["id"])
    assert pay.class_id == cls["id"]
    txn = db_session.get(Transaction, pay.transaction_id)
    own = _accrual(txn)
    assert len(own) == 2 and {ln.class_id for ln in own} == {cls["id"]}
    # the cash-basis lines read the invoice's lines, which have no class
    assert _cash(txn) and {ln.class_id for ln in _cash(txn)} == {None}
    # the payment's view names it, and the register's class filter finds it
    assert client.get(f"/api/payments/{pay.id}").json()["class_name"] == "North Crew"
    reg = client.get(
        f"/api/banking/check-register?account_id={bank}&class_id={cls['id']}"
    ).json()
    assert [e["transaction_id"] for e in reg["entries"]] == [txn.id]
    _balanced(db_session)
    # voided, the reversal keeps the class on the same lines
    assert client.post(f"/api/payments/{pay.id}/void").status_code == 200
    void = (
        db_session.query(Transaction)
        .filter_by(source_type="payment_void", source_id=pay.id)
        .one()
    )
    assert {ln.class_id for ln in _accrual(void)} == {cls["id"]}


def test_a_payment_without_a_class_posts_as_before(
    client, db_session, seed_accounts, seed_customer
):
    _class(client)
    inv = _invoice(client, seed_customer.id, "Net 30")
    r = _receive(client, seed_customer.id, inv["id"])
    assert r.status_code == 201, r.text
    assert r.json()["class_id"] is None and r.json()["class_name"] is None
    txn = (
        db_session.query(Transaction)
        .filter_by(source_type="payment", source_id=r.json()["id"])
        .one()
    )
    assert txn.class_id is None and {ln.class_id for ln in txn.lines} == {None}


def test_an_unknown_or_archived_class_is_refused_on_a_payment(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id, "Net 30")
    r = _receive(client, seed_customer.id, inv["id"], class_id=999999)
    assert r.status_code == 404, r.text
    cls = _class(client, "Old Crew")
    assert (
        client.put(f"/api/classes/{cls['id']}", json={"is_archived": True}).status_code
        == 200
    )
    r = _receive(client, seed_customer.id, inv["id"], class_id=cls["id"])
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    assert db_session.query(Payment).count() == 0


def test_the_discount_memo_taken_with_a_payment_carries_its_class(
    client, db_session, seed_accounts, seed_customer
):
    _term(client, **TWO_TEN)
    cls = _class(client)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    acct = _account_id(db_session, "4900")
    r = _receive(
        client,
        seed_customer.id,
        inv["id"],
        amount="98.00",
        class_id=cls["id"],
        discount_account_id=acct,
        alloc={"discount": "2.00"},
    )
    assert r.status_code == 201, r.text
    cm = (
        db_session.query(CreditMemo).filter_by(discount_payment_id=r.json()["id"]).one()
    )
    assert cm.class_id == cls["id"]
    memo_txn = db_session.get(Transaction, cm.transaction_id)
    assert {ln.class_id for ln in _accrual(memo_txn)} == {cls["id"]}
    assert {ln.class_id for ln in _cash(memo_txn)} == {None}
    _balanced(db_session)


def test_a_discount_without_a_payment_class_keeps_the_invoices(
    client, db_session, seed_accounts, seed_customer
):
    _term(client, **TWO_TEN)
    cls = _class(client)
    inv = _invoice(client, seed_customer.id, "2% 10 Net 30")
    client.put(f"/api/invoices/{inv['id']}", json={"class_id": cls["id"]})
    acct = _account_id(db_session, "4900")
    r = _receive(
        client,
        seed_customer.id,
        inv["id"],
        amount="98.00",
        discount_account_id=acct,
        alloc={"discount": "2.00"},
    )
    assert r.status_code == 201, r.text
    cm = (
        db_session.query(CreditMemo).filter_by(discount_payment_id=r.json()["id"]).one()
    )
    db_session.refresh(cm)
    from app.models.invoices import Invoice

    assert cm.class_id == db_session.get(Invoice, inv["id"]).class_id


# ── bill payments ───────────────────────────────────────────────────────


def test_a_bill_payment_with_a_class_posts_its_lines_in_it(
    client, db_session, seed_accounts
):
    cls = _class(client)
    bill = _bill(client, _vendor(client)["id"], "Net 30")
    r = _pay_bill(client, bill, class_id=cls["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["class_id"] == cls["id"] and body["class_name"] == "North Crew"
    pay = db_session.get(BillPayment, body["id"])
    txn = db_session.get(Transaction, pay.transaction_id)
    own = _accrual(txn)
    assert len(own) == 2 and {ln.class_id for ln in own} == {cls["id"]}
    assert {ln.class_id for ln in _cash(txn)} == {None}
    assert (
        client.get(f"/api/bill-payments/{pay.id}").json()["class_name"] == "North Crew"
    )
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
        f"&class_id={cls['id']}"
    ).json()
    assert [e["transaction_id"] for e in reg["entries"]] == [txn.id]
    _balanced(db_session)
    # without one: no class on any line
    bill2 = _bill(client, bill["vendor_id"], "Net 30", number="B-2")
    r = _pay_bill(client, bill2)
    assert r.status_code == 201 and r.json()["class_id"] is None
    txn = db_session.get(
        Transaction, db_session.get(BillPayment, r.json()["id"]).transaction_id
    )
    assert txn.class_id is None and {ln.class_id for ln in txn.lines} == {None}


def test_an_unknown_or_archived_class_is_refused_on_a_bill_payment(
    client, db_session, seed_accounts
):
    bill = _bill(client, _vendor(client)["id"], "Net 30")
    assert _pay_bill(client, bill, class_id=999999).status_code == 404
    cls = _class(client, "Old Crew")
    client.put(f"/api/classes/{cls['id']}", json={"is_archived": True})
    r = _pay_bill(client, bill, class_id=cls["id"])
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    assert db_session.query(BillPayment).count() == 0


def test_the_vendor_credit_taken_with_a_bill_payment_carries_its_class(
    client, db_session, seed_accounts
):
    _term(client, **TWO_TEN)
    cls = _class(client)
    bill = _bill(client, _vendor(client, terms="2% 10 Net 30")["id"], "2% 10 Net 30")
    acct = _account_id(db_session, "4900")
    r = _pay_bill(
        client,
        bill,
        490.0,
        class_id=cls["id"],
        discount_account_id=acct,
        alloc={"discount": "10.00"},
    )
    assert r.status_code == 201, r.text
    vc = (
        db_session.query(VendorCredit)
        .filter_by(discount_bill_payment_id=r.json()["id"])
        .one()
    )
    assert vc.class_id == cls["id"]
    vc_txn = db_session.get(Transaction, vc.transaction_id)
    assert {ln.class_id for ln in _accrual(vc_txn)} == {cls["id"]}
    _balanced(db_session)


# ── export and import ───────────────────────────────────────────────────


def test_the_iif_export_writes_the_class_and_the_import_reads_it(
    client, db_session, seed_accounts, seed_customer
):
    from app.services.iif_export import export_bill_payments, export_payments
    from app.services.iif_import import import_all

    cls = _class(client)
    inv = _invoice(client, seed_customer.id, "Net 30")
    assert (
        _receive(client, seed_customer.id, inv["id"], class_id=cls["id"]).status_code
        == 201
    )
    bill = _bill(client, _vendor(client)["id"], "Net 30")
    assert _pay_bill(client, bill, class_id=cls["id"]).status_code == 201

    bp_rows = [ln.split("\t") for ln in export_bill_payments(db_session).splitlines()]
    assert [r[-1] for r in bp_rows if r[0] in ("TRNS", "SPL")] == ["North Crew"] * 2
    text = export_payments(db_session)
    rows = [ln.split("\t") for ln in text.splitlines()]
    assert {r[-1] for r in rows if r[0] in ("TRNS", "SPL")} == {"North Crew"}

    # into the books again, with the payment gone: it comes back classed
    pay = db_session.query(Payment).one()
    for a in list(pay.allocations):
        a.invoice.amount_paid = 0
        a.invoice.balance_due = a.invoice.total
    txn_id = pay.transaction_id
    db_session.delete(pay)
    db_session.flush()
    db_session.query(Transaction).filter(Transaction.id == txn_id).delete()
    db_session.commit()
    result = import_all(db_session, text)
    assert result["payments"] == 1, result
    back = db_session.query(Payment).one()
    assert back.class_id == cls["id"]
    txn = db_session.get(Transaction, back.transaction_id)
    assert {ln.class_id for ln in txn.lines} == {cls["id"]}


# ── the migration ───────────────────────────────────────────────────────


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def test_the_migration_adds_the_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "payclass.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert "f6a9d3c27e51" in {r.revision for r in script.walk_revisions()}
    assert script.get_revision(REVISION).down_revision == "e5c1a7d3f9b2"
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    for table in ("payments", "bill_payments"):
        cols = {c["name"]: c for c in insp.get_columns(table)}
        assert "class_id" in cols and cols["class_id"]["nullable"], table
        fks = {
            (tuple(f["constrained_columns"]), f["referred_table"])
            for f in insp.get_foreign_keys(table)
        }
        assert (("class_id",), "classes") in fks, table
    engine.dispose()
    command.downgrade(cfg, "e5c1a7d3f9b2")
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    for table in ("payments", "bill_payments"):
        assert "class_id" not in {c["name"] for c in insp.get_columns(table)}
    engine.dispose()
    # up again; over books that already have the columns (stamped back, as
    # create_all books are) it is a no-op, not an error
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    command.stamp(cfg, "e5c1a7d3f9b2")
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)


# ── the forms ───────────────────────────────────────────────────────────


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_class_box_is_named_and_marked_data_write():
    utils = _js("utils.js")
    group = utils[utils.index("const PaymentClass = {") :]
    group = group[: group.index("\n};")]
    # labelled for its id, named class_id, hidden from a read-only sign-in
    assert "<label for=\"${id}\">${T('Class')}</label>" in group
    assert re.search(r'<select id="\$\{id\}" name="class_id" data-write', group)
    # shown once the company has a class of its own; archived ones never offered
    assert "!c.is_system_default && !c.is_archived" in group
    assert "ClassWarn.enabled()" in group
    pay = _js("payments.js")
    assert "${PaymentClass.groupHtml('payment-class')}" in pay
    assert "class_id: classIdFromForm(form)," in pay
    assert "await ClassWarn.ok(form)" in pay
    assert "PaymentClass.follow(select, applied.map(inv => inv.class_id))" in pay
    assert "${p.class_name ?" in pay
    bills = _js("bills.js")
    assert "${PaymentClass.groupHtml('pay-class')}" in bills
    assert "class_id: classIdFromForm(form)," in bills
    assert "PaymentClass.follow(select, paid.map(b => b.class_id))" in bills
    assert "${p.class_name ?" in bills
