"""Input-validation regression tests.

Live-audit revealed several routes silently accepted bad data:
  - Empty `lines` list -> $0 invoice/bill/PO/estimate with a JE attempt
  - Negative line quantity -> off-books obligation (bill with no JE, negative total)
  - Negative invoice line qty -> 500 from create_journal_entry
  - Negative payment amount -> 500 from create_journal_entry
  - Duplicate vendor+bill_number -> duplicate AP rows
These tests pin those each to a 4xx response with a useful detail.
"""


def _vendor(db_session, name="V"):
    from app.models.contacts import Vendor

    v = Vendor(name=name, is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


# ---------------------------------------------------------------------------
# Empty lines rejected
# ---------------------------------------------------------------------------


def test_invoice_create_rejects_empty_lines(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [],
        },
    )
    assert r.status_code == 422, r.text


def test_bill_create_rejects_empty_lines(client, db_session, seed_accounts):
    vendor = _vendor(db_session)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "bill_number": "B-EMPTY",
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [],
        },
    )
    assert r.status_code == 422, r.text


def test_po_create_rejects_empty_lines(client, db_session, seed_accounts):
    vendor = _vendor(db_session)
    r = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "date": "2026-05-01", "tax_rate": 0, "lines": []},
    )
    assert r.status_code == 422, r.text


def test_estimate_create_rejects_empty_lines(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [],
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Negative line quantities / rates rejected at schema validation
# ---------------------------------------------------------------------------


def test_invoice_create_rejects_negative_quantity(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [
                {"description": "refund?", "quantity": -1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "non-negative" in r.text or "credit memo" in r.text


def test_bill_create_rejects_negative_quantity(client, db_session, seed_accounts):
    """Pre-fix: -1 qty on a bill silently created a bill with no JE and a
    negative balance_due — an off-books obligation."""
    vendor = _vendor(db_session)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "bill_number": "B-NEG",
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": -1, "rate": 50, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_invoice_create_rejects_negative_rate(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [
                {
                    "description": "discount?",
                    "quantity": 1,
                    "rate": -25,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Payment guardrails
# ---------------------------------------------------------------------------


def test_payment_rejects_non_positive_amount(
    client, db_session, seed_accounts, seed_customer
):
    """Pre-fix: a negative payment amount reached create_journal_entry and
    surfaced as a 500."""
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "amount": -50,
            "allocations": [],
        },
    )
    assert r.status_code == 400, r.text
    assert "positive" in r.text.lower()


def test_payment_rejects_zero_amount(client, db_session, seed_accounts, seed_customer):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "amount": 0,
            "allocations": [],
        },
    )
    assert r.status_code == 400, r.text


def test_payment_rejects_negative_allocation(
    client, db_session, seed_accounts, seed_customer
):
    # First create a real invoice
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()

    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "amount": 100,
            "allocations": [{"invoice_id": inv["id"], "amount": -50}],
        },
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Duplicate vendor+bill_number
# ---------------------------------------------------------------------------


def test_bill_create_rejects_duplicate_vendor_bill_number(
    client, db_session, seed_accounts
):
    vendor = _vendor(db_session)
    body = {
        "vendor_id": vendor.id,
        "bill_number": "OD-100",
        "date": "2026-05-01",
        "tax_rate": 0,
        "lines": [{"description": "x", "quantity": 1, "rate": 50, "line_order": 0}],
    }
    r1 = client.post("/api/bills", json=body)
    assert r1.status_code == 201, r1.text

    r2 = client.post("/api/bills", json=body)
    assert r2.status_code == 409, r2.text
    assert "already exists" in r2.text


def test_bill_same_number_different_vendors_is_allowed(
    client, db_session, seed_accounts
):
    """Vendors number their own invoices — same number from different vendors
    is normal."""
    v1 = _vendor(db_session, name="V1")
    v2 = _vendor(db_session, name="V2")
    line = {"description": "x", "quantity": 1, "rate": 50, "line_order": 0}

    r1 = client.post(
        "/api/bills",
        json={
            "vendor_id": v1.id,
            "bill_number": "INV-1",
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [line],
        },
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/api/bills",
        json={
            "vendor_id": v2.id,
            "bill_number": "INV-1",
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [line],
        },
    )
    assert r2.status_code == 201, r2.text


# ---------------------------------------------------------------------------
# Payroll input validation
# ---------------------------------------------------------------------------


def _employee(client):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Test",
            "last_name": "E",
            "ssn": "123-45-6789",
            "filing_status": "single",
            "pay_rate": 25,
            "pay_frequency": "biweekly",
            "state": "WA",
            "date_of_hire": "2026-01-01",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_payroll_rejects_negative_hours(client, db_session):
    emp_id = _employee(client)
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-05-01",
            "period_end": "2026-05-14",
            "pay_date": "2026-05-15",
            "run_type": "regular",
            "stubs": [{"employee_id": emp_id, "hours": -40}],
        },
    )
    assert r.status_code == 422, r.text


def test_payroll_rejects_negative_overtime(client, db_session):
    emp_id = _employee(client)
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-05-01",
            "period_end": "2026-05-14",
            "pay_date": "2026-05-15",
            "run_type": "regular",
            "stubs": [{"employee_id": emp_id, "hours": 80, "overtime_hours": -10}],
        },
    )
    assert r.status_code == 422, r.text


def test_payroll_rejects_negative_gross_override(client, db_session):
    emp_id = _employee(client)
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-05-01",
            "period_end": "2026-05-14",
            "pay_date": "2026-05-15",
            "run_type": "regular",
            "stubs": [{"employee_id": emp_id, "hours": 0, "gross_override": -100}],
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Pagination bounds
# ---------------------------------------------------------------------------


def test_list_invoices_clamps_negative_limit(
    client, db_session, seed_accounts, seed_customer
):
    # Seed a couple invoices so pagination has rows to return
    for _ in range(3):
        client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-05-01",
                "terms": "Net 30",
                "tax_rate": 0,
                "lines": [
                    {"description": "x", "quantity": 1, "rate": 10, "line_order": 0}
                ],
            },
        )

    # Pre-fix: limit=-1 reached SQLAlchemy's .limit(-1) which returns all
    # rows; skip=-1 reached .offset(-1) which is undefined behavior. Both
    # should be clamped to a sane range.
    r = client.get("/api/invoices?limit=-1")
    assert r.status_code == 200
    assert len(r.json()) >= 1  # clamped to >= 1, not 0 or all

    r = client.get("/api/invoices?skip=-5")
    assert r.status_code == 200
    assert len(r.json()) == 3  # clamped offset to 0


def test_list_invoices_clamps_huge_limit(
    client, db_session, seed_accounts, seed_customer
):
    client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "terms": "Net 30",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 10, "line_order": 0}],
        },
    )
    r = client.get("/api/invoices?limit=999999")
    assert r.status_code == 200  # clamped to upper bound, not 422
