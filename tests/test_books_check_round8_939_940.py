"""Books Check after the 2.32.0 round-8 gate (#939, #940).

#939 (W-159): the customer and vendor balance rows now read every side by
date, the agings' own reading: a payment dated today applied to an invoice
dated ahead, or a post-dated payment on an invoice already due, is no
difference (the agings and the control account agreed; the balances did
not).

#940 (W-160): on a nonprofit, an invoice-kind document is named from its
own face, as its PDF and its ledger line are: a program-fee invoice stays
"Invoice", a flagged pledge is "Pledge"."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus
from app.services.books_check import books_check


def _rows(db):
    db.expire_all()
    return {r["key"]: r for r in books_check(db, None)["rows"]}


def _differences(rows):
    return {k: r["sentence"] for k, r in rows.items() if r["status"] == "difference"}


def _invoice(client, customer_id, when, rate):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": when.isoformat(),
            "due_date": (when + timedelta(days=15)).isoformat(),
            "lines": [{"description": "Work", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _payment(client, customer_id, when, amount, invoice_id, bank_id):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": when.isoformat(),
            "amount": amount,
            "deposit_to_account_id": bank_id,
            "allocations": [{"invoice_id": invoice_id, "amount": amount}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _bill(client, vendor_id, when, account_id, amount):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": when.isoformat(),
            "due_date": (when + timedelta(days=15)).isoformat(),
            "lines": [
                {"account_id": account_id, "amount": amount, "description": "Plants"}
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _bill_payment(client, vendor_id, when, amount, bill_id, bank_id):
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": when.isoformat(),
            "amount": amount,
            "pay_from_account_id": bank_id,
            "allocations": [{"bill_id": bill_id, "amount": amount}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── #939 ───────────────────────────────────────────────────────────────────


def test_a_payment_today_on_an_invoice_dated_ahead_is_no_difference(
    client, db_session, seed_accounts
):
    a = seed_accounts
    cust = Customer(name="Walt Okafor", is_active=True)
    db_session.add(cust)
    db_session.commit()
    today = date.today()
    inv_id = _invoice(client, cust.id, today + timedelta(days=10), 100.0)
    _payment(client, cust.id, today, 100.0, inv_id, a["1000"].id)
    rows = _rows(db_session)
    assert _differences(rows) == {}, _differences(rows)


def test_a_post_dated_payment_on_an_invoice_due_is_no_difference(
    client, db_session, seed_accounts
):
    a = seed_accounts
    cust = Customer(name="Ada Reyes", is_active=True)
    db_session.add(cust)
    db_session.commit()
    today = date.today()
    inv_id = _invoice(client, cust.id, today - timedelta(days=20), 60.0)
    _payment(client, cust.id, today + timedelta(days=5), 60.0, inv_id, a["1000"].id)
    rows = _rows(db_session)
    assert _differences(rows) == {}, _differences(rows)
    # the invoice is still open as of today: the customer owes it today,
    # and the dated row (the A/R Aging beside the ledger to today) shows it
    assert rows["ar_aging"]["subledger"] == 60.0
    assert rows["ar_aging"]["control"] == 60.0
    # the customer row is kept for today only and reads every document
    # entered beside the whole ledger (round 30, W-242): the post-dated
    # payment is in both figures, as on the customer's own page, and the
    # row says so
    assert rows["customer_balances"]["subledger"] == 0.0
    assert rows["customer_balances"]["control"] == 0.0
    assert "$60.00 taken off it by entries dated after today" in (
        rows["customer_balances"]["sentence"]
    )


def test_the_vendor_side_reads_by_date_too(client, db_session, seed_accounts):
    a = seed_accounts
    vendor = Vendor(name="Brookside Nursery", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    today = date.today()
    ahead = _bill(client, vendor.id, today + timedelta(days=10), a["6000"].id, 70.0)
    _bill_payment(client, vendor.id, today, 70.0, ahead, a["1000"].id)
    rows = _rows(db_session)
    assert _differences(rows) == {}, _differences(rows)
    due = _bill(client, vendor.id, today - timedelta(days=20), a["6000"].id, 45.0)
    _bill_payment(client, vendor.id, today + timedelta(days=5), 45.0, due, a["1000"].id)
    rows = _rows(db_session)
    assert _differences(rows) == {}, _differences(rows)
    # as of today: the 45.00 bill open, the 70.00 paid ahead of its bill held
    # as an unapplied payment, the agings' reading and the ledger's (-25.00)
    assert rows["ap_aging"]["subledger"] == -25.0
    assert rows["ap_aging"]["control"] == -25.0
    # the vendor row is kept for today only and reads every document
    # entered beside the whole ledger (round 30, W-242): both bills and
    # both payments, on both sides
    assert rows["vendor_balances"]["subledger"] == 0.0
    assert rows["vendor_balances"]["control"] == 0.0
    assert "$25.00 dated after today" in rows["vendor_balances"]["sentence"]


# ── #940 ───────────────────────────────────────────────────────────────────


def test_a_nonprofits_program_fee_invoice_keeps_its_face(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    donor = Customer(name="Grant Foundation", is_active=True)
    db_session.add(donor)
    db_session.commit()
    today = date.today()
    fee_id = _invoice(client, donor.id, today - timedelta(days=5), 83.0)
    pledge_id = _invoice(client, donor.id, today - timedelta(days=5), 120.0)
    fee, pledge = db_session.get(Invoice, fee_id), db_session.get(Invoice, pledge_id)
    for inv in (fee, pledge):
        inv.status = InvoiceStatus.SENT
    pledge.is_pledge = True
    db_session.commit()
    # both drift by hand, so the A/R rows name them
    fee.balance_due = Decimal(str(fee.balance_due)) + Decimal("1")
    pledge.balance_due = Decimal(str(pledge.balance_due)) + Decimal("2")
    db_session.commit()
    row = _rows(db_session)["customer_balances"]
    labels = {
        d["id"]: d["type_label"] for d in row["documents"] if d["type"] == "invoice"
    }
    assert labels[fee_id] == "Invoice" and labels[pledge_id] == "Pledge", labels
    csv_text = client.get("/api/reports/books-check/csv").text
    assert "Invoice" in csv_text and "Pledge" in csv_text
    # the sentences stay in the company's words
    assert "a pledge" in row["sentence"] and "an pledge" not in row["sentence"]
