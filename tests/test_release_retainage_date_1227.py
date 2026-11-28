"""Release retainage on a commitment (#1227): the release bill is dated the
day the release is made — the vendor is paid now, so the ledger posts the
release in the open period, not back into the purchase order's period.  An
explicit ``date`` still wins; the due date derives from the release date,
never from the PO's date.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.models.bills import Bill
from app.models.transactions import Transaction


def _vendor(client, db):
    from app.models.accounts import Account

    acct = db.query(Account).filter(Account.account_number == "5100").first()
    r = client.post(
        "/api/vendors",
        json={
            "name": "Reta Concrete",
            "vendor_type": "sub",
            "default_expense_account_id": acct.id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _sent_po(client, vendor_id, po_date):
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor_id,
            "date": po_date.isoformat(),
            "lines": [{"description": "Slab", "quantity": 1, "rate": 10000}],
            "retainage_pct": 10,
        },
    )
    assert po.status_code == 201, po.text
    up = client.put(f"/api/purchase-orders/{po.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def test_release_bill_is_dated_the_release_day_not_the_po_day(
    client, db_session, seed_accounts
):
    """PO dated in the past; releasing with no date dates the RET- bill (and
    its due date, and its journal) on the release day (#1227)."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    billed = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": po["lines"][0]["id"], "amount": 10000}]},
    )
    assert billed.status_code == 200, billed.text

    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 1000}
    )
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    today = date.today()

    assert bill.date == today  # not po.date (2026-05-01)
    assert bill.date != date(2026, 5, 1)
    # the due date derives from the release date (the sub has no terms set →
    # Net 30 from the release day), so A/P Aging is not already past due
    assert bill.terms == "Net 30"
    assert bill.due_date == today + timedelta(days=30)
    txn = db_session.query(Transaction).filter_by(id=bill.transaction_id).first()
    assert txn.date == today  # the ledger posts the release in the open period


def test_release_bill_honours_an_explicit_date(client, db_session, seed_accounts):
    """A caller-supplied date still wins over both the PO's date and today."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    billed = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": po["lines"][0]["id"], "amount": 10000}]},
    )
    assert billed.status_code == 200, billed.text
    when = date(2026, 11, 24)

    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"amount": 1000, "date": when.isoformat()},
    )
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.date == when
    assert bill.due_date == when + timedelta(days=30)
    assert bill.total == Decimal("1000.00")
