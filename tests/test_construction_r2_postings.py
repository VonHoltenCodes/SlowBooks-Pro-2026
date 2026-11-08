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


# ── 2. Use tax re-posts at the rate it was booked at ────────────────────────


def _use_tax_job(client, db_session, seed_customer):
    from app.models.accounts import Account

    r = client.put(
        "/api/settings",
        json={"company_type": "construction", "default_tax_rate": "6.5"},
    )
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200
    job = _job(client, seed_customer.id, name="Clinic")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.status_code == 200, r.text
    vendor = client.post("/api/vendors", json={"name": "Brick Co"}).json()
    payable = db_session.query(Account).filter_by(name="Use Tax Payable").one()
    return job, vendor, payable


def _brick(job_id, desc="Brick", rate=4000, **extra):
    return {
        "description": desc,
        "rate": rate,
        "cost_type": "material",
        "job_id": job_id,
        **extra,
    }


def _accruals(db_session, payable_id, bill_id):
    from app.models.bills import Bill
    from app.models.transactions import TransactionLine

    db_session.expire_all()
    txn_id = db_session.get(Bill, bill_id).transaction_id
    return [
        (tl.credit, tl.description)
        for tl in db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=payable_id)
        .order_by(TransactionLine.id)
    ]


def _ut4(client, db_session, seed_customer):
    """L bug 3's bill: UT-4 dated 2026-08-20, $4,000 of brick on a
    contractor-pays job, 260.00 of use tax at 6.5%; then the Use tax rate
    setting goes to 7."""
    job, vendor, payable = _use_tax_job(client, db_session, seed_customer)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "UT-4",
            "date": "2026-08-20",
            "job_id": job["id"],
            "lines": [_brick(job["id"])],
        },
    )
    assert r.status_code in (200, 201), r.text
    bill = r.json()
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (6.5%)")
    ]
    assert client.put("/api/settings", json={"use_tax_rate": "7"}).status_code == 200
    return job, bill, payable


def test_a_description_edit_re_posts_use_tax_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    """L bug 3: PUT /api/bills/{id} with only the line's description changed
    re-booked the 8/20 accrual at today's 7% (280.00), and the Sales Tax
    report row read rate 0.07."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={"lines": [_brick(job["id"], desc="Brick (corrected desc)")]},
    )
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (corrected desc) (6.5%)")
    ]
    u = client.get(
        "/api/reports/sales-tax?start_date=2026-08-01&end_date=2026-08-31"
    ).json()["use_tax"]
    assert [(i["rate"], i["amount"]) for i in u["items"]] == [(0.065, 260.0)]


def test_a_bill_number_edit_re_posts_use_tax_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    """M bug 4: PUT with only bill_number changed re-posted at 7%."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4A"})
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (6.5%)")
    ]


