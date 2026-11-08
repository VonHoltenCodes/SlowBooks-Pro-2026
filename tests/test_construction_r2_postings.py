"""Skeptic round 2 on #731, the postings workstream: field-ticket and
change-order figures held to the columns they land in, use tax kept at the
rate it was booked at, a pay application that cannot bill a change order by
moving its date, Job Cost by Vendor footing with a vendor credit, and the
smaller refusals (void with an unknown body, a received PO back to draft, a
billed supplier PO's cut, field labels in validation sentences).

L is ~/.cache/sb-construction-overhaul/r2/skeptic-r2-linux-report.md; M is
the macOS report quoted in the workstream brief.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.field_tickets import FieldTicket, FieldTicketLine


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def con(client):
    _construction(client)
    return client


def _message(r) -> str:
    """A 422's sentence, from an HTTPException detail or a schema error."""
    body = r.json()
    detail = body.get("detail")
    if isinstance(detail, list):
        return " ".join(e.get("message", "") for e in detail)
    return detail if isinstance(detail, str) else str(detail)


# ── 1. Field-ticket and change-order figures fit their columns ─────────────


def test_a_tm_ticket_that_overflows_is_a_422_and_saves_nothing(
    con, seed_customer, db_session
):
    """L bug 1: POST /api/field-tickets with quantity 1e15 × cost_rate 1e15
    was a 500 (decimal.InvalidOperation in ticket_out) with the ticket saved,
    and the list and Field Ticket Log then 500'd company-wide."""
    job = _job(con, seed_customer.id)
    body = {
        "job_id": job["id"],
        "kind": "tm",
        "date": "2026-10-12",
        "lines": [
            {
                "line_type": "other",
                "description": "x",
                "quantity": 1e15,
                "cost_rate": 1e15,
            }
        ],
    }
    r = con.post("/api/field-tickets", json=body)
    assert r.status_code == 422, r.text
    assert "Quantity must be less than 100,000,000,000" in _message(r)
    assert db_session.query(FieldTicket).count() == 0
    assert con.get(f"/api/field-tickets?job_id={job['id']}").status_code == 200
    assert con.get("/api/reports/field-ticket-log").status_code == 200


def test_a_line_amount_past_the_money_column_is_refused(con, seed_customer, db_session):
    """L bug 1: 99,999,999,999 × 99,999,999,999 was accepted (201) and its
    change order line read 9,999,999,999,800,000,000,001.00; a T&M cost
    rate of 1e20 (M bug 8) was accepted into Numeric(15, 4)."""
    job = _job(con, seed_customer.id)
    big = {"line_type": "other", "quantity": 99999999999, "cost_rate": 99999999999}
    r = con.post("/api/field-tickets", json={"job_id": job["id"], "lines": [big]})
    assert r.status_code == 422, r.text
    assert "more than a line can be (9,999,999,999,999.99)" in _message(r)
    r = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "lines": [{"line_type": "other", "quantity": 1, "cost_rate": 1e20}],
        },
    )
    assert r.status_code == 422, r.text
    assert "Cost rate must be less than 100,000,000,000" in _message(r)
    # a bill rate the job's markup fills in is held to its column too
    r = con.put(f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": 1000})
    assert r.status_code == 200, r.text
    r = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "lines": [{"line_type": "other", "quantity": 1, "cost_rate": 99999999999}],
        },
    )
    assert r.status_code == 422, r.text
    assert "bill rate with the job's markup" in _message(r)
    assert db_session.query(FieldTicket).count() == 0
    # an edit is held to the same limits
    t = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "lines": [{"line_type": "other", "quantity": 2, "cost_rate": 3}],
        },
    ).json()
    r = con.put(f"/api/field-tickets/{t['id']}", json={"lines": [big]})
    assert r.status_code == 422, r.text
    assert con.get(f"/api/field-tickets/{t['id']}").json()["cost_total"] == 6.0


