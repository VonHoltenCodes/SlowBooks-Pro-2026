"""Rounding-drift coverage for bill / PO / estimate / invoice routes.

The shared bug class: stored line.amount columns drift from invoice/bill/PO
subtotal columns when qty*rate produces sub-cent values (e.g. qty=1.5,
rate=33.33 -> 49.995). The fix routes every per-line money calculation
through accounting._q so each stored line amount matches both the subtotal
helper (compute_line_totals) and the journal entries it produces.
"""

from decimal import Decimal


def _vendor(db_session):
    from app.models.contacts import Vendor

    v = Vendor(name="Test Vendor", is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def _sum_decimal(rows, attr):
    return sum((Decimal(str(getattr(r, attr))) for r in rows), Decimal("0"))


def _debit_credit(db_session, txn_id):
    from app.models.transactions import TransactionLine

    rows = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return _sum_decimal(rows, "debit"), _sum_decimal(rows, "credit")


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


def test_bill_subcent_lines_match_stored_subtotal(client, db_session, seed_accounts):
    vendor = _vendor(db_session)
    body = {
        "vendor_id": vendor.id,
        "bill_number": "B-1",
        "date": "2026-04-01",
        "tax_rate": 0,
        "lines": [
            {"description": "Half A", "quantity": 1.5, "rate": 33.33, "line_order": 0},
            {"description": "Half B", "quantity": 1.5, "rate": 33.33, "line_order": 1},
        ],
    }
    r = client.post("/api/bills", json=body)
    assert r.status_code == 201, r.text
    bill_id = r.json()["id"]

    from app.models.bills import Bill, BillLine

    db_session.expire_all()
    bill = db_session.query(Bill).filter_by(id=bill_id).first()
    lines = db_session.query(BillLine).filter_by(bill_id=bill_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert (
        bill.subtotal == sum_lines
    ), f"bill.subtotal {bill.subtotal} != sum(line.amount) {sum_lines}"

    if bill.transaction_id:
        dr, cr = _debit_credit(db_session, bill.transaction_id)
        assert dr == cr, f"bill JE unbalanced: debits={dr} credits={cr}"


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------


def test_po_subcent_lines_match_stored_subtotal(client, db_session, seed_accounts):
    vendor = _vendor(db_session)
    body = {
        "vendor_id": vendor.id,
        "date": "2026-04-01",
        "tax_rate": 0,
        "lines": [
            {"description": "Widget", "quantity": 1.5, "rate": 33.33, "line_order": 0},
            {"description": "Gadget", "quantity": 1.5, "rate": 33.33, "line_order": 1},
        ],
    }
    r = client.post("/api/purchase-orders", json=body)
    assert r.status_code == 201, r.text
    po_id = r.json()["id"]

    from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine

    db_session.expire_all()
    po = db_session.query(PurchaseOrder).filter_by(id=po_id).first()
    lines = db_session.query(PurchaseOrderLine).filter_by(purchase_order_id=po_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert (
        po.subtotal == sum_lines
    ), f"po.subtotal {po.subtotal} != sum(line.amount) {sum_lines}"


def test_po_convert_to_bill_uses_rounded_amounts(client, db_session, seed_accounts):
    """PO→Bill must produce a balanced JE even with sub-cent line totals."""
    vendor = _vendor(db_session)
    po_body = {
        "vendor_id": vendor.id,
        "date": "2026-04-01",
        "tax_rate": 0,
        "lines": [
            {"description": "Half A", "quantity": 1.5, "rate": 33.33, "line_order": 0},
            {"description": "Half B", "quantity": 1.5, "rate": 33.33, "line_order": 1},
        ],
    }
    r = client.post("/api/purchase-orders", json=po_body)
    assert r.status_code == 201, r.text
    po_id = r.json()["id"]

    r = client.post(f"/api/purchase-orders/{po_id}/convert-to-bill")
    assert r.status_code == 200, r.text
    bill_id = r.json()["bill_id"]

    from app.models.bills import Bill

    db_session.expire_all()
    bill = db_session.query(Bill).filter_by(id=bill_id).first()
    assert bill.transaction_id is not None

    dr, cr = _debit_credit(db_session, bill.transaction_id)
    assert dr == cr, f"PO→Bill JE unbalanced: debits={dr} credits={cr}"


# ---------------------------------------------------------------------------
# Estimates
# ---------------------------------------------------------------------------


def test_estimate_subcent_lines_match_stored_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-04-01",
        "tax_rate": 0,
        "lines": [
            {"description": "Half A", "quantity": 1.5, "rate": 33.33, "line_order": 0},
            {"description": "Half B", "quantity": 1.5, "rate": 33.33, "line_order": 1},
        ],
    }
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    est_id = r.json()["id"]

    from app.models.estimates import Estimate, EstimateLine

    db_session.expire_all()
    est = db_session.query(Estimate).filter_by(id=est_id).first()
    lines = db_session.query(EstimateLine).filter_by(estimate_id=est_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert (
        est.subtotal == sum_lines
    ), f"estimate.subtotal {est.subtotal} != sum(line.amount) {sum_lines}"


def test_estimate_update_subcent_lines_match_stored_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    create = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": 0,
            "lines": [
                {"description": "Old", "quantity": 1, "rate": 10, "line_order": 0}
            ],
        },
    )
    est_id = create.json()["id"]

    update = client.put(
        f"/api/estimates/{est_id}",
        json={
            "lines": [
                {
                    "description": "Half A",
                    "quantity": 1.5,
                    "rate": 33.33,
                    "line_order": 0,
                },
                {
                    "description": "Half B",
                    "quantity": 1.5,
                    "rate": 33.33,
                    "line_order": 1,
                },
            ]
        },
    )
    assert update.status_code == 200, update.text

    from app.models.estimates import Estimate, EstimateLine

    db_session.expire_all()
    est = db_session.query(Estimate).filter_by(id=est_id).first()
    lines = db_session.query(EstimateLine).filter_by(estimate_id=est_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert est.subtotal == sum_lines


# ---------------------------------------------------------------------------
# Invoices — the JE side was already fixed; this test pins the InvoiceLine
# storage side. Pre-fix the JE used _q() but the stored line.amount cells
# kept the unrounded product, drifting from subtotal.
# ---------------------------------------------------------------------------


def test_invoice_subcent_lines_match_stored_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": 0,
        "lines": [
            {"description": "Half A", "quantity": 1.5, "rate": 33.33, "line_order": 0},
            {"description": "Half B", "quantity": 1.5, "rate": 33.33, "line_order": 1},
        ],
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]

    from app.models.invoices import Invoice, InvoiceLine

    db_session.expire_all()
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    lines = db_session.query(InvoiceLine).filter_by(invoice_id=inv_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert (
        inv.subtotal == sum_lines
    ), f"invoice.subtotal {inv.subtotal} != sum(line.amount) {sum_lines}"

    dr, cr = _debit_credit(db_session, inv.transaction_id)
    assert dr == cr


def test_invoice_update_subcent_lines_match_stored_subtotal(
    client, db_session, seed_accounts, seed_customer
):
    create = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [
                {"description": "Old", "quantity": 1, "rate": 10, "line_order": 0}
            ],
        },
    )
    inv_id = create.json()["id"]

    update = client.put(
        f"/api/invoices/{inv_id}",
        json={
            "lines": [
                {
                    "description": "Half A",
                    "quantity": 1.5,
                    "rate": 33.33,
                    "line_order": 0,
                },
                {
                    "description": "Half B",
                    "quantity": 1.5,
                    "rate": 33.33,
                    "line_order": 1,
                },
            ]
        },
    )
    assert update.status_code == 200, update.text

    from app.models.invoices import Invoice, InvoiceLine

    db_session.expire_all()
    inv = db_session.query(Invoice).filter_by(id=inv_id).first()
    lines = db_session.query(InvoiceLine).filter_by(invoice_id=inv_id).all()

    sum_lines = _sum_decimal(lines, "amount")
    assert inv.subtotal == sum_lines
    dr, cr = _debit_credit(db_session, inv.transaction_id)
    assert dr == cr
