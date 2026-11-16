"""The Payables and Receivables cards count a vendor- or customer-named
journal entry's open amount as overdue the way the agings bucket it (#924,
then #935: every Home number reads the aging, so an entry is past due from
its first day, as the aging's 1-30 column has it): an imported book has only
those entries until its bills and invoices are entered, and the card read
"0 overdue" beside an aging at 31–60 days."""

from datetime import date, timedelta

from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services import dashboard_widgets


def _entry(
    db, when, debit_acct, credit_acct, amount, *, vendor_id=None, customer_id=None
):
    txn = Transaction(
        date=when,
        description="imported",
        source_type="qbo_export_import",
        vendor_id=vendor_id,
    )
    db.add(txn)
    db.flush()
    db.add_all(
        [
            TransactionLine(
                transaction_id=txn.id,
                account_id=debit_acct,
                debit=amount,
                credit=0,
                customer_id=customer_id,
            ),
            TransactionLine(
                transaction_id=txn.id,
                account_id=credit_acct,
                debit=0,
                credit=amount,
                customer_id=customer_id,
            ),
        ]
    )
    db.commit()
    return txn


def test_the_payables_card_counts_the_vendors_the_aging_puts_past_due(
    db_session, seed_accounts
):
    a = seed_accounts
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    ap, expense, bank = a["2000"].id, a["6000"].id, a["1000"].id
    today = date.today()
    assert dashboard_widgets.payables(db_session)["overdue_count"] == 0
    # a bill 45 days old, unpaid: one overdue; its total is the card's
    _entry(
        db_session,
        today - timedelta(days=45),
        expense,
        ap,
        2236.90,
        vendor_id=vendor.id,
    )
    card = dashboard_widgets.payables(db_session)
    assert card == {"total": 2236.90, "overdue_count": 1}
    # a second vendor's bill 10 days old: in the aging's 1-30, so past due
    # too (#935: the card reads the aging)
    other = Vendor(name="Dockside Supply", is_active=True)
    db_session.add(other)
    db_session.commit()
    _entry(db_session, today - timedelta(days=10), expense, ap, 100, vendor_id=other.id)
    card = dashboard_widgets.payables(db_session)
    assert card["overdue_count"] == 2 and card["total"] == 2336.90
    # the old bill paid in full (applied oldest first): that vendor drops out
    _entry(
        db_session, today - timedelta(days=5), ap, bank, 2236.90, vendor_id=vendor.id
    )
    card = dashboard_widgets.payables(db_session)
    assert card == {"total": 100.0, "overdue_count": 1}
    # an entry dated today is current, not past due
    _entry(db_session, today, expense, ap, 7, vendor_id=vendor.id)
    assert dashboard_widgets.payables(db_session)["overdue_count"] == 1


def test_the_receivables_card_counts_the_customers_the_aging_puts_past_due(
    db_session, seed_accounts
):
    a = seed_accounts
    customer = Customer(name="Dockside Marina", is_active=True)
    db_session.add(customer)
    db_session.commit()
    ar, income, bank = a["1100"].id, a["4000"].id, a["1000"].id
    today = date.today()
    _entry(
        db_session, today - timedelta(days=45), ar, income, 500, customer_id=customer.id
    )
    card = dashboard_widgets.receivables(db_session)
    assert card["overdue_count"] == 1 and card["total"] == 500.0
    _entry(
        db_session, today - timedelta(days=2), bank, ar, 500, customer_id=customer.id
    )
    card = dashboard_widgets.receivables(db_session)
    assert card["overdue_count"] == 0 and card["total"] == 0.0


def test_a_nameless_entry_is_not_counted(db_session, seed_accounts):
    a = seed_accounts
    _entry(
        db_session, date.today() - timedelta(days=90), a["6000"].id, a["2000"].id, 75
    )
    assert dashboard_widgets.payables(db_session)["overdue_count"] == 0
