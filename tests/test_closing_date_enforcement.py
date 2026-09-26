"""Closing-date enforcement coverage on every JE-posting path.

A code audit found three ways to side-step the closing-date guard:

  1. POST /api/purchase-orders/{id}/convert-to-bill — posts a dated JE
     but never called check_closing_date.
  2. POST /api/estimates/{id}/convert — same.
  3. POST /api/payroll/{id}/process — same.

Each is a "convert an old document into a JE that lands in the closed
period" loophole. These tests pin the guard so a regression that drops
any check_closing_date call surfaces immediately.

The exhaustive sweep at the bottom covers every direct create route
that takes a user-supplied date and posts a JE. Stripe-payment webhook
is intentionally NOT covered — its JE date is always `date.today()`,
which cannot be backdated by definition.
"""

from decimal import Decimal

import pytest


def _set_closing_date(client, iso: str):
    r = client.put("/api/settings", json={"closing_date": iso})
    assert r.status_code == 200, r.text


def _mk_vendor(db_session, name="V"):
    from app.models.contacts import Vendor

    v = Vendor(name=name, is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def test_po_convert_to_bill_respects_closing_date(client, db_session, seed_accounts):
    from app.models.contacts import Vendor

    v = Vendor(name="V", is_active=True)
    db_session.add(v)
    db_session.commit()

    # Create the PO before setting the closing date so it can sit in the
    # "to be closed" period legitimately.
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v.id,
            "date": "2025-06-15",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    po_id = r.json()["id"]

    _set_closing_date(client, "2025-12-31")

    r = client.post(f"/api/purchase-orders/{po_id}/convert-to-bill")
    assert r.status_code == 403, r.text
    assert "closing" in r.text.lower()


def test_estimate_convert_respects_closing_date(
    client, db_session, seed_accounts, seed_customer
):
    """A converted invoice is dated the day it is made (2.17.3 exploratory
    W-L10), so its journal lands today: converting an old estimate after a
    close posts into the open period, and the guard is checked against
    today."""
    from datetime import date, timedelta

    def estimate():
        r = client.post(
            "/api/estimates",
            json={
                "customer_id": seed_customer.id,
                "date": "2025-06-15",
                "tax_rate": 0,
                "lines": [
                    {"description": "x", "quantity": 1, "rate": 100, "line_order": 0}
                ],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    old, blocked = estimate(), estimate()

    _set_closing_date(client, "2025-12-31")
    r = client.post(f"/api/estimates/{old}/convert")
    assert r.status_code == 200, r.text
    assert r.json()["date"] == date.today().isoformat()

    _set_closing_date(client, (date.today() + timedelta(days=1)).isoformat())
    r = client.post(f"/api/estimates/{blocked}/convert")
    assert r.status_code == 403, r.text


def test_payroll_process_respects_closing_date(client, db_session):
    """A backdated pay run can be created at any time; processing it (which
    posts the payroll JE) must respect the closing date."""
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "T",
            "last_name": "E",
            "ssn_last_four": "1111",
            "filing_status": "single",
            "pay_rate": 25,
            "pay_frequency": "biweekly",
            "state": "WA",
            "hire_date": "2026-01-01",
        },
    ).json()

    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2025-06-01",
            "period_end": "2025-06-14",
            "pay_date": "2025-06-15",
            "run_type": "regular",
            "stubs": [{"employee_id": emp["id"], "hours": 80}],
        },
    ).json()

    _set_closing_date(client, "2025-12-31")

    r = client.post(f"/api/payroll/{run['id']}/process")
    assert r.status_code == 403, r.text


def test_bill_payment_void_respects_closing_date(client, db_session, seed_accounts):
    """Voiding a bill payment posts a dated reversing JE — must also
    respect the closing date, matching the customer-payment void guard."""
    v = _mk_vendor(db_session)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": "2025-06-15",
            "bill_number": "B-VOID-CLOSING",
            "lines": [{"description": "x", "quantity": 1, "rate": 75, "line_order": 0}],
        },
    )
    bill = r.json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2025-06-15",
            "amount": 75.0,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 75.0}],
        },
    )
    bp = r.json()

    _set_closing_date(client, "2025-12-31")

    r = client.post(f"/api/bill-payments/{bp['id']}/void")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Exhaustive sweep — every direct create that takes a user-supplied date
