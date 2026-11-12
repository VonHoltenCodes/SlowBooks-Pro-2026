"""The upgrade pass for retainage payments recorded before 2.31.11 (#778).

2.31.11 changed what a payment recognizes on the cash basis when retainage
is involved: an owner's payment on a pay application recognizes its income
less the retainage held, and a paid sub retainage-release bill recognizes
the held cost on the held bills' own account, job and cost code. A payment
recorded before the upgrade kept the lines the old rule wrote (held owner
retainage as income with Retainage Receivable on the cash Balance Sheet; a
paid release on 6990 with no job), because the start-up backfill only writes
lines for a payment that has none.

These tests build a book, put its retainage payments' cash lines back to the
shape the old rule wrote (reconstructed below from the code before
79da763d), run the pass, and check the book reads as fresh postings do.
"""

from decimal import Decimal

import pytest

from app.models.bills import Bill, BillPayment, BillPaymentAllocation
from app.models.invoices import Invoice
from app.models.payments import Payment, PaymentAllocation
from app.models.settings import Settings
from app.models.transactions import (
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ALL,
    Transaction,
    TransactionLine,
)
from app.services import cash_basis
from tests.test_backups_per_company import _backup, books  # noqa: F401
from tests.test_cash_basis import (
    _amount,
    _bill_payment,
    _bs,
    _bs_amount,
    _each_entry_balances_on_every_basis,
    _invoice,
    _payment,
    _pl,
)
from tests.test_cash_basis_construction import (
    _approved_app,
    _cash_lines,
    _commitment,
    _job,
    _job_row,
    _owner_side,
    _release,
    _sub,
    _sub_bill,
    _sub_side,
)

D = Decimal


# ---------------------------------------------------------------------------
# the old rule, as cash_basis wrote it before 2.31.11
# ---------------------------------------------------------------------------


def _old_rule_lines(db, payment, invoice: bool) -> list[dict]:
    """The cash lines the rule before 2.31.11 wrote for a payment applied in
    full: each document's lines less the control account prorated by the
    applied amount (an invoice's Retainage Receivable included; a bill's
    Retainage Payable left out, so a release bill had nothing of its own and
    fell to the unapplied account), then one control line for the whole."""
    from app.services import control_accounts
    from app.services.accounting import get_ap_account_id, get_ar_account_id

    if invoice:
        control_id = get_ar_account_id(db)
        allocs = db.query(PaymentAllocation).filter_by(payment_id=payment.id).all()
        pairs = [(db.get(Invoice, a.invoice_id), a.amount) for a in allocs]
        source_types, skip = cash_basis.INVOICE_SOURCE_TYPES, ()
        fallback, link, sign = cash_basis.unapplied_income_id, "invoice", 1
    else:
        control_id = get_ap_account_id(db)
        allocs = (
            db.query(BillPaymentAllocation).filter_by(bill_payment_id=payment.id).all()
        )
        pairs = [(db.get(Bill, a.bill_id), a.amount) for a in allocs]
        source_types = cash_basis.BILL_SOURCE_TYPES
        found = control_accounts.find(db, "2050")
        skip = (found,) if found is not None else ()
        fallback, link, sign = cash_basis.unapplied_expense_id, "bill", -1
    memo = db.get(Transaction, payment.transaction_id).description or "Payment"
    out = []
    for document, applied in pairs:
        applied = sign * cash_basis._q(applied)
        lines = cash_basis.document_lines(
            db, source_types, document.id, control_id, skip
        )
        total = sum((ln["amount"] for ln in lines), D("0"))
        shares = cash_basis.prorate(lines, applied, total) if total else []
        if not shares:
            shares = [{"account_id": fallback(db), "amount": applied}]
        key = f"recognizes_{link}_id"
        out.extend(cash_basis._cash_line({**s, key: document.id}, memo) for s in shares)
    return cash_basis._balanced_on(out, control_id, memo)


