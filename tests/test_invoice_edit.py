"""Regression tests for bugs in the invoice edit path.

All tests here should FAIL against the pre-fix code and PASS after the fix.
"""

from decimal import Decimal

import pytest


def _create_invoice(client, customer_id, amount="100.00", tax_rate="0", qty="1"):
    body = {
        "customer_id": customer_id,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": tax_rate,
        "lines": [
            {"description": "Service", "quantity": qty, "rate": amount, "line_order": 0}
        ],
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sum_debits_credits(db_session, txn_id):
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return (
        sum((Decimal(str(l.debit)) for l in lines), Decimal("0")),
        sum((Decimal(str(l.credit)) for l in lines), Decimal("0")),
    )


def test_identical_invoice_edit_preserves_line_dimensions_and_journal(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.classes import TxnClass
    from app.models.cost_codes import CostCode
    from app.models.jobs import Job
    from app.models.invoices import Invoice
    from app.models.transactions import Transaction, TransactionLine

    cls = TxnClass(name="Service Department", default_function="program")
    job = Job(customer_id=seed_customer.id, name="Service Job")
    code = CostCode(code="SVC", name="Service labor", cost_type="labor")
    db_session.add_all([cls, job, code])
    db_session.commit()
    dimensions = (cls.id, job.id)
    line = {
        "description": "Consulting",
        "quantity": "2",
        "rate": "50",
        "class_id": cls.id,
        "job_id": job.id,
        "cost_code_id": code.id,
    }
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0.10",
            "lines": [line],
        },
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["id"]

    def snapshot():
        db_session.expire_all()
        invoice = db_session.get(Invoice, invoice_id)
        txn = db_session.get(Transaction, invoice.transaction_id)
        splits = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=txn.id)
            .order_by(TransactionLine.account_id)
            .all()
        )
        revenue = next(split for split in splits if split.credit == Decimal("100"))
        assert (revenue.class_id, revenue.job_id) == dimensions
        assert (revenue.cost_code_id, revenue.cost_type, revenue.function) == (
            code.id,
            "labor",
            "program",
        )
        assert (invoice.lines[0].class_id, invoice.lines[0].job_id) == dimensions
        assert invoice.lines[0].cost_code_id == code.id
        assert txn.source_type == "invoice" and txn.source_id == invoice_id
        return (
            (txn.date, txn.source_type, txn.source_id, txn.class_id, txn.job_id),
            [(s.account_id, s.debit, s.credit, s.class_id, s.job_id) for s in splits],
            (
                invoice.total,
                invoice.amount_paid,
                invoice.balance_due,
                invoice.currency,
                invoice.exchange_rate,
            ),
        )

    before = snapshot()
    response = client.put(f"/api/invoices/{invoice_id}", json={"lines": [line]})
    assert response.status_code == 200, response.text
    assert snapshot() == before


@pytest.mark.parametrize(
    "currency,rate,home_amount", [("USD", "1", "100"), ("EUR", "1.20", "120")]
)
@pytest.mark.parametrize("edit_kind", ["identical_lines", "identical_tax"])
def test_identical_invoice_edit_preserves_home_currency_posting(
    client,
    db_session,
    seed_accounts,
    seed_customer,
    currency,
    rate,
    home_amount,
    edit_kind,
):
    from app.models.accounts import Account
    from app.models.invoices import Invoice
    from app.models.settings import Settings
    from app.models.transactions import Transaction, TransactionLine

    db_session.add(Settings(key="home_currency", value="USD"))
    db_session.commit()
    line = {"description": "Consulting", "quantity": "1", "rate": "100.00"}
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "currency": currency,
            "exchange_rate": rate,
            "tax_rate": "0",
            "lines": [line],
        },
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["id"]
    other = _create_invoice(client, seed_customer.id, amount="25")

    def snapshot():
        db_session.expire_all()
        inv = db_session.get(Invoice, invoice_id)
        txn = db_session.get(Transaction, inv.transaction_id)
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        postings = sorted(
            (db_session.get(Account, s.account_id).account_number, s.debit, s.credit)
            for s in lines
        )
        assert postings == [
            ("1100", Decimal(home_amount), Decimal("0")),
            ("4000", Decimal("0"), Decimal(home_amount)),
        ]
        assert sum(s.debit for s in lines) == sum(s.credit for s in lines)
        assert inv.currency == currency and inv.exchange_rate == Decimal(rate)
        assert inv.total == inv.balance_due == Decimal("100")
        assert inv.amount_paid == Decimal("0")
        balances = [
            (a.id, a.balance) for a in db_session.query(Account).order_by(Account.id)
        ]
        other_txn = db_session.get(Invoice, other["id"]).transaction_id
        unrelated = [
            (s.id, s.account_id, s.debit, s.credit)
            for s in db_session.query(TransactionLine)
            .filter_by(transaction_id=other_txn)
            .order_by(TransactionLine.id)
        ]
        return (
            txn.id,
            txn.date,
            txn.source_type,
            txn.source_id,
            postings,
            balances,
            unrelated,
        )

    before = snapshot()
    payload = {"lines": [line]} if edit_kind == "identical_lines" else {"tax_rate": "0"}
    response = client.put(f"/api/invoices/{invoice_id}", json=payload)
    assert response.status_code == 200, response.text
    assert snapshot() == before


