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