def _to_old_rule(db):
    """Overwrite the cash lines of every payment on a retainage document with
    the old rule's, as a book used before 2.31.11 holds them. Returns the
    entry ids rewritten."""
    entries = []
    for invoice, model, alloc_model, fk, doc_model, doc_fk in (
        (True, Payment, PaymentAllocation, "payment_id", Invoice, "invoice_id"),
        (False, BillPayment, BillPaymentAllocation, "bill_payment_id", Bill, "bill_id"),
    ):
        for payment in db.query(model).all():
            allocs = db.query(alloc_model).filter_by(**{fk: payment.id}).all()
            docs = [db.get(doc_model, getattr(a, doc_fk)) for a in allocs]
            if not any(
                (d.retainage_held or 0) or (d.retainage_released or 0) for d in docs
            ):
                continue
            db.query(TransactionLine).filter(
                TransactionLine.transaction_id == payment.transaction_id,
                TransactionLine.basis == BASIS_CASH,
            ).execution_options(**{LEDGER_BASIS_OPTION: READ_ALL}).delete(
                synchronize_session=False
            )
            txn = db.get(Transaction, payment.transaction_id)
            cash_basis.append_cash_lines(db, txn, _old_rule_lines(db, payment, invoice))
            entries.append(txn.id)
    db.commit()
    return entries


def _entry_lines(db, txn_id):
    """One entry's cash lines with their ids, to show they were not touched."""
    rows = (
        db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == txn_id,
            TransactionLine.basis == BASIS_CASH,
        )
        .order_by(TransactionLine.id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )
    return [
        (r.id, r.account_id, r.job_id, D(str(r.debit)), D(str(r.credit))) for r in rows
    ]


def _done_flag(db):
    row = db.query(Settings).filter_by(key=cash_basis.RETAINAGE_REWRITE_KEY).first()
    return row.value if row else None


def _accrual_reports(client):
    return [
        _pl(client, "2026-01-01", "2026-12-31", "accrual"),
        _bs(client, "2026-12-31", "accrual"),
    ]


# ---------------------------------------------------------------------------
# the book in the issue: one pay application, one sub commitment
# ---------------------------------------------------------------------------


def _small_book(client, seed_accounts, customer_id):
    """A pay application for 39,232.86 at 10% retainage and no tax, its net
    paid 08-20; a sub bill of 10,000 holding 1,000, paid net, released and
    the release paid 11-06. Returns (job, invoice id, release bill id)."""
    job, sov = _job(client, customer_id)
    inv = _approved_app(
        client, job["id"], "2026-07-31", sov, (39232.86, 0), tax_rate="0"
    )
    sub = _sub(client, "Ridge Concrete")
    po = _commitment(client, sub["id"], job["id"], 10000, None)
    bill_id = _sub_bill(client, po, "2026-07-15", 10000, seed_accounts["5100"].id)
    _bill_payment(client, sub["id"], "2026-07-25", 9000, [(bill_id, 9000)])
    rel, amount = _release(client, po, "2026-11-02")
    assert amount == D("1000.00")
    _bill_payment(client, sub["id"], "2026-11-06", amount, [(rel, amount)])
    return job, inv, rel


def test_the_old_rule_lines_are_the_figures_in_the_issue(
    client, db_session, seed_accounts, seed_customer
):
    """The reconstruction writes what Keith found in the book: Cr income
    39,232.86, Dr 1150 3,923.29, Dr A/R 35,309.57, and the release on 6990."""
    job, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    inv = db_session.get(Invoice, inv_id)
    assert D(str(inv.retainage_held)) == D("3923.29")
    net = D(str(inv.total)) - D(str(inv.retainage_held))
    assert net == D("35309.57")
    _payment(client, seed_customer.id, "2026-08-20", net, [(inv_id, net)])
    _to_old_rule(db_session)

    year = _pl(client, "2026-01-01", "2026-12-31", "cash")
    assert D(str(year["total_income"])) == D("39232.86")
    assert _bs_amount(_bs(client, "2026-12-31", "cash"), "1150") == D("3923.29")
    assert _amount(year, "6990") == D("1000.00")


