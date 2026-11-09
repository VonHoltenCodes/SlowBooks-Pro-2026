"""Job Cost by Vendor reads each entry's vendor from the document behind it
(owner, PM and accountant views alike): bill and bill-payment postings never
stamped Transaction.vendor_id, so every dollar fell under "No vendor". The
bill figures tie to A/P Aging for the same job and date."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer, Vendor
from app.services.accounting import create_journal_entry


@pytest.fixture
def world(client, db_session, seed_accounts):
    cust = Customer(name="Lakeside Homes", is_active=True)
    sparks = Vendor(
        name="Sparks Electric",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    metro = Vendor(name="Metro Hardware", is_active=True)
    db_session.add_all([cust, sparks, metro])
    db_session.commit()
    r = client.post("/api/jobs", json={"customer_id": cust.id, "name": "Lakeside"})
    assert r.status_code == 201, r.text
    return {"job": r.json(), "sparks": sparks, "metro": metro, "acc": seed_accounts}


def _rows(client, job_id, **params):
    params = {"start_date": "2026-01-01", **params}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    data = client.get(f"/api/reports/job-cost-by-vendor?job_id={job_id}&{qs}").json()
    return {r["vendor_name"]: r for r in data["jobs"][0]["rows"]}, data


def test_every_posting_reads_under_its_vendor(client, db_session, world):
    job, sparks, metro, acc = (
        world["job"],
        world["sparks"],
        world["metro"],
        world["acc"],
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": sparks.id,
            "date": "2026-03-01",
            "job_id": job["id"],
            "retainage_held": 1000,
            "lines": [{"description": "Rough-in", "quantity": 1, "rate": 10000}],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": sparks.id,
            "date": "2026-03-15",
            "amount": 4000,
            "allocations": [{"bill_id": bill["id"], "amount": 4000}],
        },
    )
    assert r.status_code == 201, r.text
    # an expense names its payee vendor; a journal has none
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-03-20",
            "vendor_id": metro.id,
            "expense_account_id": acc["6000"].id,
            "paid_from_account_id": acc["1000"].id,
            "amount": "500",
            "job_id": job["id"],
        },
    )
    assert r.status_code == 201, r.text
    create_journal_entry(
        db_session,
        date(2026, 3, 22),
        "job cost",
        [
            {
                "account_id": acc["6000"].id,
                "debit": Decimal("75"),
                "job_id": job["id"],
            },
            {"account_id": acc["1000"].id, "credit": Decimal("75")},
        ],
    )
    db_session.commit()

    rows, data = _rows(client, job["id"], end_date="2026-12-31")
    assert set(rows) == {"Sparks Electric", "Metro Hardware", "No vendor"}
    s = rows["Sparks Electric"]
    assert (s["cost"], s["bills_count"], s["paid_to_date"]) == (10000.0, 1, 4000.0)
    assert (s["retainage_outstanding"], s["open_ap"]) == (1000.0, 5000.0)
    assert rows["Metro Hardware"]["cost"] == 500.0
    assert rows["No vendor"]["cost"] == 75.0
    assert data["totals"]["cost"] == 10575.0

    # open A/P ties to A/P Aging for the same date; before the payment it was whole
    aging = client.get("/api/reports/ap-aging?as_of_date=2026-12-31").json()
    sparks_aging = next(i for i in aging["items"] if i["vendor_id"] == sparks.id)
    assert sparks_aging["total"] == s["open_ap"]
    early, _ = _rows(client, job["id"], end_date="2026-03-10")
    assert early["Sparks Electric"]["paid_to_date"] == 0.0
    assert early["Sparks Electric"]["open_ap"] == 9000.0
    aging = client.get("/api/reports/ap-aging?as_of_date=2026-03-10").json()
    sparks_aging = next(i for i in aging["items"] if i["vendor_id"] == sparks.id)
    assert sparks_aging["total"] == 9000.0


def test_a_voided_bill_nets_to_zero_under_its_vendor(client, world):
    job, sparks = world["job"], world["sparks"]
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": sparks.id,
            "date": "2026-03-01",
            "job_id": job["id"],
            "lines": [{"description": "Rough-in", "quantity": 1, "rate": 800}],
        },
    )
    assert r.status_code == 201, r.text
    assert client.post(f"/api/bills/{r.json()['id']}/void").status_code == 200
    rows, _ = _rows(client, job["id"], end_date="2026-12-31")
    assert rows["Sparks Electric"]["cost"] == 0.0
    assert rows["Sparks Electric"]["bills_count"] == 0
    assert "No vendor" not in rows


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_job_cost_by_vendor_foots_with_an_unapplied_vendor_credit(
    client, db_session, seed_accounts, seed_customer
):
    """A $500 vendor credit for Ready Mix on the job
    read cost 37,500, paid 38,000, retainage 0, open A/P 0 (paid + held +
    open ≠ cost) while A/P Aging showed Ready Mix -500. A Business company,
    since the report is in every mode."""
    from app.models.contacts import Vendor

    mix = Vendor(
        name="Ready Mix",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add(mix)
    db_session.commit()
    job = _job(client, seed_customer.id, name="Bayview")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": mix.id,
            "date": "2026-08-01",
            "job_id": job["id"],
            "lines": [{"description": "Concrete", "quantity": 1, "rate": 38000}],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": mix.id,
            "date": "2026-08-15",
            "amount": 38000,
            "allocations": [{"bill_id": bill["id"], "amount": 38000}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": mix.id,
            "date": "2026-09-02",
            "lines": [
                {
                    "description": "Short load",
                    "quantity": 1,
                    "rate": 500,
                    "account_id": seed_accounts["6000"].id,
                    "job_id": job["id"],
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    def row(end):
        data = client.get(
            f"/api/reports/job-cost-by-vendor?job_id={job['id']}"
            f"&start_date=2026-01-01&end_date={end}"
        ).json()
        return next(x for x in data["jobs"][0]["rows"] if x["vendor_id"] == mix.id)

    now = row("2026-12-31")
    assert (now["cost"], now["paid_to_date"], now["open_ap"]) == (
        37500.0,
        38000.0,
        -500.0,
    )
    assert now["cost"] == (
        now["paid_to_date"] + now["retainage_outstanding"] + now["open_ap"]
    )
    aging = client.get("/api/reports/ap-aging?as_of_date=2026-12-31").json()
    assert next(i for i in aging["items"] if i["vendor_id"] == mix.id)["total"] == (
        now["open_ap"]
    )
    # before the credit, nothing is open
    assert row("2026-08-31")["open_ap"] == 0.0
