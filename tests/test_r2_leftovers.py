"""Four items the round-2 fix workers found and left open on #731:

1. a sub change order voided after a date dropped its commitment lines from
   that date's Commitment Status and cost-to-complete;
2. a draft pay application leaves out a change order voided since its date
   (pinned: its schedule lines are gone, so the header must agree);
3. a fully billed commitment's refusal sent the user to a sub change order,
   which a closed commitment refuses;
4. the field-ticket list's kind and status filters answered in raw keys.
"""

from datetime import date, datetime, timedelta, timezone

from app.models.change_orders import ChangeOrder
from app.services import job_reports
from app.services.job_costing import commitment_lines
from tests.test_change_orders import (
    _co,
    _job,
    _sent_commitment,
    _sub_line,
    _submit_approve,
    _vendor,
)

TODAY = date.today()


def _void_later(db, co_id, on):
    """Mark a CO voided on `on` (the void route stamps now)."""
    co = db.get(ChangeOrder, co_id)
    co.voided_at = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc)
    db.commit()


def _commitment_with_voided_sub(client, db, seed_accounts, customer_id):
    job = _job(client, customer_id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    owner = _co(client, job["id"], [_sub_line("Added", 2500)])
    sub = client.post(
        f"/api/change-orders/{owner['id']}/sub-order", json={"commitment_id": po["id"]}
    ).json()
    assert _submit_approve(client, owner["id"]).status_code == 200
    assert _submit_approve(client, sub["id"]).status_code == 200
    assert client.post(f"/api/change-orders/{sub['id']}/void").status_code == 200
    _void_later(db, sub["id"], TODAY + timedelta(days=5))
    db.expire_all()
    return job, po


def test_a_sub_co_voided_later_still_counts_on_an_earlier_date(
    client, db_session, seed_accounts, seed_customer
):
    job, po = _commitment_with_voided_sub(
        client, db_session, seed_accounts, seed_customer.id
    )

    report = job_reports.commitment_status(db_session, as_of=TODAY, job_id=job["id"])
    row = report["jobs"][0]["rows"][0]
    assert row["committed"] == 7500.0
    assert row["remaining"] == 7500.0
    lines = commitment_lines(db_session, [job["id"]], TODAY)
    assert sorted(float(ln["remaining"]) for ln in lines) == [2500.0, 5000.0]

    # after the void it is gone from both
    after = TODAY + timedelta(days=10)
    report = job_reports.commitment_status(db_session, as_of=after, job_id=job["id"])
    assert report["jobs"][0]["rows"][0]["committed"] == 5000.0
    assert [
        float(ln["amount"]) for ln in commitment_lines(db_session, [job["id"]], after)
    ] == [5000.0]


def test_a_draft_pay_app_leaves_out_a_change_order_voided_since(
    client, db_session, seed_customer
):
    """The void took the change order's schedule lines, so the draft's
    contract sum to date reads without it and still foots with them."""
    from app.services.contracts import contract_changes_after

    job = _job(client, seed_customer.id)
    co = _co(client, job["id"], [_sub_line("Added", 1000)])
    assert _submit_approve(client, co["id"]).status_code == 200
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    _void_later(db_session, co["id"], TODAY + timedelta(days=5))
    db_session.expire_all()
    from app.models.jobs import Job

    row = db_session.get(Job, job["id"])
    assert contract_changes_after(db_session, row, TODAY) == 0


def _closed_commitment(client, seed_accounts, customer_id):
    job = _job(client, customer_id)
    vendor = _vendor(client, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_commitment(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Base", "quantity": 1, "rate": 5000}],
        commitment_type="subcontract",
    )
    line_id = po["lines"][0]["id"]
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={"lines": [{"line_id": line_id, "amount": 5000}]},
    )
    assert r.status_code == 200, r.text
    po = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert po["status"] == "closed"
    return po, line_id


def test_a_closed_commitment_does_not_send_the_user_to_a_sub_co(
    client, seed_accounts, seed_customer
):
    po, line_id = _closed_commitment(client, seed_accounts, seed_customer.id)
    for rate, says in ((6000, "new commitment"), (4000, "void the bill")):
        r = client.put(
            f"/api/purchase-orders/{po['id']}",
            json={
                "lines": [
                    {"id": line_id, "description": "Base", "quantity": 1, "rate": rate}
                ]
            },
        )
        assert r.status_code == 409, r.text
        message = r.json()["detail"]["message"]
        assert says in message, message
        assert "sub change order" not in message.replace("no sub change order", "")


def test_the_field_ticket_filters_answer_in_labels(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    r = client.get("/api/field-tickets?kind=slip")
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith(
        "Kind must be T&M ticket, Material slip or Delivery ticket."
    )
    r = client.get("/api/field-tickets?status=open")
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith(
        "Status must be Draft, Signed, Closed or Void."
    )
