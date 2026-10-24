"""A register entry opens for editing, with a warning when it is reconciled
(#388, from a bookkeeper's notes): GET gives the + Entry form's shape, PUT
rewrites the entry in place on the same journal entry — lines, balances and
the audit trail following — refuses a void one, and asks first about one a
completed reconciliation holds."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.banking import BankTransaction
from app.models.transactions import Transaction, TransactionLine
from tests.test_atomic_account_balance import postgres_url  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _entry(client, seed_accounts, account="1000", **over):
    body = {
        "account_id": seed_accounts[account].id,
        "date": "2026-09-02",
        "amount": "-125.00",
        "category_account_id": seed_accounts["6900"].id,
        "payee": "City Water",
        "description": "August water",
        "check_number": "1042",
    }
    body.update(over)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _lines(db, txn_id):
    return {
        ln.account_id: (Decimal(str(ln.debit or 0)), Decimal(str(ln.credit or 0)))
        for ln in db.query(TransactionLine).filter(
            TransactionLine.transaction_id == txn_id
        )
    }


def _balance(db, acct):
    db.expire_all()
    return Decimal(str(db.get(Account, acct.id).balance or 0))


def _form(entry, **over):
    """The PUT body as the form sends it: the GET's fields back."""
    body = {
        "account_id": entry["account_id"],
        "date": entry["date"],
        "amount": entry["amount"],
        "category_account_id": entry["category_account_id"],
        "payee": entry["payee"] or None,
        "description": entry["description"] or None,
        "check_number": entry["reference"] or None,
        "class_id": entry["class_id"],
        "job_id": entry["job_id"],
    }
    if entry.get("splits"):
        body["splits"] = entry["splits"]
    body.update(over)
    return body


def _reconcile(client, seed_accounts, balance, day=30):
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
    s = client.get(f"/api/banking/reconciliations/{recon['id']}/transactions").json()
    for ln in s["transactions"]:
        client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{ln['id']}")
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    return recon


# ---------------------------------------------------------------- GET


def test_get_returns_the_entry_in_the_forms_shape(client, seed_accounts):
    posted = _entry(client, seed_accounts)
    r = client.get(f"/api/banking/entries/{posted['id']}")
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["id"] == posted["id"] and e["account_id"] == seed_accounts["1000"].id
    assert e["date"] == "2026-09-02" and e["amount"] == "-125.00"
    assert e["category_account_id"] == seed_accounts["6900"].id
    assert e["payee"] == "City Water" and e["description"] == "August water"
    assert e["reference"] == "1042" and e["status"] == "recorded"
    assert e["class_id"] is None and e["job_id"] is None and e["class_name"] is None
    assert e["splits"] is None and e["reconciled_on"] is None
    assert e["reconciled_note"] is None
    # no memo typed: the line carries the payee, the form's Memo stays blank
    blank = _entry(client, seed_accounts, description=None, check_number=None)
    e = client.get(f"/api/banking/entries/{blank['id']}").json()
    assert e["payee"] == "City Water" and e["description"] == ""
    assert client.get("/api/banking/entries/999999").status_code == 404


# ---------------------------------------------------------------- PUT