def test_a_change_order_line_past_the_money_column_is_refused(
    con, seed_customer, db_session
):
    """L bug 1: a direct POST /api/change-orders took 99,999,999,999 ×
    99,999,999,999 too."""
    from app.models.change_orders import ChangeOrder

    job = _job(con, seed_customer.id)
    line = {"cost_type": "labor", "quantity": 99999999999, "unit_cost": 99999999999}
    r = con.post("/api/change-orders", json={"job_id": job["id"], "lines": [line]})
    assert r.status_code == 422, r.text
    assert "Line 1 comes to" in _message(r)
    assert db_session.query(ChangeOrder).count() == 0
    r = con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "lines": [{"cost_type": "labor", "quantity": 1e15, "unit_cost": 1}],
        },
    )
    assert r.status_code == 422, r.text
    assert "Quantity must be between" in _message(r)
    # a markup change alone can take the sell past the column
    co = con.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "lines": [
                {"cost_type": "labor", "quantity": 100, "unit_cost": 90000000000}
            ],
        },
    )
    assert co.status_code == 201, co.text
    r = con.put(f"/api/change-orders/{co.json()['id']}", json={"markup_pct": 50})
    assert r.status_code == 422, r.text
    assert "more than a line can be" in _message(r)


def test_make_change_order_refuses_tickets_that_sum_past_a_change_order(
    con, seed_customer
):
    """L bug 1: make-change-order built a PCO line no column can hold; now
    each ticket is in range, and their sum is refused before a PCO is made."""
    job = _job(con, seed_customer.id)
    ids = []
    for _ in range(2):
        line = {"line_type": "other", "quantity": 1, "cost_rate": 60000000000}
        t = con.post(
            "/api/field-tickets",
            json={"job_id": job["id"], "lines": [{**line, "quantity": 90}]},
        ).json()
        assert "id" in t, t
        r = con.post(
            f"/api/field-tickets/{t['id']}/sign", json={"signed_by_name": "Pat"}
        )
        assert r.status_code == 200, r.text
        ids.append(t["id"])
    r = con.post("/api/field-tickets/make-change-order", json={"ticket_ids": ids})
    assert r.status_code == 422, r.text
    assert "more than a change order can be" in _message(r)
    assert con.get(f"/api/change-orders?job_id={job['id']}").json() == []


def test_a_stored_ticket_past_the_columns_still_lists(con, seed_customer, db_session):
    """L bug 1: a ticket saved before the limits (1e15 × 1e15) must not fail
    the job's Field tickets tab, the company list or the Field Ticket Log."""
    job = _job(con, seed_customer.id)
    t = FieldTicket(
        job_id=job["id"],
        kind="tm",
        number="OLD-TM-001",
        date=date(2026, 10, 12),
        status="draft",
    )
    t.lines.append(
        FieldTicketLine(
            line_order=0,
            line_type="other",
            quantity=Decimal("1e15"),
            cost_rate=Decimal("1e15"),
            bill_rate=Decimal("1e15"),
            cost_type="other",
        )
    )
    db_session.add(t)
    db_session.commit()
    for url in (
        f"/api/field-tickets?job_id={job['id']}",
        "/api/field-tickets",
        f"/api/field-tickets/{t.id}",
        "/api/reports/field-ticket-log",
        "/api/reports/field-ticket-log/csv",
    ):
        r = con.get(url)
        assert r.status_code == 200, (url, r.text)


@pytest.fixture
def po(con, seed_customer, seed_accounts):
    """A sent PO on a job: 100 sticks of conduit at $3."""
    job = _job(con, seed_customer.id)
    v = con.post(
        "/api/vendors",
        json={
            "name": "City Supply",
            "default_expense_account_id": seed_accounts["5100"].id,
        },
    ).json()
    r = con.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-06-01",
            "job_id": job["id"],
            "lines": [{"description": "Conduit", "quantity": 100, "rate": 3}],
        },
    )
    assert r.status_code == 201, r.text
    up = con.put(f"/api/purchase-orders/{r.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return job, up.json()


def test_a_delivery_is_capped_at_what_received_qty_holds(con, po, db_session):
    """M bug 8: delivery lines allowed up to 1e11 while the PO line's
    received_qty is Numeric(12, 4) (at most 99,999,999.9999), counting what
    is already received."""
    job, order = po
    line_id = order["lines"][0]["id"]

    def deliver(qty):
        return con.post(
            "/api/field-tickets",
            json={
                "job_id": job["id"],
                "kind": "delivery",
                "purchase_order_id": order["id"],
                "over_receipt": True,
                "lines": [{"po_line_id": line_id, "quantity": qty}],
            },
        )

    assert deliver(60).status_code == 201
    r = deliver(99999999.9999)
    assert r.status_code == 422, r.text
    assert "60 already received" in _message(r)
    assert "at most 99,999,999.9999 received" in _message(r)
    assert deliver(99999939.9999).status_code == 201
    po_now = con.get(f"/api/purchase-orders/{order['id']}").json()
    assert float(po_now["lines"][0]["received_qty"]) == 99999999.9999
