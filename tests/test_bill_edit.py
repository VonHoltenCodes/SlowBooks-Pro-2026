"""Editing a posted bill (#225), the way an invoice is edited.

Bills were post-and-void only: no PUT, no Edit. Reported by a 2.19.0 user
who could view a bill but not change it. Now `PUT /api/bills/{id}` edits
the header, the lines, or both, re-posting the journal through create's own
path with the same transaction id, and every balance that reads from a
bill holds: the journal, Accounts Payable, the vendor's balance, the A/P
aging, the trial balance and the stock ledger.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.bills import Bill, BillLine
from app.models.items import InventoryMovement, Item, ItemType, MovementType
from app.models.transactions import Transaction, TransactionLine

D = Decimal


def _vendor(client, name="Harbor Supply", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _bill(client, vendor_id, lines, **extra):
    body = {"vendor_id": vendor_id, "date": "2026-07-10", "lines": lines, **extra}
    r = client.post("/api/bills", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _gl(db, account_id):
    """Natural-sign sum of every line on the account: Dr - Cr for a
    debit-normal account, Cr - Dr otherwise."""
    acct = db.get(Account, account_id)
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    dr = sum((r.debit for r in rows), D("0"))
    cr = sum((r.credit for r in rows), D("0"))
    return (
        (dr - cr)
        if acct.account_type.value in ("asset", "expense", "cogs")
        else (cr - dr)
    )


def _balanced(db):
    for txn in db.query(Transaction).all():
        dr = sum((ln.debit for ln in txn.lines), D("0"))
        cr = sum((ln.credit for ln in txn.lines), D("0"))
        assert dr == cr, f"transaction {txn.id} {txn.description}: {dr} != {cr}"


@pytest.fixture
def books(client, seed_accounts):
    v = _vendor(client)
    bill = _bill(
        client,
        v["id"],
        [
            {
                "description": "Signage",
                "account_id": seed_accounts["6000"].id,
                "quantity": 2,
                "rate": 150,
                "line_order": 0,
            },
            {
                "description": "Install",
                "account_id": seed_accounts["5300"].id,
                "quantity": 1,
                "rate": 100,
                "line_order": 1,
            },
        ],
        bill_number="HS-100",
    )
    return {"vendor": v, "bill": bill, "ap": seed_accounts["2000"].id}


# ── the header ───────────────────────────────────────────────────────────────


def test_a_header_edit_changes_the_words_and_leaves_the_posting(
    client, db_session, books
):
    bill = books["bill"]
    txn_before = db_session.get(Bill, bill["id"]).transaction_id
    lines_before = {
        (ln.account_id, ln.debit, ln.credit)
        for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn_before)
    }
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={"notes": "Approved by TVH", "ref_number": "PO-7", "terms": "Net 15"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["notes"] == "Approved by TVH" and out["ref_number"] == "PO-7"
    assert (
        out["terms"] == "Net 15" and out["due_date"] == "2026-07-25"
    )  # follows the terms
    assert D(out["total"]) == D("400.00") and D(out["balance_due"]) == D("400.00")
    db_session.expire_all()
    b = db_session.get(Bill, bill["id"])
    assert b.transaction_id == txn_before
    lines_after = {
        (ln.account_id, ln.debit, ln.credit)
        for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn_before)
    }
    assert lines_after == lines_before


def test_a_header_only_edit_that_reposts_needs_no_lines(client, db_session, books):
    """An edit that has to re-post the journal — a class, a job, the tax
    rate, a new vendor or number, the currency — sent without its lines
    took the stored lines into code that reads request lines: a 500 for
    `{"class_id": 3}` alone (found by an API client tagging bills to a
    class, 2.22.0). The stored lines are re-posted as they are."""
    bill = books["bill"]
    lines_before = [
        (ln.description, ln.account_id, ln.quantity, ln.rate)
        for ln in sorted(
            db_session.get(Bill, bill["id"]).lines, key=lambda x: x.line_order
        )
    ]
    cls = client.post("/api/classes", json={"name": "Shop"}).json()
    other = _vendor(client, name="Pier Lumber")
    for body in (
        {"class_id": cls["id"]},
        {"tax_rate": 0.05},
        {"vendor_id": other["id"]},
        {"bill_number": "PL-200"},
        {"currency": "USD"},
    ):
        r = client.put(f"/api/bills/{bill['id']}", json=body)
        assert r.status_code == 200, (body, r.text)
    db_session.expire_all()
    got = db_session.get(Bill, bill["id"])
    assert [
        (ln.description, ln.account_id, ln.quantity, ln.rate)
        for ln in sorted(got.lines, key=lambda x: x.line_order)
    ] == lines_before
    assert got.class_id == cls["id"] and got.vendor_id == other["id"]
    assert got.bill_number == "PL-200"
    # re-posted under the class, with the tax, and the books balance
    assert got.total == D("420.00") and got.tax_amount == D("20.00")
    txn = db_session.get(Transaction, got.transaction_id)
    assert txn.class_id == cls["id"]
    assert _gl(db_session, books["ap"]) == D("420.00")
    _balanced(db_session)


def test_a_header_only_edit_keeps_each_lines_function(
    client, db_session, seed_accounts
):
    """Re-posting the stored lines keeps what each journal line carried: a
    line with no function of its own takes its fund's default again, as on
    the day it was entered, and a line that named one keeps it."""
    fund = client.post(
        "/api/classes", json={"name": "Music Programs", "default_function": "program"}
    ).json()
    v = _vendor(client)
    bill = _bill(
        client,
        v["id"],
        [
            {
                "description": "Sheet music",
                "account_id": seed_accounts["6000"].id,
                "quantity": 1,
                "rate": 80,
                "class_id": fund["id"],
                "line_order": 0,
            },
            {
                "description": "Audit share",
                "account_id": seed_accounts["5300"].id,
                "quantity": 1,
                "rate": 40,
                "class_id": fund["id"],
                "function": "management",
                "line_order": 1,
            },
        ],
        bill_number="MP-1",
    )

    def functions():
        db_session.expire_all()
        txn = db_session.get(
            Transaction, db_session.get(Bill, bill["id"]).transaction_id
        )
        return sorted((ln.description, ln.function) for ln in txn.lines if ln.debit)

    before = functions()
    assert before == [("Audit share", "management"), ("Sheet music", "program")]
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "MP-2"})
    assert r.status_code == 200, r.text
    assert functions() == before
    _balanced(db_session)


def test_a_date_edit_moves_the_posting_without_replacing_it(client, db_session, books):
    bill = books["bill"]
    r = client.put(f"/api/bills/{bill['id']}", json={"date": "2026-07-20"})
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-07-20"
    assert r.json()["due_date"] == "2026-08-19"  # Net 30 from the new date
    db_session.expire_all()
    b = db_session.get(Bill, bill["id"])
    txn = db_session.get(Transaction, b.transaction_id)
    assert txn.date.isoformat() == "2026-07-20"
    assert len(txn.lines) == 3  # two debits and the A/P credit, untouched
    _balanced(db_session)


# ── the lines ────────────────────────────────────────────────────────────────


def test_editing_the_lines_reposts_the_journal_and_moves_payables(
    client, db_session, books, seed_accounts
):
    bill, ap = books["bill"], books["ap"]
    assert _gl(db_session, ap) == D("400.00")
    txn_id = db_session.get(Bill, bill["id"]).transaction_id

    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "Signage",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 3,
                    "rate": 150,
                    "line_order": 0,
                },
                {
                    "description": "Permit",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": 25,
                    "line_order": 1,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert D(out["total"]) == D("475.00") and D(out["balance_due"]) == D("475.00")
    assert [ln["description"] for ln in out["lines"]] == ["Signage", "Permit"]

    db_session.expire_all()
    b = db_session.get(Bill, bill["id"])
    assert b.transaction_id == txn_id, "the journal keeps its identity"
    assert db_session.query(BillLine).filter_by(bill_id=b.id).count() == 2
    assert _gl(db_session, ap) == D("475.00")
    assert _gl(db_session, seed_accounts["6000"].id) == D("475.00")
    assert _gl(db_session, seed_accounts["5300"].id) == D(
        "0"
    )  # the Install line is gone
    _balanced(db_session)
    # no second posting for the bill: one transaction, re-posted
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type.like("bill%"), Transaction.source_id == b.id)
        .count()
        == 1
    )


def test_each_line_keeps_its_job_class_and_cost_code_through_an_edit(
    client, db_session, books, seed_accounts
):
    bill = books["bill"]
    cust = client.post("/api/customers", json={"name": "Lakeside"}).json()
    job = client.post(
        "/api/jobs", json={"customer_id": cust["id"], "name": "Lakeside signs"}
    ).json()
    cls = client.post("/api/classes", json={"name": "Retail"}).json()
    code = client.post(
        "/api/cost-codes",
        json={"code": "02-100", "name": "Signs", "cost_type": "labor"},
    ).json()
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "Signage",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 2,
                    "rate": 150,
                    "job_id": job["id"],
                    "class_id": cls["id"],
                    "cost_code_id": code["id"],
                    "is_billable": True,
                    "line_order": 0,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    ln = r.json()["lines"][0]
    assert (ln["job_id"], ln["class_id"], ln["cost_code_id"], ln["is_billable"]) == (
        job["id"],
        cls["id"],
        code["id"],
        True,
    )
    db_session.expire_all()
    b = db_session.get(Bill, bill["id"])
    posted = [
        tl
        for tl in db_session.query(TransactionLine).filter_by(
            transaction_id=b.transaction_id
        )
        if tl.debit > 0
    ]
    assert len(posted) == 1
    assert (posted[0].job_id, posted[0].class_id, posted[0].cost_code_id) == (
        job["id"],
        cls["id"],
        code["id"],
    )
    assert posted[0].is_billable is True


def test_the_vendors_balance_and_the_aging_follow_an_edit(client, db_session, books):
    bill, v = books["bill"], books["vendor"]
    before = client.get("/api/reports/ap-aging").json()["totals"]["total"]
    assert D(str(before)) == D("400.00")
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "Signage",
                    "quantity": 1,
                    "rate": 90,
                    "line_order": 0,
                    "account_id": db_session.query(Account)
                    .filter_by(account_number="6000")
                    .one()
                    .id,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    aging = client.get("/api/reports/ap-aging").json()["totals"]["total"]
    assert D(str(aging)) == D("90.00")
    vend = client.get(f"/api/vendors/{v['id']}").json()
    assert D(str(vend["balance"])) == D("90.00")


# ── the rules ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "new_rate, status_code, total, balance, status",
    [
        ("250", 200, "250.00", "100.00", "partial"),  # above what's paid: fine
        ("150", 200, "150.00", "0.00", "paid"),  # exactly what's paid: paid
        ("100", 400, "400.00", "250.00", "partial"),  # below: refused, nothing moves
    ],
)
def test_a_paid_bills_edit_may_not_go_below_what_was_paid(
    client,
    db_session,
    books,
    seed_accounts,
    new_rate,
    status_code,
    total,
    balance,
    status,
):
    bill, v, ap = books["bill"], books["vendor"], books["ap"]
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v["id"],
            "date": "2026-07-12",
            "amount": 150,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 150}],
        },
    )
    assert r.status_code == 201, r.text

    def snapshot():
        db_session.expire_all()
        return {
            m.__tablename__: [
                tuple(getattr(row, c.name) for c in m.__table__.columns)
                for row in db_session.query(m).order_by(m.id)
            ]
            for m in (Bill, BillLine, Transaction, TransactionLine, Account)
        }

    before = snapshot()
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "Signage",
                    "account_id": seed_accounts["6000"].id,
                    "quantity": 1,
                    "rate": new_rate,
                    "line_order": 0,
                }
            ]
        },
    )
    assert r.status_code == status_code, r.text
    if status_code == 400:
        assert "already paid" in r.json()["detail"]
        assert snapshot() == before
    b = client.get(f"/api/bills/{bill['id']}").json()
    assert (D(b["total"]), D(b["balance_due"]), D(b["amount_paid"]), b["status"]) == (
        D(total),
        D(balance),
        D("150.00"),
        status,
    )
    # A/P = what's still owed on the bill; the payment's own posting is untouched
    assert _gl(db_session, ap) == D(balance)
    _balanced(db_session)


def test_a_voided_bill_cannot_be_edited(client, books):
    bill = books["bill"]
    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200
    r = client.put(f"/api/bills/{bill['id']}", json={"notes": "late"})
    assert r.status_code == 400 and "voided" in r.json()["detail"]


def test_an_edit_respects_the_closing_date(client, books):
    bill = books["bill"]
    r = client.put("/api/settings", json={"closing_date": "2026-07-31"})
    assert r.status_code == 200, r.text
    # the bill is dated inside the closed period: no edit at all
    r = client.put(f"/api/bills/{bill['id']}", json={"notes": "x"})
    assert r.status_code == 403, r.text
    # a bill after the closing date can't be moved into it
    open_bill = _bill(
        client,
        books["vendor"]["id"],
        [
            {
                "description": "Later",
                "quantity": 1,
                "rate": 10,
                "line_order": 0,
                "account_id": client.get("/api/accounts").json()[0]["id"],
            }
        ],
        date="2026-08-05",
        bill_number="HS-200",
    )
    r = client.put(f"/api/bills/{open_bill['id']}", json={"date": "2026-07-20"})
    assert r.status_code == 403, r.text


def test_a_zero_total_edit_is_refused_and_a_renumber_may_not_collide(
    client, books, seed_accounts
):
    bill = books["bill"]
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "x",
                    "quantity": 1,
                    "rate": 0,
                    "line_order": 0,
                    "account_id": seed_accounts["6000"].id,
                }
            ]
        },
    )
    assert r.status_code == 400 and "more than zero" in r.json()["detail"]
    other = _bill(
        client,
        books["vendor"]["id"],
        [
            {
                "description": "Other",
                "quantity": 1,
                "rate": 10,
                "line_order": 0,
                "account_id": seed_accounts["6000"].id,
            }
        ],
        bill_number="HS-101",
    )
    r = client.put(f"/api/bills/{other['id']}", json={"bill_number": "HS-100"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


def test_an_edit_of_a_reconciled_posting_is_refused_but_its_notes_are_not(
    client, db_session, books
):
    """A bill whose posting is in a completed reconciliation can have its
    words changed, not its amounts (as an invoice can)."""
    from app.models.transactions import TransactionLine

    bill = books["bill"]
    b = db_session.get(Bill, bill["id"])
    for ln in db_session.query(TransactionLine).filter_by(
        transaction_id=b.transaction_id
    ):
        ln.reconciliation_id = 999  # as a completed reconciliation stamps it
    db_session.commit()
    assert (
        client.put(f"/api/bills/{bill['id']}", json={"notes": "ok"}).status_code == 200
    )
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "description": "x",
                    "quantity": 1,
                    "rate": 5,
                    "line_order": 0,
                    "account_id": db_session.query(Account)
                    .filter_by(account_number="6000")
                    .one()
                    .id,
                }
            ]
        },
    )
    assert r.status_code == 400 and "reconciliation" in r.json()["detail"]


# ── stock ────────────────────────────────────────────────────────────────────


def test_an_inventory_lines_edit_nets_the_stock_ledger(
    client, db_session, seed_accounts
):
    panel = Item(
        name="Aluminium panel",
        item_type=ItemType.PRODUCT,
        rate=D("60"),
        cost=D("22.50"),
        track_inventory=True,
    )
    db_session.add(panel)
    db_session.commit()
    v = _vendor(client, "Sign Supply")
    bill = _bill(
        client,
        v["id"],
        [
            {
                "item_id": panel.id,
                "description": "panels",
                "quantity": 20,
                "rate": 22.50,
                "line_order": 0,
            }
        ],
        bill_number="SS-1",
    )
    db_session.refresh(panel)
    assert D(str(panel.quantity_on_hand)) == D("20")
    assert _gl(db_session, seed_accounts["1300"].id) == D("450.00")

    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "item_id": panel.id,
                    "description": "panels",
                    "quantity": 12,
                    "rate": 25,
                    "line_order": 0,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["total"]) == D("300.00")
    db_session.expire_all()
    panel = db_session.get(Item, panel.id)
    assert D(str(panel.quantity_on_hand)) == D("12")
    assert _gl(db_session, seed_accounts["1300"].id) == D("300.00")
    valuation = D(str(panel.quantity_on_hand)) * D(str(panel.avg_cost))
    assert abs(valuation - D("300.00")) < D("0.01")
    kinds = [
        (m.movement_type, D(str(m.quantity)))
        for m in db_session.query(InventoryMovement)
        .filter_by(item_id=panel.id)
        .order_by(InventoryMovement.id)
    ]
    assert kinds == [
        (MovementType.PURCHASE, D("20")),
        (MovementType.VOID, D("-20")),  # the earlier receipt reversed
        (MovementType.PURCHASE, D("12")),  # the edited quantity received
    ]
    _balanced(db_session)


# ── the audit trail ──────────────────────────────────────────────────────────


def test_the_edit_is_in_the_audit_log(client, books):
    bill = books["bill"]
    assert (
        client.put(f"/api/bills/{bill['id']}", json={"notes": "audited"}).status_code
        == 200
    )
    rows = client.get(
        f"/api/audit?table_name=bills&record_id={bill['id']}&limit=50"
    ).json()
    rows = (
        rows if isinstance(rows, list) else rows.get("items", rows.get("entries", []))
    )
    # newest first; the create's own rows are in here too (transaction_id
    # is set after the insert), so find the edit by what it changed
    edits = [
        r
        for r in rows
        if r.get("action", "").lower() == "update"
        and "notes" in (r.get("changed_fields") or [])
    ]
    assert edits, [(r.get("action"), r.get("changed_fields")) for r in rows[:5]]
    assert edits[0]["new_values"]["notes"] == "audited"