def test_date_only_invoice_edit_moves_owned_journals_without_changing_postings(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.classes import TxnClass
    from app.models.jobs import Job
    from app.models.items import Item, ItemType
    from app.models.invoices import Invoice
    from app.models.transactions import Transaction, TransactionLine

    cls = TxnClass(name="Date parity department")
    job = Job(customer_id=seed_customer.id, name="Date parity job")
    item = Item(
        name="Date parity stock",
        item_type=ItemType.PRODUCT,
        track_inventory=True,
        quantity_on_hand=Decimal("10"),
        avg_cost=Decimal("20"),
    )
    db_session.add_all([cls, job, item])
    db_session.commit()
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "lines": [
                {
                    "item_id": item.id,
                    "quantity": "2",
                    "rate": "50",
                    "class_id": cls.id,
                    "job_id": job.id,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["id"]
    unrelated_invoice = _create_invoice(client, seed_customer.id)
    # Same source id with a different owner must not move with the invoice.
    sentinel = Transaction(
        date=_date(2026, 4, 1), source_type="payment", source_id=invoice_id
    )
    db_session.add(sentinel)
    db_session.commit()

    def snapshot():
        db_session.expire_all()
        return {
            txn.id: (
                txn.date,
                txn.source_type,
                txn.source_id,
                txn.class_id,
                txn.job_id,
                [
                    (s.id, s.account_id, s.debit, s.credit, s.class_id, s.job_id)
                    for s in db_session.query(TransactionLine)
                    .filter_by(transaction_id=txn.id)
                    .order_by(TransactionLine.id)
                ],
            )
            for txn in db_session.query(Transaction).all()
        }

    before = snapshot()
    owned = {
        txn_id for txn_id, row in before.items() if row[1:3] == ("invoice", invoice_id)
    }
    assert len(owned) == 2  # Main AR/revenue journal and inventory/COGS journal.
    assert all(before[txn_id][0] == _date(2026, 4, 1) for txn_id in owned)
    response = client.put(f"/api/invoices/{invoice_id}", json={"date": "2026-04-02"})
    assert response.status_code == 200, response.text
    after = snapshot()
    expected = {
        txn_id: ((_date(2026, 4, 2), *row[1:]) if txn_id in owned else row)
        for txn_id, row in before.items()
    }
    assert after == expected
    assert db_session.get(Invoice, invoice_id).date == _date(2026, 4, 2)
    assert db_session.get(Invoice, unrelated_invoice["id"]).date == _date(2026, 4, 1)


@pytest.mark.parametrize(
    "new_total,expected_total,expected_due,expected_status,status_code",
    [
        ("50", "100", "40", "partial", 400),
        ("60", "60", "0", "paid", 200),
        ("80", "80", "20", "partial", 200),
    ],
)
def test_paid_invoice_edit_preserves_payment_consistency(
    client,
    db_session,
    seed_accounts,
    seed_customer,
    new_total,
    expected_total,
    expected_due,
    expected_status,
    status_code,
):
    from app.models.accounts import Account
    from app.models.invoices import Invoice, InvoiceLine
    from app.models.payments import Payment, PaymentAllocation
    from app.models.transactions import Transaction, TransactionLine

    inv = _create_invoice(client, seed_customer.id, amount="100.00")
    response = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-02",
            "amount": "60.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "60.00"}],
        },
    )
    assert response.status_code == 201, response.text

    def snapshot():
        db_session.expire_all()
        return {
            model.__tablename__: [
                tuple(getattr(row, c.name) for c in model.__table__.columns)
                for row in db_session.query(model).order_by(model.id)
            ]
            for model in (
                Invoice,
                InvoiceLine,
                Payment,
                PaymentAllocation,
                Transaction,
                TransactionLine,
                Account,
            )
        }

    before = snapshot()
    response = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {
                    "description": "Service",
                    "quantity": "1",
                    "rate": new_total,
                    "line_order": 0,
                }
            ],
        },
    )
    assert response.status_code == status_code, response.text
    after = snapshot()
    if status_code == 400:
        assert "paid" in response.json()["detail"].lower()
        assert after == before
    assert after["payments"] == before["payments"]
    assert after["payment_allocations"] == before["payment_allocations"]
    invoice = db_session.get(Invoice, inv["id"])
    assert invoice.total == Decimal(expected_total)
    assert invoice.amount_paid == Decimal("60")
    assert invoice.balance_due == Decimal(expected_due)
    assert invoice.status.value == expected_status
    allocation = (
        db_session.query(PaymentAllocation).filter_by(invoice_id=invoice.id).one()
    )
    assert allocation.amount == Decimal("60")
    ar = seed_accounts["1100"]
    postings = db_session.query(TransactionLine).filter_by(account_id=ar.id).all()
    assert sum((s.debit - s.credit for s in postings), Decimal("0")) == Decimal(
        expected_due
    )
    assert db_session.get(Account, ar.id).balance == Decimal(expected_due)


