"""Release retainage on a commitment (#1227): the release bill is dated the
day the release is made — the retainage is owed now, so the ledger posts the
release in the open period, not back into the purchase order's period.  An
explicit ``date`` still wins; the due date derives from the release date,
never from the PO's date.  A release dated before the latest bill that holds
the retainage (or before the PO) is refused.
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

    # today is read on both sides of the request: a run that crosses midnight
    # dates the bill on either day, and the asserts follow the day it took
    before = date.today()
    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"amount": 1000}
    )
    after = date.today()
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.date in {before, after}
    today = bill.date

    assert bill.date == today  # not po.date (2026-05-01)
    assert bill.date != date(2026, 5, 1)
    # the due date derives from the release date (the sub has no terms set →
    # Net 30 from the release day), so A/P Aging is not already past due
    assert bill.terms == "Net 30"
    assert bill.due_date == today + timedelta(days=30)
    txn = db_session.query(Transaction).filter_by(id=bill.transaction_id).first()
    assert txn.date == today  # the ledger posts the release in the open period


def test_release_bill_honors_an_explicit_date(client, db_session, seed_accounts):
    """A caller-supplied date is honored over both the PO's date and today."""
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


def _held(client, po, bill_date=None):
    body = {"lines": [{"line_id": po["lines"][0]["id"], "amount": 5000}]}
    if bill_date is not None:
        body["date"] = bill_date.isoformat()
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_release_dated_before_the_latest_holding_bill_is_refused(
    client, db_session, seed_accounts
):
    """Retainage held on May 1 and Jun 15 can't be released on Jun 1: the
    latest bill holding it is the floor, and nothing posts."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    _held(client, po, date(2026, 6, 15))
    bills_before = db_session.query(Bill).count()

    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"amount": 500, "date": "2026-06-01"},
    )
    assert rel.status_code == 422, rel.text
    assert rel.json()["detail"] == (
        f"A retainage release can't be dated Jun 1, 2026: that is before "
        f"Jun 15, 2026, the date of the latest bill on {po['po_number']} that "
        "holds retainage."
    )
    assert db_session.query(Bill).count() == bills_before

    # the day of the latest holding bill itself is fine
    ok = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"amount": 500, "date": "2026-06-15"},
    )
    assert ok.status_code == 200, ok.text


def test_release_dated_before_the_po_is_refused(client, db_session, seed_accounts):
    """The PO's own date is a floor too: a release dated before the
    commitment existed is refused with a plain sentence."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po)  # dated the PO's date

    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"amount": 500, "date": "2026-01-01"},
    )
    assert rel.status_code == 422, rel.text
    detail = rel.json()["detail"]
    assert detail.startswith("A retainage release can't be dated Jan 1, 2026:")
    assert "May 1, 2026" in detail


def test_a_voided_holding_bill_no_longer_sets_the_floor(
    client, db_session, seed_accounts
):
    """Only a standing bill that holds retainage counts: voiding the June
    bill lets a release dated in May through."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    june = _held(client, po, date(2026, 6, 15))
    june_id = june.get("bill_id") or june.get("id")
    voided = client.post(f"/api/bills/{june_id}/void")
    assert voided.status_code == 200, voided.text

    rel = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"amount": 500, "date": "2026-05-20"},
    )
    assert rel.status_code == 200, rel.text
