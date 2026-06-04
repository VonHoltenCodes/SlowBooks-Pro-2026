"""Regression tests for bugs in the invoice edit path.

All tests here should FAIL against the pre-fix code and PASS after the fix.
"""

from decimal import Decimal


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


def test_editing_invoice_total_below_amount_paid_clamps_balance_due_at_zero(
    client, db_session, seed_accounts, seed_customer
):
    inv = _create_invoice(client, seed_customer.id, amount="100.00")

    client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-02",
            "amount": "60.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "60.00"}],
        },
    )

    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {
                    "description": "Service",
                    "quantity": "1",
                    "rate": "40.00",
                    "line_order": 0,
                }
            ],
            "tax_rate": "0",
        },
    )
    assert r.status_code == 200, r.text

    from app.models.invoices import Invoice

    db_session.expire_all()
    invoice = db_session.query(Invoice).filter_by(id=inv["id"]).first()
    assert invoice.total == Decimal("40.00")
    assert invoice.amount_paid == Decimal("60.00")
    assert invoice.balance_due >= Decimal("0.00"), (
        "balance_due went negative: an invoice with $60 paid shouldn't get negative "
        "balance when its total is edited down to $40"
    )


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
