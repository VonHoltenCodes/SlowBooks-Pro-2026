"""Journal entries and deposits open for editing from the register (#388,
the second half, after the bank-entry edit shipped in 2.27.0). A manual
journal entry opens the journal form filled in and PUT rewrites it in
place; a deposit opens the deposit form and PUT reposts it, moving which
undeposited payments it takes. Both follow the bank-entry rules: an entry a
completed reconciliation holds keeps its amount and date (400), a change
that moves an amount between accounts asks first (409 for the journal
entry), and the edit is on the same journal entry, its audit trail and
statement match following."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func

from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.banking import BankTransaction
from app.models.contacts import Customer
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------- helpers


def _je(client, seed_accounts, **over):
    """A hand-entered journal entry with a Checking line, so it shows in
    the register: CR Checking 100 / DR an expense 100."""
    body = {
        "date": "2026-09-02",
        "description": "Owner draw",
        "reference": "JE-1",
        "lines": [
            {
                "account_id": seed_accounts["1000"].id,
                "debit": 0,
                "credit": 100,
                "description": "cash out",
            },
            {
                "account_id": seed_accounts["6900"].id,
                "debit": 100,
                "credit": 0,
                "description": "expense",
            },
        ],
    }
    body.update(over)
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _je_form(entry, **over):
    """The PUT body as the journal form sends it: the GET's fields back."""
    body = {
        "date": entry["date"],
        "description": entry["description"],
        "reference": entry["reference"] or None,
        "class_id": entry["class_id"],
        "job_id": entry["job_id"],
        "is_adjusting": entry["is_adjusting"],
        "lines": [
            {
                "account_id": ln["account_id"],
                "debit": str(ln["debit"]),
                "credit": str(ln["credit"]),
                "description": ln["description"],
            }
            for ln in entry["lines"]
        ],
    }
    body.update(over)
    return body


def _reconcile_lines(client, db, seed_accounts, line_ids, balance, day=30):
    r = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": f"2026-09-{day:02d}",
            "statement_balance": balance,
        },
    )
    assert r.status_code == 201, r.text
    recon = r.json()
    for line_id in line_ids:
        r = client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{line_id}")
        assert r.status_code == 200, r.text
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    return recon


def _bank_line(db, txn_id, acct):
    db.expire_all()
    return (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=acct.id)
        .one()
    )


def _gl(db, acct):
    db.expire_all()
    dr, cr = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == acct.id)
        .one()
    )
    return Decimal(str(dr)) - Decimal(str(cr))


# ---------------------------------------------------------- journal entry


