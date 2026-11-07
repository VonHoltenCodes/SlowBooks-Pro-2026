"""#723 part 2: a pay application with no lines (made before the job had a
schedule of values; 2.31.8's #714 stops new ones) could not be given amounts,
approved, voided or removed, and New answered that it was still open. The
user voided the whole job to get out. A draft with no lines can now be
deleted, an approved one with a zero total voided, and New works after."""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.pay_applications import PayApplication


def _job(client, customer_id, name="Birch Lane Addition"):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    job = r.json()
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 36000},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Framing", "kind": "work", "scheduled_value": 36000}
            ]
        },
    )
    assert r.status_code == 200, r.text
    return job


def _empty(db_session, job_id, status="draft", no=1):
    """An application with no lines, as 2.31.7 made one on a job with no
    schedule of values."""
    pa = PayApplication(
        job_id=job_id,
        application_no=no,
        number=f"PA-{job_id}-{no:02d}",
        date=date(2026, 6, 1),
        status=status,
        tax_rate=Decimal("0"),
    )
    if status == "approved":
        pa.approved_at = datetime.now(timezone.utc)
        pa.contract_sum_to_date = Decimal("36000.00")
    db_session.add(pa)
    db_session.commit()
    return pa.id


def _new(client, job_id):
    return client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": "2026-07-01"}
    )


def test_an_empty_draft_says_why_new_is_refused_and_can_be_deleted(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    app_id = _empty(db_session, job["id"])
    # it can't be given amounts or approved: it has no lines
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    r = client.put(
        f"/api/pay-applications/{app_id}",
        json={"lines": [{"sov_line_id": sov[0]["id"], "this_period": 100}]},
    )
    assert r.status_code == 422
    assert client.post(f"/api/pay-applications/{app_id}/approve").status_code == 422
    # New says what is in the way and what to do about it
    r = _new(client, job["id"])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "application_open"
    assert detail["message"] == (
        f"PA-{job['id']}-01 has no lines because it was made before this job "
        "had a schedule of values. Delete it, then make a new pay application."
    )
    # the way out
    r = client.delete(f"/api/pay-applications/{app_id}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(PayApplication, app_id) is None
    r = _new(client, job["id"])
    assert r.status_code == 201, r.text
    assert r.json()["number"] == f"PA-{job['id']}-01"
    assert len(r.json()["lines"]) == 1


def test_an_empty_submitted_application_can_be_deleted_too(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    app_id = _empty(db_session, job["id"], status="submitted")
    assert client.delete(f"/api/pay-applications/{app_id}").status_code == 200
    assert _new(client, job["id"]).status_code == 201


def test_an_open_application_with_lines_is_deleted_too(
    client, db_session, seed_accounts, seed_customer
):
    """Whatever its lines; New names the open application, and says delete is
    a way out, until it is."""
    job = _job(client, seed_customer.id)
    r = _new(client, job["id"])
    assert r.status_code == 201
    app_id = r.json()["id"]
    r = _new(client, job["id"])
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == (
        f"PA-{job['id']}-01 is still open; submit or approve it first, or delete it."
    )
    r = client.delete(f"/api/pay-applications/{app_id}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(PayApplication, app_id) is None
    assert _new(client, job["id"]).status_code == 201


def test_an_approved_or_void_application_is_not_deleted(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    app_id = _empty(db_session, job["id"], status="approved")
    r = client.delete(f"/api/pay-applications/{app_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "application_not_open"


def test_an_empty_approved_application_is_voided_and_new_works(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    app_id = _empty(db_session, job["id"], status="approved")
    r = client.get(f"/api/pay-applications/{app_id}")
    assert r.json()["totals"]["current_due"] == 0
    r = client.post(f"/api/pay-applications/{app_id}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    r = _new(client, job["id"])
    assert r.status_code == 201, r.text
    assert r.json()["number"] == f"PA-{job['id']}-02"
    assert len(r.json()["lines"]) == 1


def test_deleting_the_empty_draft_unlocks_the_contract_sum(
    client, db_session, seed_accounts, seed_customer
):
    """A draft counts as billing for the contract-sum lock, so the empty one
    also kept the sum from being corrected."""
    job = _job(client, seed_customer.id)
    app_id = _empty(db_session, job["id"])
    url = f"/api/jobs/{job['id']}/contract"
    assert client.put(url, json={"contract_amount": 37000}).status_code == 409
    assert client.delete(f"/api/pay-applications/{app_id}").status_code == 200
    assert client.put(url, json={"contract_amount": 37000}).status_code == 200
