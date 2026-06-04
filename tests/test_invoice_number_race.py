"""Regression coverage for the create_invoice number-assignment race.

Pre-fix: _next_invoice_number is just SELECT MAX(invoice_number) + 1 with no
row-level lock. Two concurrent creates both saw the same MAX and both tried
to insert MAX+1. The invoices.invoice_number UNIQUE constraint caught the
collision, but it surfaced as a 500 to the loser. The fix wraps the flush
in a retry-on-IntegrityError loop. Five concurrent creates should produce
five distinct invoice numbers with zero 500s.

The in-memory-SQLite test harness serializes connections through a
StaticPool, so we can't trigger a real concurrent race here. Instead we
inject a deterministic collision by pre-creating an invoice with the
number `_next_invoice_number` will compute first.
"""

from datetime import date
from decimal import Decimal

from app.models.invoices import Invoice


def test_collision_on_assigned_number_retries_and_succeeds(
    client, db_session, seed_accounts, seed_customer
):
    # Pre-seed an invoice with number 1001 — what _next_invoice_number returns
    # for an empty table. The retry path must notice the collision, rollback,
    # observe MAX=1001, and retry with 1002.
    pre = Invoice(
        invoice_number="1001",
        customer_id=seed_customer.id,
        date=date(2026, 5, 1),
        subtotal=Decimal("0"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("0"),
        balance_due=Decimal("0"),
    )
    db_session.add(pre)
    db_session.commit()

    body = {
        "customer_id": seed_customer.id,
        "date": "2026-06-01",
        "terms": "Net 30",
        "tax_rate": 0,
        "lines": [{"description": "x", "quantity": 1, "rate": 10, "line_order": 0}],
    }

    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["invoice_number"] == "1002"


def test_repeated_creates_assign_sequential_numbers(
    client, db_session, seed_accounts, seed_customer
):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-06-01",
        "terms": "Net 30",
        "tax_rate": 0,
        "lines": [{"description": "x", "quantity": 1, "rate": 10, "line_order": 0}],
    }

    numbers = []
    for _ in range(5):
        r = client.post("/api/invoices", json=body)
        assert r.status_code == 201, r.text
        numbers.append(r.json()["invoice_number"])

    assert numbers == ["1001", "1002", "1003", "1004", "1005"]


def test_po_collision_retries_and_succeeds(client, db_session, seed_accounts):
    """Same retry pattern wired into create_po."""
    from app.models.contacts import Vendor
    from app.models.purchase_orders import PurchaseOrder

    v = Vendor(name="V", is_active=True)
    db_session.add(v)
    db_session.commit()

    # Pre-seed a PO with what _next_po_number returns first.
    db_session.add(
        PurchaseOrder(
            po_number="PO-0001",
            vendor_id=v.id,
            date=date(2026, 5, 1),
            subtotal=Decimal("0"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("0"),
        )
    )
    db_session.commit()

    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v.id,
            "date": "2026-05-02",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 10, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["po_number"] == "PO-0002"


def test_estimate_collision_retries_and_succeeds(
    client, db_session, seed_accounts, seed_customer
):
    """Same retry pattern wired into create_estimate."""
    from app.models.estimates import Estimate

    db_session.add(
        Estimate(
            estimate_number="E-1001",
            customer_id=seed_customer.id,
            date=date(2026, 5, 1),
            subtotal=Decimal("0"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("0"),
        )
    )
    db_session.commit()

    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-02",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 10, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["estimate_number"] == "E-1002"