def test_put_rewrites_amount_and_category_on_the_same_entry(
    client, db_session, seed_accounts
):
    bank, old_cat, new_cat = (
        seed_accounts["1000"],
        seed_accounts["6900"],
        seed_accounts["6400"],
    )
    posted = _entry(client, seed_accounts)
    before = db_session.query(Transaction).count()
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            amount="-152.00",
            category_account_id=new_cat.id,
            payee="City Water Dept",
            description="August water, corrected",
            check_number="1043",
            date="2026-09-03",
        ),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"] and out["amount"] == "-152.00"
    assert out["category_account_id"] == new_cat.id and out["uncleared"] == []
    assert out["note"] is None
    # the same journal entry, its lines rewritten, the balances following
    assert db_session.query(Transaction).count() == before
    txn = db_session.get(Transaction, posted["id"])
    db_session.refresh(txn)
    assert txn.source_type == "bank_entry" and txn.reference == "1043"
    assert txn.description == "City Water Dept" and str(txn.date) == "2026-09-03"
    assert _lines(db_session, txn.id) == {
        new_cat.id: (Decimal("152.00"), Decimal("0")),
        bank.id: (Decimal("0"), Decimal("152.00")),
    }
    assert _balance(db_session, old_cat) == 0
    assert _balance(db_session, new_cat) == Decimal("152.00")
    assert _balance(db_session, bank) == Decimal("-152.00")
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    [row] = reg["entries"]
    assert row["transaction_id"] == posted["id"] and row["payment"] == 152.0
    assert row["payee"] == "City Water Dept" and row["reference"] == "1043"
    assert row["editable"] is True and row["voidable"] is True
    # the trail: the header's UPDATE, each old line a DELETE, each new one an INSERT
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "transactions", AuditLog.record_id == txn.id)
        .all()
    )
    assert [a.action for a in rows] == ["INSERT", "UPDATE"]
    assert "description" in rows[-1].changed_fields
    lines = db_session.query(AuditLog).filter_by(table_name="transaction_lines").all()
    actions = sorted(a.action for a in lines)
    assert actions == ["DELETE", "DELETE", "INSERT", "INSERT", "INSERT", "INSERT"]
    deleted = [a for a in lines if a.action == "DELETE"]
    assert {a.old_values["account_id"] for a in deleted} == {old_cat.id, bank.id}
    assert {a.old_values["debit"] for a in deleted} == {"125.00", "0.00"}


def test_a_void_entry_is_not_edited(client, seed_accounts):
    posted = _entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert client.post(f"/api/banking/entries/{posted['id']}/void").status_code == 200
    r = client.put(f"/api/banking/entries/{posted['id']}", json=_form(e, amount="-5"))
    assert r.status_code == 400 and "void" in r.json()["detail"], r.text
    assert client.get(f"/api/banking/entries/{posted['id']}").json()["status"] == "void"
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert [x["editable"] for x in reg["entries"]] == [False, False]


def _reconciled_entry(client, seed_accounts):
    """The reviewer's books: 1000.00 in on Sep 1, a 125.00 water bill on
    Sep 2, the Sep 30 statement reconciled at 875.00."""
    _entry(
        client,
        seed_accounts,
        amount="1000",
        category_account_id=seed_accounts["4000"].id,
        payee="walk-in",
        date="2026-09-01",
        description=None,
        check_number=None,
    )
    posted = _entry(client, seed_accounts)
    recon = _reconcile(client, seed_accounts, "875.00")
    return posted, recon


def _bank_row(db, txn_id, acct):
    db.expire_all()
    return (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=acct.id)
        .one()
    )


def test_a_reconciled_entrys_amount_and_date_are_refused_as_its_void_is(
    client, db_session, seed_accounts
):
    """#388 review: an amount edit with confirm took the line off its
    statement (the Sep 30 report out by −125.00) and then let it be voided;
    a date moved past the statement kept Sep's reconciliation."""
    bank = seed_accounts["1000"]
    posted, recon = _reconciled_entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    # the form says so as it opens
    assert e["reconciled_on"] == "2026-09-30"
    assert e["reconciled_note"] == (
        "This entry was reconciled on the Sep 30, 2026 statement, so its amount "
        "and date stay as they are (a reconciled entry cannot be voided either). "
        "Its payee, memo, category, class and job can still be corrected."
    )
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    row = next(x for x in reg["entries"] if x["transaction_id"] == posted["id"])
    assert row["editable"] is True and row["voidable"] is False
    refused = (
        "This entry was reconciled on the Sep 30, 2026 statement, so its amount "
        "and date cannot be changed, just as it cannot be voided. Correct the "
        "difference with a new entry."
    )
    for change in (
        {"amount": "-125.01"},
        {"date": "2026-10-02"},
        {"date": "2026-09-03"},
    ):
        r = client.put(
            f"/api/banking/entries/{posted['id']}",
            json=_form(e, confirm_reconciled=True, **change),
        )
        assert r.status_code == 400, (change, r.text)
        assert r.json()["detail"] == refused
    # nothing moved, and the void is still refused
    line = _bank_row(db_session, posted["id"], bank)
    assert Decimal(str(line.credit)) == Decimal("125.00")
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _balance(db_session, bank) == Decimal("875.00")
    r = client.post(f"/api/banking/entries/{posted['id']}/void")
    assert r.status_code == 400 and "completed reconciliation" in r.text
    report = client.get(f"/api/banking/reconciliations/{recon['id']}/report").json()
    assert "125" in str(report)