def test_get_opens_a_journal_entry_as_editable(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert e["editable"] is True
    assert e["reconciled_on"] is None and e["reconciled_note"] is None
    assert len(e["lines"]) == 2


def test_put_rewrites_a_journal_entry_on_the_same_id(client, db_session, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    r = client.put(
        f"/api/journal/{posted['id']}",
        json=_je_form(e, description="Owner draw — July", reference="JE-9"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"]
    assert out["description"] == "Owner draw — July" and out["reference"] == "JE-9"
    # one journal entry, still two lines, still balanced
    assert out["total_debit"] == out["total_credit"] == 100.0
    assert db_session.query(Transaction).filter_by(source_type="manual").count() == 1


def test_a_void_journal_entry_is_not_edited(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert client.post(f"/api/journal/{posted['id']}/void").status_code == 200
    r = client.put(f"/api/journal/{posted['id']}", json=_je_form(e, reference="X"))
    assert r.status_code == 400 and "void" in r.json()["detail"], r.text


def test_a_memo_fix_on_a_reconciled_journal_entry_is_allowed(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert e["reconciled_on"] == "2026-09-30"
    assert "amount and date stay as they are" in e["reconciled_note"]
    # a memo/reference change keeps every line's account and amount
    r = client.put(
        f"/api/journal/{posted['id']}",
        json=_je_form(e, description="Owner draw (corrected)", reference="JE-2"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["uncleared"] == []
    line = _bank_line(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]


def test_an_amount_or_date_change_on_a_reconciled_journal_entry_is_refused(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    refused = (
        "This entry was reconciled on the Sep 30, 2026 statement, so its amount "
        "and date cannot be changed, just as it cannot be voided. Correct the "
        "difference with a new entry."
    )
    # the amount changed (both lines to 130) and, separately, the date
    bigger = _je_form(e, confirm_reconciled=True)
    bigger["lines"][0]["credit"] = "130"
    bigger["lines"][1]["debit"] = "130"
    for body in (bigger, _je_form(e, confirm_reconciled=True, date="2026-10-02")):
        r = client.put(f"/api/journal/{posted['id']}", json=body)
        assert r.status_code == 400 and r.json()["detail"] == refused, r.text
    # nothing moved; still on the statement
    line = _bank_line(db_session, posted["id"], bank)
    assert Decimal(str(line.credit)) == Decimal("100.00")
    assert line.reconciliation_id == recon["id"]


def test_an_account_change_on_a_reconciled_journal_entry_asks_first(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    # move the expense from 6900 to 6400 — same amounts, same date
    body = _je_form(e)
    body["lines"][1]["account_id"] = seed_accounts["6400"].id
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "reconciled"
    assert r.json()["detail"]["statement_date"] == "2026-09-30"
    # confirmed, it goes through and the Checking line keeps its mark
    r = client.put(
        f"/api/journal/{posted['id']}", json={**body, "confirm_reconciled": True}
    )
    assert r.status_code == 200, r.text
    line = _bank_line(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _gl(db_session, seed_accounts["6400"]) == Decimal("100.00")
    assert _gl(db_session, seed_accounts["6900"]) == Decimal("0")
    # the move within a reconciled period is in the trail, as the bank edit's
    marks = [
        a
        for a in db_session.query(AuditLog).filter_by(
            table_name="transactions", record_id=posted["id"], action="UPDATE"
        )
        if a.changed_fields == ["confirm_reconciled"]
    ]
    assert len(marks) == 1
    assert marks[0].old_values == {"reconciled_on": "2026-09-30"}
    assert marks[0].new_values["reconciliation_id"] == recon["id"]
    # the lines were replaced the way a bill's are on edit (#289)
    actions = {
        a.action
        for a in db_session.query(AuditLog).filter_by(table_name="transaction_lines")
    }
    assert {"DELETE", "INSERT"} <= actions


# --------------------------------------------------------------- deposits


def _customer(db):
    c = Customer(name="Acme Diner", is_active=True)
    db.add(c)
    db.commit()
    return c.id


def _receive(client, cid, amount, check=None):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": "Check",
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pending(client):
    return client.get("/api/deposits/pending").json()


def _line_of(client, db, payment_id):
    txn_id = db.get(Payment, payment_id).transaction_id
    return next(p for p in _pending(client) if p["transaction_id"] == txn_id)


def _make_deposit(client, seed_accounts, pend_lines, ref="DEP-1", day=12, when=None):
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": when or f"2026-09-{day:02d}",
            "total": float(sum(Decimal(str(p["amount"])) for p in pend_lines)),
            "reference": ref,
            "line_ids": [p["transaction_line_id"] for p in pend_lines],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["transaction_id"]


def test_put_changes_a_deposits_date_and_reference(client, db_session, seed_accounts):
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client, seed_accounts, [_line_of(client, db_session, pay["id"])]
    )
    r = client.put(
        f"/api/deposits/{dep}", json={"date": "2026-09-15", "reference": "DEP-9"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["date"] == "2026-09-15" and out["reference"] == "DEP-9"
    assert out["amount"] == "300.00" and out["items"] == 1
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 1


def test_a_receipt_swap_reposts_and_ties(client, db_session, seed_accounts):
    bank, uf = seed_accounts["1000"], seed_accounts["1200"]
    cid = _customer(db_session)
    first = _receive(client, cid, 300, check="4420")
    second = _receive(client, cid, 200, check="4421")
    l1 = _line_of(client, db_session, first["id"])
    l2 = _line_of(client, db_session, second["id"])
    dep = _make_deposit(client, seed_accounts, [l1, l2])
    assert _pending(client) == []
    assert _gl(db_session, bank) == Decimal("500.00")
    # drop the second payment from the deposit
    r = client.put(
        f"/api/deposits/{dep}", json={"line_ids": [l1["transaction_line_id"]]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == "300.00" and r.json()["items"] == 1
    # the register and the GL follow; Undeposited Funds nets to the 200 left
    assert _gl(db_session, bank) == Decimal("300.00")
    assert _gl(db_session, uf) == Decimal("200.00")
    # the dropped payment is back on the Make Deposits list, the other gone
    waiting = {p["transaction_id"] for p in _pending(client)}
    assert waiting == {db_session.get(Payment, second["id"]).transaction_id}
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    dep_rows = [e for e in reg["entries"] if e["source_type"] == "deposit"]
    assert [e["deposit"] for e in dep_rows] == [300.0]


def test_a_reconciled_deposit_keeps_its_amount_and_date(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    cid = _customer(db_session)
    first = _receive(client, cid, 300, check="4420")
    second = _receive(client, cid, 200, check="4421")
    l1 = _line_of(client, db_session, first["id"])
    l2 = _line_of(client, db_session, second["id"])
    dep = _make_deposit(client, seed_accounts, [l1, l2])
    bank_line = _bank_line(db_session, dep, bank)
    recon = _reconcile_lines(
        client, db_session, seed_accounts, [bank_line.id], "500.00"
    )
    # dropping a payment changes the amount: refused
    r = client.put(
        f"/api/deposits/{dep}", json={"line_ids": [l1["transaction_line_id"]]}
    )
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"], r.text
    # so does a new date
    r = client.put(f"/api/deposits/{dep}", json={"date": "2026-10-01"})
    assert r.status_code == 400, r.text
    # a reference fix still goes through
    r = client.put(f"/api/deposits/{dep}", json={"reference": "DEP-RE"})
    assert r.status_code == 200 and r.json()["reference"] == "DEP-RE", r.text
    line = _bank_line(db_session, dep, bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _gl(db_session, bank) == Decimal("500.00")


def test_the_register_marks_journal_and_deposit_rows_editable(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    je = _je(client, seed_accounts)
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client, seed_accounts, [_line_of(client, db_session, pay["id"])]
    )
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    by_id = {e["transaction_id"]: e for e in reg["entries"]}
    assert by_id[je["id"]]["editable"] is True
    assert by_id[je["id"]]["source_type"] == "manual"
    assert by_id[dep]["editable"] is True
    assert by_id[dep]["source_type"] == "deposit"


# ----------------------------------------------------------------- the pages


def test_the_journal_form_opens_for_editing():
    js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert "async showForm(entry = null)" in js
    assert "async editEntry(entryId)" in js
    assert "API.get(`/journal/${entryId}`)" in js
    assert "API.put(`/journal/${entryId}`, data)" in js
    assert "JournalPage.saveAnyway()" in js


def test_the_deposit_form_opens_for_editing():
    js = (ROOT / "app/static/js/deposits.js").read_text(encoding="utf-8")
    assert "async editDeposit(id)" in js
    assert "API.put(`/deposits/${id}`, body)" in js


def test_the_register_routes_journal_and_deposit_rows_to_their_editors():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "JournalPage.editEntry(${e.transaction_id})" in js
    assert "DepositsPage.editDeposit(${e.transaction_id})" in js


# --------------------------------------------------- the reconciled guard


def test_a_sign_flip_on_a_reconciled_entry_is_refused(
    client, db_session, seed_accounts
):
    """A bare (debit, credit) multiset would pass a sign flip; it moves the
    reconciled account's balance, so it is refused by account."""
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    flip = _je_form(e, confirm_reconciled=True)
    # CR Checking 100 / DR 6900 100 → DR Checking 100 / CR 6900 100
    flip["lines"][0]["debit"], flip["lines"][0]["credit"] = "100", "0"
    flip["lines"][1]["debit"], flip["lines"][1]["credit"] = "0", "100"
    r = client.put(f"/api/journal/{posted['id']}", json=flip)
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"], r.text
    line = _bank_line(db_session, posted["id"], bank)
    assert Decimal(str(line.credit)) == Decimal("100.00")
    assert line.reconciliation_id == recon["id"]
    assert _gl(db_session, bank) == Decimal("-100.00")


def test_a_reconciled_line_cannot_be_moved_to_another_account(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], bank)
    recon = _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e, confirm_reconciled=True)
    # move the reconciled Checking line itself to 1200 — refused even confirmed
    body["lines"][0]["account_id"] = seed_accounts["1200"].id
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"], r.text
    line = _bank_line(db_session, posted["id"], bank)
    assert line.account_id == bank.id and line.reconciliation_id == recon["id"]


def test_the_form_note_narrows_what_a_reconciled_entry_allows(
    client, db_session, seed_accounts
):
    posted = _je(client, seed_accounts)
    line = _bank_line(db_session, posted["id"], seed_accounts["1000"])
    _reconcile_lines(client, db_session, seed_accounts, [line.id], "-100.00")
    note = client.get(f"/api/journal/{posted['id']}").json()["reconciled_note"]
    assert "the reconciled line keeps its account" in note
    assert "the other lines' accounts can still be corrected" in note


# ------------------------------------------------------ validation + guards


def test_a_non_manual_entry_is_not_editable(client, db_session, seed_accounts):
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client, seed_accounts, [_line_of(client, db_session, pay["id"])]
    )
    assert client.get(f"/api/journal/{dep}").json()["editable"] is False
    r = client.put(
        f"/api/journal/{dep}",
        json={"date": "2026-09-12", "description": "x", "lines": []},
    )
    assert r.status_code == 400 and "hand-entered" in r.json()["detail"], r.text


def test_an_unbalanced_edit_is_refused_and_nothing_moves(
    client, db_session, seed_accounts
):
    bank, exp = seed_accounts["1000"], seed_accounts["6900"]
    posted = _je(client, seed_accounts)
    before = (_gl(db_session, bank), _gl(db_session, exp))
    bal_before = (
        db_session.get(Account, bank.id).balance,
        db_session.get(Account, exp.id).balance,
    )
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e)
    body["lines"][1]["debit"] = "130"  # debit 130 vs credit 100
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 400 and "out of balance" in r.json()["detail"], r.text
    db_session.expire_all()
    assert (_gl(db_session, bank), _gl(db_session, exp)) == before
    assert (
        db_session.get(Account, bank.id).balance,
        db_session.get(Account, exp.id).balance,
    ) == bal_before
    assert (
        db_session.query(TransactionLine).filter_by(transaction_id=posted["id"]).count()
        == 2
    )


def test_an_edit_with_one_line_is_refused(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e)
    body["lines"] = body["lines"][:1]
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 400 and "2 lines" in r.json()["detail"], r.text


def test_an_edit_to_an_unknown_account_is_404(client, seed_accounts):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e)
    body["lines"][1]["account_id"] = 999999
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 404, r.text


def _set_closing_date(client, day):
    r = client.put("/api/settings", json={"closing_date": day})
    assert r.status_code == 200, r.text


def test_closing_date_holds_for_the_old_and_the_new_date_journal(
    client, db_session, seed_accounts
):
    old = _je(client, seed_accounts, date="2026-08-01")
    new = _je(client, seed_accounts, date="2026-12-01")
    _set_closing_date(client, "2026-09-30")
    # the old date sits in the closed period: no edit at all
    eo = client.get(f"/api/journal/{old['id']}").json()
    r = client.put(f"/api/journal/{old['id']}", json=_je_form(eo, reference="X"))
    assert r.status_code in (400, 403), r.text
    # a new date reaching back into the closed period is refused
    en = client.get(f"/api/journal/{new['id']}").json()
    r = client.put(f"/api/journal/{new['id']}", json=_je_form(en, date="2026-08-15"))
    assert r.status_code in (400, 403), r.text
    # a reference fix that stays after the close goes through
    r = client.put(f"/api/journal/{new['id']}", json=_je_form(en, reference="JE-9"))
    assert r.status_code == 200, r.text


def test_closing_date_holds_for_the_new_date_deposit(client, db_session, seed_accounts):
    cid = _customer(db_session)
    pay = _receive(client, cid, 300, check="4420")
    dep = _make_deposit(
        client,
        seed_accounts,
        [_line_of(client, db_session, pay["id"])],
        when="2026-12-05",
    )
    _set_closing_date(client, "2026-09-30")
    r = client.put(f"/api/deposits/{dep}", json={"date": "2026-09-15"})
    assert r.status_code in (400, 403), r.text


def test_is_adjusting_is_kept_when_the_put_omits_it(client, db_session, seed_accounts):
    posted = _je(client, seed_accounts, is_adjusting=True)
    assert client.get(f"/api/journal/{posted['id']}").json()["is_adjusting"] is True
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e, description="still adjusting")
    body.pop("is_adjusting", None)
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 200 and r.json()["is_adjusting"] is True, r.text


def test_a_billable_reclassified_line_survives_a_memo_edit(
    client, db_session, seed_accounts
):
    """A memo-only PUT from a client that sends bare lines keeps each line's
    billable flag and its own class (#388 review)."""
    from app.models.classes import TxnClass

    cls = TxnClass(name="Job A")
    db_session.add(cls)
    db_session.commit()
    posted = client.post(
        "/api/journal",
        json={
            "date": "2026-09-02",
            "description": "Owner draw",
            "lines": [
                {"account_id": seed_accounts["1000"].id, "debit": 0, "credit": 100},
                {
                    "account_id": seed_accounts["6900"].id,
                    "debit": 100,
                    "credit": 0,
                    "is_billable": True,
                    "class_id": cls.id,
                },
            ],
        },
    ).json()
    exp_line = _bank_line(db_session, posted["id"], seed_accounts["6900"])
    assert exp_line.is_billable is True and exp_line.class_id == cls.id
    # the form reads the entry and PUTs bare lines (no class/billable keys)
    e = client.get(f"/api/journal/{posted['id']}").json()
    r = client.put(
        f"/api/journal/{posted['id']}", json=_je_form(e, description="Owner draw (fix)")
    )
    assert r.status_code == 200, r.text
    exp_line = _bank_line(db_session, posted["id"], seed_accounts["6900"])
    assert exp_line.is_billable is True and exp_line.class_id == cls.id


def test_a_memo_only_edit_updates_in_place_without_deleting_lines(
    client, db_session, seed_accounts
):
    posted = _je(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    line_ids_before = {
        ln.id
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=posted["id"]
        )
    }
    r = client.put(
        f"/api/journal/{posted['id']}", json=_je_form(e, description="A new memo")
    )
    assert r.status_code == 200, r.text
    # nothing was replaced: the same line rows, no DELETE in the trail
    line_ids_after = {
        ln.id
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=posted["id"]
        )
    }
    assert line_ids_after == line_ids_before
    actions = {
        a.action
        for a in db_session.query(AuditLog).filter_by(table_name="transaction_lines")
    }
    assert "DELETE" not in actions


# --------------------------------------------- the statement line follows


def _feed_line(client, db_session, seed_accounts, txn_id, account_id, amount):
    """A statement line matched to one line of a journal entry."""
    resp = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert resp.status_code == 201, resp.text
    feed = resp.json()
    line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=account_id)
        .one()
    )
    bt = BankTransaction(
        bank_account_id=feed["id"],
        date=date(2026, 9, 2),
        amount=Decimal(amount),
        payee="OWNER",
        match_status="matched",
        transaction_id=txn_id,
        transaction_line_id=line.id,
        category_account_id=seed_accounts["6900"].id,
    )
    db_session.add(bt)
    db_session.commit()
    return bt.id


def test_a_statement_line_follows_a_kept_line(client, db_session, seed_accounts):
    # the Checking line is untouched by an account change on the other line,
    # so the statement line matched to it follows to the new row
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    bt_id = _feed_line(client, db_session, seed_accounts, posted["id"], bank.id, "-100")
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e)
    body["lines"][1]["account_id"] = seed_accounts["6400"].id
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    bt = db_session.get(BankTransaction, bt_id)
    line = _bank_line(db_session, posted["id"], bank)
    assert bt.transaction_id == posted["id"] and bt.transaction_line_id == line.id


def test_a_statement_line_releases_a_changed_line(client, db_session, seed_accounts):
    # the matched Checking line's amount changes → back to the review queue
    bank = seed_accounts["1000"]
    posted = _je(client, seed_accounts)
    bt_id = _feed_line(client, db_session, seed_accounts, posted["id"], bank.id, "-100")
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = _je_form(e)
    body["lines"][0]["credit"] = "120"
    body["lines"][1]["debit"] = "120"
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    bt = db_session.get(BankTransaction, bt_id)
    assert bt.transaction_id is None and bt.transaction_line_id is None
    assert bt.match_status == "unmatched" and bt.category_account_id is None


# ----------------------------------------------------- deposit edge cases


def test_a_payment_in_another_deposit_cannot_be_added(
    client, db_session, seed_accounts
):
    cid = _customer(db_session)
    first = _receive(client, cid, 300, check="4420")
    second = _receive(client, cid, 200, check="4421")
    l1 = _line_of(client, db_session, first["id"])
    l2 = _line_of(client, db_session, second["id"])
    dep_a = _make_deposit(client, seed_accounts, [l1], ref="A", day=12)
    _make_deposit(client, seed_accounts, [l2], ref="B", day=13)
    # l2 is in deposit B, neither held by A nor waiting
    r = client.put(
        f"/api/deposits/{dep_a}",
        json={"line_ids": [l1["transaction_line_id"], l2["transaction_line_id"]]},
    )
    assert r.status_code == 400, r.text


def test_a_legacy_deposit_naming_no_payments_still_takes_a_date_fix(
    client, db_session, seed_accounts
):
    # a deposit posted as an amount (no line_ids): the older API form
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-12",
            "total": "250.00",
            "reference": "LEGACY",
        },
    )
    dep = r.json()["transaction_id"]
    # the modal renders no held payments, so it omits line_ids
    r = client.put(
        f"/api/deposits/{dep}", json={"date": "2026-09-14", "reference": "L2"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-09-14" and r.json()["reference"] == "L2"
    assert r.json()["amount"] == "250.00"
