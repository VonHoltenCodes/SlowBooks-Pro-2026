"""Field tickets (construction mode): T&M tickets, material slips and
delivery tickets on a job — numbering per job and kind, the default rates,
draft → signed → closed and void, the photo as an attachment, the Field
ticket log report, and the job tab.

docs/features/field-tickets/PLAN.md is the design.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import pdf_service

ROOT = Path(__file__).resolve().parents[1]

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _job(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _ticket(client, job_id, lines, kind="tm", status=201, **extra):
    body = {"job_id": job_id, "kind": kind, "lines": lines, **extra}
    r = client.post("/api/field-tickets", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _sign(client, ticket_id, name="Pat Owner", status=200):
    r = client.post(
        f"/api/field-tickets/{ticket_id}/sign",
        json={"signed_by_name": name, "signed_at": "2026-06-02"},
    )
    assert r.status_code == status, r.text
    return r.json()


def _equipment(client, name="Skid steer", rate=85):
    r = client.post("/api/equipment", json={"name": name, "hourly_rate": rate})
    assert r.status_code == 201, r.text
    return r.json()


LABOR = {
    "line_type": "labor",
    "description": "Foreman",
    "quantity": 8,
    "cost_rate": 50,
    "bill_rate": 75,
}


@pytest.fixture
def con(client):
    _construction(client)
    return client


# ── M1: records, numbering, defaults, lifecycle ─────────────────────────


def test_a_business_company_writes_no_ticket(client, seed_customer):
    job = _job(client, seed_customer.id)
    r = client.post("/api/field-tickets", json={"job_id": job["id"], "lines": []})
    assert r.status_code == 409
    assert "construction mode" in r.json()["detail"]
    # reading answers, empty
    assert client.get(f"/api/field-tickets?job_id={job['id']}").json() == []


def test_numbering_per_job_and_kind(con, seed_customer):
    job = _job(con, seed_customer.id, job_number="2601")
    other = _job(con, seed_customer.id, name="Annex")
    a = _ticket(con, job["id"], [LABOR])
    b = _ticket(con, job["id"], [LABOR])
    assert (a["number"], b["number"]) == ("2601-TM-001", "2601-TM-002")
    # another job counts its own; a job with no number uses J<id>
    c = _ticket(con, other["id"], [LABOR])
    assert c["number"] == f"J{other['id']}-TM-001"


def test_tm_totals_at_cost_and_bill_rates(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [LABOR, {**LABOR, "quantity": 2.5}])
    assert t["status"] == "draft"
    assert t["cost_total"] == 525.0  # 10.5 h × 50
    assert t["bill_total"] == 787.5  # 10.5 h × 75
    assert t["lines"][0]["cost_type"] == "labor"
    assert t["lines"][0]["unit"] == "hr"


def test_default_rates_from_equipment_and_job_markup(con, seed_customer):
    job = _job(con, seed_customer.id)
    r = con.put(f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": 10})
    assert r.status_code == 200, r.text
    eq = _equipment(con)
    t = _ticket(
        con,
        job["id"],
        [{"line_type": "equipment", "equipment_id": eq["id"], "quantity": 4}],
    )
    line = t["lines"][0]
    # the equipment's hourly rate, its name, the equipment cost type
    assert line["cost_rate"] == 85.0
    assert line["description"] == "Skid steer"
    assert line["cost_type"] == "equipment"
    # the bill rate defaults to cost plus the job's change-order markup
    assert line["bill_rate"] == 93.5
    assert t["bill_total"] == 374.0


def test_sign_locks_the_ticket(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [LABOR])
    # the typed name is required
    r = con.post(f"/api/field-tickets/{t['id']}/sign", json={"signed_by_name": " "})
    assert r.status_code == 422
    s = _sign(con, t["id"])
    assert s["status"] == "signed"
    assert (s["signed_by_name"], s["signed_at"]) == ("Pat Owner", "2026-06-02")
    # a signed ticket is what was signed: no edit, no delete, no second sign
    r = con.put(f"/api/field-tickets/{t['id']}", json={"description": "x"})
    assert r.status_code == 409
    assert con.delete(f"/api/field-tickets/{t['id']}").status_code == 409
    _sign(con, t["id"], status=409)


def test_an_empty_ticket_is_not_signed(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [])
    _sign(con, t["id"], status=422)


def test_edit_and_delete_a_draft(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [LABOR])
    r = con.put(
        f"/api/field-tickets/{t['id']}",
        json={"description": "Extra trenching", "lines": [{**LABOR, "quantity": 1}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Extra trenching"
    assert r.json()["bill_total"] == 75.0
    assert con.delete(f"/api/field-tickets/{t['id']}").json() == {"deleted": True}
    assert con.get(f"/api/field-tickets/{t['id']}").status_code == 404


def test_void_a_signed_ticket(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [LABOR])
    _sign(con, t["id"])
    r = con.post(f"/api/field-tickets/{t['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void" and r.json()["voided_at"]
    assert con.post(f"/api/field-tickets/{t['id']}/void").status_code == 409


def test_a_material_slip_is_not_signed(con, seed_customer):
    job = _job(con, seed_customer.id)
    r = con.post("/api/field-tickets", json={"job_id": job["id"], "kind": "material"})
    t = r.json()
    r = con.post(f"/api/field-tickets/{t['id']}/sign", json={"signed_by_name": "Yard"})
    assert r.status_code == 409
    assert "post it" in r.json()["detail"]


def test_a_delivery_ticket_needs_a_purchase_order(con, seed_customer):
    job = _job(con, seed_customer.id)
    r = con.post("/api/field-tickets", json={"job_id": job["id"], "kind": "delivery"})
    assert r.status_code == 422
    assert "purchase order" in r.json()["detail"]


def test_commitment_must_be_on_the_job(con, seed_customer, db_session):
    from app.models.contacts import Vendor
    from app.models.purchase_orders import POStatus, PurchaseOrder

    job = _job(con, seed_customer.id)
    other = _job(con, seed_customer.id, name="Annex")
    v = Vendor(name="Sparky Electric")
    db_session.add(v)
    db_session.flush()
    po = PurchaseOrder(
        po_number="PO-9001",
        vendor_id=v.id,
        job_id=other["id"],
        status=POStatus.SENT,
        date=__import__("datetime").date(2026, 6, 1),
        commitment_type="subcontract",
    )
    db_session.add(po)
    db_session.commit()
    r = con.post(
        "/api/field-tickets",
        json={"job_id": job["id"], "commitment_id": po.id, "lines": [LABOR]},
    )
    assert r.status_code == 422
    assert "another job" in r.json()["detail"]


def test_list_filters_by_job_kind_and_status(con, seed_customer):
    job = _job(con, seed_customer.id)
    a = _ticket(con, job["id"], [LABOR])
    _ticket(con, job["id"], [LABOR])
    _sign(con, a["id"])
    got = con.get(f"/api/field-tickets?job_id={job['id']}&status=signed").json()
    assert [t["id"] for t in got] == [a["id"]]
    assert len(con.get(f"/api/field-tickets?job_id={job['id']}&kind=tm").json()) == 2
    assert con.get("/api/field-tickets?kind=nope").status_code == 422


def test_the_signed_paper_attaches_to_the_ticket(con, seed_customer):
    job = _job(con, seed_customer.id)
    t = _ticket(con, job["id"], [LABOR])
    r = con.post(
        f"/api/attachments/field_ticket/{t['id']}",
        files={"file": ("signed.jpg", b"\xff\xd8\xff photo", "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    files = con.get(f"/api/attachments/field_ticket/{t['id']}").json()
    assert [f["filename"] for f in files] == ["signed.jpg"]
    assert con.get(f"/api/field-tickets/{t['id']}").json()["attachment_count"] == 1


# ── M1: Field ticket log ────────────────────────────────────────────────


def test_field_ticket_log_unsigned_and_signed_unbilled(con, seed_customer):
    job = _job(con, seed_customer.id)
    a = _ticket(con, job["id"], [LABOR], date="2026-06-01", description="Trench")
    _ticket(con, job["id"], [{**LABOR, "quantity": 2}], date="2026-06-03")
    voided = _ticket(con, job["id"], [LABOR], date="2026-06-01")
    _sign(con, a["id"])
    _sign(con, voided["id"])
    con.post(f"/api/field-tickets/{voided['id']}/void")
    data = con.get("/api/reports/field-ticket-log?as_of=2026-06-30").json()
    assert len(data["jobs"]) == 1
    rows = data["jobs"][0]["rows"]
    assert [r["status"] for r in rows] == ["Signed", "Unsigned"]
    assert rows[0]["signed_unbilled"] == 600.0 and rows[0]["unsigned"] is None
    assert rows[0]["signed_by"] == "Pat Owner"
    assert rows[1]["unsigned"] == 150.0
    assert data["totals"] == {"unsigned": 150.0, "signed_unbilled": 600.0}
    # as of a date before the second ticket, only the first counts
    early = con.get("/api/reports/field-ticket-log?as_of=2026-06-02").json()
    assert early["totals"] == {"unsigned": 0.0, "signed_unbilled": 600.0}


def test_field_ticket_log_csv_and_pdf(con, seed_customer, monkeypatch):
    job = _job(con, seed_customer.id, name="Tower")
    _ticket(con, job["id"], [LABOR], date="2026-06-01")
    csv = con.get("/api/reports/field-ticket-log/csv?as_of=2026-06-30")
    assert csv.status_code == 200, csv.text
    assert "Field Ticket Log" in csv.text and "Signed, not billed" in csv.text
    assert "600.00" in csv.text
    # the PDF's HTML, without WeasyPrint
    pages = []
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: pages.append(html) or b"%PDF-"
    )
    pdf = con.get("/api/reports/field-ticket-log/pdf?as_of=2026-06-30")
    assert pdf.status_code == 200, pdf.text
    assert "Field Ticket Log" in pages[0] and "Tower" in pages[0]


# ── M1: the job tab ─────────────────────────────────────────────────────


def _jobs_tabs(mode: str) -> dict:
    script = f"""
