"""Void-reversal symmetry invariants.

Voiding any posted document MUST restore the ledger to a books-balanced
state. Specifically:

  1. Payment void: invoice's balance_due returns to pre-payment value;
     amount_paid drops by the voided amount; the reversing JE balances
     and the full ledger Σ debits == Σ credits.
  2. Bill-payment void: bill's balance_due returns; ledger still
     balances.
  3. Invoice void (with no payments applied): balance_due goes to zero;
     status becomes VOID; reversing JE posts and the full ledger
     remains balanced.
  4. Invoice void with payments applied: the route must REJECT — voiding
     would double-reverse A/R. (This guard is in the production code;
     the test pins it.)

The general property we're enforcing: void + posted == zero, and the
ledger stays balanced no matter how many void cycles happen.
"""

from decimal import Decimal


def _ledger_balanced(db_session) -> tuple[Decimal, Decimal]:
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).all()
    dr = sum((Decimal(str(ln.debit or 0)) for ln in lines), Decimal("0"))
    cr = sum((Decimal(str(ln.credit or 0)) for ln in lines), Decimal("0"))
    return dr, cr


def _mk_vendor(db_session, name="V"):
    from app.models.contacts import Vendor

    v = Vendor(name=name, is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def test_payment_void_restores_invoice_balance(
    client, db_session, seed_accounts, seed_customer
):
    """A payment void reverses the cash-receipt JE and restores AR."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 200, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    inv_total = Decimal(str(inv["total"]))

    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-15",
            "amount": float(inv_total),
            "method": "check",
            "allocations": [{"invoice_id": inv["id"], "amount": float(inv_total)}],
        },
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]

    # Pre-void: invoice is paid, ledger balanced
    r = client.get(f"/api/invoices/{inv['id']}")
    paid_state = r.json()
    assert paid_state["status"] == "paid"
    assert Decimal(str(paid_state["balance_due"])) == Decimal("0")
    dr, cr = _ledger_balanced(db_session)
    assert dr == cr

    # Void the payment
    r = client.post(f"/api/payments/{payment_id}/void")
    assert r.status_code == 200, r.text

    # Post-void: invoice balance restored, ledger STILL balanced
    r = client.get(f"/api/invoices/{inv['id']}")
    restored = r.json()
    assert Decimal(str(restored["balance_due"])) == inv_total, (
        f"void should restore full balance: got {restored['balance_due']}, "
        f"expected {inv_total}"
    )
    assert restored["status"] != "paid"

    dr, cr = _ledger_balanced(db_session)
    assert dr == cr, f"ledger imbalanced after payment void: dr={dr} cr={cr}"


def test_partial_payment_void_restores_partial_balance(
    client, db_session, seed_accounts, seed_customer
):
    """A partial-payment void restores only the voided portion."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 300, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()

    # First payment: $100 of $300
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-10",
            "amount": 100.0,
            "method": "check",
            "reference": "P1",
            "allocations": [{"invoice_id": inv["id"], "amount": 100.0}],
        },
    )
    p1 = r.json()

    # Second payment: $50 of remaining $200
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-15",
            "amount": 50.0,
            "method": "check",
            "reference": "P2",
            "allocations": [{"invoice_id": inv["id"], "amount": 50.0}],
        },
    )
    assert r.status_code == 201, r.text

    # State after both payments
    r = client.get(f"/api/invoices/{inv['id']}")
    assert Decimal(str(r.json()["balance_due"])) == Decimal("150")
    assert Decimal(str(r.json()["amount_paid"])) == Decimal("150")

    # Void only the first payment
    r = client.post(f"/api/payments/{p1['id']}/void")
    assert r.status_code == 200, r.text

    r = client.get(f"/api/invoices/{inv['id']}")
    after = r.json()
    # P1 ($100) gone → balance back up to 250, paid down to 50
    assert Decimal(str(after["balance_due"])) == Decimal("250")
    assert Decimal(str(after["amount_paid"])) == Decimal("50")

    dr, cr = _ledger_balanced(db_session)
    assert dr == cr


