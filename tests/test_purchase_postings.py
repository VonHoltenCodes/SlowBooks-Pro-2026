"""Purchases post where the user said, and tax on them is part of their cost.

From the 2.17.3 exploratory tests (skytech W-*, macbase1 F*):

* A PO's tax (pre-filled with the company's *selling* rate) was debited to
  2200 Sales Tax Payable when the PO became a bill, so Pay Sales Tax offered
  $0.33 where $59.57 had been collected (F9, W-H5).
* A line with no item fell back to account "6000" — Advertising & Marketing
  in the seeded chart — so flour and sign panels were booked as advertising
  (F8, W-H5). Items with no expense account landed there too.
* A bill made from a PO had no due date and never aged; bills ignored the
  vendor's terms (F10, F11).
* Bill → Save PDF opened a route that didn't exist (W-M8); a PO couldn't be
  seen or sent, and a paid bill's payment couldn't be voided on screen
  (W-L19).
* A $0.00 bill was accepted (W-M1).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.bills import Bill
from app.models.transactions import Transaction, TransactionLine

PDF_MAGIC = b"%PDF"


def _gl(db, account_id):
    """Debits minus credits on one account."""
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum(
        (Decimal(str(r.debit)) - Decimal(str(r.credit)) for r in rows), Decimal(0)
    )


def _vendor(client, name="Cascade Flour Mill", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _po(client, vendor_id, lines, tax_rate=0.0, day="2026-09-01"):
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor_id,
            "date": day,
            "tax_rate": tax_rate,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill_txn_lines(db, bill_id):
    bill = db.query(Bill).filter_by(id=bill_id).one()
    return db.query(TransactionLine).filter_by(transaction_id=bill.transaction_id).all()


FLOUR = [
    {"description": "Bread flour 50 lb", "quantity": 20, "rate": 31.50},
    {"description": "Rye flour 25 lb", "quantity": 4, "rate": 22.00},
]


# ── A5: tax on a purchase is part of what it cost ────────────────────────


def test_a_pos_tax_goes_into_its_lines_and_sales_tax_payable_keeps_only_sales_tax(
    client, db_session, seed_accounts, seed_customer
):
    # macbase1's bakery: tax collected on a sale, then a taxed flour PO.
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0.0825,
            "lines": [{"description": "Cake", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    sales_tax = Decimal(r.json()["tax_amount"])
    assert sales_tax == Decimal("8.25")

    mill = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, mill["id"], FLOUR, tax_rate=0.0825)
    assert Decimal(po["tax_amount"]) == Decimal("59.24")
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert conv.status_code == 200, conv.text

    # 2200 holds the tax owed on sales, and nothing else …
    assert -_gl(db_session, seed_accounts["2200"].id) == sales_tax
    # … which is what Pay Sales Tax offers to remit.
    bs = client.get("/api/reports/balance-sheet").json()
    stp = next(x for x in bs["liabilities"] if x["account_number"] == "2200")
    assert Decimal(str(stp["amount"])) == sales_tax

    # The tax is spread over the flour lines in proportion (630 : 88).
    lines = _bill_txn_lines(db_session, conv.json()["bill_id"])
    debits = sorted(Decimal(str(ln.debit)) for ln in lines if ln.debit)
    assert debits == [Decimal("95.26"), Decimal("681.98")]
    assert {ln.account_id for ln in lines if ln.debit} == {seed_accounts["5100"].id}
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("777.24")
    assert -_gl(db_session, seed_accounts["2000"].id) == Decimal("777.24")

    bill = client.get(f"/api/bills/{conv.json()['bill_id']}").json()
    assert Decimal(bill["subtotal"]) == Decimal("718.00")
    assert Decimal(bill["tax_amount"]) == Decimal("59.24")
    assert Decimal(bill["total"]) == Decimal("777.24")


def test_a_bill_entered_with_tax_spreads_it_over_its_lines(
    client, db_session, seed_accounts
):
    shop = _vendor(client, "Sign Supply")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-02",
            "tax_rate": 0.10,
            "lines": [
                {
                    "description": "panels",
                    "quantity": 1,
                    "rate": 100,
                    "account_id": seed_accounts["5100"].id,
                },
                {
                    "description": "screws",
                    "quantity": 1,
                    "rate": 50,
                    "account_id": seed_accounts["6800"].id,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["total"]) == Decimal("165.00")
    by_acct = {
        ln.account_id: Decimal(str(ln.debit))
        for ln in _bill_txn_lines(db_session, r.json()["id"])
        if ln.debit
    }
    assert by_acct == {
        seed_accounts["5100"].id: Decimal("110.00"),
        seed_accounts["6800"].id: Decimal("55.00"),
    }
    assert _gl(db_session, seed_accounts["2200"].id) == 0


def test_tax_on_stock_bought_is_in_its_inventory_cost(
    client, db_session, seed_accounts
):
    # The Inventory debit carries the tax, and so must the item's cost, or
    # the stock valuation stops agreeing with account 1300.
    from app.models.items import Item, ItemType

    panel = Item(
        name="Aluminium panel",
        item_type=ItemType.PRODUCT,
        rate=Decimal("60"),
        cost=Decimal("22.50"),
        track_inventory=True,
    )
    db_session.add(panel)
    db_session.commit()
    shop = _vendor(client, "Sign Supply")
    po = _po(
        client,
        shop["id"],
        [{"item_id": panel.id, "description": "panels", "quantity": 20, "rate": 22.50}],
        tax_rate=0.0825,
    )
    assert Decimal(po["total"]) == Decimal("487.13")
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert conv.status_code == 200, conv.text

    assert _gl(db_session, seed_accounts["1300"].id) == Decimal("487.13")
    assert _gl(db_session, seed_accounts["2200"].id) == 0
    db_session.refresh(panel)
    assert Decimal(str(panel.quantity_on_hand)) == Decimal("20")
    valuation = Decimal(str(panel.quantity_on_hand)) * Decimal(str(panel.avg_cost))
    assert abs(valuation - Decimal("487.13")) < Decimal("0.01")


def test_the_tax_spread_is_exact_to_the_cent_and_never_negative():
    from app.services.purchase_posting import spread

    assert spread(Decimal("59.24"), [Decimal("630"), Decimal("88")]) == [
        Decimal("51.98"),
        Decimal("7.26"),
    ]
    # Rounding every share and giving the difference to one line would put
    # -0.04 on it here: ten $1 lines each round up to a cent of $0.06.
    shares = spread(Decimal("0.06"), [Decimal("1")] * 10 + [Decimal("0.01")])
    assert sum(shares) == Decimal("0.06")
    assert all(s >= 0 for s in shares)
    # Lines with no amount take no share.
    assert spread(Decimal("1.00"), [Decimal("0"), Decimal("3")]) == [
        Decimal("0.00"),
        Decimal("1.00"),
    ]
    assert spread(Decimal("0"), [Decimal("5")]) == [Decimal("0.00")]


# ── A6: no line is booked to a guessed account ───────────────────────────


def test_a_bill_line_nothing_names_an_account_for_is_refused_not_booked_to_6000(
    client, db_session, seed_accounts
):
    shop = _vendor(client, "Sign Supply")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-02",
            "lines": [{"description": "panels", "quantity": 20, "rate": 22.50}],
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Line 1 (panels)" in detail and "Sign Supply" in detail
    assert "default expense account" in detail
    assert db_session.query(Bill).count() == 0
    assert _gl(db_session, seed_accounts["6000"].id) == 0


def test_an_item_with_no_expense_account_takes_the_vendors_default(
    client, db_session, seed_accounts
):
    from app.models.items import Item, ItemType

    panel = Item(name="Panel", item_type=ItemType.MATERIAL, rate=Decimal("60"))
    db_session.add(panel)
    db_session.commit()
    shop = _vendor(
        client, "Sign Supply", default_expense_account_id=seed_accounts["5100"].id
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-02",
            "lines": [{"item_id": panel.id, "quantity": 20, "rate": 22.50}],
        },
    )
    assert r.status_code == 201, r.text
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("450.00")
    assert _gl(db_session, seed_accounts["6000"].id) == 0


def test_a_description_only_line_needs_no_account(client, db_session, seed_accounts):
    shop = _vendor(client, "Sign Supply")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-02",
            "lines": [
                {
                    "description": "panels",
                    "quantity": 1,
                    "rate": 40,
                    "account_id": seed_accounts["5100"].id,
                },
                {"description": "delivered to the back door", "quantity": 1, "rate": 0},
            ],
        },
    )
    assert r.status_code == 201, r.text


def test_a_po_line_with_no_account_is_refused_at_conversion_and_nothing_is_written(
    client, db_session, seed_accounts
):
    from app.models.purchase_orders import PurchaseOrder

    mill = _vendor(client)
    po = _po(client, mill["id"], FLOUR)
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert r.status_code == 400, r.text
    assert "Line 1 (Bread flour 50 lb)" in r.json()["detail"]
    assert db_session.query(Bill).count() == 0
    assert db_session.query(Transaction).filter_by(source_type="bill").count() == 0
    db_session.expire_all()
    assert db_session.get(PurchaseOrder, po["id"]).status.value != "closed"


def test_the_accounts_chosen_at_to_bill_are_where_the_lines_post(
    client, db_session, seed_accounts
):
    mill = _vendor(client)
    po = _po(client, mill["id"], FLOUR)
    ids = [ln["id"] for ln in po["lines"]]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={
            "lines": [
                {"line_id": ids[0], "account_id": seed_accounts["5100"].id},
                {"line_id": ids[1], "account_id": seed_accounts["5000"].id},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("630.00")
    assert _gl(db_session, seed_accounts["5000"].id) == Decimal("88.00")
    assert _gl(db_session, seed_accounts["6000"].id) == 0
    bill = client.get(f"/api/bills/{r.json()['bill_id']}").json()
    assert [ln["account_id"] for ln in bill["lines"]] == [
        seed_accounts["5100"].id,
        seed_accounts["5000"].id,
    ]


def test_a_to_bill_choice_for_a_line_not_on_the_po_is_refused(
    client, db_session, seed_accounts
):
    mill = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _po(client, mill["id"], FLOUR)
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": 999999, "account_id": seed_accounts["5000"].id}]},
    )
    assert r.status_code == 400, r.text
    assert db_session.query(Bill).count() == 0


# ── B23: a bill has the vendor's terms and a due date ────────────────────


def test_a_bill_from_a_po_takes_the_vendors_terms_and_falls_due(
    client, db_session, seed_accounts
):
    mill = _vendor(
        client,
        terms="Net 15",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    po = _po(client, mill["id"], FLOUR, day="2026-09-26")
    conv = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert conv.status_code == 200, conv.text
    bill = client.get(f"/api/bills/{conv.json()['bill_id']}").json()
    assert bill["terms"] == "Net 15"
    assert bill["due_date"] == "2026-10-11"


def test_a_bill_entered_without_terms_takes_the_vendors(
    client, db_session, seed_accounts
):
    heron = _vendor(
        client,
        "Blue Heron Packaging",
        terms="Net 15",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": heron["id"],
            "date": "2026-09-26",
            "lines": [{"description": "cake boxes", "quantity": 1, "rate": 90}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["terms"] == "Net 15"
    assert r.json()["due_date"] == "2026-10-11"


def test_due_on_receipt_is_due_the_day_of_the_bill(client, db_session, seed_accounts):
    shop = _vendor(
        client, "Sign Supply", default_expense_account_id=seed_accounts["5100"].id
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-26",
            "terms": "Due on Receipt",
            "lines": [{"description": "panels", "quantity": 1, "rate": 90}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["due_date"] == "2026-09-26"


# ── B1: a document for nothing is refused ────────────────────────────────


def test_a_zero_bill_is_refused(client, db_session, seed_accounts):
    shop = _vendor(
        client, "Sign Supply", default_expense_account_id=seed_accounts["5100"].id
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "date": "2026-09-26",
            "lines": [{"description": "panels", "quantity": 2, "rate": 0}],
        },
    )
    assert r.status_code == 400, r.text
    assert "more than zero" in r.json()["detail"]
    assert db_session.query(Bill).count() == 0


def test_a_po_for_nothing_does_not_become_a_bill(client, db_session, seed_accounts):
    shop = _vendor(
        client, "Sign Supply", default_expense_account_id=seed_accounts["5100"].id
    )
    po = _po(client, shop["id"], [{"description": "panels", "quantity": 2, "rate": 0}])
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill")
    assert r.status_code == 400, r.text
    assert "$0.00" in r.json()["detail"]
    assert db_session.query(Bill).count() == 0


# ── B8, C17: documents to keep and send; voiding a payment on screen ────


@pytest.fixture
def a_bill(client, seed_accounts):
    shop = _vendor(
        client,
        "Sign Supply & Co",
        address1="12 Dock Rd",
        city="Port Alder",
        default_expense_account_id=seed_accounts["5100"].id,
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": shop["id"],
            "bill_number": "INV/77 #3",
            "date": "2026-09-26",
            "lines": [{"description": "panels", "quantity": 20, "rate": 22.50}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bill_has_a_pdf_and_a_print_page(client, a_bill):
    r = client.get(f"/api/bills/{a_bill['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(PDF_MAGIC)
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("inline;") and "Bill_INV_77_3.pdf" in disposition

    page = client.get(f"/api/bills/{a_bill['id']}/print-preview")
    assert page.status_code == 200
    assert "INV/77 #3" in page.text and "Sign Supply &amp; Co" in page.text
    assert "Port Alder" in page.text and "Port Alder," not in page.text
    assert "window.print()" in page.text
    assert client.get("/api/bills/999999/pdf").status_code == 404


def test_a_po_has_a_pdf_and_a_print_page(client, db_session, seed_accounts):
    shop = _vendor(client, "Sign Supply", terms="Net 15")
    po = _po(client, shop["id"], FLOUR, tax_rate=0.0825)
    r = client.get(f"/api/purchase-orders/{po['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.content.startswith(PDF_MAGIC)
    assert f"PurchaseOrder_{po['po_number']}.pdf" in r.headers["content-disposition"]

    page = client.get(f"/api/purchase-orders/{po['id']}/print-preview").text
    assert "PURCHASE ORDER" in page and po["po_number"] in page
    assert "Bread flour 50 lb" in page and "$777.24" in page
    assert "window.print()" in page
    assert client.get("/api/purchase-orders/999999/pdf").status_code == 404


def _pay(client, vendor_id, allocations, amount=None, day="2026-09-27"):
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": day,
            "amount": amount or sum(a["amount"] for a in allocations),
            "method": "check",
            "check_number": "1050",
            "allocations": allocations,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bills_payments_are_listed_with_it_and_one_can_be_voided(
    client, db_session, seed_accounts, a_bill
):
    other = client.post(
        "/api/bills",
        json={
            "vendor_id": a_bill["vendor_id"],
            "bill_number": "INV-78",
            "date": "2026-09-26",
            "lines": [{"description": "vinyl", "quantity": 1, "rate": 80}],
        },
    ).json()
    paid = _pay(
        client, a_bill["vendor_id"], [{"bill_id": a_bill["id"], "amount": 450.0}]
    )
    _pay(client, a_bill["vendor_id"], [{"bill_id": other["id"], "amount": 80.0}])

    listed = client.get(f"/api/bill-payments?bill_id={a_bill['id']}").json()
    assert [p["id"] for p in listed] == [paid["id"]]
    assert listed[0]["allocations"] == [{"bill_id": a_bill["id"], "amount": "450.00"}]
    assert client.get(f"/api/bills/{a_bill['id']}").json()["status"] == "paid"

    r = client.post(f"/api/bill-payments/{paid['id']}/void")
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/bills/{a_bill['id']}").json()
    assert bill["status"] == "unpaid" and Decimal(bill["balance_due"]) == Decimal(
        "450.00"
    )
    assert client.get(f"/api/bill-payments?bill_id={a_bill['id']}").json()[0][
        "is_voided"
    ]


def test_a_bill_payment_in_a_completed_reconciliation_cannot_be_voided(
    client, db_session, seed_accounts, a_bill
):
    from app.models.banking import Reconciliation, ReconciliationStatus
    from app.models.bills import BillPayment

    paid = _pay(
        client, a_bill["vendor_id"], [{"bill_id": a_bill["id"], "amount": 450.0}]
    )
    rec = Reconciliation(
        account_id=seed_accounts["1000"].id,
        statement_date=date(2026, 9, 30),
        statement_balance=Decimal("-450.00"),
        status=ReconciliationStatus.COMPLETED,
    )
    db_session.add(rec)
    db_session.flush()
    payment = db_session.get(BillPayment, paid["id"])
    for ln in payment.transaction.lines:
        if ln.account_id == seed_accounts["1000"].id:
            ln.reconciliation_id = rec.id
            ln.cleared = True
    db_session.commit()

    r = client.post(f"/api/bill-payments/{paid['id']}/void")
    assert r.status_code == 400, r.text
    assert "reconciliation" in r.json()["detail"]
    assert client.get(f"/api/bills/{a_bill['id']}").json()["status"] == "paid"
