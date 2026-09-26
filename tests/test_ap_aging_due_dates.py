"""A/P aging ages a bill with no due date from its date and terms. Bills made
from a PO before 2.18 were saved with terms and no due date, and the aging
counted every one of them as current however old it was."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.bills import Bill, BillStatus
from app.models.contacts import Vendor


def test_a_bill_without_a_due_date_ages_by_its_terms(client, db_session, seed_accounts):
    vendor = Vendor(name="Cascade Flour Mill", is_active=True)
    db_session.add(vendor)
    db_session.flush()
    as_of = date(2026, 9, 26)
    db_session.add(
        Bill(
            vendor_id=vendor.id,
            bill_number="BILL-PO-0001",
            date=as_of - timedelta(days=75),  # Net 30: due 45 days ago
            due_date=None,
            terms="Net 30",
            subtotal=Decimal("777.24"),
            tax_amount=Decimal("0"),
            total=Decimal("777.24"),
            amount_paid=Decimal("0"),
            balance_due=Decimal("777.24"),
            status=BillStatus.UNPAID,
        )
    )
    db_session.commit()
    r = client.get(f"/api/reports/ap-aging?as_of_date={as_of.isoformat()}")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["items"] if isinstance(body, dict) and "items" in body else body
    row = next(
        x
        for x in (rows if isinstance(rows, list) else rows.values())
        if x["vendor_name"] == "Cascade Flour Mill"
    )
    # 45 days past due: the report's 31-60 column, not current
    assert Decimal(str(row["over_60"])) == Decimal("777.24")
    assert Decimal(str(row["current"])) == Decimal("0")