# and posts a JE must reject when the date falls in a closed period.
# ---------------------------------------------------------------------------


_CLOSING_DATE = "2025-12-31"
_BACKDATED = "2025-06-15"  # Inside the closed period


def test_create_invoice_respects_closing_date(
    client, db_session, seed_accounts, seed_customer
):
    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": _BACKDATED,
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 50, "line_order": 0}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_bill_respects_closing_date(client, db_session, seed_accounts):
    v = _mk_vendor(db_session)
    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": _BACKDATED,
            "bill_number": "B-1",
            "lines": [{"description": "x", "quantity": 1, "rate": 50, "line_order": 0}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_payment_respects_closing_date(
    client, db_session, seed_accounts, seed_customer
):
    # An invoice to receive payment against — created BEFORE the closing
    # date so it's a legitimate setup, not a backdoor.
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": _BACKDATED,
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 50, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()

    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": _BACKDATED,
            "amount": 50.0,
            "method": "check",
            "allocations": [{"invoice_id": inv["id"], "amount": 50.0}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_bill_payment_respects_closing_date(client, db_session, seed_accounts):
    v = _mk_vendor(db_session)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": _BACKDATED,
            "bill_number": "B-2",
            "lines": [{"description": "x", "quantity": 1, "rate": 75, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()

    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": _BACKDATED,
            "amount": 75.0,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 75.0}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_credit_memo_respects_closing_date(
    client, db_session, seed_accounts, seed_customer
):
    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": _BACKDATED,
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 25, "line_order": 0}],
        },
    )
    assert r.status_code == 403, r.text


def test_create_journal_entry_respects_closing_date(client, db_session, seed_accounts):
    cash_id = seed_accounts["1000"].id
    rev_id = seed_accounts["4000"].id if "4000" in seed_accounts else None
    if rev_id is None:
        pytest.skip("Revenue account not in seed CoA")

    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/journal",
        json={
            "date": _BACKDATED,
            "description": "Backdated",
            "lines": [
                {"account_id": cash_id, "debit": "10", "credit": "0"},
                {"account_id": rev_id, "debit": "0", "credit": "10"},
            ],
        },
    )
    assert r.status_code == 403, r.text


def test_create_cc_charge_respects_closing_date(client, db_session, seed_accounts):
    # CC charge requires a credit-card liability account.
    cc_id = (
        seed_accounts["2100"].id
        if "2100" in seed_accounts
        else next(iter(seed_accounts.values())).id
    )
    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/cc-charges",
        json={
            "date": _BACKDATED,
            "account_id": cc_id,
            "amount": "12.34",
            "payee": "X",
        },
    )
    assert r.status_code == 403, r.text


def test_create_deposit_respects_closing_date(client, db_session, seed_accounts):
    bank_id = seed_accounts["1000"].id
    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": bank_id,
            "date": _BACKDATED,
            "total": "100",
            "line_ids": [],
        },
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Service-layer enforcement — create_journal_entry() itself is the choke point.
# These pin the guard at the shared accounting layer so non-route posting paths
# (recurring invoices, IIF/QBO imports, inventory hooks) cannot side-step it.
# ---------------------------------------------------------------------------


def _set_closing_date_db(db_session, iso: str):
    from app.models.settings import Settings

    db_session.add(Settings(key="closing_date", value=iso))
    db_session.commit()


