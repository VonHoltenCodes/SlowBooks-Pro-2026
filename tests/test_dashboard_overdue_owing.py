"""The dashboard's overdue lists count what is owed (2.18.0 gate, skytech N7).

A $0.00 invoice made before 2.18.0 (which now starts one as paid) is still a
draft or sent, so on an upgraded company the Overdue Invoices card listed it
at $0.00 and Total Receivables counted it as overdue: the queries read the
status and the due date, never the balance. They require a balance now, on the
payable side too.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus


def _today():
    # today as the code under test reads it, at the moment the test runs: a
    # module constant, taken at import, failed seven tests when a CI run
    # crossed midnight UTC (#773)
    return date.today()


ZERO = Decimal("0.00")


@pytest.fixture
def books(db_session, seed_accounts):
    """An upgraded company: a $0.00 invoice left sent and a $0.00 bill left
    unpaid, both past due, beside an invoice and a bill that are owed."""
    cust = Customer(name="Harbor Light Bakery", is_active=True)
    vend = Vendor(name="Coastal Flour", is_active=True)
    db_session.add_all([cust, vend])
    db_session.flush()
    db_session.add_all(
        [
            Invoice(
                invoice_number="1001",
                customer_id=cust.id,
                status=InvoiceStatus.SENT,
                date=_today() - timedelta(days=42),
                due_date=_today() - timedelta(days=12),
                total=ZERO,
                balance_due=ZERO,
            ),
            Invoice(
                invoice_number="1002",
                customer_id=cust.id,
                status=InvoiceStatus.SENT,
                date=_today() - timedelta(days=50),
                due_date=_today() - timedelta(days=20),
                total=Decimal("250.00"),
                balance_due=Decimal("250.00"),
            ),
            Invoice(
                invoice_number="1003",
                customer_id=cust.id,
                status=InvoiceStatus.DRAFT,
                date=_today() - timedelta(days=40),
                due_date=_today() - timedelta(days=10),
                total=ZERO,
                balance_due=ZERO,
            ),
            Bill(
                bill_number="CF-7",
                vendor_id=vend.id,
                status=BillStatus.UNPAID,
                date=_today() - timedelta(days=35),
                due_date=_today() - timedelta(days=5),
                total=ZERO,
                balance_due=ZERO,
            ),
            Bill(
                bill_number="CF-8",
                vendor_id=vend.id,
                status=BillStatus.UNPAID,
                date=_today() - timedelta(days=33),
                due_date=_today() - timedelta(days=3),
                total=Decimal("80.00"),
                balance_due=Decimal("80.00"),
            ),
        ]
    )
    db_session.commit()


def _cards(client, ids):
    return client.get(f"/api/dashboard/data?ids={','.join(ids)}").json()


def test_a_zero_invoice_is_not_on_the_overdue_list(client, books):
    d = _cards(client, ["overdue_invoices", "receivables"])
    listed = d["overdue_invoices"]
    assert [i["invoice_number"] for i in listed["items"]] == ["1002"]
    assert listed["count"] == 1
    assert all(i["balance_due"] > 0 for i in listed["items"])
    assert d["receivables"]["overdue_count"] == 1


def test_a_zero_bill_is_not_counted_overdue(client, books):
    assert _cards(client, ["payables"])["payables"]["overdue_count"] == 1


class _UsEvening(date):
    """The local date in a US evening, when UTC has already moved on to
    tomorrow: the day before UTC's date."""

    @classmethod
    def today(cls):
        utc = datetime.now(timezone.utc).date()
        return cls(utc.year, utc.month, utc.day) - timedelta(days=1)


def test_an_invoice_due_today_is_not_overdue_yet(
    client, db_session, seed_accounts, monkeypatch
):
    """Overdue is judged by the local date, as the A/R Aging card judges
    Current. The query compared with SQL's CURRENT_DATE, which SQLite gives
    in UTC, so from a US evening on an invoice due today was already
    "overdue, 0 days" while the aging card still called it Current."""
    from app.services import dashboard_widgets

    monkeypatch.setattr(dashboard_widgets, "date", _UsEvening)
    today = _UsEvening.today()
    cust = Customer(name="Salt & Pine", is_active=True)
    db_session.add(cust)
    db_session.flush()
    db_session.add(
        Invoice(
            invoice_number="2001",
            customer_id=cust.id,
            status=InvoiceStatus.SENT,
            date=today - timedelta(days=30),
            due_date=today,
            total=Decimal("40.00"),
            balance_due=Decimal("40.00"),
        )
    )
    db_session.commit()
    d = _cards(client, ["overdue_invoices", "receivables", "ar_aging"])
    assert d["overdue_invoices"]["count"] == 0
    assert d["receivables"]["overdue_count"] == 0
    assert d["ar_aging"]["current"] == 40.0
