"""Duplicate Invoice copies the sale, currency and all.

The copy dropped the invoice's currency and exchange rate, so the duplicate
of a EUR 850.00 invoice was a $850.00 invoice (and posted $850 where the
original booked $935), and it dropped the job and each line's job, class and
cost code, so a copied job invoice was missing from job costing. Found while
integrating the 2.17.3 exploratory fixes. The copy now carries them and
posts the way a new invoice does.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.models.accounts import Account
from app.models.transactions import Transaction, TransactionLine


def _postings(db_session, invoice_id):
    from app.models.invoices import Invoice

    db_session.expire_all()
    inv = db_session.get(Invoice, invoice_id)
    txn = db_session.get(Transaction, inv.transaction_id)
    assert txn.source_type == "invoice" and txn.source_id == invoice_id
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    return txn, sorted(
        (
            db_session.get(Account, s.account_id).account_number,
            s.debit,
            s.credit,
            s.job_id,
            s.class_id,
            s.cost_code_id,
        )
        for s in lines
    )


def test_a_foreign_invoice_is_copied_in_its_currency_with_its_dimensions(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.classes import TxnClass
    from app.models.cost_codes import CostCode
    from app.models.jobs import Job

    cls = TxnClass(name="Export")
    job = Job(customer_id=seed_customer.id, name="Hamburg fit-out")
    code = CostCode(code="SGN", name="Signage", cost_type="material")
    line_job = Job(customer_id=seed_customer.id, name="Hamburg phase 2")
    db_session.add_all([cls, job, code, line_job])
    db_session.commit()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "terms": "Net 15",
            "po_number": "PO-4411",
            "currency": "EUR",
            "exchange_rate": "1.10",
            "class_id": cls.id,
            "job_id": job.id,
            "tax_rate": "0",
            "lines": [
                {
                    "description": "Shop sign",
                    "quantity": 1,
                    "rate": 850,
                    "job_id": line_job.id,
                    "class_id": cls.id,
                    "cost_code_id": code.id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    original = r.json()

    r = client.post(f"/api/invoices/{original['id']}/duplicate")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["invoice_number"] != original["invoice_number"]
    assert copy["status"] == "draft" and copy["date"] == date.today().isoformat()
    assert copy["due_date"] == (date.today() + timedelta(days=15)).isoformat()
    assert copy["currency"] == "EUR"
    assert Decimal(copy["exchange_rate"]) == Decimal("1.10")
    assert Decimal(copy["total"]) == Decimal("850.00")
    assert (copy["job_id"], copy["class_id"], copy["po_number"]) == (
        job.id,
        cls.id,
        "PO-4411",
    )
    [ln] = copy["lines"]
    assert (ln["job_id"], ln["class_id"], ln["cost_code_id"]) == (
        line_job.id,
        cls.id,
        code.id,
    )

    # booked like the original: EUR 850 at 1.10 is $935 on A/R and income,
    # the income line keeping its job, class and cost code
    _, before = _postings(db_session, original["id"])
    txn, after = _postings(db_session, copy["id"])
    assert after == before
    assert ("1100", Decimal("935.00"), Decimal("0"), job.id, cls.id, None) in after
    assert (txn.job_id, txn.class_id) == (job.id, cls.id)


def test_a_copy_due_on_receipt_is_due_the_day_it_is_made(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "terms": "Due on Receipt",
            "lines": [{"description": "Repair", "quantity": 1, "rate": 90}],
        },
    )
    assert r.status_code == 201, r.text
    copy = client.post(f"/api/invoices/{r.json()['id']}/duplicate").json()
    assert copy["due_date"] == date.today().isoformat()