def test_a_payee_fix_on_a_reconciled_entry_needs_no_warning(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    posted, recon = _reconciled_entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, payee="City Water Dept", description="Aug water"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["payee"] == "City Water Dept" and r.json()["uncleared"] == []
    line = _bank_row(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert line.description == "Aug water"
    # the lines were updated where they are, not deleted and re-made
    actions = {
        a.action
        for a in db_session.query(AuditLog).filter_by(table_name="transaction_lines")
    }
    assert "DELETE" not in actions
    assert not [
        a
        for a in db_session.query(AuditLog).filter_by(
            table_name="transactions", record_id=posted["id"], action="UPDATE"
        )
        if a.changed_fields == ["confirm_reconciled"]
    ]


def test_a_category_change_on_a_reconciled_entry_asks_first_and_keeps_its_mark(
    client, db_session, seed_accounts
):
    bank, new_cat = seed_accounts["1000"], seed_accounts["6400"]
    posted, recon = _reconciled_entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    body = _form(e, category_account_id=new_cat.id)
    r = client.put(f"/api/banking/entries/{posted['id']}", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {
        "code": "reconciled",
        "message": "This entry was reconciled on the Sep 30, 2026 statement. "
        "Changing its category moves its amount between accounts in a period "
        "already reconciled.",
        "statement_date": "2026-09-30",
    }
    assert _balance(db_session, new_cat) == 0
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json={**body, "confirm_reconciled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["category_account_id"] == new_cat.id
    assert r.json()["uncleared"] == [] and r.json()["note"] is None
    # the bank line is as the statement has it, still on that reconciliation
    line = _bank_row(db_session, posted["id"], bank)
    assert line.cleared is True and line.reconciliation_id == recon["id"]
    assert _balance(db_session, new_cat) == Decimal("125.00")
    assert _balance(db_session, bank) == Decimal("875.00")
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
    assert marks[0].new_values["lines_uncleared"] == 0


def test_an_amount_change_on_a_cleared_entry_loses_the_mark_and_says_so(
    client, db_session, seed_accounts
):
    """Cleared but not reconciled (ticked in an open reconciliation): the
    amount may change; the line loses its tick and the answer says so."""
    bank = seed_accounts["1000"]
    posted = _entry(client, seed_accounts)
    line = _bank_row(db_session, posted["id"], bank)
    line.cleared = True
    db_session.commit()
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}", json=_form(e, amount="-130.00")
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["uncleared"] == [
        {"account_id": bank.id, "account_name": "Checking", "amount": "-125.00"}
    ]
    assert out["note"] == (
        "The Checking line changed, so it is no longer marked cleared; tick it "
        "again on the next reconciliation."
    )
    line = _bank_row(db_session, posted["id"], bank)
    assert line.cleared is False and Decimal(str(line.credit)) == Decimal("130.00")


# ------------------------------------------------- statement lines (#388 review)


def _feed_entry(client, db_session, seed_accounts, amount="-130.00"):
    """A statement line, categorised and added from the review queue: the
    entry it posts is linked to it."""
    from app.services.ofx_import import import_transactions

    feed = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    ).json()
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "F1",
                "date": date(2026, 9, 8),
                "amount": Decimal(amount),
                "payee": "CITY WATER",
                "memo": "",
            }
        ],
    )
    bt = db_session.query(BankTransaction).one()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={"category_account_id": seed_accounts["6900"].id},
    )
    assert r.status_code == 200, r.text
    return feed, bt.id, r.json()["transaction_id"]


