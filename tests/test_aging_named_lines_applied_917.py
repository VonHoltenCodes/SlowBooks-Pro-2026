"""The agings on a vendor- or customer-named journal entry (#917, 2.32.0
round 4, W-152): the party's entries that lower what is owed pay its oldest
raising entries first, so a paid bill leaves nothing overdue and a payment
is not a credit the party holds; only what is left when every raising entry
is covered is an unapplied credit. Before, each entry aged by its own date:
an imported vendor with one open bill of 2,236.90 read 12,694.65 overdue
beside a 12,694.65 credit."""

from datetime import date
from decimal import Decimal

from app.models.contacts import Customer, Vendor
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check
from app.services.control_journal_lines import (
    ControlLine,
    applied,
    payable_lines,
    receivable_lines,
)

AS_OF = "2026-10-15"


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


def _ap_row(client, vendor_id):
    r = client.get(f"/api/reports/ap-aging?as_of_date={AS_OF}")
    assert r.status_code == 200, r.text
    return next((i for i in r.json()["items"] if i["vendor_id"] == vendor_id), None)


def _ar_row(client, customer_id):
    r = client.get(f"/api/reports/ar-aging?as_of_date={AS_OF}")
    assert r.status_code == 200, r.text
    return next((i for i in r.json()["items"] if i["customer_id"] == customer_id), None)


def _rows(db):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, None)["rows"]}


# ── the service ────────────────────────────────────────────────────────────


class _T:
    def __init__(self, i, when):
        self.id, self.date = i, when


def _line(i, when, party, amount):
    return ControlLine(_T(i, when), party, Decimal(amount))


def test_a_payment_pays_the_oldest_open_entry_first():
    aged = applied(
        [
            _line(1, date(2026, 7, 1), 5, "300"),
            _line(2, date(2026, 8, 1), 5, "200"),
            _line(3, date(2026, 9, 1), 5, "-350"),
        ]
    )[5]
    assert aged.open_items == [(date(2026, 8, 1), Decimal("150.00"))]
    assert aged.credit == Decimal("0")


def test_a_payment_past_what_is_owed_is_the_credit_left_over():
    aged = applied(
        [_line(1, date(2026, 7, 1), 5, "300"), _line(2, date(2026, 9, 1), 5, "-400")]
    )[5]
    assert aged.open_items == [] and aged.credit == Decimal("100.00")


def test_a_payment_before_any_bill_waits_and_pays_the_next_one():
    aged = applied(
        [_line(1, date(2026, 7, 1), 5, "-100"), _line(2, date(2026, 8, 1), 5, "300")]
    )[5]
    assert aged.open_items == [(date(2026, 8, 1), Decimal("200.00"))]
    assert aged.credit == Decimal("0")


def test_parties_are_kept_apart_and_the_nameless_part_is_left_out():
    aged = applied(
        [
            _line(1, date(2026, 7, 1), 5, "300"),
            _line(2, date(2026, 7, 2), 6, "50"),
            _line(3, date(2026, 7, 3), None, "999"),
            _line(4, date(2026, 8, 1), 6, "-50"),
        ]
    )
    assert set(aged) == {5, 6}
    assert aged[5].open_items == [(date(2026, 7, 1), Decimal("300"))]
    assert aged[6].open_items == [] and aged[6].credit == Decimal("0")


# ── the A/P Aging ──────────────────────────────────────────────────────────