def test_an_amount_change_keeps_the_rate_and_a_new_use_tax_line_takes_todays(
    client, db_session, seed_accounts, seed_customer
):
    """A line whose amount changes keeps its stored rate; only a line newly
    marked for use tax accrues at today's setting."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                _brick(job["id"], rate=5000),
                _brick(
                    job["id"], desc="Sand", rate=1000, cost_type="other", use_tax=True
                ),
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("325.00"), "Use tax - Brick (6.5%)"),
        (Decimal("70.00"), "Use tax - Sand (7%)"),
    ]
    # and both keep their own rate on the next edit, after the setting moves
    assert client.put("/api/settings", json={"use_tax_rate": "8"}).status_code == 200
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4B"})
    assert r.status_code == 200, r.text
    assert [a for a, _ in _accruals(db_session, payable.id, bill["id"])] == [
        Decimal("325.00"),
        Decimal("70.00"),
    ]


def test_a_void_after_an_edit_reverses_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4A"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(account_id=payable.id).all()
    assert sum((tl.credit - tl.debit for tl in lines), Decimal("0")) == 0
    assert sorted(tl.debit for tl in lines if tl.debit) == [Decimal("260.00")]


def test_a_bill_from_a_po_keeps_its_use_tax_rate_on_an_edit(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_job(client, db_session, seed_customer)
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "job_id": job["id"],
            "lines": [_brick(job["id"], quantity=1, rate=2000)],
        },
    )
    assert po.status_code == 201, po.text
    r = client.post(f"/api/purchase-orders/{po.json()['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    bill_id = r.json()["bill_id"] if "bill_id" in r.json() else r.json()["id"]
    assert client.put("/api/settings", json={"use_tax_rate": "7"}).status_code == 200
    r = client.put(f"/api/bills/{bill_id}", json={"bill_number": "PO-B"})
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill_id) == [
        (Decimal("130.00"), "Use tax - Brick (6.5%)")
    ]


def test_the_migration_backfills_the_booked_rate_from_the_entry(
    client, db_session, seed_accounts, seed_customer
):
    """Bills booked before the column: each line named by a 2220 credit
    takes the rate its memo ends with, else credit / amount."""
    import importlib.util
    from pathlib import Path

    from app.models.bills import Bill, BillLine
    from app.models.transactions import TransactionLine

    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": bill["vendor_id"],
            "bill_number": "UT-5",
            "date": "2026-08-21",
            "job_id": job["id"],
            "lines": [
                {"description": "Labor", "rate": 300, "cost_type": "labor"},
                _brick(job["id"], desc="Block", rate=1000),
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    second = r.json()
    # the older memo had no rate on it: credit / amount
    txn_id = db_session.get(Bill, second["id"]).transaction_id
    for tl in db_session.query(TransactionLine).filter_by(
        transaction_id=txn_id, account_id=payable.id
    ):
        tl.description = "Use tax - Block"
    db_session.query(BillLine).update({BillLine.use_tax_rate: None})
    db_session.commit()

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/c7e4a9b2d1f5_bill_line_use_tax_rate.py"
    )
    spec = importlib.util.spec_from_file_location("ut_rate_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._backfill(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    rates = {
        (ln.bill_id, ln.description): ln.use_tax_rate
        for ln in db_session.query(BillLine)
    }
    assert rates == {
        (bill["id"], "Brick"): Decimal("0.065"),
        (second["id"], "Labor"): None,
        (second["id"], "Block"): Decimal("0.07"),
    }


# ── 3. A pay application cannot bill a change order by moving its date ─────


def _bayview(client, seed_customer):
    """M bug 2's job: a $100,000 contract, CO-003 dated 8/10 and approved
    effective 9/20, and a pay application on 9/01 (CO line absent)."""
    job = _job(client, seed_customer.id, name="Bayview")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 100000,
            "retainage_pct": 10,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Base", "scheduled_value": 100000}]},
    )
    assert r.status_code == 200, r.text
    co = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "date": "2026-08-10",
            "lines": [{"cost_type": "material", "quantity": 1, "unit_cost": 1000}],
        },
    ).json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve", json={"approved_on": "2026-09-20"}
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(s for s in sov if s["kind"] == "change_order")
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-09-01"}
    )
    assert r.status_code == 201, r.text
    app = r.json()
    assert co_line["id"] not in {ln["sov_line_id"] for ln in app["lines"]}
    return job, co, co_line, app


def test_moving_a_billed_change_order_line_before_its_date_is_refused(
    client, seed_accounts, seed_customer
):
    """M bug 2: PUT date 9/25 (the CO line appears), bill 100 on it, PUT
    date 9/01 → was 200 and approve billed CO-003 on a 9/01 invoice."""
    job, co, co_line, app = _bayview(client, seed_customer)
    url = f"/api/pay-applications/{app['id']}"
    assert client.put(url, json={"date": "2026-09-25"}).status_code == 200
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 100}]}
    )
    assert r.status_code == 200, r.text
    r = client.put(url, json={"date": "2026-09-01"})
    assert r.status_code == 409, r.text
    message = r.json()["detail"]["message"]
    assert co["number"] in message and "2026-09-20" in message
    assert client.get(url).json()["date"] == "2026-09-25"
    # cleared, the line no longer carries the change, and the date moves
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 0}]}
    )
    assert r.status_code == 200, r.text
    assert client.put(url, json={"date": "2026-09-01"}).status_code == 200


def test_approve_re_checks_a_change_order_line_before_its_date(
    client, db_session, seed_accounts, seed_customer
):
    """M bug 2: approve re-checks what the date PUT does (an application whose
    date reached the row some other way)."""
    job, co, co_line, app = _bayview(client, seed_customer)
    url = f"/api/pay-applications/{app['id']}"
    assert client.put(url, json={"date": "2026-09-25"}).status_code == 200
    r = client.put(
        url, json={"lines": [{"sov_line_id": co_line["id"], "this_period": 100}]}
    )
    assert r.status_code == 200, r.text
    from app.models.pay_applications import PayApplication

    db_session.expire_all()
    db_session.get(PayApplication, app["id"]).date = date(2026, 9, 1)
    db_session.commit()
    r = client.post(f"{url}/approve")
    assert r.status_code == 409, r.text
    assert co["number"] in r.json()["detail"]["message"]
    assert client.get(url).json()["status"] == "draft"


# ── 4. Job Cost by Vendor foots with a vendor credit (every mode) ───────────


def test_job_cost_by_vendor_foots_with_an_unapplied_vendor_credit(
    client, db_session, seed_accounts, seed_customer
):
    """M bug 7 / L weakness: a $500 vendor credit for Ready Mix on the job
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
            f"/api/reports/job-cost-by-vendor?job_id={job['id']}&end_date={end}"
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


