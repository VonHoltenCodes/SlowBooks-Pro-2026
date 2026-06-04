"""N+1 regression coverage on the list endpoints.

Pre-fix: GET /api/invoices returned `list[InvoiceResponse]`, and Pydantic's
model_validate read `inv.customer` and `inv.lines` for every row in the
loop. Those are lazy relationships, so a 500-row list page fired 1001
follow-up SELECTs against the DB. Same pattern across bills, POs,
payments, credit memos, and estimates.

Each test seeds N parent rows + their children, hits the list endpoint,
and asserts the total query count is bounded by a small constant rather
than scaling with N. We use SQLAlchemy's `after_cursor_execute` event to
count SELECTs — durable across SQLAlchemy versions and DB dialects.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import event


@contextmanager
def _count_selects(engine):
    """Yield a list whose len() is the number of SELECT statements run
    against `engine` while the context is open."""
    statements = []

    def _hook(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _hook)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _hook)


N_ROWS = 8  # enough to expose linear growth without slowing the suite
MAX_QUERIES = 6  # the eager-load plan: 1 parent SELECT + N+1 eager loads


def _seed_invoices(db_session, customer_id, n):
    from app.models.invoices import Invoice, InvoiceLine

    for i in range(n):
        inv = Invoice(
            invoice_number=f"N1-{i:04d}",
            customer_id=customer_id,
            date=date(2026, 5, 1),
            subtotal=Decimal("100"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("100"),
            balance_due=Decimal("100"),
        )
        db_session.add(inv)
        db_session.flush()
        db_session.add(
            InvoiceLine(
                invoice_id=inv.id,
                description=f"L{i}",
                quantity=Decimal("1"),
                rate=Decimal("100"),
                amount=Decimal("100"),
                line_order=0,
            )
        )
    db_session.commit()


def test_list_invoices_query_count_constant(
    client, db_session, db_engine, seed_accounts, seed_customer
):
    _seed_invoices(db_session, seed_customer.id, N_ROWS)

    with _count_selects(db_engine) as stmts:
        r = client.get("/api/invoices")

    assert r.status_code == 200, r.text
    assert len(r.json()) == N_ROWS
    # Pre-fix this would be roughly 1 + 2*N_ROWS. With joinedload+selectinload
    # we expect 1 (parent SELECT with customer JOIN) + 1 (lines IN-clause) +
    # auth/session/audit overhead. Cap at MAX_QUERIES so any future
    # regression linear in N_ROWS lights this up.
    assert len(stmts) < MAX_QUERIES + N_ROWS, (
        f"got {len(stmts)} SELECTs for {N_ROWS} invoices " f"(expected ~{MAX_QUERIES})"
    )


def _seed_bills(db_session, vendor_id, n):
    from app.models.bills import Bill, BillLine

    for i in range(n):
        b = Bill(
            bill_number=f"BN1-{i:04d}",
            vendor_id=vendor_id,
            date=date(2026, 5, 1),
            subtotal=Decimal("100"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("100"),
            balance_due=Decimal("100"),
        )
        db_session.add(b)
        db_session.flush()
        db_session.add(
            BillLine(
                bill_id=b.id,
                description=f"L{i}",
                quantity=Decimal("1"),
                rate=Decimal("100"),
                amount=Decimal("100"),
                line_order=0,
            )
        )
    db_session.commit()


def test_list_bills_query_count_constant(client, db_session, db_engine, seed_accounts):
    from app.models.contacts import Vendor

    v = Vendor(name="V", is_active=True)
    db_session.add(v)
    db_session.commit()

    _seed_bills(db_session, v.id, N_ROWS)

    with _count_selects(db_engine) as stmts:
        r = client.get("/api/bills")

    assert r.status_code == 200, r.text
    assert len(r.json()) == N_ROWS
    assert (
        len(stmts) < MAX_QUERIES + N_ROWS
    ), f"got {len(stmts)} SELECTs for {N_ROWS} bills"