def test_an_amount_edit_sends_the_statement_line_back_uncategorised(
    client, db_session, seed_accounts
):
    """#388 review: the line went back to review keeping its category, so
    "Add all categorised" posted it again — one bank line as 130.00 and
    125.00, the balance −255.00."""
    bank = seed_accounts["1000"]
    feed, bt_id, txn_id = _feed_entry(client, db_session, seed_accounts)
    e = client.get(f"/api/banking/entries/{txn_id}").json()
    r = client.put(f"/api/banking/entries/{txn_id}", json=_form(e, amount="-125.00"))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    bt = db_session.get(BankTransaction, bt_id)
    assert bt.match_status == "unmatched" and bt.category_account_id is None
    assert bt.transaction_id is None and bt.transaction_line_id is None
    out = client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all").json()
    assert out["added"] == 0
    assert _balance(db_session, bank) == Decimal("-125.00")
    reg = client.get(f"/api/banking/check-register?account_id={bank.id}").json()
    assert [x["payment"] for x in reg["entries"]] == [125.0]


def test_a_payee_or_category_edit_keeps_the_statement_line_linked(
    client, db_session, seed_accounts
):
    feed, bt_id, txn_id = _feed_entry(client, db_session, seed_accounts)
    e = client.get(f"/api/banking/entries/{txn_id}").json()
    for change in (
        {"payee": "City Water Dept"},
        {"category_account_id": seed_accounts["6400"].id},
    ):
        r = client.put(f"/api/banking/entries/{txn_id}", json=_form(e, **change))
        assert r.status_code == 200, (change, r.text)
        e = r.json()
        db_session.expire_all()
        bt = db_session.get(BankTransaction, bt_id)
        line = _bank_row(db_session, txn_id, seed_accounts["1000"])
        assert bt.match_status == "added" and bt.transaction_id == txn_id
        assert bt.transaction_line_id == line.id and line.cleared is True


def _statement_line_edits(db, accounts):
    """The reviewer's 500, below the HTTP layer so it can run on any
    engine: a feed-added entry has its payee fixed, its category changed
    (the lines replaced, the bank line kept) and its amount changed.
    `accounts` is {number: Account} on `db`."""
    from app.models.banking import BankAccount
    from app.routes.banking import update_bank_entry
    from app.schemas.banking import BankEntryUpdate
    from app.services import bank_matching

    feed = BankAccount(name="Checking feed", account_id=accounts["1000"].id)
    db.add(feed)
    db.flush()
    bt = BankTransaction(
        bank_account_id=feed.id,
        date=date(2026, 9, 8),
        amount=Decimal("-130.00"),
        payee="CITY WATER",
        match_status="unmatched",
    )
    db.add(bt)
    db.flush()
    bank_matching.add(db, bt, category_account_id=accounts["6900"].id)
    db.commit()
    txn_id = bt.transaction_id
    out = update_bank_entry(txn_id, BankEntryUpdate(payee="City Water Dept"), db)
    assert out["payee"] == "City Water Dept"
    out = update_bank_entry(
        txn_id, BankEntryUpdate(category_account_id=accounts["6400"].id), db
    )
    assert out["category_account_id"] == accounts["6400"].id
    db.expire_all()
    line = (
        db.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=accounts["1000"].id)
        .one()
    )
    assert db.get(BankTransaction, bt.id).transaction_line_id == line.id
    update_bank_entry(txn_id, BankEntryUpdate(amount=Decimal("-125.00")), db)
    db.expire_all()
    bt = db.get(BankTransaction, bt.id)
    assert bt.transaction_line_id is None and bt.category_account_id is None


