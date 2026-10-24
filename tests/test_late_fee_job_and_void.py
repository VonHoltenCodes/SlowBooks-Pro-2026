"""A late fee carries the invoice's job, and a void takes the fee with it
(#500).

A late fee posts its own A/R debit on top of the invoice. It used to carry
no job even when the invoice was job-costed, so the job's A/R was short, and
voiding the invoice reversed only the invoice — the fee's A/R debit stayed
open with nothing to apply it to. The fee now carries the invoice's job on
both lines, and the invoice void reverses the fee's posting too, so the
customer balance, A/R (1100) and the job's A/R all return to zero.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.models.transactions import Transaction, TransactionLine


def _ar_net(db_session, ar_id, job_id=None):
    q = db_session.query(
        func.coalesce(func.sum(TransactionLine.debit), 0),
        func.coalesce(func.sum(TransactionLine.credit), 0),
    ).filter(TransactionLine.account_id == ar_id)
    if job_id is not None:
        q = q.filter(TransactionLine.job_id == job_id)
    dr, cr = q.one()
    return Decimal(str(dr)) - Decimal(str(cr))


def _overdue_job_invoice(client, seed_customer):
    job = client.post(
        "/api/jobs", json={"name": "123 Main St", "customer_id": seed_customer.id}
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]

    today = date.today()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": (today - timedelta(days=60)).isoformat(),
            "due_date": (today - timedelta(days=30)).isoformat(),
            "tax_rate": 0,
            "job_id": job_id,
            "lines": [{"description": "Service", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv_id = r.json()["id"]
    assert client.post(f"/api/invoices/{inv_id}/send").status_code == 200
    return job_id, inv_id


def _apply_late_fees(client):
    client.put(
        "/api/settings",
        json={
            "late_fee_enabled": "true",
            "late_fee_rate": "5",
            "late_fee_grace_days": "0",
        },
    )
    r = client.post("/api/invoices/apply-late-fees")
    assert r.status_code == 200, r.text
    assert r.json().get("applied", 0) == 1, r.text


def test_late_fee_carries_the_invoice_job(
    client, db_session, seed_accounts, seed_customer
):
    job_id, inv_id = _overdue_job_invoice(client, seed_customer)
    _apply_late_fees(client)

    fee_txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "late_fee",
            Transaction.source_id == inv_id,
        )
        .first()
    )
    assert fee_txn is not None
    assert fee_txn.job_id == job_id
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == fee_txn.id)
        .all()
    )
    assert lines, "the late fee posted no lines"
    assert all(line.job_id == job_id for line in lines)


def test_voiding_a_late_fee_invoice_clears_ar_and_the_job(
    client, db_session, seed_accounts, seed_customer
):
    ar_id = seed_accounts["1100"].id
    job_id, inv_id = _overdue_job_invoice(client, seed_customer)
    _apply_late_fees(client)

    db_session.expire_all()
    # $100 invoice + $5 fee, all on the job.
    assert _ar_net(db_session, ar_id) == Decimal("105.00")
    assert _ar_net(db_session, ar_id, job_id) == Decimal("105.00")
    bal = client.get(f"/api/customers/{seed_customer.id}").json()["balance"]
    assert Decimal(str(bal)) == Decimal("105.00")

    r = client.post(f"/api/invoices/{inv_id}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"

    db_session.expire_all()
    assert _ar_net(db_session, ar_id) == Decimal("0")
    assert _ar_net(db_session, ar_id, job_id) == Decimal("0")
    bal = client.get(f"/api/customers/{seed_customer.id}").json()["balance"]
    assert Decimal(str(bal)) == Decimal("0")