def test_the_pass_rewrites_retainage_payments_to_the_new_rule(
    client, db_session, seed_accounts, seed_customer
):
    job, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    fresh = _cash_lines(db_session)
    accrual = _accrual_reports(client)
    _to_old_rule(db_session)
    assert _cash_lines(db_session) != fresh

    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done == {
        "payments": 1,
        "bill_payments": 2,
        "kept_closed": 0,
        "failed": 0,
        "already_done": False,
    }

    # income less the retainage held, 1150 at zero, nothing on 6990
    year = _pl(client, "2026-01-01", "2026-12-31", "cash")
    assert D(str(year["total_income"])) == D("35309.57")
    assert _bs_amount(_bs(client, "2026-12-31", "cash"), "1150") == 0
    assert _amount(year, "6990") == 0
    # the release is the job's cost on the cash basis
    nov = _pl(client, "2026-11-01", "2026-11-30", "cash")
    assert _amount(nov, "5100") == D("1000.00")
    row, no_job = _job_row(client, "2026-01-01", "2026-12-31", "cash", job["id"])
    assert D(str(row["total_costs"])) == D("10000.00")
    assert no_job is None or D(str(no_job["total_costs"])) == 0
    # line for line what a fresh posting writes; the accrual books unmoved
    assert _cash_lines(db_session) == fresh
    assert _accrual_reports(client) == accrual
    assert _done_flag(db_session)
    _each_entry_balances_on_every_basis(db_session)


def test_a_second_run_rewrites_nothing(
    client, db_session, seed_accounts, seed_customer
):
    _job_, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    _to_old_rule(db_session)
    first = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert first["payments"] == 1 and not first["already_done"]
    flag = _done_flag(db_session)
    assert flag

    # put the old lines back: a pass that ran again would rewrite them
    entries = _to_old_rule(db_session)
    before = [_entry_lines(db_session, t) for t in entries]
    again = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert again == {
        "payments": 0,
        "bill_payments": 0,
        "kept_closed": 0,
        "failed": 0,
        "already_done": True,
    }
    assert [_entry_lines(db_session, t) for t in entries] == before
    assert _done_flag(db_session) == flag


def test_a_payment_on_or_before_the_closing_date_keeps_its_lines_and_is_counted(
    client, db_session, seed_accounts, seed_customer
):
    _job_, inv_id, rel = _small_book(client, seed_accounts, seed_customer.id)
    pay = _payment(
        client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")]
    )
    fresh = _cash_lines(db_session)
    _to_old_rule(db_session)
    r = client.put("/api/settings", json={"closing_date": "2026-08-31"})
    assert r.status_code == 200, r.text
    owner_entry = db_session.get(Payment, pay["id"]).transaction_id
    before = _entry_lines(db_session, owner_entry)

    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    # the owner payment (08-20) and the held bill's (07-25) are closed; the
    # release payment (11-06) is rewritten
    assert done["kept_closed"] == 2
    assert done["payments"] == 0 and done["bill_payments"] == 1
    assert done["failed"] == 0
    assert _entry_lines(db_session, owner_entry) == before
    year = _pl(client, "2026-01-01", "2026-12-31", "cash")
    assert D(str(year["total_income"])) == D("39232.86")
    assert _amount(year, "6990") == 0
    assert _amount(_pl(client, "2026-11-01", "2026-11-30", "cash"), "5100") == D(
        "1000.00"
    )
    assert _cash_lines(db_session) != fresh
    assert _done_flag(db_session)
    _each_entry_balances_on_every_basis(db_session)


def test_a_payment_with_no_retainage_is_not_touched(
    client, db_session, seed_accounts, seed_customer
):
    _job_, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    plain = _invoice(client, seed_customer.id, "2026-09-01", [{"rate": 500}])
    other = _payment(client, seed_customer.id, "2026-09-10", 500, [(plain["id"], 500)])
    other_entry = db_session.get(Payment, other["id"]).transaction_id
    _to_old_rule(db_session)
    before = _entry_lines(db_session, other_entry)
    assert before

    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done["payments"] == 1
    assert _entry_lines(db_session, other_entry) == before