# ── 5. The smaller refusals ─────────────────────────────────────────────────


def test_void_refuses_an_unknown_field_as_post_does(con, seed_customer):
    """M bug 9: POST /api/field-tickets/{id}/void {"foo": 1} voided (200)
    while /post answered 422 "'foo' is not a field this accepts."."""
    job = _job(con, seed_customer.id)
    t = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "lines": [{"line_type": "other", "quantity": 1, "cost_rate": 5}],
        },
    ).json()
    r = con.post(f"/api/field-tickets/{t['id']}/void", json={"foo": 1})
    assert r.status_code == 422, r.text
    assert "'foo' is not a field this accepts" in _message(r)
    assert con.get(f"/api/field-tickets/{t['id']}").json()["status"] == "draft"
    for action, body in (("sign", {"signed_by_name": "Pat", "foo": 1}),):
        r = con.post(f"/api/field-tickets/{t['id']}/{action}", json=body)
        assert r.status_code == 422, (action, r.text)
    # no body, or an empty one, still voids
    assert con.post(f"/api/field-tickets/{t['id']}/void", json={}).status_code == 200


def test_a_po_with_receipts_does_not_go_back_to_draft(con, po):
    """M / L4 edge: PUT status draft on a PO with a delivery received on it
    was accepted."""
    job, order = po
    t = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "kind": "delivery",
            "purchase_order_id": order["id"],
            "lines": [{"po_line_id": order["lines"][0]["id"], "quantity": 10}],
        },
    ).json()
    url = f"/api/purchase-orders/{order['id']}"
    r = con.put(url, json={"status": "draft"})
    assert r.status_code == 409, r.text
    assert "cannot go back to draft" in r.json()["detail"]["message"]
    assert t["number"] in r.json()["detail"]["message"]
    assert con.get(url).json()["status"] == "partial"
    # with the ticket void, it can
    assert con.post(f"/api/field-tickets/{t['id']}/void").status_code == 200
    assert con.put(url, json={"status": "draft"}).status_code == 200


def test_a_billed_supplier_po_says_to_void_the_bill_not_a_sub_change_order(con, po):
    """L weakness: a billed supplier PO refused a cut with "change it with a
    sub change order", which is for subcontracts."""
    job, order = po
    r = con.post(f"/api/purchase-orders/{order['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    line = con.get(f"/api/purchase-orders/{order['id']}").json()["lines"][0]
    r = con.put(
        f"/api/purchase-orders/{order['id']}",
        json={
            "lines": [
                {
                    "id": line["id"],
                    "description": "Conduit",
                    "quantity": 10,
                    "rate": 3,
                }
            ]
        },
    )
    assert r.status_code == 409, r.text
    message = r.json()["detail"]["message"]
    assert "void the bill" in message and "sub change order" not in message


def test_validation_sentences_name_the_field_and_its_choices_by_label(
    con, seed_customer
):
    """M weakness 9: "Sales_tax_treatment must be charge_owner …" and "Line 1:
    Line_type must be one of labor, equipment, material, ot…"."""
    job = _job(con, seed_customer.id)
    r = con.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": "owner"}
    )
    assert r.status_code == 422, r.text
    assert _message(r) == (
        "Sales tax must be Charge sales tax to the owner or Contractor pays tax "
        "on materials."
    )
    assert "sales_tax_treatment is one of" in r.json()["detail"][0]["msg"]
    r = con.post(
        "/api/field-tickets",
        json={"job_id": job["id"], "lines": [{"line_type": "labour", "quantity": 1}]},
    )
    assert r.status_code == 422, r.text
    assert (
        _message(r) == "Line 1: Line type must be Labor, Equipment, Material or Other."
    )
    for url, body, label in (
        ("/api/field-tickets", {"job_id": job["id"], "kind": "slip"}, "Kind"),
        ("/api/change-orders", {"job_id": job["id"], "funding": "x"}, "Funding"),
        (
            f"/api/jobs/{job['id']}/contract",
            {"contract_type": "fixed"},
            "Contract type",
        ),
        (f"/api/jobs/{job['id']}/contract", {"retainage_pct": 150}, "Retainage"),
    ):
        r = con.request("PUT" if "contract" in url else "POST", url, json=body)
        assert r.status_code == 422, (url, r.text)
        message = _message(r)
        assert message.startswith(label + " must be"), message
        assert "_" not in message, message