def test_editing_only_tax_rate_recomputes_totals(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00", tax_rate="0")

    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": "0.10"})
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice

    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.tax_rate == Decimal("0.1000")
    assert invoice.tax_amount == Decimal(
        "10.00"
    ), f"tax_amount not recomputed after tax_rate change: got {invoice.tax_amount}"
    assert invoice.total == Decimal(
        "110.00"
    ), f"total not recomputed after tax_rate change: got {invoice.total}"
    assert invoice.balance_due == Decimal("110.00")


def test_editing_lines_keeps_journal_balanced(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {"description": "A", "quantity": "2", "rate": "50.00", "line_order": 0},
                {"description": "B", "quantity": "1", "rate": "25.00", "line_order": 1},
            ],
            "tax_rate": "0.08",
        },
    )
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice

    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.subtotal == Decimal("125.00")
    assert invoice.tax_amount == Decimal("10.00")
    assert invoice.total == Decimal("135.00")

    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert (
        dr == cr == Decimal("135.00")
    ), f"journal unbalanced after line edit: dr={dr}, cr={cr}"


def test_editing_only_tax_rate_keeps_journal_balanced(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00", tax_rate="0")

    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": "0.10"})
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice

    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    # After the fix, journal should track the new total of 110.
    assert (
        dr == cr == Decimal("110.00")
    ), f"journal not updated to match new tax: dr={dr}, cr={cr}, invoice.total={invoice.total}"


# ---------------------------------------------------------------------------
# Due-date computation regression tests.
#
# A "Due Date" field + JS auto-calc was added in the UX pass. The SPA now
# always sends due_date (a value or explicit null). These tests pin the
# server-side behavior so the two known bugs can't come back:
#   1. Clearing the field on edit must RECOMPUTE from terms, not persist NULL.
#   2. "Due on Receipt" must mean same-day, not the old +30 ValueError fallback.
# ---------------------------------------------------------------------------

from datetime import date as _date  # noqa: E402


def test_due_date_helper_terms_math():
    """The shared _due_date_from_terms helper covers Net N, Due on Receipt,
    and unknown terms (fallback Net 30)."""
    from app.routes.invoices import _due_date_from_terms

    base = _date(2026, 4, 1)
    assert _due_date_from_terms(base, "Net 30") == _date(2026, 5, 1)
    assert _due_date_from_terms(base, "Net 15") == _date(2026, 4, 16)
    assert _due_date_from_terms(base, "Due on Receipt") == base  # NOT +30
    assert _due_date_from_terms(base, "due upon receipt") == base
    assert _due_date_from_terms(base, "Net 0") == base
    assert _due_date_from_terms(base, "gibberish") == _date(2026, 5, 1)  # fallback
    assert _due_date_from_terms(base, None) == _date(2026, 5, 1)


def test_create_invoice_due_on_receipt_is_same_day(
    client, db_session, seed_accounts, seed_customer
):
    """Regression: 'Due on Receipt' used to fall through int() ValueError to
    a 30-day due date. It must be the invoice date."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "terms": "Due on Receipt",
            "tax_rate": "0",
            "lines": [
                {"description": "X", "quantity": "1", "rate": "10", "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["due_date"] == "2026-04-01"


def test_create_invoice_explicit_due_date_wins(
    client, db_session, seed_accounts, seed_customer
):
    """An explicit due_date overrides the terms-derived one."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "terms": "Net 30",
            "due_date": "2026-04-10",
            "tax_rate": "0",
            "lines": [
                {"description": "X", "quantity": "1", "rate": "10", "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["due_date"] == "2026-04-10"


def test_editing_invoice_with_cleared_due_date_recomputes_not_null(
    client, db_session, seed_accounts, seed_customer
):
    """Regression: the SPA sends due_date=null when the field is cleared.
    exclude_unset lets the explicit null through; without the fix it would
    persist as NULL. It must recompute from terms instead."""
    inv = _create_invoice(client, seed_customer.id, amount="100.00")  # Net 30
    assert inv["due_date"] == "2026-05-01"

    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"due_date": None, "terms": "Net 30"},
    )
    assert r.status_code == 200, r.text
    # Recomputed from date(2026-04-01) + Net 30, NOT wiped to null.
    assert r.json()["due_date"] == "2026-05-01"


def test_editing_invoice_preserves_explicit_due_date(
    client, db_session, seed_accounts, seed_customer
):
    """Sending a concrete due_date on edit stores that exact date."""
    inv = _create_invoice(client, seed_customer.id, amount="100.00")
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"due_date": "2026-06-15"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["due_date"] == "2026-06-15"


# ---------------------------------------------------------------------------
# JE rounding regression (enterprise eval CRITICAL):
# sub-cent unit rates produced an unbalanced journal entry → 500 on create.
# AR debit used the rounded total while income credits used unrounded qty*rate.
# ---------------------------------------------------------------------------


def test_subcent_rate_invoice_posts_balanced_je(
    client, db_session, seed_accounts, seed_customer
):
    """qty=3 @ rate=1.005 (fuel-style sub-cent price) must create a balanced
    JE, not 500. Regression for the rounded-debit / unrounded-credit bug."""
    from app.models.transactions import TransactionLine

    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [
                {
                    "description": "fuel",
                    "quantity": "3",
                    "rate": "1.005",
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text  # was 500 before the fix
    txn_id = (
        db_session.query(
            __import__("app.models.invoices", fromlist=["Invoice"]).Invoice
        )
        .filter_by(id=r.json()["id"])
        .first()
        .transaction_id
    )
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert dr == cr, f"journal unbalanced after sub-cent rate: dr={dr} cr={cr}"


def test_subcent_rate_invoice_edit_stays_balanced(
    client, db_session, seed_accounts, seed_customer
):
    """The edit path rebuilds the JE via the shared helper — same rounding
    rule must hold."""
    from app.models.transactions import TransactionLine
    from app.models.invoices import Invoice

    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": "0",
            "lines": [
                {"description": "x", "quantity": "1", "rate": "10", "line_order": 0}
            ],
        },
    ).json()
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {
                    "description": "fuel",
                    "quantity": "7",
                    "rate": "2.005",
                    "line_order": 0,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    txn_id = db_session.query(Invoice).filter_by(id=inv["id"]).first().transaction_id
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert dr == cr, f"edit JE unbalanced: dr={dr} cr={cr}"


@pytest.mark.parametrize(
    "edit_kind",
    ["rate", "currency", "class", "job", "clear_class", "clear_job", "notes"],
)
def test_accounting_header_edits_repost_but_notes_do_not(
    client, db_session, seed_accounts, seed_customer, edit_kind
):
    from unittest.mock import patch
    from app.models.classes import TxnClass
    from app.models.jobs import Job
    from app.models.invoices import Invoice
    from app.models.settings import Settings
    from app.models.transactions import Transaction, TransactionLine
    from app.routes.invoices import crud

    classes = [TxnClass(name=f"Repost class {i}") for i in range(2)]
    jobs = [Job(customer_id=seed_customer.id, name=f"Repost job {i}") for i in range(2)]
    db_session.add_all([*classes, *jobs, Settings(key="home_currency", value="USD")])
    db_session.commit()
    response = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "currency": "EUR",
            "exchange_rate": "1.20",
            "tax_rate": "0",
            "class_id": classes[0].id,
            "job_id": jobs[0].id,
            "lines": [{"description": "Service", "quantity": "1", "rate": "100"}],
        },
    )
    assert response.status_code == 201, response.text
    invoice_id = response.json()["id"]
    inv = db_session.get(Invoice, invoice_id)
    txn_id = inv.transaction_id
    before = [
        (s.id, s.account_id, s.debit, s.credit, s.class_id, s.job_id)
        for s in db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id)
        .order_by(TransactionLine.id)
    ]
    assert (
        sum(row[2] for row in before) == sum(row[3] for row in before) == Decimal("120")
    )
    edits = {
        "rate": {"exchange_rate": "1.30"},
        "currency": {"currency": "USD"},
        "class": {"class_id": classes[1].id},
        "job": {"job_id": jobs[1].id},
        "clear_class": {"class_id": None},
        "clear_job": {"job_id": None},
        "notes": {"notes": "No accounting change"},
    }
    expected_class = edits[edit_kind].get("class_id", classes[0].id)
    expected_job = edits[edit_kind].get("job_id", jobs[0].id)
    with patch.object(
        crud, "_post_invoice_journal", wraps=crud._post_invoice_journal
    ) as post:
        response = client.put(f"/api/invoices/{invoice_id}", json=edits[edit_kind])
        assert response.status_code == 200, response.text
        assert post.call_count == (0 if edit_kind == "notes" else 1)
    db_session.expire_all()
    inv = db_session.get(Invoice, invoice_id)
    txn = db_session.get(Transaction, txn_id)
    splits = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id)
        .order_by(TransactionLine.id)
        .all()
    )
    expected_amount = Decimal({"rate": "130", "currency": "100"}.get(edit_kind, "120"))
    assert (
        sum(s.debit for s in splits) == sum(s.credit for s in splits) == expected_amount
    )
    assert sorted(s.account_id for s in splits) == sorted(row[1] for row in before)
    assert (inv.class_id, txn.class_id) == (expected_class, expected_class)
    assert (inv.job_id, txn.job_id) == (expected_job, expected_job)
    assert all((s.class_id, s.job_id) == (expected_class, expected_job) for s in splits)
    assert inv.total == inv.balance_due == Decimal("100")
    assert inv.amount_paid == Decimal("0")
    assert inv.exchange_rate == Decimal(
        {"rate": "1.30", "currency": "1"}.get(edit_kind, "1.20")
    )
    assert inv.currency == ("USD" if edit_kind == "currency" else "EUR")
    assert (
        inv.transaction_id == txn_id
        and txn.source_type == "invoice"
        and txn.source_id == invoice_id
    )
    assert inv.date == txn.date == _date(2026, 4, 1)
    if edit_kind == "notes":
        assert [
            (s.id, s.account_id, s.debit, s.credit, s.class_id, s.job_id)
            for s in splits
        ] == before