def _seeded(db):
    from app.models.accounts import AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    accounts = {}
    for data in CHART_OF_ACCOUNTS:
        acct = Account(
            account_number=data["account_number"],
            name=data["name"],
            account_type=AccountType(data["account_type"]),
            bank_kind=data.get("bank_kind"),
            is_system=True,
            balance=Decimal("0"),
        )
        db.add(acct)
        accounts[data["account_number"]] = acct
    db.commit()
    return accounts


def test_statement_line_edits_with_sqlite_foreign_keys_on(tmp_path):
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'fk.db').as_posix()}")

    @sa.event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        assert db.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
        _statement_line_edits(db, _seeded(db))
    finally:
        db.close()
        engine.dispose()


def test_statement_line_edits_on_postgres(postgres_url):  # noqa: F811
    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = sa.create_engine(postgres_url)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        _statement_line_edits(db, _seeded(db))
    finally:
        db.close()
        engine.dispose()


# ------------------------------------- what the form doesn't carry (#388 review)


def _class(client, name):
    r = client.post("/api/classes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_field_left_out_of_the_put_is_kept(
    client, db_session, seed_accounts, seed_customer
):
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Kitchen"}
    ).json()["id"]
    cls = _class(client, "Remodels")
    posted = _entry(client, seed_accounts, class_id=cls, job_id=job)
    # the form before this fix sent no job_id: the job is kept, not cleared
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    body = _form(e, payee="City Water Dept")
    del body["job_id"]
    r = client.put(f"/api/banking/entries/{posted['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == job and r.json()["class_id"] == cls
    # an API client may send only what it changes
    r = client.put(f"/api/banking/entries/{posted['id']}", json={"memo": "x"})
    assert r.status_code == 422  # the field is `description`, as on a post
    r = client.put(
        f"/api/banking/entries/{posted['id']}", json={"description": "Water, Aug"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["payee"] == "City Water Dept" and out["description"] == "Water, Aug"
    assert out["amount"] == "-125.00" and out["date"] == "2026-09-02"
    assert out["category_account_id"] == seed_accounts["6900"].id
    assert out["reference"] == "1042" and out["job_id"] == job
    assert out["class_id"] == cls
    db_session.expire_all()
    assert {
        (ln.class_id, ln.job_id)
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=posted["id"]
        )
    } == {(cls, job)}


def test_a_lines_own_class_survives_an_edit_and_an_archived_class_stays(
    client, db_session, seed_accounts
):
    """An imported check's lines can carry classes of their own; the form
    shows the header's. And a class archived since stays on the entry."""
    head, own, other = (
        _class(client, "Bakery"),
        _class(client, "Catering"),
        _class(client, "Retail"),
    )
    posted = _entry(client, seed_accounts, class_id=head)
    cat_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=seed_accounts["6900"].id)
        .one()
    )
    cat_line.class_id = own
    db_session.commit()
    assert client.put(f"/api/classes/{head}", json={"is_archived": True}).is_success
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e["class_id"] == head and e["class_name"] == "Bakery"
    # a new category and amount: the lines are re-made, the classes kept
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, category_account_id=seed_accounts["6400"].id, amount="-99"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["class_id"] == head

    def classes():
        db_session.expire_all()
        return {
            ln.account_id: ln.class_id
            for ln in db_session.query(TransactionLine).filter_by(
                transaction_id=posted["id"]
            )
        }

    assert classes() == {
        seed_accounts["6400"].id: own,
        seed_accounts["1000"].id: head,
    }
    # the header's class changed: the line that followed it follows; the
    # line with its own keeps it
    e = r.json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}", json=_form(e, class_id=other)
    )
    assert r.status_code == 200, r.text
    assert classes() == {
        seed_accounts["6400"].id: own,
        seed_accounts["1000"].id: other,
    }