def test_a_vendors_paid_bills_leave_nothing_overdue(client, db_session, seed_accounts):
    a = seed_accounts
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    ap, expense, bank = a["2000"].id, a["6000"].id, a["1000"].id
    # three bills, the first two paid, the third 22 days old
    _entry(db_session, date(2026, 5, 8), expense, ap, 1162.40, vendor_id=vendor.id)
    _entry(db_session, date(2026, 6, 2), expense, ap, 1236.45, vendor_id=vendor.id)
    _entry(db_session, date(2026, 6, 20), ap, bank, 1162.40, vendor_id=vendor.id)
    _entry(db_session, date(2026, 7, 15), ap, bank, 1236.45, vendor_id=vendor.id)
    _entry(db_session, date(2026, 9, 23), expense, ap, 2236.90, vendor_id=vendor.id)
    row = _ap_row(client, vendor.id)
    assert row is not None
    assert row["over_30"] == 2236.90 and row["total"] == 2236.90
    assert row["current"] == 0.0 and row["over_60"] == 0.0 and row["over_90"] == 0.0
    assert row["unapplied_credits"] == 0.0
    # the day every bill was paid: nothing owed, no row worth reading
    r = client.get("/api/reports/ap-aging?as_of_date=2026-09-01").json()
    paid = next((i for i in r["items"] if i["vendor_id"] == vendor.id), None)
    assert paid is None or (paid["total"] == 0.0 and paid["unapplied_credits"] == 0.0)
    # the totals the other readers agree on
    rows = _rows(db_session)
    for key in ("ap_aging", "vendor_balances"):
        assert rows[key]["status"] == "pass", rows[key]
    assert sum(
        c.amount for c in payable_lines(db_session, date.fromisoformat(AS_OF))
    ) == Decimal("2236.90")


def test_a_vendor_paid_more_than_owed_holds_the_rest_as_a_credit(
    client, db_session, seed_accounts
):
    a = seed_accounts
    vendor = Vendor(name="Coastline Framing", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    ap, expense, bank = a["2000"].id, a["6000"].id, a["1000"].id
    _entry(db_session, date(2026, 8, 1), expense, ap, 300, vendor_id=vendor.id)
    _entry(db_session, date(2026, 9, 15), ap, bank, 400, vendor_id=vendor.id)
    row = _ap_row(client, vendor.id)
    assert row["unapplied_credits"] == 100.0
    assert row["total"] == -100.0 and row["over_90"] == 0.0
    # a part payment leaves the rest aging from the bill's date (75 days: 61–90+)
    other = Vendor(name="Dockside Supply", is_active=True)
    db_session.add(other)
    db_session.commit()
    _entry(db_session, date(2026, 8, 1), expense, ap, 300, vendor_id=other.id)
    _entry(db_session, date(2026, 9, 15), ap, bank, 100, vendor_id=other.id)
    row = _ap_row(client, other.id)
    assert (
        row["over_90"] == 200.0
        and row["total"] == 200.0
        and row["unapplied_credits"] == 0.0
    )


# ── the A/R Aging ──────────────────────────────────────────────────────────


def test_a_customers_paid_invoices_leave_nothing_overdue(
    client, db_session, seed_accounts
):
    a = seed_accounts
    customer = Customer(name="Dockside Marina", is_active=True)
    db_session.add(customer)
    db_session.commit()
    ar, income, bank = a["1100"].id, a["4000"].id, a["1000"].id
    _entry(db_session, date(2026, 6, 1), ar, income, 500, customer_id=customer.id)
    _entry(db_session, date(2026, 7, 1), bank, ar, 500, customer_id=customer.id)
    _entry(db_session, date(2026, 10, 1), ar, income, 250, customer_id=customer.id)
    row = _ar_row(client, customer.id)
    assert row is not None
    assert row["current"] == 0.0 and row["over_30"] == 250.0 and row["over_90"] == 0.0
    assert row["total"] == 250.0 and row["unapplied_credits"] == 0.0
    # paid past what is owed: the rest is a credit against the newest bucket
    _entry(db_session, date(2026, 10, 10), bank, ar, 300, customer_id=customer.id)
    row = _ar_row(client, customer.id)
    assert row["over_30"] == 0.0 and row["unapplied_credits"] == 50.0
    assert row["current"] == -50.0 and row["total"] == -50.0
    rows = _rows(db_session)
    for key in ("ar_aging", "customer_balances"):
        assert rows[key]["status"] == "pass", rows[key]
    assert sum(
        c.amount for c in receivable_lines(db_session, date.fromisoformat(AS_OF))
    ) == Decimal("-50.00")