def test_create_journal_entry_service_rejects_backdated(db_session, seed_accounts):
    """Direct create_journal_entry() call with a date on/before the closing
    date must raise, even though no route-level check ran."""
    from datetime import date

    from fastapi import HTTPException

    from app.services.accounting import create_journal_entry

    _set_closing_date_db(db_session, _CLOSING_DATE)

    cash_id = seed_accounts["1000"].id
    rev_id = seed_accounts["4000"].id

    with pytest.raises(HTTPException) as exc:
        create_journal_entry(
            db_session,
            date(2025, 6, 15),  # inside the closed period
            "Backdated service-layer JE",
            [
                {"account_id": cash_id, "debit": Decimal("10"), "credit": Decimal("0")},
                {"account_id": rev_id, "debit": Decimal("0"), "credit": Decimal("10")},
            ],
        )
    assert exc.value.status_code == 403
    assert "closing" in str(exc.value.detail).lower()


def test_create_journal_entry_on_closing_date_rejected(db_session, seed_accounts):
    """The boundary case: a JE dated exactly on the closing date is rejected
    (closed period is inclusive of the closing date)."""
    from datetime import date

    from fastapi import HTTPException

    from app.services.accounting import create_journal_entry

    _set_closing_date_db(db_session, _CLOSING_DATE)

    cash_id = seed_accounts["1000"].id
    rev_id = seed_accounts["4000"].id

    with pytest.raises(HTTPException) as exc:
        create_journal_entry(
            db_session,
            date(2025, 12, 31),  # exactly the closing date
            "On-closing-date JE",
            [
                {"account_id": cash_id, "debit": Decimal("5"), "credit": Decimal("0")},
                {"account_id": rev_id, "debit": Decimal("0"), "credit": Decimal("5")},
            ],
        )
    assert exc.value.status_code == 403


def test_create_journal_entry_after_closing_date_ok(db_session, seed_accounts):
    """A JE dated after the closing date posts normally — the guard does not
    over-block open periods."""
    from datetime import date

    from app.services.accounting import create_journal_entry

    _set_closing_date_db(db_session, _CLOSING_DATE)

    cash_id = seed_accounts["1000"].id
    rev_id = seed_accounts["4000"].id

    txn = create_journal_entry(
        db_session,
        date(2026, 1, 1),  # day after the closing date
        "Open-period JE",
        [
            {"account_id": cash_id, "debit": Decimal("7"), "credit": Decimal("0")},
            {"account_id": rev_id, "debit": Decimal("0"), "credit": Decimal("7")},
        ],
    )
    db_session.flush()
    assert txn.id is not None


def test_recurring_service_respects_closing_date(
    db_session, seed_accounts, seed_customer
):
    """The recurring-invoice generator posts JEs via create_journal_entry with
    a date of rec.next_due. If that date is in a closed period, generation must
    be blocked by the shared guard — this path has no route-level check."""
    from datetime import date

    from fastapi import HTTPException

    from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
    from app.services.recurring_service import generate_due_invoices

    rec = RecurringInvoice(
        customer_id=seed_customer.id,
        frequency="monthly",
        start_date=date(2025, 6, 1),
        next_due=date(2025, 6, 1),  # inside the soon-to-be-closed period
        is_active=True,
        terms="Net 30",
        tax_rate=Decimal("0"),
    )
    db_session.add(rec)
    db_session.flush()
    db_session.add(
        RecurringInvoiceLine(
            recurring_invoice_id=rec.id,
            quantity=Decimal("1"),
            rate=Decimal("100.00"),
            description="Service",
            line_order=0,
        )
    )
    db_session.commit()

    _set_closing_date_db(db_session, _CLOSING_DATE)

    with pytest.raises(HTTPException) as exc:
        generate_due_invoices(db_session, as_of=date(2025, 7, 1))
    assert exc.value.status_code == 403


def test_create_batch_payment_respects_closing_date(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": _BACKDATED,
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()

    _set_closing_date(client, _CLOSING_DATE)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": _BACKDATED,
            "method": "check",
            "allocations": [
                {
                    "customer_id": seed_customer.id,
                    "invoice_id": inv["id"],
                    "amount": "40",
                }
            ],
        },
    )
    assert r.status_code == 403, r.text
