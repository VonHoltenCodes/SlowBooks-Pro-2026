"""Deleting a job, and deleting the files attached to a deleted record.

The app never turns SQLite's foreign keys on, so a ``CASCADE`` on a child's
job key never runs there: a job deleted with a change order, pay application
or field ticket left them keyed to its id, and the next job given that id
took them over. A job is deleted only when nothing was made for it; its own
setup (schedule of values, budget, cost-to-complete) goes with it.
"""

import pytest

from app.models.attachments import Attachment
from app.models.job_costing import JobBudget
from app.models.stored_files import StoredFile
from app.services import file_store
from tests.test_field_tickets import LABOR, _construction, _job, _ticket, _vendor


@pytest.fixture
def con(client):
    _construction(client)
    return client


def _contract(client, job_id):
    r = client.put(
        f"/api/jobs/{job_id}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 1000},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job_id}/sov",
        json={
            "lines": [{"description": "Base", "kind": "work", "scheduled_value": 1000}]
        },
    )
    assert r.status_code == 200, r.text


def test_a_job_with_a_change_order_a_pay_app_and_a_ticket_is_refused_with_the_counts(
    con, seed_customer, seed_accounts
):
    job = _job(con, seed_customer.id, name="Doomed")
    _contract(con, job["id"])
    co = con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "x",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 10,
                }
            ],
        },
    )
    assert co.status_code == 201, co.text
    pa = con.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-04-30"}
    )
    assert pa.status_code == 201, pa.text
    _ticket(con, job["id"], [LABOR])
    _ticket(con, job["id"], [LABOR])

    r = con.delete(f"/api/jobs/{job['id']}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1 change order" in detail
    assert "1 pay application" in detail
    assert "2 field tickets" in detail
    assert "mark the job inactive" in detail

    # Nothing moved: the job and its records are all still there.
    assert con.get(f"/api/jobs/{job['id']}").status_code == 200
    assert len(con.get(f"/api/change-orders?job_id={job['id']}").json()) == 1


def test_a_new_job_never_takes_over_a_deleted_jobs_records(
    con, seed_customer, seed_accounts
):
    """The takeover: delete a job with a draft ticket, a change order and a
    pay application, then make a job — it must start empty."""
    job = _job(con, seed_customer.id, name="Doomed")
    _contract(con, job["id"])
    con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "x",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 10,
                }
            ],
        },
    )
    con.post("/api/pay-applications", json={"job_id": job["id"], "date": "2026-04-30"})
    _ticket(con, job["id"], [LABOR])
    con.delete(f"/api/jobs/{job['id']}")

    fresh = _job(con, seed_customer.id, name="Brand new")
    if fresh["id"] == job["id"]:  # the old job is gone and its id reused
        assert con.get(f"/api/field-tickets?job_id={fresh['id']}").json() == []
        assert con.get(f"/api/change-orders?job_id={fresh['id']}").json() == []
        assert con.get(f"/api/pay-applications?job_id={fresh['id']}").json() == []
    else:  # the delete was refused, so the old job keeps its own records
        assert con.get(f"/api/jobs/{job['id']}").status_code == 200


def test_a_job_with_only_a_draft_purchase_order_is_refused_too(
    client, seed_customer, seed_accounts
):
    """Not only construction records: a draft purchase order on the job is
    something made for it, and would be handed to the next job."""
    job = _job(client, seed_customer.id, name="Doomed")
    vendor = _vendor(client)
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "job_id": job["id"],
            "date": "2026-06-01",
            "lines": [{"description": "wire", "quantity": 10, "rate": 2}],
        },
    )
    assert r.status_code == 201, r.text
    r = client.delete(f"/api/jobs/{job['id']}")
    assert r.status_code == 409, r.text
    assert "1 purchase order" in r.json()["detail"]


def test_a_job_with_only_its_own_setup_deletes_and_the_setup_goes_with_it(
    con, seed_customer, seed_accounts, db_session
):
    """A contract, a schedule of values and a budget are the job's own
    setup, not records made for it: the job deletes, and none of it is left
    for the next job given that id."""
    job = _job(con, seed_customer.id, name="Setup only")
    _contract(con, job["id"])
    r = con.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_type": "material", "amount": 500}]},
    )
    assert r.status_code == 200, r.text

    assert con.delete(f"/api/jobs/{job['id']}").status_code == 200
    db_session.expire_all()
    assert db_session.query(JobBudget).filter_by(job_id=job["id"]).count() == 0

    fresh = _job(con, seed_customer.id, name="Next")
    assert con.get(f"/api/jobs/{fresh['id']}/budgets").json() == []
    assert con.get(f"/api/jobs/{fresh['id']}/sov").json().get("lines", []) == []


def test_a_job_with_nothing_on_it_deletes(client, seed_customer):
    job = _job(client, seed_customer.id, name="Empty")
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_deleting_the_draft_ticket_first_lets_the_job_delete_and_its_paper_goes(
    con, seed_customer, db_session
):
    """The way out of the refusal: delete the draft, then the job. The
    ticket's photo goes with the ticket."""
    job = _job(con, seed_customer.id, name="Doomed")
    t = _ticket(con, job["id"], [LABOR])
    r = con.post(
        f"/api/attachments/field_ticket/{t['id']}",
        files={"file": ("signed.jpg", b"\xff\xd8\xff photo", "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    assert con.delete(f"/api/jobs/{job['id']}").status_code == 409
    assert con.delete(f"/api/field-tickets/{t['id']}").status_code == 200
    assert con.delete(f"/api/jobs/{job['id']}").status_code == 200
    db_session.expire_all()
    assert db_session.query(Attachment).count() == 0
    assert db_session.query(StoredFile).count() == 0


# ── The one function a deleting route calls for its record's files ──────────

_TYPES = [
    "invoice",
    "bill",
    "expense",
    "estimate",
    "purchase_order",
    "vendor_credit",
    "vendor",
    "customer",
    "journal_entry",
    "bank_entry",
    "field_ticket",
]


@pytest.mark.parametrize("entity_type", _TYPES)
def test_deleting_a_records_attachments_takes_its_rows_and_bytes_and_nothing_else(
    db_session, entity_type
):
    mine = [
        file_store.add_attachment(
            db_session,
            entity_type=entity_type,
            entity_id=7,
            filename=f"{n}.pdf",
            content_type="application/pdf",
            data=b"%PDF " + n.encode(),
        )
        for n in ("one", "two")
    ]
    # The same id on another type and another id on this type stay.
    other_type = "bill" if entity_type != "bill" else "invoice"
    keep = [
        file_store.add_attachment(
            db_session,
            entity_type=other_type,
            entity_id=7,
            filename="other-type.pdf",
            content_type="application/pdf",
            data=b"%PDF other type",
        ),
        file_store.add_attachment(
            db_session,
            entity_type=entity_type,
            entity_id=8,
            filename="other-id.pdf",
            content_type="application/pdf",
            data=b"%PDF other id",
        ),
    ]
    db_session.commit()
    mine_files = [a.stored_file_id for a in mine]

    assert file_store.delete_record_attachments(db_session, entity_type, 7) == 2
    db_session.commit()

    left = db_session.query(Attachment).all()
    assert sorted(a.id for a in left) == sorted(a.id for a in keep)
    assert (
        db_session.query(StoredFile).filter(StoredFile.id.in_(mine_files)).count() == 0
    )
    assert db_session.query(StoredFile).count() == len(keep)
