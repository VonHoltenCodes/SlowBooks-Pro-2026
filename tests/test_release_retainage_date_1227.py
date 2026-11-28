"""Release retainage on a commitment (#1227): the release bill is dated the
day the release is made — the retainage is owed now, so the ledger posts the
release in the open period, not back into the purchase order's period.  An
explicit ``date`` still wins; the due date derives from the release date,
never from the PO's date.  A release of more than the retainage held by its
date (less what was already released by then) is refused.
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


def _release(client, po, amount, on=None):
    body = {"amount": amount}
    if on is not None:
        body["date"] = on.isoformat()
    return client.post(f"/api/purchase-orders/{po['id']}/release-retainage", json=body)


def test_partial_release_between_two_holding_bills_is_allowed(
    client, db_session, seed_accounts
):
    """$500 held May 1 and $500 more Jun 15: a $300 release dated Jun 1 draws
    on what was already held by then, so the later bill doesn't block it."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    _held(client, po, date(2026, 6, 15))

    rel = _release(client, po, 300, date(2026, 6, 1))
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.date == date(2026, 6, 1)
    assert bill.total == Decimal("300.00")


def test_a_release_over_what_was_held_by_its_date_is_refused(
    client, db_session, seed_accounts
):
    """Only $500 was held on Jun 1, so $600 can't be released that day: the
    refusal says what was held by then and when enough is, and nothing
    posts. Earlier releases count against the date's holding."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    _held(client, po, date(2026, 6, 15))
    bills_before = db_session.query(Bill).count()

    rel = _release(client, po, 600, date(2026, 6, 1))
    assert rel.status_code == 422, rel.text
    assert rel.json()["detail"] == (
        "A retainage release of $600.00 can't be dated Jun 1, 2026: by then "
        f"{po['po_number']} held $500.00 of retainage not yet released. "
        "Enough is held from Jun 15, 2026."
    )
    assert db_session.query(Bill).count() == bills_before

    # $300 released on May 20 leaves $200 for a second release dated Jun 1
    assert _release(client, po, 300, date(2026, 5, 20)).status_code == 200
    again = _release(client, po, 300, date(2026, 6, 1))
    assert again.status_code == 422, again.text
    assert "held $200.00 of retainage not yet released" in again.json()["detail"]
    assert _release(client, po, 200, date(2026, 6, 1)).status_code == 200


def test_the_holding_bill_day_itself_is_enough(client, db_session, seed_accounts):
    """Same-day boundary: retainage held on Jun 15 can be released on Jun 15,
    but not on Jun 14."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    _held(client, po, date(2026, 6, 15))

    early = _release(client, po, 1000, date(2026, 6, 14))
    assert early.status_code == 422, early.text
    assert early.json()["detail"].endswith("Enough is held from Jun 15, 2026.")
    ok = _release(client, po, 1000, date(2026, 6, 15))
    assert ok.status_code == 200, ok.text


def test_void_and_release_again_after_a_later_holding_bill(
    client, db_session, seed_accounts
):
    """A May 20 release is voided after a June bill holds more retainage;
    releasing again on May 20 is still allowed (the voided release no longer
    counts, and the June bill doesn't matter to a May date)."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    first = _release(client, po, 500, date(2026, 5, 20))
    assert first.status_code == 200, first.text
    _held(client, po, date(2026, 6, 15))
    voided = client.post(f"/api/bills/{first.json()['bill_id']}/void")
    assert voided.status_code == 200, voided.text

    again = _release(client, po, 500, date(2026, 5, 20))
    assert again.status_code == 200, again.text


def test_release_today_with_a_future_dated_holding_bill(
    client, db_session, seed_accounts
):
    """One holding bill is dated today and one in the future: a release
    taking the default date (today) of what today's bill holds goes
    through."""
    v = _vendor(client, db_session)
    today = date.today()
    po = _sent_po(client, v["id"], today)
    _held(client, po, today)
    _held(client, po, today + timedelta(days=30))

    rel = _release(client, po, 500)
    assert rel.status_code == 200, rel.text
    bill = db_session.query(Bill).filter_by(id=rel.json()["bill_id"]).first()
    assert bill.date in {today, date.today()}


def test_release_dated_before_the_po_is_refused(client, db_session, seed_accounts):
    """Before the commitment existed nothing was held: a release dated before
    the PO is refused by the same rule, naming $0.00 and the first day
    enough is held."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po)  # dated the PO's date

    rel = _release(client, po, 500, date(2026, 1, 1))
    assert rel.status_code == 422, rel.text
    assert rel.json()["detail"] == (
        "A retainage release of $500.00 can't be dated Jan 1, 2026: by then "
        f"{po['po_number']} held $0.00 of retainage not yet released. "
        "Enough is held from May 1, 2026."
    )


def test_a_voided_holding_bill_no_longer_counts(client, db_session, seed_accounts):
    """Only standing bills count: voiding the June bill leaves May's $500, so
    a May 20 release of $500 goes through and $600 would not."""
    v = _vendor(client, db_session)
    po = _sent_po(client, v["id"], date(2026, 5, 1))
    _held(client, po, date(2026, 5, 1))
    june = _held(client, po, date(2026, 6, 15))
    june_id = june.get("bill_id") or june.get("id")
    voided = client.post(f"/api/bills/{june_id}/void")
    assert voided.status_code == 200, voided.text

    assert _release(client, po, 600, date(2026, 5, 20)).status_code == 422
    rel = _release(client, po, 500, date(2026, 5, 20))
    assert rel.status_code == 200, rel.text
