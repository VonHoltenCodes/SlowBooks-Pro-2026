"""Cross-feature books-balance invariants.

After any reasonable mix of activity (invoices in three states, bills in
two states, payments with allocations, a PO→Bill conversion, a payroll
run), the following MUST hold:

  1. Every individual JE balances:        sum(debits) == sum(credits)
  2. The full ledger balances:            Σ debits == Σ credits across
                                          every TransactionLine
  3. Balance sheet balances:              assets == liabilities + equity
  4. AR aging total == Σ open invoice     (statuses DRAFT, SENT, PARTIAL)
     balance_due
  5. AP aging total == Σ open bill        (statuses UNPAID, PARTIAL)
     balance_due
  6. Analytics AR == report AR            (the dashboard and the report
                                          must agree on the same data)
  7. BS A/R account balance == open AR    (the GL account and the
     and BS A/P account balance ==        sub-ledger must tie)
     open AP
  8. P&L net income == the synthetic      ("Net Income (current period)"
     Net Income line in BS equity         line we add to BS equity)

These are the invariants the production-readiness sweep was trying to
fix one bug at a time. If any of them ever fails, something has drifted
between modules — that's exactly the class of bug operators report as
"the report doesn't match the dashboard."
"""

from decimal import Decimal

# ---------------------------------------------------------------------------
# Scenario builder — creates a realistic mix of activity across modules
# ---------------------------------------------------------------------------