def test_bill_payment_void_restores_bill_balance(client, db_session, seed_accounts):
    """Voiding a bill payment posts a reversing JE and restores the bill's
    open balance — AP-side mirror of test_payment_void_restores_invoice_balance.
    """
    v = _mk_vendor(db_session)

    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": "2026-05-05",
            "bill_number": "B-VOID-1",
            "lines": [
                {"description": "x", "quantity": 1, "rate": 175, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()

    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2026-05-20",
            "amount": 175.0,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 175.0}],
        },
    )
    assert r.status_code == 201, r.text
    bp = r.json()

    # Pre-void: bill is paid, ledger balanced
    r = client.get(f"/api/bills/{bill['id']}")
    assert r.json()["status"] == "paid"
    dr, cr = _ledger_balanced(db_session)
    assert dr == cr

    # Void the bill payment
    r = client.post(f"/api/bill-payments/{bp['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["is_voided"] is True

    # Bill balance restored, ledger still balanced
    r = client.get(f"/api/bills/{bill['id']}")
    restored = r.json()
    assert Decimal(str(restored["balance_due"])) == Decimal("175.00")
    assert restored["status"] != "paid"

    dr, cr = _ledger_balanced(db_session)
    assert dr == cr, f"ledger imbalanced after bill-payment void: dr={dr} cr={cr}"

    # Double-void idempotency
    r = client.post(f"/api/bill-payments/{bp['id']}/void")
    assert r.status_code == 400, "second void of same bill payment must reject"


def test_invoice_void_without_payments_zeros_balance_and_keeps_ledger_balanced(
    client, db_session, seed_accounts, seed_customer
):
    """Voiding an unpaid invoice should: status→VOID, balance_due→0, and
    the ledger must remain balanced (reversing JE posts cleanly)."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 400, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()

    dr_before, cr_before = _ledger_balanced(db_session)
    assert dr_before == cr_before

    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text
    voided = r.json()
    assert voided["status"] == "void"
    assert Decimal(str(voided["balance_due"])) == Decimal("0")

    dr_after, cr_after = _ledger_balanced(db_session)
    assert (
        dr_after == cr_after
    ), f"ledger imbalanced after invoice void: dr={dr_after} cr={cr_after}"


def test_invoice_void_with_payments_rejects_to_protect_ledger(
    client, db_session, seed_accounts, seed_customer
):
    """A paid invoice cannot be voided directly — the payment's cash-receipt
    JE would still be on the books while the invoice's reversing JE would
    double-credit A/R. The route must require the payment(s) be voided
    first."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 60, "line_order": 0}],
        },
    )
    inv = r.json()

    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-10",
            "amount": 60.0,
            "method": "check",
            "allocations": [{"invoice_id": inv["id"], "amount": 60.0}],
        },
    )
    assert r.status_code == 201, r.text

    # Try to void the paid invoice — must be rejected
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code in (
        400,
        409,
    ), f"voiding a paid invoice should be rejected; got {r.status_code} {r.text}"

    # Ledger still balanced (the failed void didn't leave the books bad)
    dr, cr = _ledger_balanced(db_session)
    assert dr == cr


def test_double_void_is_idempotent_or_rejected(
    client, db_session, seed_accounts, seed_customer
):
    """Voiding an already-voided payment must NOT post a second reversing
    JE (which would double-credit AR back). Either reject with 400 or
    treat as a no-op — both are safe outcomes."""
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 80, "line_order": 0}],
        },
    )
    inv = r.json()

    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-05-10",
            "amount": 80.0,
            "method": "check",
            "allocations": [{"invoice_id": inv["id"], "amount": 80.0}],
        },
    )
    payment_id = r.json()["id"]

    r = client.post(f"/api/payments/{payment_id}/void")
    assert r.status_code == 200, r.text
    dr1, cr1 = _ledger_balanced(db_session)

    r = client.post(f"/api/payments/{payment_id}/void")
    assert r.status_code in (
        200,
        400,
    ), f"second void of same payment should reject or no-op; got {r.status_code}"

    # Ledger unchanged either way — no second reversing JE.
    dr2, cr2 = _ledger_balanced(db_session)
    assert (dr1, cr1) == (dr2, cr2), "double-void must not post a second reversing JE"