def test_a_book_with_no_retainage_is_marked_done_with_nothing_to_do(
    client, db_session, seed_accounts, seed_customer
):
    plain = _invoice(client, seed_customer.id, "2026-09-01", [{"rate": 500}])
    _payment(client, seed_customer.id, "2026-09-10", 500, [(plain["id"], 500)])
    lines = _cash_lines(db_session)
    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done == {
        "payments": 0,
        "bill_payments": 0,
        "kept_closed": 0,
        "failed": 0,
        "already_done": False,
    }
    assert _cash_lines(db_session) == lines
    assert _done_flag(db_session)


def test_a_document_that_fails_is_skipped_and_tried_again_next_start(
    client, db_session, seed_accounts, seed_customer, monkeypatch, caplog
):
    _job_, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    fresh = _cash_lines(db_session)
    _to_old_rule(db_session)

    real = cash_basis.recompute_invoice_cash_lines

    def broken(db, invoice, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr(cash_basis, "recompute_invoice_cash_lines", broken)
    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done["failed"] == 1
    assert done["bill_payments"] == 2
    assert "could not be rewritten" in caplog.text
    assert _done_flag(db_session) is None

    monkeypatch.setattr(cash_basis, "recompute_invoice_cash_lines", real)
    again = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert again["failed"] == 0 and again["payments"] == 1
    assert _cash_lines(db_session) == fresh
    assert _done_flag(db_session)


# ---------------------------------------------------------------------------
# the construction book of test_cash_basis_construction, old rule throughout
# ---------------------------------------------------------------------------


def test_the_construction_book_reads_as_fresh_postings_after_the_pass(
    client, db_session, seed_accounts, seed_customer
):
    """Four pay applications (a deposit, tax, a release application) and two
    commitments released and paid: after the old rule's lines are put back
    and the pass runs, every cash line and report is the fresh one."""
    job, _invoices = _owner_side(client, db_session, seed_customer.id)
    _sub_side(client, seed_accounts, job["id"])
    fresh = _cash_lines(db_session)
    reports = [
        _pl(client, "2026-01-01", "2026-09-30", "cash"),
        _pl(client, "2026-10-01", "2026-12-31", "cash"),
        _bs(client, "2026-09-30", "cash"),
        _bs(client, "2026-12-31", "cash"),
    ]
    accrual = _accrual_reports(client)

    _to_old_rule(db_session)
    assert _bs_amount(_bs(client, "2026-09-30", "cash"), "1150") != 0
    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done["failed"] == 0
    # the four applications and the deposit applied on the first
    assert done["payments"] == 5 and done["bill_payments"] == 5

    assert _cash_lines(db_session) == fresh
    assert [
        _pl(client, "2026-01-01", "2026-09-30", "cash"),
        _pl(client, "2026-10-01", "2026-12-31", "cash"),
        _bs(client, "2026-09-30", "cash"),
        _bs(client, "2026-12-31", "cash"),
    ] == reports
    assert _accrual_reports(client) == accrual
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# start-up
# ---------------------------------------------------------------------------


def test_the_pass_runs_when_the_server_starts(monkeypatch, lifespan_client, capsys):
    """The start-up runs the pass beside the backfill, passing the progress
    note the desktop launcher reads, and prints its counts."""
    calls = []

    def recorder(db, on_progress=None):
        calls.append(on_progress)
        return {
            "payments": 3,
            "bill_payments": 2,
            "kept_closed": 1,
            "failed": 0,
            "already_done": False,
        }

    monkeypatch.setattr(cash_basis, "rewrite_retainage_cash_lines", recorder)
    with lifespan_client as c:
        assert c.get("/api/accounts").status_code == 200
    assert len(calls) == 1 and calls[0] is not None
    assert (
        "Cash basis and retainage: rewrote the cash lines of 3 payment(s) and 2 "
        "bill payment(s) to the 2.31.11 rule; 1 payment(s) on or before the "
        "closing date were left as they were, unread"
    ) in capsys.readouterr().out


# ---------------------------------------------------------------------------
# what is counted as rewritten
# ---------------------------------------------------------------------------


def test_a_payment_the_recompute_leaves_is_not_counted_as_rewritten(
    client, db_session, seed_accounts, seed_customer, caplog
):
    """One payment on two pay applications whose old lines name neither (a
    payment from before lines named their document): the recompute cannot
    tell them apart and leaves them, logged, so the pass counts nothing."""
    job, sov = _job(client, seed_customer.id)
    a = _approved_app(client, job["id"], "2026-07-31", sov, (10000, 0), tax_rate="0")
    b = _approved_app(client, job["id"], "2026-08-31", sov, (5000, 0), tax_rate="0")
    pay = _payment(
        client, seed_customer.id, "2026-09-10", "13500", [(a, "9000"), (b, "4500")]
    )
    _to_old_rule(db_session)
    entry = db_session.get(Payment, pay["id"]).transaction_id
    db_session.query(TransactionLine).filter(
        TransactionLine.transaction_id == entry
    ).execution_options(**{LEDGER_BASIS_OPTION: READ_ALL}).update(
        {TransactionLine.recognizes_invoice_id: None}, synchronize_session=False
    )
    db_session.commit()
    before = _entry_lines(db_session, entry)

    done = cash_basis.rewrite_retainage_cash_lines(db_session)
    assert done["payments"] == 0 and done["failed"] == 0
    assert _entry_lines(db_session, entry) == before
    assert "not recomputed" in caplog.text


# ---------------------------------------------------------------------------
# mid-session: an import, a restore
# ---------------------------------------------------------------------------


def _forget_the_pass(db):
    """The file as a backup from before the pass holds it: no done row."""
    db.query(Settings).filter_by(key=cash_basis.RETAINAGE_REWRITE_KEY).delete()
    db.commit()


def test_an_import_runs_the_pass_on_a_file_that_has_not_had_it(
    client, db_session, seed_accounts, seed_customer, capsys
):
    _job_, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    fresh = _cash_lines(db_session)
    _to_old_rule(db_session)
    _forget_the_pass(db_session)

    cash_basis.backfill_after_import(db_session)
    assert _cash_lines(db_session) == fresh
    assert _done_flag(db_session)
    # the sentence is printed, so it reaches launcher.log (W-144)
    out = capsys.readouterr().out
    assert "Cash basis and retainage: rewrote the cash lines of" in out
    assert "to the 2.31.11 rule" in out and "(after an import)" in out


def test_restored_books_are_caught_up_without_a_restart(
    client, db_session, seed_accounts, seed_customer, capsys
):
    """A backup from before the pass comes back with old-rule lines and no
    done row; the restore's catch-up gives it the 2.31.11 lines."""
    _job_, inv_id, _rel = _small_book(client, seed_accounts, seed_customer.id)
    _payment(client, seed_customer.id, "2026-08-20", "35309.57", [(inv_id, "35309.57")])
    fresh = _cash_lines(db_session)
    cash_basis.rewrite_retainage_cash_lines(db_session)
    _to_old_rule(db_session)
    _forget_the_pass(db_session)

    cash_basis.catch_up_after_restore(db_session)
    assert _cash_lines(db_session) == fresh
    assert _done_flag(db_session)
    year = _pl(client, "2026-01-01", "2026-12-31", "cash")
    assert D(str(year["total_income"])) == D("35309.57")
    # the sentence is printed, so it reaches launcher.log after a restore in
    # the desktop app, whose server logs WARNING and up (W-144)
    out = capsys.readouterr().out
    assert "Cash basis and retainage: rewrote the cash lines of" in out
    assert "to the 2.31.11 rule" in out and "(after a restore)" in out


@pytest.mark.usefixtures("books")
def test_the_restore_route_runs_the_catch_up(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cash_basis, "catch_up_after_restore", lambda db: calls.append(db)
    )
    name = _backup(client)
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    assert len(calls) == 1


@pytest.mark.usefixtures("books")
def test_a_catch_up_that_fails_does_not_fail_the_restore(client, monkeypatch, caplog):
    def broken(db, on_progress=None):
        raise RuntimeError("simulated")

    monkeypatch.setattr(cash_basis, "backfill", broken)
    monkeypatch.setattr(cash_basis, "rewrite_retainage_cash_lines", broken)
    name = _backup(client)
    r = client.post("/api/backups/restore", json={"filename": name})
    assert r.status_code == 200, r.text
    assert "could not be caught up" in caplog.text
    assert "could not be rewritten after a restore" in caplog.text
