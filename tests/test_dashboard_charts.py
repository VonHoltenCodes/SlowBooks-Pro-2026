"""Regression tests for the monthly_revenue dashboard widget (/api/dashboard/data).

Covers the monthly_revenue fix: previously summed Invoice.total, which
made non-invoice income (W-2 deposits, paychecks, journal-entered
consulting income) invisible on the dashboard. New behavior sums
transaction_lines.credit against any account whose account_type is
INCOME, so the general ledger is the source of truth.
"""

from datetime import date
from decimal import Decimal


def _post_journal_credit(
    db_session, *, income_account_id, amount, when, contra_account_id
):
    """Insert a balanced journal entry that credits an INCOME account.

    Mirrors what the IIF importer and manual journal route emit: one
    credit on the income side, one offsetting debit on a balance-sheet
    asset (typically a checking account). Returns the Transaction.
    """
    from app.models.transactions import Transaction, TransactionLine

    txn = Transaction(
        date=when,
        reference="TEST",
        description="regression: journal-entered income",
        source_type="journal",
    )
    db_session.add(txn)
    db_session.flush()

    db_session.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=contra_account_id,
                debit=Decimal(str(amount)),
                credit=Decimal("0"),
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=income_account_id,
                debit=Decimal("0"),
                credit=Decimal(str(amount)),
            ),
        ]
    )
    db_session.commit()
    return txn


def _income_and_asset_account_ids(seed_accounts):
    """Pick any INCOME account + any ASSET account from the seeded COA."""
    from app.models.accounts import AccountType

    income = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.INCOME
    )
    asset = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.ASSET
    )
    return income.id, asset.id


def _current_month_label():
    return date.today().strftime("%b")


def test_monthly_revenue_picks_up_journal_credits_to_income(
    client,
    db_session,
    seed_accounts,
):
    """A journal entry that credits an INCOME account must surface in
    monthly_revenue — this is the W-2/paycheck case the previous
    invoice-only query was silently dropping."""
    income_id, asset_id = _income_and_asset_account_ids(seed_accounts)
    _post_journal_credit(
        db_session,
        income_account_id=income_id,
        amount="17000.00",
        when=date.today(),
        contra_account_id=asset_id,
    )

    resp = client.get("/api/dashboard/data?ids=monthly_revenue")
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert "monthly_revenue" in payload
    months = payload["monthly_revenue"]["months"]
    assert len(months) == 12, "monthly_revenue must always have 12 entries"
    # Current month is the LAST slot in the response, per the back-counting
    # loop in the route handler.
    current = months[-1]
    assert current["month"] == _current_month_label()
    assert current["amount"] == 17000.00


def test_monthly_revenue_ignores_invoice_balance_due(
    client,
    db_session,
    seed_accounts,
):
    """Invoices that have NOT been posted to the journal must NOT appear
    in monthly_revenue — the previous implementation summed Invoice.total
    directly regardless of whether the invoice had been posted. The new
    implementation only sees what landed in the general ledger."""
    from app.models.contacts import Customer
    from app.models.invoices import Invoice, InvoiceStatus

    cust = Customer(name="Acme", is_active=True)
    db_session.add(cust)
    db_session.flush()
    db_session.add(
        Invoice(
            customer_id=cust.id,
            invoice_number="INV-001",
            date=date.today(),
            due_date=date.today(),
            subtotal=Decimal("500.00"),
            total=Decimal("500.00"),
            balance_due=Decimal("500.00"),
            status=InvoiceStatus.DRAFT,  # not posted → no journal entry
        )
    )
    db_session.commit()

    resp = client.get("/api/dashboard/data?ids=monthly_revenue")
    assert resp.status_code == 200
    months = resp.json()["monthly_revenue"]["months"]
    current = months[-1]
    assert current["amount"] == 0.0, (
        "Draft invoice should not contribute to monthly_revenue — "
        "only journal credits to INCOME accounts count"
    )


def test_monthly_revenue_zero_fills_missing_months(
    client,
    seed_accounts,
):
    """With no journal activity, every month is present with amount=0."""
    resp = client.get("/api/dashboard/data?ids=monthly_revenue")
    assert resp.status_code == 200
    months = resp.json()["monthly_revenue"]["months"]
    assert len(months) == 12
    for entry in months:
        assert "month" in entry and "amount" in entry
        assert entry["amount"] == 0.0


def test_monthly_revenue_excludes_credits_to_non_income_accounts(
    client,
    db_session,
    seed_accounts,
):
    """Credits to LIABILITY / EQUITY / ASSET / EXPENSE accounts must NOT
    bleed into monthly_revenue."""
    from app.models.accounts import AccountType

    asset = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.ASSET
    )
    # Find a liability to play the role of the contra (e.g. an AP-style
    # accrual). A balanced journal that credits a LIABILITY and debits an
    # ASSET is unusual but legal — point is, the LIABILITY credit must
    # not show up in revenue.
    liability = next(
        a for a in seed_accounts.values() if a.account_type == AccountType.LIABILITY
    )
    _post_journal_credit(
        db_session,
        income_account_id=liability.id,  # NOT income
        amount="9999.00",
        when=date.today(),
        contra_account_id=asset.id,
    )

    resp = client.get("/api/dashboard/data?ids=monthly_revenue")
    assert resp.status_code == 200
    months = resp.json()["monthly_revenue"]["months"]
    assert months[-1]["amount"] == 0.0