def test_reconciled_invoice_rejects_posting_and_date_edits_but_allows_notes(
    client, db_session, seed_accounts, seed_customer
):
    from datetime import datetime
    from app.models.banking import Reconciliation, ReconciliationStatus
    from app.models.invoices import Invoice
    from app.models.transactions import Transaction, TransactionLine

    inv = _create_invoice(client, seed_customer.id)
    invoice_id = inv["id"]
    txn_id = db_session.get(Invoice, invoice_id).transaction_id
    split = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).first()
    # The completed-reconciliation state stamped by reconciliation.complete.
    recon = Reconciliation(
        account_id=split.account_id,
        statement_date=_date(2026, 4, 30),
        statement_balance=Decimal("100"),
        cleared_total=Decimal("100"),
        status=ReconciliationStatus.COMPLETED,
        completed_at=datetime(2026, 4, 30),
    )
    db_session.add(recon)
    db_session.flush()
    split.cleared = True
    split.reconciliation_id = recon.id
    db_session.commit()

    def snapshot():
        db_session.expire_all()
        invoice = db_session.get(Invoice, invoice_id)
        txn = db_session.get(Transaction, txn_id)
        return (
            invoice.date,
            invoice.total,
            invoice.amount_paid,
            invoice.balance_due,
            txn.date,
            txn.source_type,
            txn.source_id,
            txn.class_id,
            txn.job_id,
            [
                (
                    s.id,
                    s.account_id,
                    s.debit,
                    s.credit,
                    s.class_id,
                    s.job_id,
                    s.cleared,
                    s.reconciliation_id,
                )
                for s in db_session.query(TransactionLine)
                .filter_by(transaction_id=txn_id)
                .order_by(TransactionLine.id)
            ],
            db_session.get(Reconciliation, recon.id).status,
        )

    before = snapshot()
    for payload in [
        {"lines": [{"description": "Service", "quantity": "1", "rate": "125"}]},
        {"date": "2026-04-02"},
    ]:
        response = client.put(f"/api/invoices/{invoice_id}", json=payload)
        assert response.status_code == 400, response.text
        assert "reconciliation" in response.json()["detail"]
        assert snapshot() == before
    response = client.put(f"/api/invoices/{invoice_id}", json={"notes": "Allowed note"})
    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "Allowed note"
    assert snapshot() == before