def test_a_split_lines_own_class_round_trips(client, db_session, seed_accounts):
    head, own = _class(client, "Bakery"), _class(client, "Catering")
    a, b = seed_accounts["6900"], seed_accounts["6400"]
    posted = _entry(
        client,
        seed_accounts,
        amount="-100.00",
        category_account_id=a.id,
        class_id=head,
        splits=[
            {"category_account_id": a.id, "amount": "-60.00"},
            {"category_account_id": b.id, "amount": "-40.00", "class_id": own},
        ],
    )
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert [(s["class_id"], s["job_id"]) for s in e["splits"]] == [
        (None, None),
        (own, None),
    ]
    splits = [dict(s) for s in e["splits"]]
    splits[0]["amount"] = "-65.00"
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, amount="-105.00", splits=splits),
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert {
        ln.account_id: ln.class_id
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=posted["id"]
        )
    } == {a.id: head, b.id: own, seed_accounts["1000"].id: head}


# ------------------------------------------- open and save as is (#388 review)


def test_opening_and_saving_an_entry_unchanged_writes_nothing(
    client, db_session, seed_accounts
):
    cls = _class(client, "Bakery")
    shapes = {
        "payee and memo": {},
        "payee only": {"description": None},
        "memo only": {"payee": None},
        "neither": {"payee": None, "description": None, "check_number": None},
        "split": {
            "amount": "-100.00",
            "class_id": cls,
            "splits": [
                {"category_account_id": seed_accounts["6900"].id, "amount": "-70"},
                {
                    "category_account_id": seed_accounts["6400"].id,
                    "amount": "-30",
                    "description": "fee",
                },
            ],
        },
    }
    posted = {k: _entry(client, seed_accounts, **v) for k, v in shapes.items()}

    def stored(txn_id):
        db_session.expire_all()
        txn = db_session.get(Transaction, txn_id)
        return (
            txn.description,
            txn.reference,
            txn.class_id,
            sorted(
                (ln.account_id, str(ln.debit), str(ln.credit), ln.description)
                for ln in txn.lines
            ),
        )

    before = {k: stored(p["id"]) for k, p in posted.items()}
    audit_rows = db_session.query(AuditLog).count()
    for name, p in posted.items():
        e = client.get(f"/api/banking/entries/{p['id']}").json()
        r = client.put(f"/api/banking/entries/{p['id']}", json=_form(e))
        assert r.status_code == 200, (name, r.text)
        assert stored(p["id"]) == before[name], name
    assert db_session.query(AuditLog).count() == audit_rows
    # an entry with neither opens with a blank payee, not "Bank entry"
    e = client.get(f"/api/banking/entries/{posted['neither']['id']}").json()
    assert e["payee"] == "" and e["description"] == ""
    assert before["neither"][0] == "Bank entry"
    assert [ln[3] for ln in before["neither"][3]] == [None, None]
    # a memo-only entry opens with the memo in Memo and a blank Payee, not the
    # memo in Payee (#388 review): the header carries the sentinel, not the memo
    e = client.get(f"/api/banking/entries/{posted['memo only']['id']}").json()
    assert e["payee"] == "" and e["description"] == "August water"
    assert before["memo only"][0] == "Bank entry"
    assert [ln[3] for ln in before["memo only"][3]] == ["August water", "August water"]
    # a payee-only entry still opens with the payee in Payee and a blank Memo
    e = client.get(f"/api/banking/entries/{posted['payee only']['id']}").json()
    assert e["payee"] == "City Water" and e["description"] == ""
    assert before["payee only"][0] == "City Water"