const fs = require('fs'), vm = require('vm');
const ctx = {{ console, window: {{}}, Terms: {{ isConstruction: () => {json.dumps(mode == 'construction')} }} }};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/jobs.js', 'utf8') + '\\nthis.J = JobsPage;', ctx);
console.log(JSON.stringify(ctx.J.tabLabels()));
"""
    out = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@requires_node
def test_field_tickets_tab_in_construction_mode_only():
    con = _jobs_tabs("construction")
    assert con["fieldtickets"] == "Field tickets"
    keys = list(con)
    assert keys.index("fieldtickets") == keys.index("payapps") + 1
    assert "fieldtickets" not in _jobs_tabs("business")


def test_field_tickets_script_is_loaded():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/field_tickets.js"></script>' in index
    src = (ROOT / "app/static/js/jobs.js").read_text(encoding="utf-8")
    assert "case 'fieldtickets': return await FieldTickets.html(JobsPage._job);" in src


# ── M2: T&M ticket → change order or invoice ────────────────────────────


def _vendor(client, name="Sparky Electric", **extra):
    r = client.post("/api/vendors", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _sent_po(client, vendor_id, job_id, lines, **extra):
    body = {
        "vendor_id": vendor_id,
        "date": "2026-06-01",
        "job_id": job_id,
        "lines": lines,
        **extra,
    }
    r = client.post("/api/purchase-orders", json=body)
    assert r.status_code == 201, r.text
    up = client.put(f"/api/purchase-orders/{r.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return up.json()


def _signed(client, job_id, lines, **extra):
    t = _ticket(client, job_id, lines, **extra)
    return _sign(client, t["id"])


def _make_co(client, ids, status=200):
    r = client.post("/api/field-tickets/make-change-order", json={"ticket_ids": ids})
    assert r.status_code == status, r.text
    return r.json()


def _approve(client, co_id):
    assert client.post(f"/api/change-orders/{co_id}/submit").status_code == 200
    r = client.post(f"/api/change-orders/{co_id}/approve")
    assert r.status_code == 200, r.text
    return r.json()


def test_make_change_order_from_several_tickets_at_cost_with_job_markup(
    con, seed_customer
):
    job = _job(con, seed_customer.id, job_number="2601")
    con.put(f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": 15})
    a = _signed(con, job["id"], [LABOR])  # 8 h × 50 cost
    b = _signed(
        con,
        job["id"],
        [
            {
                "line_type": "material",
                "description": "Conduit",
                "quantity": 10,
                "cost_rate": 12,
            }
        ],
    )
    made = _make_co(con, [a["id"], b["id"]])
    pco = made["change_order"]
    assert pco["kind"] == "pco" and pco["status"] == "draft"
    assert pco["number"] == "2601-PCO-001"
    # a line per ticket line, at cost; the job's markup prices it
    assert [float(ln["unit_cost"]) for ln in pco["lines"]] == [50.0, 12.0]
    assert [ln["cost_type"] for ln in pco["lines"]] == ["labor", "material"]
    assert pco["lines"][0]["description"].startswith("2601-TM-001")
    assert pco["cost_total"] == 520.0
    assert pco["sell_total"] == 598.0
    # each ticket links to it and stays signed until the owner approves
    for t in made["tickets"]:
        assert (t["change_order_number"], t["status"]) == ("2601-PCO-001", "signed")
    # already priced: not twice
    _make_co(con, [a["id"]], status=409)


def test_tickets_close_when_the_owner_co_is_approved_and_reopen_on_void(
    con, seed_customer
):
    job = _job(con, seed_customer.id)
    a = _signed(con, job["id"], [LABOR])
    pco = _make_co(con, [a["id"]])["change_order"]
    owner = con.post(f"/api/change-orders/{pco['id']}/promote").json()
    t = con.get(f"/api/field-tickets/{a['id']}").json()
    # the ticket follows its PCO into the owner CO
    assert t["change_order_number"] == owner["number"] and t["status"] == "signed"
    _approve(con, owner["id"])
    assert con.get(f"/api/field-tickets/{a['id']}").json()["status"] == "closed"
    # a closed ticket on an approved CO is not voided
    assert con.post(f"/api/field-tickets/{a['id']}/void").status_code == 409
    r = con.post(f"/api/change-orders/{owner['id']}/void")
    assert r.status_code == 200, r.text
    t = con.get(f"/api/field-tickets/{a['id']}").json()
    assert (t["status"], t["change_order_number"]) == ("signed", None)
    # free to price again
    _make_co(con, [a["id"]])


def test_a_rejected_pco_frees_its_tickets(con, seed_customer):
    job = _job(con, seed_customer.id)
    a = _signed(con, job["id"], [LABOR])
    pco = _make_co(con, [a["id"]])["change_order"]
    # while it stands, the ticket is not voided
    assert con.post(f"/api/field-tickets/{a['id']}/void").status_code == 409
    assert con.post(f"/api/change-orders/{pco['id']}/reject").status_code == 200
    assert con.get(f"/api/field-tickets/{a['id']}").json()["change_order_id"] is None
    _make_co(con, [a["id"]])


def test_only_signed_tm_tickets_on_one_job_are_priced(con, seed_customer):
    job = _job(con, seed_customer.id)
    other = _job(con, seed_customer.id, name="Annex")
    draft = _ticket(con, job["id"], [LABOR])
    r = _make_co(con, [draft["id"]], status=409)
    assert "only a signed" in r["detail"]
    a = _signed(con, job["id"], [LABOR])
    b = _signed(con, other["id"], [LABOR])
    assert "different jobs" in _make_co(con, [a["id"], b["id"]], status=409)["detail"]


def test_a_subs_ticket_adds_a_sub_change_order_on_its_commitment(
    con, seed_customer, seed_accounts
):
    job = _job(con, seed_customer.id)
    v = _vendor(con, default_expense_account_id=seed_accounts["5100"].id)
    po = _sent_po(
        con,
        v["id"],
        job["id"],
        [{"description": "Electrical", "quantity": 1, "rate": 10000}],
        commitment_type="subcontract",
    )
    sub_ticket = _signed(
        con,
        job["id"],
        [{**LABOR, "description": "Electrician", "cost_rate": 90}],
        commitment_id=po["id"],
    )
    own = _signed(con, job["id"], [LABOR])
    made = _make_co(con, [sub_ticket["id"], own["id"]])
    pco = made["change_order"]
    # the owner side carries both; the sub's work is subcontract cost
    assert [ln["cost_type"] for ln in pco["lines"]] == ["subcontract", "labor"]
    [sub] = made["sub_change_orders"]
    assert sub["kind"] == "sub" and sub["commitment_id"] == po["id"]
    # the sub CO carries the sub's ticket only, at cost, no markup
    assert sub["cost_total"] == 720.0 and sub["sell_total"] == 720.0
    tickets = {t["id"]: t for t in made["tickets"]}
    assert tickets[sub_ticket["id"]]["sub_change_order_number"] == sub["number"]
    assert tickets[own["id"]]["sub_change_order_id"] is None


def test_bill_it_on_a_job_with_no_schedule_of_values(con, seed_customer, seed_accounts):
    job = _job(con, seed_customer.id)
    a = _signed(con, job["id"], [LABOR])  # 8 h × 75 bill
    b = _signed(con, job["id"], [{**LABOR, "quantity": 2}])
    r = con.post(
        "/api/field-tickets/bill",
        json={"ticket_ids": [a["id"], b["id"]], "date": "2026-06-30"},
    )
    assert r.status_code == 200, r.text
    billed = r.json()
    assert billed["total"] == 750.0  # bill rates, not cost
    inv = con.get(f"/api/invoices/{billed['invoice_id']}").json()
    assert inv["job_id"] == job["id"] and len(inv["lines"]) == 2
    assert [float(ln["rate"]) for ln in inv["lines"]] == [75.0, 75.0]
    for t in billed["tickets"]:
        assert t["status"] == "closed" and t["invoice_number"] == inv["invoice_number"]
    # billed tickets leave the log
    log = con.get("/api/reports/field-ticket-log").json()
    assert log["totals"]["signed_unbilled"] == 0.0
    # voiding the invoice opens them again
    assert con.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    t = con.get(f"/api/field-tickets/{a['id']}").json()
    assert (t["status"], t["invoice_id"]) == ("signed", None)


def test_bill_it_refused_on_a_job_with_a_schedule_of_values(con, seed_customer):
    job = _job(con, seed_customer.id)
    r = con.put(
        f"/api/jobs/{job['id']}/sov",
        json={"lines": [{"description": "Base", "scheduled_value": 100000}]},
    )
    assert r.status_code in (200, 201), r.text
    a = _signed(con, job["id"], [LABOR])
    r = con.post("/api/field-tickets/bill", json={"ticket_ids": [a["id"]]})
    assert r.status_code == 409
    assert "change order" in r.json()["detail"]


# ── M3: material slip posting ───────────────────────────────────────────


@pytest.fixture
def stock(con, db_session, seed_accounts):
    """A tracked item with 100 on hand at $4.00 average, and the material cost
    type pointed at Materials Cost (5100), as construction setup does."""
    from decimal import Decimal

    from app.models.items import Item, ItemType
    from app.services import inventory_service
    from app.services.job_costing import cost_type_map

    material = cost_type_map(db_session)["material"]
    material.default_account_id = seed_accounts["5100"].id
    item = Item(
        name="Rebar #4",
        item_type=ItemType.PRODUCT,
        rate=Decimal("9"),
        cost=Decimal("4"),
        track_inventory=True,
        asset_account_id=seed_accounts["1300"].id,
    )
    db_session.add(item)
    db_session.flush()
    inventory_service.record_purchase(
        db_session,
        item,
        quantity=Decimal("100"),
        unit_cost=Decimal("4"),
        source_type="adjustment",
        source_id=item.id,
        post_journal=False,
    )
    db_session.commit()
    return item


def _balance(db_session, account_id, job_id=None):
    from sqlalchemy import func

    from app.models.transactions import TransactionLine

    q = db_session.query(
        func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0)
    ).filter(TransactionLine.account_id == account_id)
    if job_id is not None:
        q = q.filter(TransactionLine.job_id == job_id)
    return float(q.scalar())


def _slip(con, job_id, item_id, qty, status=201):
    return _ticket(
        con,
        job_id,
        [{"line_type": "material", "item_id": item_id, "quantity": qty}],
        kind="material",
        status=status,
    )


def test_posting_a_material_slip_charges_the_job_and_moves_stock(
    con, seed_customer, stock, seed_accounts, db_session
):
    job = _job(con, seed_customer.id)
    slip = _slip_on(con, job["id"], stock.id, 25, date="2026-06-05")
    # the slip's line is at the item's average cost
    assert slip["lines"][0]["cost_rate"] == 4.0 and slip["cost_total"] == 100.0
    r = con.post(f"/api/field-tickets/{slip['id']}/post", json={"date": "2026-06-05"})
    assert r.status_code == 200, r.text
    posted = r.json()
    assert posted["status"] == "closed" and posted["job_cost_number"]
    jc = con.get(f"/api/job-costs/{posted['job_cost_id']}").json()
    assert jc["source"] == "material_slip" and float(jc["total"]) == 100.0
    # DR Materials Cost on the job / CR Inventory
    assert _balance(db_session, seed_accounts["5100"].id, job["id"]) == 100.0
    assert _balance(db_session, seed_accounts["1300"].id) == -100.0
    db_session.expire_all()
    assert float(stock.quantity_on_hand) == 75.0
    # the job's cost detail shows it as material
    tree = con.get(f"/api/jobs/{job['id']}/cost-tree").json()
    assert tree["totals"]["actual"] == 100.0
    # posted once
    assert con.post(f"/api/field-tickets/{slip['id']}/post").status_code == 409


def test_voiding_a_slip_reverses_at_the_booked_cost(
    con, seed_customer, stock, seed_accounts, db_session
):
    from decimal import Decimal

    from app.services import inventory_service

    job = _job(con, seed_customer.id)
    slip = _slip(con, job["id"], stock.id, 25)
    con.post(f"/api/field-tickets/{slip['id']}/post")
    # the average moves after the posting: 75 @ 4 + 75 @ 8 → 150 @ 6
    db_session.expire_all()
    inventory_service.record_purchase(
        db_session,
        stock,
        quantity=Decimal("75"),
        unit_cost=Decimal("8"),
        source_type="adjustment",
        source_id=stock.id,
        post_journal=False,
    )
    db_session.commit()
    r = con.post(f"/api/field-tickets/{slip['id']}/void")
    assert r.status_code == 200, r.text
    # the job's cost and the stock come back at the $4.00 booked, not $6.00
    assert _balance(db_session, seed_accounts["5100"].id, job["id"]) == 0.0
    assert _balance(db_session, seed_accounts["1300"].id) == 0.0
    db_session.expire_all()
    assert float(stock.quantity_on_hand) == 175.0
    # 150 @ 6 + 25 back @ 4 = 1,000 over 175: the stock re-averages on what
    # the slip took out, not on today's average
    assert float(stock.avg_cost) == pytest.approx(5.7143, abs=1e-4)
    jc = con.get(f"/api/job-costs/{slip['job_cost_id'] or r.json()['job_cost_id']}")
    assert jc.json()["status"] == "void"


def test_a_return_slip_takes_cost_off_the_job_and_restocks(
    con, seed_customer, stock, seed_accounts, db_session
):
    job = _job(con, seed_customer.id)
    out = _slip(con, job["id"], stock.id, 30)
    con.post(f"/api/field-tickets/{out['id']}/post")
    back = _slip(con, job["id"], stock.id, -10)
    r = con.post(f"/api/field-tickets/{back['id']}/post")
    assert r.status_code == 200, r.text
    assert r.json()["cost_total"] == -40.0
    assert _balance(db_session, seed_accounts["5100"].id, job["id"]) == 80.0
    assert _balance(db_session, seed_accounts["1300"].id) == -80.0
    db_session.expire_all()
    assert float(stock.quantity_on_hand) == 80.0


def test_the_job_cost_entry_of_a_slip_is_voided_through_the_slip(
    con, seed_customer, stock
):
    job = _job(con, seed_customer.id)
    slip = _slip(con, job["id"], stock.id, 5)
    posted = con.post(f"/api/field-tickets/{slip['id']}/post").json()
    r = con.post(f"/api/job-costs/{posted['job_cost_id']}/void")
    assert r.status_code == 409
    assert "Field tickets tab" in r.json()["detail"]


def test_a_material_slip_line_is_a_stock_item(con, seed_customer, db_session):
    from app.models.items import Item, ItemType

    job = _job(con, seed_customer.id)
    r = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "kind": "material",
            "lines": [{"line_type": "material", "description": "Sand", "quantity": 1}],
        },
    )
    assert r.status_code == 422 and "stock item" in r.json()["detail"]
    service = Item(name="Hauling", item_type=ItemType.SERVICE)
    db_session.add(service)
    db_session.commit()
    _slip(con, job["id"], service.id, 1, status=422)


# ── M4: delivery tickets against PO lines ───────────────────────────────


@pytest.fixture
def po(con, seed_customer, seed_accounts):
    """A sent PO on a job: 100 sticks of conduit at $3 and 10 boxes at $20."""
    job = _job(con, seed_customer.id)
    v = _vendor(
        con, name="City Supply", default_expense_account_id=seed_accounts["5100"].id
    )
    order = _sent_po(
        con,
        v["id"],
        job["id"],
        [
            {"description": "Conduit", "quantity": 100, "rate": 3},
            {"description": "Boxes", "quantity": 10, "rate": 20},
        ],
    )
    return job, order


def _deliver(con, job_id, po_id, qtys, status=201, **extra):
    lines = [{"po_line_id": lid, "quantity": q} for lid, q in qtys.items()]
    return _ticket(
        con,
        job_id,
        lines,
        kind="delivery",
        status=status,
        purchase_order_id=po_id,
        **extra,
    )


def _po_get(con, po_id):
    return con.get(f"/api/purchase-orders/{po_id}").json()


def test_a_delivery_adds_received_quantity_and_moves_the_po(con, po):
    job, order = po
    conduit, boxes = (ln["id"] for ln in order["lines"])
    t = _deliver(
        con, job["id"], order["id"], {conduit: 60}, vendor_ticket_ref="CS-88123"
    )
    assert t["purchase_order_number"] == order["po_number"]
    assert t["lines"][0]["ordered_qty"] == 100.0
    assert t["lines"][0]["received_qty"] == 60.0
    got = _po_get(con, order["id"])
    assert [float(ln["received_qty"]) for ln in got["lines"]] == [60.0, 0.0]
    assert got["status"] == "partial"
    # the rest arrives: every line received in full
    _deliver(con, job["id"], order["id"], {conduit: 40, boxes: 10})
    assert _po_get(con, order["id"])["status"] == "received"


def test_editing_and_voiding_a_delivery_move_received_by_the_change(con, po):
    job, order = po
    conduit = order["lines"][0]["id"]
    t = _deliver(con, job["id"], order["id"], {conduit: 60})
    r = con.put(
        f"/api/field-tickets/{t['id']}",
        json={"lines": [{"po_line_id": conduit, "quantity": 45}]},
    )
    assert r.status_code == 200, r.text
    assert float(_po_get(con, order["id"])["lines"][0]["received_qty"]) == 45.0
    _sign(con, t["id"], name="Gate receiver")
    assert con.post(f"/api/field-tickets/{t['id']}/void").status_code == 200
    got = _po_get(con, order["id"])
    assert float(got["lines"][0]["received_qty"]) == 0.0
    assert got["status"] == "sent"
    # a deleted draft gives its quantity back too
    d = _deliver(con, job["id"], order["id"], {conduit: 5})
    con.delete(f"/api/field-tickets/{d['id']}")
    assert float(_po_get(con, order["id"])["lines"][0]["received_qty"]) == 0.0


def test_a_delivery_line_is_one_of_its_pos_lines(con, po, seed_accounts):
    job, order = po
    v = _vendor(con, name="Other Supply")
    other = _sent_po(
        con, v["id"], job["id"], [{"description": "Wire", "quantity": 1, "rate": 5}]
    )
    r = _deliver(con, job["id"], order["id"], {other["lines"][0]["id"]: 1}, status=422)
    assert "purchase order's lines" in r["detail"]
    _deliver(con, job["id"], order["id"], {order["lines"][0]["id"]: 0}, status=422)


def test_billing_more_than_received_warns_and_still_bills(con, po, seed_accounts):
    job, order = po
    conduit, boxes = (ln["id"] for ln in order["lines"])
    _deliver(con, job["id"], order["id"], {conduit: 50})  # $150 received
    acct = seed_accounts["5100"].id
    r = con.post(
        f"/api/purchase-orders/{order['id']}/convert-to-bill",
        json={
            "lines": [
                {"line_id": conduit, "amount": 210, "account_id": acct},
                {"line_id": boxes, "amount": 0, "account_id": acct},
            ]
        },
    )
    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert "Conduit" in warning and "$210.00" in warning and "$150.00" in warning
    # an all-zero request still bills nothing, as before
    r = con.post(
        f"/api/purchase-orders/{order['id']}/convert-to-bill",
        json={
            "lines": [
                {"line_id": boxes, "amount": 0, "account_id": acct},
                {"line_id": conduit, "amount": 0, "account_id": acct},
            ]
        },
    )
    assert r.status_code == 409  # nothing to bill, as before


def test_an_order_not_received_by_ticket_never_warns(con, po, seed_accounts):
    job, order = po
    acct = seed_accounts["5100"].id
    r = con.post(
        f"/api/purchase-orders/{order['id']}/convert-to-bill",
        json={
            "lines": [
                {"line_id": ln["id"], "account_id": acct} for ln in order["lines"]
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []


def test_commitment_status_has_a_received_column(con, po):
    job, order = po
    conduit, boxes = (ln["id"] for ln in order["lines"])
    _deliver(con, job["id"], order["id"], {conduit: 50}, date="2026-06-10")
    _deliver(con, job["id"], order["id"], {boxes: 5}, date="2026-07-10")
    data = con.get("/api/reports/commitment-status?as_of=2026-06-30").json()
    assert "Received" in [c["label"] for c in data["columns"]]
    [row] = data["jobs"][0]["rows"]
    assert row["received"] == 150.0  # the July delivery is after the date
    late = con.get("/api/reports/commitment-status?as_of=2026-07-31").json()
    assert late["jobs"][0]["rows"][0]["received"] == 250.0
    assert late["totals"]["received"] == 250.0
    csv = con.get("/api/reports/commitment-status/csv?as_of=2026-07-31").text
    assert "Received" in csv and "250.00" in csv


# ── Round 1: receiving, slips and voids that used to pass silently ──────


def _over(con, job_id, po_id, qtys, status=201, **extra):
    return _deliver(con, job_id, po_id, qtys, status=status, **extra)


def test_a_delivery_past_the_ordered_quantity_is_refused_with_a_sentence(con, po):
    job, order = po
    conduit = order["lines"][0]["id"]
    _deliver(con, job["id"], order["id"], {conduit: 60})
    for qty in (50, 10**12):
        lines = [{"po_line_id": conduit, "quantity": qty}]
        r = con.post(
            "/api/field-tickets",
            json={
                "job_id": job["id"],
                "kind": "delivery",
                "purchase_order_id": order["id"],
                "lines": lines,
            },
        )
        assert r.status_code == 409, r.text
        message = r.json()["detail"]["message"]
        assert r.json()["detail"]["code"] == "over_receipt"
        assert "100 ordered" in message and "60 already received" in message
    # nothing was received by the refused requests, and the order is as it was
    got = _po_get(con, order["id"])
    assert float(got["lines"][0]["received_qty"]) == 60.0
    assert got["status"] == "partial"


def test_an_over_delivery_is_recorded_only_when_the_request_says_so(con, po):
    job, order = po
    conduit = order["lines"][0]["id"]
    _deliver(con, job["id"], order["id"], {conduit: 60})
    r = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "kind": "delivery",
            "purchase_order_id": order["id"],
            "over_receipt": True,
            "lines": [{"po_line_id": conduit, "quantity": 50}],
        },
    )
    assert r.status_code == 201, r.text
    ticket = r.json()
    assert ticket["lines"][0]["over_receipt"] is True
    [warning] = ticket["warnings"]
    assert "110 received against 100 ordered" in warning
    assert float(_po_get(con, order["id"])["lines"][0]["received_qty"]) == 110.0
    # the mark and the warning stay on a later read, and a plain edit of the
    # ticket (no new lines) keeps the over-delivery it already recorded
    again = con.get(f"/api/field-tickets/{ticket['id']}").json()
    assert again["lines"][0]["over_receipt"] and again["warnings"] == [warning]
    r = con.put(f"/api/field-tickets/{ticket['id']}", json={"description": "Extra"})
    assert r.status_code == 200, r.text
    # an in-range delivery carries no mark
    clean = _deliver(con, job["id"], order["id"], {order["lines"][1]["id"]: 5})
    assert clean["lines"][0]["over_receipt"] is False and clean["warnings"] == []
    # a quantity beyond what the column holds is a typo, over_receipt or not
    r = con.post(
        "/api/field-tickets",
        json={
            "job_id": job["id"],
            "kind": "delivery",
            "purchase_order_id": order["id"],
            "over_receipt": True,
            "lines": [{"po_line_id": conduit, "quantity": 10**15}],
        },
    )
    assert r.status_code == 422 and "100,000,000,000" in r.text


def test_an_edit_that_takes_a_delivery_past_the_order_is_refused_too(con, po):
    job, order = po
    conduit = order["lines"][0]["id"]
    t = _deliver(con, job["id"], order["id"], {conduit: 60})
    r = con.put(
        f"/api/field-tickets/{t['id']}",
        json={"lines": [{"po_line_id": conduit, "quantity": 150}]},
    )
    assert r.status_code == 409 and "100 ordered" in r.json()["detail"]["message"]
    # the refused edit left the ticket's 60 where it was
    assert float(_po_get(con, order["id"])["lines"][0]["received_qty"]) == 60.0
    assert con.get(f"/api/field-tickets/{t['id']}").json()["lines"][0]["quantity"] == 60


def _put_po_lines(con, order, lines):
    return con.put(f"/api/purchase-orders/{order['id']}", json={"lines": lines})


def test_a_po_line_with_a_delivery_cannot_be_removed_or_cut_below_it(con, po):
    job, order = po
    conduit, boxes = order["lines"]
    t = _deliver(con, job["id"], order["id"], {conduit["id"]: 100})
    keep = {"id": boxes["id"], "description": "Boxes", "quantity": 10, "rate": 20}
    full = {"id": conduit["id"], "description": "Conduit", "quantity": 100, "rate": 3}
    # dropping the received line
    r = _put_po_lines(con, order, [keep])
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "commitment_received"
    assert t["number"] in r.json()["detail"]["message"]
    # shrinking it below what arrived
    r = _put_po_lines(con, order, [keep, {**full, "quantity": 20}])
    assert r.status_code == 409, r.text
    assert t["number"] in r.json()["detail"]["message"]
    assert "100 received" in r.json()["detail"]["message"]
    got = _po_get(con, order["id"])
    assert [float(ln["quantity"]) for ln in got["lines"]] == [100.0, 10.0]
    # the line can grow, and an unreceived line is edited freely
    r = _put_po_lines(con, order, [{**keep, "quantity": 4}, {**full, "quantity": 120}])
    assert r.status_code == 200, r.text
    # with the ticket void, the line goes
    _sign(con, t["id"], name="Gate")
    assert con.post(f"/api/field-tickets/{t['id']}/void").status_code == 200
    assert _put_po_lines(con, order, [keep]).status_code == 200


def _slip_on(con, job_id, item_id, qty, **extra):
    return _ticket(
        con,
        job_id,
        [{"line_type": "material", "item_id": item_id, "quantity": qty}],
        kind="material",
        **extra,
    )


def test_a_slip_past_the_stock_on_hand_warns_when_oversell_is_allowed(
    con, seed_customer, stock, db_session
):
    job = _job(con, seed_customer.id)
    slip = _slip(con, job["id"], stock.id, 150)  # 100 on hand
    r = con.post(f"/api/field-tickets/{slip['id']}/post")
    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert "Rebar #4" in warning and "100 on hand" in warning and "-50" in warning
    db_session.expire_all()
    assert float(stock.quantity_on_hand) == -50.0


def test_a_slip_past_the_stock_is_refused_when_the_company_refuses_oversell(
    con, seed_customer, stock, db_session
):
    r = con.put("/api/settings", json={"inventory_refuse_oversell": "true"})
    assert r.status_code == 200, r.text
    job = _job(con, seed_customer.id)
    slip = _slip(con, job["id"], stock.id, 150)
    r = con.post(f"/api/field-tickets/{slip['id']}/post")
    assert r.status_code == 422, r.text
    assert "100 on hand" in r.json()["detail"] and "50 short" in r.json()["detail"]
    # nothing posted: the slip is still a draft and the shelf is as it was
    assert con.get(f"/api/field-tickets/{slip['id']}").json()["status"] == "draft"
    db_session.expire_all()
    assert float(stock.quantity_on_hand) == 100.0
    # a slip within the stock, and a return, post as ever
    ok = _slip(con, job["id"], stock.id, 90)
    assert con.post(f"/api/field-tickets/{ok['id']}/post").json()["warnings"] == []
    back = _slip(con, job["id"], stock.id, -10)
    assert con.post(f"/api/field-tickets/{back['id']}/post").status_code == 200


def test_a_slip_posts_on_its_own_date_with_movements_on_that_date(
    con, seed_customer, stock, db_session
):
    from app.models.items import InventoryMovement

    job = _job(con, seed_customer.id)
    slip = _slip_on(con, job["id"], stock.id, 20, date="2026-09-20")
    # a posting date that is not the slip's is refused, not followed
    r = con.post(f"/api/field-tickets/{slip['id']}/post", json={"date": "2026-10-02"})
    assert r.status_code == 422 and "2026-09-20" in r.json()["detail"]
    r = con.post(f"/api/field-tickets/{slip['id']}/post", json={})
    assert r.status_code == 200, r.text
    jc = con.get(f"/api/job-costs/{r.json()['job_cost_id']}").json()
    assert jc["date"] == "2026-09-20"
    db_session.expire_all()
    [move] = (
        db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "job_cost",
            InventoryMovement.source_id == jc["id"],
        )
        .all()
    )
    assert move.date.date().isoformat() == "2026-09-20"
    # the void's movement carries the slip's date as well
    assert con.post(f"/api/field-tickets/{slip['id']}/void").status_code == 200
    db_session.expire_all()
    back = (
        db_session.query(InventoryMovement)
        .filter(InventoryMovement.source_type == "job_cost_void")
        .one()
    )
    assert back.date.date().isoformat() == "2026-09-20"


def test_a_slip_dated_in_a_closed_period_is_not_posted(con, seed_customer, stock):
    job = _job(con, seed_customer.id)
    slip = _slip_on(con, job["id"], stock.id, 5, date="2026-09-20")
    assert (
        con.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code == 200
    )
    try:
        r = con.post(f"/api/field-tickets/{slip['id']}/post", json={})
        assert r.status_code == 403 and "closing date" in r.json()["detail"]
    finally:
        con.put("/api/settings", json={"closing_date": ""})
    assert con.get(f"/api/field-tickets/{slip['id']}").json()["status"] == "draft"
    # a posted slip's void is refused there too, and the tab does not offer it
    assert con.post(f"/api/field-tickets/{slip['id']}/post", json={}).status_code == 200
    con.put("/api/settings", json={"closing_date": "2026-09-30"})
    try:
        assert con.get(f"/api/field-tickets/{slip['id']}").json()["can_void"] is False
        r = con.post(f"/api/field-tickets/{slip['id']}/void")
        assert r.status_code == 403 and "closing date" in r.json()["detail"]
    finally:
        con.put("/api/settings", json={"closing_date": ""})
    assert con.post(f"/api/field-tickets/{slip['id']}/void").status_code == 200


def test_a_delivery_in_a_closed_period_is_not_edited_deleted_or_voided(con, po):
    job, order = po
    conduit = order["lines"][0]["id"]
    signed = _deliver(con, job["id"], order["id"], {conduit: 10}, date="2026-09-05")
    _sign(con, signed["id"], name="Gate")
    draft = _deliver(con, job["id"], order["id"], {conduit: 5}, date="2026-09-06")
    assert (
        con.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code == 200
    )
    try:
        r = con.post(f"/api/field-tickets/{signed['id']}/void")
        assert r.status_code == 403 and "closing date" in r.json()["detail"]
        r = con.put(f"/api/field-tickets/{draft['id']}", json={"description": "x"})
        assert r.status_code == 403
        # moving a draft out of the closed period into it is no way round it
        open_draft = _deliver(
            con, job["id"], order["id"], {conduit: 1}, date="2026-10-05"
        )
        r = con.put(
            f"/api/field-tickets/{open_draft['id']}", json={"date": "2026-09-01"}
        )
        assert r.status_code == 403
        assert con.delete(f"/api/field-tickets/{draft['id']}").status_code == 403
        # the quantity stood: 10 + 5 + 1
        assert float(_po_get(con, order["id"])["lines"][0]["received_qty"]) == 16.0
        # and the tab does not offer the void
        assert con.get(f"/api/field-tickets/{signed['id']}").json()["can_void"] is False
    finally:
        con.put("/api/settings", json={"closing_date": ""})
    assert con.post(f"/api/field-tickets/{signed['id']}/void").status_code == 200


def test_a_tm_ticket_line_cannot_have_a_negative_quantity(con, seed_customer):
    job = _job(con, seed_customer.id)
    r = con.post(
        "/api/field-tickets",
        json={"job_id": job["id"], "kind": "tm", "lines": [{**LABOR, "quantity": -8}]},
    )
    assert r.status_code == 422, r.text
    assert "negative quantity" in r.json()["detail"]
    t = _ticket(con, job["id"], [LABOR])
    r = con.put(
        f"/api/field-tickets/{t['id']}", json={"lines": [{**LABOR, "quantity": -1}]}
    )
    assert r.status_code == 422


def test_the_ticket_says_whether_void_would_be_accepted(
    con, seed_customer, seed_accounts
):
    job = _job(con, seed_customer.id)
    a = _signed(con, job["id"], [LABOR])
    assert a["can_void"] is True
    billed = con.post("/api/field-tickets/bill", json={"ticket_ids": [a["id"]]})
    assert billed.status_code == 200, billed.text
    closed = con.get(f"/api/field-tickets/{a['id']}").json()
    assert closed["status"] == "closed" and closed["can_void"] is False
    assert con.post(f"/api/field-tickets/{a['id']}/void").status_code == 409
    # the invoice voided, the ticket is signed again and can be voided
    assert (
        con.post(f"/api/invoices/{billed.json()['invoice_id']}/void").status_code == 200
    )
    assert con.get(f"/api/field-tickets/{a['id']}").json()["can_void"] is True
    voided = con.post(f"/api/field-tickets/{a['id']}/void").json()
    assert voided["status"] == "void" and voided["can_void"] is False


def test_the_tab_offers_void_only_where_the_server_allows_it():
    src = (ROOT / "app/static/js/field_tickets.js").read_text(encoding="utf-8")
    assert "&& t.can_void)" in src
    # the over-receipt question is asked and the same ticket sent again
    assert "'over_receipt'" in src and "over_receipt: true" in src


def test_a_sent_order_reads_issued_in_construction_mode():
    src = (ROOT / "app/static/js/purchase_orders.js").read_text(encoding="utf-8")
    assert "status === 'sent' ? 'Issued'" in src
    assert "statusBadge(po.status)" in src.replace(
        "PurchaseOrdersPage.statusBadge(po.status)", "statusBadge(po.status)"
    )
    assert src.count("PurchaseOrdersPage.statusBadge(po.status)") == 2
