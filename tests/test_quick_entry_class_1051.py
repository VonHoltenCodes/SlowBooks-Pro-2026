"""Quick Entry and Bill Quick Entry carry a Class on each line (#1051).

The pages send each line's class on the line, with no class on the header
(tests/js/quick_entry_class_1051.test.cjs pins the pages). These tests post
what the pages send and check that each line's class reaches its posting and
the P&L by Class."""

from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.transactions import (
    LEDGER_BASIS_OPTION,
    READ_ALL,
    TransactionLine,
)

PERIOD = "?start_date=2026-11-01&end_date=2026-11-30"


def _classes(client):
    return {
        name: client.post("/api/classes", json={"name": name}).json()["id"]
        for name in ("North Building", "South Building")
    }


def _posted_classes(db_session, transaction_id, account_id):
    lines = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == transaction_id,
            TransactionLine.account_id == account_id,
        )
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )
    return sorted(
        (ln.class_id or 0, float(ln.credit or 0) + float(ln.debit or 0)) for ln in lines
    )


def _by_class(client, key):
    data = client.get("/api/reports/profit-loss-by-class" + PERIOD).json()
    return {c["class_name"]: c[key] for c in data["classes"]}


def test_a_quick_entry_invoice_posts_each_line_in_its_class(
    client, db_session, seed_accounts, seed_customer
):
    cls = _classes(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-18",
            "terms": "Net 30",
            "po_number": None,
            "tax_rate": 0,
            "notes": None,
            "lines": [
                {
                    "item_id": None,
                    "description": "Rent",
                    "quantity": 1,
                    "rate": 1500,
                    "line_order": 0,
                    "is_taxable": False,
                    "class_id": cls["North Building"],
                },
                {
                    "item_id": None,
                    "description": "Rent",
                    "quantity": 1,
                    "rate": 1200,
                    "line_order": 1,
                    "is_taxable": False,
                    "class_id": cls["South Building"],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert [ln["class_id"] for ln in r.json()["lines"]] == [
        cls["North Building"],
        cls["South Building"],
    ]
    inv = db_session.get(Invoice, r.json()["id"])
    assert _posted_classes(
        db_session, inv.transaction_id, seed_accounts["4000"].id
    ) == sorted([(cls["North Building"], 1500.0), (cls["South Building"], 1200.0)])
    by = _by_class(client, "income")
    assert (by["North Building"], by["South Building"]) == (1500.0, 1200.0)


def test_a_quick_entry_bill_posts_each_line_in_its_class(
    client, db_session, seed_accounts
):
    cls = _classes(client)
    vendor = client.post("/api/vendors", json={"name": "Harbor Plumbing"}).json()
    expense = seed_accounts["6900"].id
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": None,
            "date": "2026-11-18",
            "terms": "Net 30",
            "lines": [
                {
                    "item_id": None,
                    "account_id": expense,
                    "description": "Repair",
                    "amount": 200,
                    "class_id": cls["North Building"],
                    "line_order": 0,
                },
                {
                    "item_id": None,
                    "account_id": expense,
                    "description": "Repair",
                    "amount": 75,
                    "class_id": None,
                    "line_order": 1,
                },
            ],
            "pending_attachments": [],
        },
    )
    assert r.status_code == 201, r.text
    assert [ln["class_id"] for ln in r.json()["lines"]] == [cls["North Building"], None]
    bill = db_session.get(Bill, r.json()["id"])
    posted = _posted_classes(db_session, bill.transaction_id, expense)
    assert (cls["North Building"], 200.0) in posted
    assert len(posted) == 2
    assert _by_class(client, "expenses")["North Building"] == 200.0