def test_a_memo_only_entry_shows_a_blank_payee_in_the_register(
    client, db_session, seed_accounts
):
    """#388 review: a memo-only register entry reopens with the memo in Memo,
    and the register's Payee column is blank — not the memo, not the
    sentinel. The round trip is exact (open + save unchanged is a no-op)."""
    from app.services.bank_register import account_register

    posted = _entry(client, seed_accounts, payee=None, description="Fuel receipt")
    rows = account_register(db_session, seed_accounts["1000"])["entries"]
    row = next(r for r in rows if r["transaction_id"] == posted["id"])
    assert row["payee"] == ""
    assert row["description"] == "Fuel receipt"

    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e["payee"] == "" and e["description"] == "Fuel receipt"
    audit = db_session.query(AuditLog).count()
    r = client.put(f"/api/banking/entries/{posted['id']}", json=_form(e))
    assert r.status_code == 200, r.text
    assert db_session.query(AuditLog).count() == audit  # save-unchanged is a no-op
    e2 = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e2["payee"] == "" and e2["description"] == "Fuel receipt"


def test_a_memo_only_entry_shows_its_memo_in_the_ledger_and_journal(
    client, seed_accounts
):
    """A memo-only entry's header carries the "Bank entry" placeholder; the
    General Ledger (JSON and CSV) and the journal API show its memo instead."""
    posted = _entry(client, seed_accounts, payee=None, description="Fuel receipt")
    span = "start_date=2026-09-01&end_date=2026-09-30"
    gl = client.get(f"/api/reports/general-ledger?{span}").json()
    rows = [
        e
        for a in gl["accounts"]
        if a["account_id"] in (seed_accounts["1000"].id, seed_accounts["6900"].id)
        for e in a["entries"]
    ]
    assert len(rows) == 2
    assert {e["description"] for e in rows} == {"Fuel receipt"}

    csv = client.get(f"/api/reports/general-ledger/csv?{span}")
    assert csv.status_code == 200, csv.text
    assert "Fuel receipt" in csv.text and "Bank entry" not in csv.text

    j = client.get(f"/api/journal/{posted['id']}").json()
    assert j["description"] == "Fuel receipt"


def test_the_closing_date_holds_for_the_old_date_and_the_new(client, seed_accounts):
    posted = _entry(client, seed_accounts)  # Sep 2
    later = _entry(client, seed_accounts, date="2026-10-02")
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-15"}).status_code
        == 200
    )
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(f"/api/banking/entries/{posted['id']}", json=_form(e, amount="-1"))
    assert r.status_code == 403, r.text
    e = client.get(f"/api/banking/entries/{later['id']}").json()
    # an open entry cannot be moved into the closed period either
    r = client.put(
        f"/api/banking/entries/{later['id']}", json=_form(e, date="2026-09-10")
    )
    assert r.status_code == 403, r.text
    r = client.put(
        f"/api/banking/entries/{later['id']}", json=_form(e, date="2026-10-03")
    )
    assert r.status_code == 200, r.text