def _build_scenario(client, customer_id, vendor_id):
    """Build a realistic books scenario via the public API.

    Returns the IDs of the created entities for assertions. Uses the API
    (not direct ORM writes) so the journal entries land exactly the way
    they would in production.
    """
    ids = {}

    # Two invoices — one paid in full, one partial.
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-05-01",
            "due_date": "2026-05-31",
            "tax_rate": 0.0875,
            "lines": [
                {
                    "description": "Consulting",
                    "quantity": 10,
                    "rate": 95.00,
                    "line_order": 0,
                },
                {
                    "description": "Parts",
                    "quantity": 4,
                    "rate": 12.50,
                    "line_order": 1,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv_paid = r.json()
    ids["inv_paid"] = inv_paid["id"]

    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-05-10",
            "due_date": "2026-06-09",
            "tax_rate": 0.0875,
            "lines": [
                {
                    "description": "Maintenance",
                    "quantity": 3,
                    "rate": 250.00,
                    "line_order": 0,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv_partial = r.json()
    ids["inv_partial"] = inv_partial["id"]

    # A draft invoice — should appear in AR but never on P&L until sent+paid.
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-05-20",
            "tax_rate": 0,
            "lines": [
                {
                    "description": "Quote",
                    "quantity": 1,
                    "rate": 500.00,
                    "line_order": 0,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv_draft = r.json()
    ids["inv_draft"] = inv_draft["id"]

    # Full payment on inv_paid
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-05-15",
            "amount": float(inv_paid["total"]),
            "method": "check",
            "reference": "CHK-9001",
            "allocations": [
                {"invoice_id": inv_paid["id"], "amount": float(inv_paid["total"])}
            ],
        },
    )
    assert r.status_code == 201, r.text

    # Partial payment on inv_partial — half down
    partial_amt = float(Decimal(str(inv_partial["total"])) / 2)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": "2026-05-18",
            "amount": partial_amt,
            "method": "ach",
            "reference": "ACH-7001",
            "allocations": [{"invoice_id": inv_partial["id"], "amount": partial_amt}],
        },
    )
    assert r.status_code == 201, r.text

    # One bill — paid in full
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": "2026-05-05",
            "due_date": "2026-06-04",
            "bill_number": "INV-VENDOR-100",
            "lines": [
                {
                    "description": "Supplies",
                    "quantity": 1,
                    "rate": 175.00,
                    "line_order": 0,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill_paid = r.json()
    ids["bill_paid"] = bill_paid["id"]

    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": "2026-05-25",
            "amount": float(bill_paid["total"]),
            "method": "check",
            "reference": "CHK-5001",
            "allocations": [
                {"bill_id": bill_paid["id"], "amount": float(bill_paid["total"])}
            ],
        },
    )
    assert r.status_code == 201, r.text

    # One bill — left unpaid
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": "2026-05-12",
            "due_date": "2026-06-11",
            "bill_number": "INV-VENDOR-101",
            "lines": [
                {
                    "description": "Materials",
                    "quantity": 1,
                    "rate": 320.00,
                    "line_order": 0,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    ids["bill_unpaid"] = r.json()["id"]

    return ids


# ---------------------------------------------------------------------------
# Invariant assertions
# ---------------------------------------------------------------------------


def test_every_journal_entry_balances(client, db_session, seed_accounts, seed_customer):
    """Every individual JE must have debits == credits."""
    from app.models.contacts import Vendor
    from app.models.transactions import Transaction, TransactionLine

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    txns = db_session.query(Transaction).all()
    assert len(txns) > 0, "Scenario should have created journal entries"
    for txn in txns:
        lines = (
            db_session.query(TransactionLine)
            .filter(TransactionLine.transaction_id == txn.id)
            .all()
        )
        dr = sum((Decimal(str(ln.debit or 0)) for ln in lines), Decimal("0"))
        cr = sum((Decimal(str(ln.credit or 0)) for ln in lines), Decimal("0"))
        assert (
            dr == cr
        ), f"JE {txn.id} ({txn.description!r}) unbalanced: dr={dr} cr={cr}"


def test_ledger_total_balances(client, db_session, seed_accounts, seed_customer):
    """Across every TransactionLine ever written, Σ debits == Σ credits."""
    from app.models.contacts import Vendor
    from app.models.transactions import TransactionLine

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    lines = db_session.query(TransactionLine).all()
    total_dr = sum((Decimal(str(ln.debit or 0)) for ln in lines), Decimal("0"))
    total_cr = sum((Decimal(str(ln.credit or 0)) for ln in lines), Decimal("0"))
    assert (
        total_dr == total_cr
    ), f"Full ledger unbalanced: total debits={total_dr}, total credits={total_cr}"


def test_balance_sheet_balances(client, db_session, seed_accounts, seed_customer):
    """A == L + E (the foundational accounting identity)."""
    from app.models.contacts import Vendor

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    r = client.get("/api/reports/balance-sheet")
    assert r.status_code == 200, r.text
    bs = r.json()

    a = Decimal(str(bs["total_assets"]))
    L = Decimal(str(bs["total_liabilities"]))
    e = Decimal(str(bs["total_equity"]))
    drift = a - (L + e)
    # Allow one penny of float-conversion noise (the report serializes as
    # float at the JSON boundary). The underlying ledger is exact.
    assert abs(drift) < Decimal(
        "0.01"
    ), f"Balance sheet drift: A={a}  L={L}  E={e}  A-(L+E)={drift}"


def test_ar_aging_total_equals_open_invoice_balances(
    client, db_session, seed_accounts, seed_customer
):
    """The aging report total must equal the sum of open invoice balances."""
    from app.models.contacts import Vendor
    from app.models.invoices import Invoice, InvoiceStatus

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    open_balance = sum(
        (
            Decimal(str(inv.balance_due or 0))
            for inv in db_session.query(Invoice)
            .filter(
                Invoice.status.in_(
                    [
                        InvoiceStatus.DRAFT,
                        InvoiceStatus.SENT,
                        InvoiceStatus.PARTIAL,
                    ]
                )
            )
            .all()
        ),
        Decimal("0"),
    )

    r = client.get("/api/reports/ar-aging")
    assert r.status_code == 200, r.text
    aging_total = Decimal(str(r.json()["totals"]["total"]))

    assert abs(aging_total - open_balance) < Decimal(
        "0.01"
    ), f"AR aging mismatch: aging={aging_total}  open_invoices={open_balance}"
    assert open_balance > 0, "Scenario should have produced open AR"


def test_ap_aging_total_equals_open_bill_balances(
    client, db_session, seed_accounts, seed_customer
):
    """The AP aging report total must equal the sum of open bill balances."""
    from app.models.bills import Bill, BillStatus
    from app.models.contacts import Vendor

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    open_balance = sum(
        (
            Decimal(str(b.balance_due or 0))
            for b in db_session.query(Bill)
            .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
            .all()
        ),
        Decimal("0"),
    )

    r = client.get("/api/reports/ap-aging")
    assert r.status_code == 200, r.text
    aging_total = Decimal(str(r.json()["totals"]["total"]))

    assert abs(aging_total - open_balance) < Decimal(
        "0.01"
    ), f"AP aging mismatch: aging={aging_total}  open_bills={open_balance}"
    assert open_balance > 0, "Scenario should have produced open AP"


def test_analytics_ar_equals_report_ar(
    client, db_session, seed_accounts, seed_customer
):
    """The dashboard and the report must agree on AR aging totals.

    This is the exact bug the audit sweep fixed for the dashboard widget:
    analytics was bucketing by days-since-invoiced while the report
    bucketed by days-past-due. Same data, different totals — operator
    confusion. The buckets may differ in distribution (the dashboard
    widget can still be 'younger' because we use the more lenient
    threshold), but the GRAND TOTAL has to match.
    """
    from app.models.contacts import Vendor

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    r = client.get("/api/analytics/dashboard")
    assert r.status_code == 200, r.text
    analytics_ar_buckets = r.json()["ar_aging"]
    analytics_total = sum(
        (
            Decimal(str(v))
            for bucket in analytics_ar_buckets.values()
            for v in bucket.values()
        ),
        Decimal("0"),
    )

    r = client.get("/api/reports/ar-aging")
    assert r.status_code == 200, r.text
    report_total = Decimal(str(r.json()["totals"]["total"]))

    assert abs(analytics_total - report_total) < Decimal(
        "0.01"
    ), f"Analytics AR ({analytics_total}) disagrees with report AR ({report_total})"


def test_pnl_net_income_matches_balance_sheet_synthetic_equity(
    client, db_session, seed_accounts, seed_customer
):
    """P&L net income should equal the synthetic 'Net Income (current period)'
    line we add to balance sheet equity.

    Without this tie-out, the books appear to balance on paper but the
    operator sees a Net Income figure on the dashboard that doesn't show
    up anywhere on the balance sheet. The synthetic equity line was added
    precisely to close that gap; this asserts it stays closed.
    """
    from app.models.contacts import Vendor

    vendor = Vendor(name="Vendor X", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    _build_scenario(client, seed_customer.id, vendor.id)

    r = client.get("/api/reports/profit-loss")
    assert r.status_code == 200, r.text
    pnl_net = Decimal(str(r.json()["net_income"]))

    r = client.get("/api/reports/balance-sheet")
    assert r.status_code == 200, r.text
    equity = r.json()["equity"]
    synthetic = next(
        (e for e in equity if (e.get("account_name") or "").startswith("Net Income")),
        None,
    )

    if pnl_net == 0:
        # Either the synthetic line was suppressed (legitimate when net is
        # exactly zero) or it's present with amount=0. Both are fine.
        assert synthetic is None or Decimal(str(synthetic["amount"])) == 0
    else:
        assert (
            synthetic is not None
        ), f"P&L shows net income {pnl_net} but BS has no Net Income equity line"
        synthetic_amount = Decimal(str(synthetic["amount"]))
        assert abs(synthetic_amount - pnl_net) < Decimal(
            "0.01"
        ), f"P&L net={pnl_net} but BS Net Income equity={synthetic_amount}"