@pytest.mark.parametrize(
    "payment_amount,submitted_status,expected_status,expected_due",
    [
        ("60", "paid", "partial", "40"),
        ("100", "partial", "paid", "0"),
        ("0", "paid", "draft", "100"),
    ],
)
def test_status_only_edit_respects_payment_state(
    client,
    db_session,
    seed_accounts,
    seed_customer,
    payment_amount,
    submitted_status,
    expected_status,
    expected_due,
):
    from app.models.invoices import Invoice
    from app.models.payments import PaymentAllocation
    from app.models.transactions import Transaction, TransactionLine

    inv = _create_invoice(client, seed_customer.id)
    if Decimal(payment_amount) > 0:
        response = client.post(
            "/api/payments",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-04-02",
                "amount": payment_amount,
                "allocations": [{"invoice_id": inv["id"], "amount": payment_amount}],
            },
        )
        assert response.status_code == 201, response.text

    def snapshot():
        db_session.expire_all()
        return {
            model.__tablename__: [
                tuple(getattr(row, column.name) for column in model.__table__.columns)
                for row in db_session.query(model).order_by(model.id)
            ]
            for model in (Transaction, TransactionLine, PaymentAllocation)
        }

    before = snapshot()
    response = client.put(
        f"/api/invoices/{inv['id']}", json={"status": submitted_status}
    )
    assert response.status_code == 200, response.text
    assert snapshot() == before
    invoice = db_session.get(Invoice, inv["id"])
    assert invoice.status.value == expected_status
    assert invoice.total == Decimal("100")
    assert invoice.amount_paid == Decimal(payment_amount)
    assert invoice.balance_due == Decimal(expected_due)