def test_a_split_entry_round_trips(client, db_session, seed_accounts):
    bank, a, b, c = (
        seed_accounts["1000"],
        seed_accounts["2400"],
        seed_accounts["6200"],
        seed_accounts["6900"],
    )
    posted = _entry(
        client,
        seed_accounts,
        amount="-500.00",
        category_account_id=a.id,
        payee="Bank loan",
        description="September payment",
        check_number="1050",
        splits=[
            {"category_account_id": a.id, "amount": "-450.00", "description": None},
            {
                "category_account_id": b.id,
                "amount": "-50.00",
                "description": "interest",
            },
        ],
    )
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    assert e["amount"] == "-500.00" and e["category_account_id"] == a.id
    no_own = {"class_id": None, "job_id": None}
    assert e["splits"] == [
        {
            "category_account_id": a.id,
            "amount": "-450.00",
            "description": None,
            **no_own,
        },
        {
            "category_account_id": b.id,
            "amount": "-50.00",
            "description": "interest",
            **no_own,
        },
    ]
    # the interest was 45, not 50, and a bank fee was on the same check
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            amount="-505.00",
            splits=[
                {"category_account_id": a.id, "amount": "-450.00", "description": None},
                {
                    "category_account_id": b.id,
                    "amount": "-45.00",
                    "description": "interest",
                },
                {"category_account_id": c.id, "amount": "-10.00", "description": "fee"},
            ],
        ),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == posted["id"] and out["amount"] == "-505.00"
    assert [s["amount"] for s in out["splits"]] == ["-450.00", "-45.00", "-10.00"]
    assert _lines(db_session, posted["id"]) == {
        a.id: (Decimal("450.00"), Decimal("0")),
        b.id: (Decimal("45.00"), Decimal("0")),
        c.id: (Decimal("10.00"), Decimal("0")),
        bank.id: (Decimal("0"), Decimal("505.00")),
    }
    assert _balance(db_session, bank) == Decimal("-505.00")
    assert _balance(db_session, b) == Decimal("45.00")
    # the split rules hold on an edit as on a post
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(
            e,
            splits=[
                {"category_account_id": a.id, "amount": "-450.00", "description": None},
                {"category_account_id": b.id, "amount": "-40.00", "description": None},
            ],
        ),
    )
    assert r.status_code == 400 and "must agree" in r.json()["detail"]


def test_an_edit_that_would_make_a_transfer_or_move_accounts_is_refused(
    client, seed_accounts
):
    posted = _entry(client, seed_accounts)
    e = client.get(f"/api/banking/entries/{posted['id']}").json()
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, category_account_id=seed_accounts["1010"].id),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "Savings is a bank or card account, so this would be a transfer. A "
        "transfer is its own entry: void this one and post the transfer."
    )
    r = client.put(
        f"/api/banking/entries/{posted['id']}",
        json=_form(e, account_id=seed_accounts["1010"].id),
    )
    assert r.status_code == 400 and "void it and enter it there" in r.json()["detail"]
    # a transfer row is not a register entry
    t = client.post(
        "/api/transfers",
        json={
            "date": "2026-09-04",
            "from_account_id": seed_accounts["1000"].id,
            "to_account_id": seed_accounts["1010"].id,
            "amount": "20",
        },
    )
    assert t.status_code == 201, t.text
    assert client.get(f"/api/banking/entries/{t.json()['id']}").status_code == 404


# ---------------------------------------------------------------- the page


def test_the_register_row_opens_the_entry_for_editing():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "${BankingPage._printCheckButton(e)}${BankingPage._editButton(e, id)}" in js
    assert "if (!e.editable) return '';" in js
    # the bank-entry branch still routes to the + Entry editor
    assert (
        "else onclick = `BankingPage.editEntry(${e.transaction_id}, ${accountId})`"
        in js
    )
    assert ">Edit</button>" in js
    assert "async showEntryForm(accountId, entry = null)" in js
    assert "if (entry) BankingPage._fillEntryForm(entry);" in js
    assert "title.textContent = 'Edit entry'" in js
    assert "API.get(`/banking/entries/${txnId}`)" in js
    assert "API.put(`/banking/entries/${entryId}`, data)" in js


def test_a_reconciled_refusal_is_asked_in_the_form_not_a_browser_dialog():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "err.status === 409 && err.detail && err.detail.code === 'reconciled'" in js
    assert "Change it anyway" in js and "id = 'entry-reconciled-warning'" in js
    assert "confirm_reconciled: true" in js
    start = js.index("async _putEntry(")
    end = js.index("async saveEntryAnyway(")
    assert "confirm(" not in js[start:end].replace("confirm_reconciled", "")


def test_the_edit_form_shows_a_fee_line_as_typed():
    """A deposit's fee line (#387) reopens as its own number with Taken out
    ticked, not as a negative amount."""
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    fill = js[js.index("_fillEntryForm(entry) {") :]
    fill = fill[: fill.index("\n    },")]
    assert ".split-fee').checked = against" in fill
    assert "(against ? -part : part)" in fill
