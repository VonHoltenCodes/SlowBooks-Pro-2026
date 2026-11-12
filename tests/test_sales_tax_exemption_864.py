"""Sales tax exemption on file (#864).

The reseller-permit record holds every exemption certificate (resale, public
entity, nonprofit, other) and a customer names the one its exempt sales
stand on. A job is charged sales tax or exempt; an exempt job's new
documents and new schedule-of-values lines start untaxed. Estimates,
invoices and sales receipts carry their own Tax exempt switch, which
unticks every line and prints "Sales tax exempt" (with the certificate)
under the totals. The Sales Tax report adds the exempt sales by reason, on
the screen, the PDF and the CSV.

What was there stays: a customer marked not taxable is never charged, the
"new lines taxable" setting starts new lines as before, and a line's own
Tax box wins on a taxed document. A read-only or accountant role reads all
of it and writes none; a bookkeeper writes it.
"""

import csv
import io
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.users import User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
PERIOD = "start_date=2026-07-01&end_date=2026-07-31"
PW = "exemption-password-864"


# ── helpers ──────────────────────────────────────────────────────────────


def _customer(client, name, taxable=True):
    r = client.post(
        "/api/customers?force=true", json={"name": name, "is_taxable": taxable}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _permit(client, customer_id, number, kind=None, **extra):
    body = {
        "entity_type": "customer",
        "entity_id": customer_id,
        "jurisdiction": "IL",
        "permit_number": number,
        **extra,
    }
    if kind is not None:
        body["kind"] = kind
    r = client.post("/api/reseller-permits", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _name_certificate(client, customer_id, permit_id):
    r = client.put(
        f"/api/customers/{customer_id}", json={"exemption_certificate_id": permit_id}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _job(client, customer_id, name="Library", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, customer_id, lines, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-07-02",
        "tax_rate": 0.10,
        "lines": lines,
        **extra,
    }
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _line(rate, taxable=True, description="Work"):
    return {
        "description": description,
        "quantity": 1,
        "rate": rate,
        "is_taxable": taxable,
    }


def _signed_in(db_session, role):
    username = f"{role}-864"
    db_session.add(
        User(
            username=username,
            display_name=username,
            password_hash=auth_service.hash_password(PW),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _token(client, role):
    r = client.post("/api/tokens", json={"label": f"{role}-864", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _who(client, db_session, role, how):
    return _signed_in(db_session, role) if how == "session" else _token(client, role)


# ── 1. the certificate kind ─────────────────────────────────────────────


def test_a_certificate_is_resale_unless_it_says_otherwise(client, seed_accounts):
    c = _customer(client, "City of Joliet")
    plain = _permit(client, c["id"], "R-1")
    assert plain["kind"] == "resale" and plain["kind_label"] == "Resale"
    for kind, label in (
        ("public_entity", "Public entity"),
        ("nonprofit", "Nonprofit"),
        ("other", "Other exemption"),
    ):
        p = _permit(client, c["id"], f"K-{kind}", kind)
        assert (p["kind"], p["kind_label"]) == (kind, label)
    listed = client.get("/api/reseller-permits?kind=public_entity").json()
    assert [p["permit_number"] for p in listed] == ["K-public_entity"]


def test_a_certificate_of_no_known_kind_is_refused_in_a_sentence(client, seed_accounts):
    c = _customer(client, "Odd Kind")
    r = client.post(
        "/api/reseller-permits",
        json={
            "entity_type": "customer",
            "entity_id": c["id"],
            "jurisdiction": "IL",
            "permit_number": "X-1",
            "kind": "diplomat",
        },
    )
    assert r.status_code == 422
    assert "resale, public entity, nonprofit or other" in r.text


def test_an_edit_that_leaves_the_kind_out_keeps_it(client, seed_accounts):
    c = _customer(client, "School District 202")
    p = _permit(client, c["id"], "PE-9", "public_entity")
    r = client.put(
        f"/api/reseller-permits/{p['id']}",
        json={
            "entity_type": "customer",
            "entity_id": c["id"],
            "jurisdiction": "IL",
            "permit_number": "PE-9",
            "notes": "renewed",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "public_entity"


def test_the_expiring_view_covers_every_kind(client, seed_accounts):
    c = _customer(client, "Food Pantry")
    soon = date.today().replace(day=1)
    _permit(client, c["id"], "NP-1", "nonprofit", expires_at=soon.isoformat())
    _permit(client, c["id"], "PE-1", "public_entity", expires_at=soon.isoformat())
    _permit(client, c["id"], "R-1", expires_at=soon.isoformat())
    kinds = {p["kind"] for p in client.get("/api/reseller-permits/expiring").json()}
    assert kinds == {"nonprofit", "public_entity", "resale"}


# ── 1b. a customer names its certificate ────────────────────────────────


def test_a_customer_names_its_own_certificate(client, seed_accounts):
    c = _customer(client, "Village Hall", taxable=False)
    p = _permit(client, c["id"], "PE-77", "public_entity")
    named = _name_certificate(client, c["id"], p["id"])
    assert named["exemption_certificate_id"] == p["id"]
    assert client.get(f"/api/customers/{c['id']}").json()[
        "exemption_certificate_id"
    ] == (p["id"])
    cleared = _name_certificate(client, c["id"], None)
    assert cleared["exemption_certificate_id"] is None


def test_a_customer_cannot_name_another_customers_certificate(client, seed_accounts):
    mine = _customer(client, "Church")
    theirs = _customer(client, "Retailer")
    p = _permit(client, theirs["id"], "R-5")
    r = client.put(
        f"/api/customers/{mine['id']}", json={"exemption_certificate_id": p["id"]}
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("That certificate is on file for someone")
    r = client.put(
        f"/api/customers/{mine['id']}", json={"exemption_certificate_id": 99999}
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "That exemption certificate is not on file."
    # a new customer has no certificate on file to name yet
    r = client.post(
        "/api/customers?force=true",
        json={"name": "New One", "exemption_certificate_id": p["id"]},
    )
    assert r.status_code == 422


def test_deleting_or_moving_a_named_certificate_unnames_it(client, seed_accounts):
    c = _customer(client, "Park District", taxable=False)
    other = _customer(client, "Elsewhere")
    p = _permit(client, c["id"], "PE-3", "public_entity")
    _name_certificate(client, c["id"], p["id"])
    r = client.put(
        f"/api/reseller-permits/{p['id']}",
        json={
            "entity_type": "customer",
            "entity_id": other["id"],
            "jurisdiction": "IL",
            "permit_number": "PE-3",
        },
    )
    assert r.status_code == 200
    assert (
        client.get(f"/api/customers/{c['id']}").json()["exemption_certificate_id"]
        is None
    )
    q = _permit(client, c["id"], "PE-4", "public_entity")
    _name_certificate(client, c["id"], q["id"])
    assert client.delete(f"/api/reseller-permits/{q['id']}").status_code == 200
    assert (
        client.get(f"/api/customers/{c['id']}").json()["exemption_certificate_id"]
        is None
    )


# ── 2. the job ───────────────────────────────────────────────────────────


def test_a_new_job_is_charged_as_its_customer_is(client, seed_accounts):
    taxed = _customer(client, "Homeowner")
    exempt = _customer(client, "County", taxable=False)
    assert _job(client, taxed["id"])["is_taxable"] is True
    assert _job(client, exempt["id"])["is_taxable"] is False
    assert (
        _job(client, taxed["id"], name="Public", is_taxable=False)["is_taxable"]
        is False
    )


def test_a_job_switched_exempt_changes_new_documents_only(client, seed_accounts):
    gc = _customer(client, "General Contractor")
    job = _job(client, gc["id"])
    before = _invoice(client, gc["id"], [_line(100)], job_id=job["id"])
    assert before["tax_amount"] == "10.00" and before["tax_exempt"] is False
    r = client.put(f"/api/jobs/{job['id']}", json={"is_taxable": False})
    assert r.status_code == 200 and r.json()["is_taxable"] is False
    still = client.get(f"/api/invoices/{before['id']}").json()
    assert still["tax_amount"] == "10.00" and still["tax_exempt"] is False
    after = _invoice(client, gc["id"], [_line(100)], job_id=job["id"])
    assert after["tax_exempt"] is True
    assert after["tax_amount"] == "0.00"
    assert [ln["is_taxable"] for ln in after["lines"]] == [False]


def test_an_exempt_jobs_new_schedule_lines_start_untaxed(client, seed_accounts):
    gc = _customer(client, "Builder")
    job = _job(client, gc["id"], is_taxable=False)
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Sitework", "kind": "work", "scheduled_value": 1000},
                # a person's own tick still wins
                {
                    "description": "Fixtures",
                    "kind": "work",
                    "scheduled_value": 500,
                    "is_taxable": True,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [ln["is_taxable"] for ln in sov] == [False, True]


def test_an_exempt_jobs_new_pay_application_charges_no_tax(client, seed_accounts):
    gc = _customer(client, "Owner Rep")
    job = _job(client, gc["id"], is_taxable=False)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 1000},
    )
    client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [{"description": "Work", "kind": "work", "scheduled_value": 1000}]
        },
    )
    r = client.post("/api/pay-applications", json={"job_id": job["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["charge_tax"] is False


def test_an_exempt_jobs_change_order_line_is_untaxed(db_session, seed_accounts):
    from types import SimpleNamespace

    from app.models.jobs import Job, SovLine
    from app.services.contracts import change_order_sov_fields, schedule_is_taxed

    job = Job(customer_id=1, name="J", is_taxable=False)
    job.sov_lines = [
        SovLine(line_no=1, kind="work", scheduled_value=10, is_taxable=True)
    ]
    co = SimpleNamespace(lines=[])
    assert schedule_is_taxed(job) is False
    db_session.add(job)
    assert change_order_sov_fields(db_session, job, co)["is_taxable"] is False


# ── 3. the document's switch ─────────────────────────────────────────────


def test_a_non_taxable_customer_is_still_never_charged(client, seed_accounts):
    c = _customer(client, "Reseller", taxable=False)
    inv = _invoice(client, c["id"], [_line(100)], tax_exempt=False)
    assert inv["tax_exempt"] is True and inv["tax_amount"] == "0.00"


def test_new_lines_taxable_still_starts_new_lines(client, seed_accounts):
    c = _customer(client, "Shopper")
    r = client.put("/api/settings", json={"new_lines_taxable": "false"})
    assert r.status_code == 200, r.text
    inv = _invoice(client, c["id"], [{"description": "x", "quantity": 1, "rate": 50}])
    assert inv["tax_exempt"] is False
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False]


def test_the_switch_on_unticks_every_line_and_off_lets_the_lines_decide(
    client, seed_accounts
):
    c = _customer(client, "Mixed")
    on = _invoice(client, c["id"], [_line(100), _line(50)], tax_exempt=True)
    assert on["tax_amount"] == "0.00"
    assert [ln["is_taxable"] for ln in on["lines"]] == [False, False]
    # an exempt job's document can be taxed by saying so
    job = _job(client, c["id"], is_taxable=False)
    off = _invoice(
        client,
        c["id"],
        [_line(100), _line(50, False)],
        job_id=job["id"],
        tax_exempt=False,
    )
    assert off["tax_exempt"] is False and off["tax_amount"] == "10.00"


def test_switching_an_invoice_exempt_untaxes_its_stored_lines_and_reposts(
    client, db_session, seed_accounts
):
    from app.models.accounts import Account
    from app.models.transactions import TransactionLine

    c = _customer(client, "Late Paperwork")
    inv = _invoice(client, c["id"], [_line(100)])
    assert inv["tax_amount"] == "10.00"
    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_exempt": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tax_exempt"] is True and body["tax_amount"] == "0.00"
    assert body["total"] == "100.00"
    assert [ln["is_taxable"] for ln in body["lines"]] == [False]
    tax_acct = (
        db_session.query(Account).filter(Account.account_number == "2200").first()
    )
    credits = sum(
        (
            Decimal(str(tl.credit or 0)) - Decimal(str(tl.debit or 0))
            for tl in db_session.query(TransactionLine).filter(
                TransactionLine.account_id == tax_acct.id
            )
        ),
        Decimal(0),
    )
    assert credits == Decimal("0")


def test_an_estimate_carries_the_switch_and_its_invoice_keeps_it(client, seed_accounts):
    c = _customer(client, "Township")
    job = _job(client, c["id"], is_taxable=False)
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": c["id"],
            "date": "2026-07-01",
            "tax_rate": 0.10,
            "job_id": job["id"],
            "lines": [_line(400)],
        },
    )
    assert r.status_code == 201, r.text
    est = r.json()
    assert est["tax_exempt"] is True and est["tax_amount"] == "0.00"
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    assert inv.json()["tax_exempt"] is True
    assert inv.json()["tax_amount"] == "0.00"


def test_an_estimate_switched_on_by_an_edit_untaxes_its_lines(client, seed_accounts):
    c = _customer(client, "Late Exemption")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": c["id"],
            "date": "2026-07-01",
            "tax_rate": 0.10,
            "lines": [_line(200)],
        },
    ).json()
    assert est["tax_amount"] == "20.00"
    r = client.put(f"/api/estimates/{est['id']}", json={"tax_exempt": True})
    assert r.status_code == 200, r.text
    assert r.json()["tax_amount"] == "0.00"
    assert [ln["is_taxable"] for ln in r.json()["lines"]] == [False]


def test_a_duplicate_keeps_the_switch(client, seed_accounts):
    c = _customer(client, "Repeat")
    inv = _invoice(client, c["id"], [_line(100)], tax_exempt=True)
    r = client.post(f"/api/invoices/{inv['id']}/duplicate")
    assert r.status_code in (200, 201), r.text
    assert r.json()["tax_exempt"] is True and r.json()["tax_amount"] == "0.00"


def test_a_sales_receipt_takes_the_switch(client, seed_accounts):
    c = _customer(client, "Counter")
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": c["id"],
            "date": "2026-07-05",
            "tax_rate": 0.10,
            "tax_exempt": True,
            "lines": [_line(80)],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["invoice"]["tax_exempt"] is True
    assert r.json()["invoice"]["tax_amount"] == "0.00"


def test_the_pdf_says_exempt_under_the_totals_with_the_certificate(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings as get_settings

    c = _customer(client, "Water Reclamation District", taxable=False)
    p = _permit(client, c["id"], "E-1234-5678", "public_entity")
    _name_certificate(client, c["id"], p["id"])
    inv = _invoice(client, c["id"], [_line(500)])
    other = _customer(client, "Plain Exempt")
    bare = _invoice(client, other["id"], [_line(100)], tax_exempt=True)
    taxed = _invoice(client, other["id"], [_line(100)])
    settings = get_settings(db_session)
    html = render_invoice_html(db_session.get(Invoice, inv["id"]), settings)
    assert "Sales tax exempt — certificate E-1234-5678 (IL)" in html
    html = render_invoice_html(db_session.get(Invoice, bare["id"]), settings)
    assert (
        "Sales tax exempt" in html
        and "certificate" not in html.split("Sales tax exempt")[1].split("<")[0]
    )
    html = render_invoice_html(db_session.get(Invoice, taxed["id"]), settings)
    assert "Sales tax exempt" not in html


def test_the_estimate_print_preview_says_exempt(client, seed_accounts):
    c = _customer(client, "Library Board")
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": c["id"],
            "date": "2026-07-01",
            "tax_exempt": True,
            "lines": [_line(150)],
        },
    )
    assert r.status_code == 201, r.text
    page = client.get(f"/api/estimates/{r.json()['id']}/print-preview")
    assert page.status_code == 200
    assert "Sales tax exempt" in page.text


# ── 4. the Sales Tax report ──────────────────────────────────────────────


@pytest.fixture
def small_book(client, seed_accounts):
    """Five sales in July, worked by hand:

    taxable sale       100.00, taxed 100.00, tax 10.00
    resale exempt      200.00 (customer not taxable, resale certificate)
    public entity      300.00 (an exempt job, public-entity certificate)
    customer exempt    400.00 (not taxable, no certificate)
    line by line        75.00, 50.00 taxed (tax 5.00), 25.00 untaxed

    Sales 1,075.00; taxable 150.00; non-taxable 925.00; tax 15.00.
    """
    shop = _customer(client, "Corner Shop")
    _invoice(client, shop["id"], [_line(100)])
    reseller = _customer(client, "Wholesale Buyer", taxable=False)
    _name_certificate(
        client, reseller["id"], _permit(client, reseller["id"], "R-1")["id"]
    )
    _invoice(client, reseller["id"], [_line(200)])
    gc = _customer(client, "GC With Public Work")
    _name_certificate(
        client,
        gc["id"],
        _permit(client, gc["id"], "PE-1", "public_entity")["id"],
    )
    job = _job(client, gc["id"], name="City Hall", is_taxable=False)
    _invoice(client, gc["id"], [_line(300)], job_id=job["id"])
    church = _customer(client, "Church Without Paper", taxable=False)
    _invoice(client, church["id"], [_line(400)])
    _invoice(client, shop["id"], [_line(50), _line(25, False)])


def test_the_report_lists_exempt_sales_by_reason(client, small_book):
    data = client.get(f"/api/reports/sales-tax?{PERIOD}&basis=accrual").json()
    assert data["total_sales"] == 1075.0
    assert data["total_taxable"] == 150.0
    assert data["total_non_taxable"] == 925.0
    assert data["total_tax"] == 15.0
    assert data["exempt_sales"] == [
        {"reason": "resale", "label": "Resale", "amount": 200.0},
        {"reason": "public_entity", "label": "Public entity", "amount": 300.0},
        {
            "reason": "no_certificate",
            "label": "Exempt, no certificate on file",
            "amount": 400.0,
        },
        {
            "reason": "line_by_line",
            "label": "Untaxed lines on taxed sales",
            "amount": 25.0,
        },
    ]
    assert sum(e["amount"] for e in data["exempt_sales"]) == data["total_non_taxable"]
    reasons = {i["customer_name"]: i["exempt_reason"] for i in data["items"]}
    assert reasons["Wholesale Buyer"] == "resale"
    assert reasons["Church Without Paper"] == "no_certificate"


def test_the_csv_carries_the_exempt_sales(client, small_book):
    r = client.get(f"/api/reports/sales-tax/csv?{PERIOD}&basis=accrual")
    assert r.status_code == 200
    rows = [row for row in csv.reader(io.StringIO(r.text)) if row]
    by_label = {row[3]: row for row in rows if len(row) > 4}
    assert by_label["Resale"][4] == "200.00"
    assert by_label["Public entity"][4] == "300.00"
    assert by_label["Exempt, no certificate on file"][4] == "400.00"
    assert by_label["Untaxed lines on taxed sales"][4] == "25.00"
    assert by_label["Total exempt sales"][4] == "925.00"


def test_the_pdf_carries_the_exempt_sales(client, db_session, small_book):
    from app.routes.reports.payables_tax import _sales_tax_sections, sales_tax_report
    from app.services.terminology import terms_from_db

    data = sales_tax_report(date(2026, 7, 1), date(2026, 7, 31), "accrual", db_session)
    sections = _sales_tax_sections(data, terms_from_db(db_session))
    exempt = next(s for s in sections if s["title"] == "Exempt Sales")
    cells = [row["cells"] for row in exempt["rows"]]
    assert cells[0][0] == "Resale" and cells[-1][0] == "Total exempt sales"
    assert "925.00" in cells[-1][1]


def test_the_cash_basis_report_lists_exempt_sales_by_what_was_paid(
    client, seed_accounts
):
    c = _customer(client, "Paying Exempt", taxable=False)
    inv = _invoice(client, c["id"], [_line(400)])
    bank = next(
        a for a in client.get("/api/accounts").json() if a.get("bank_kind") == "bank"
    )
    r = client.post(
        "/api/payments",
        json={
            "customer_id": c["id"],
            "date": "2026-07-10",
            "amount": 100,
            "deposit_to_account_id": bank["id"],
            "allocations": [{"invoice_id": inv["id"], "amount": 100}],
        },
    )
    assert r.status_code in (200, 201), r.text
    data = client.get(f"/api/reports/sales-tax?{PERIOD}&basis=cash").json()
    assert data["exempt_sales"] == [
        {
            "reason": "no_certificate",
            "label": "Exempt, no certificate on file",
            "amount": 100.0,
        }
    ]


def test_the_sales_tax_screen_draws_the_exempt_sales():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "ReportsPage.exemptSalesHtml(data)" in js
    body = js[js.index("exemptSalesHtml(data) {") :]
    assert "data.exempt_sales" in body[:600] and "Total exempt sales" in body[:2000]


# ── roles ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["readonly", "accountant"])
@pytest.mark.parametrize("how", ["session", "token"])
def test_a_reader_reads_the_exemption_and_writes_none_of_it(
    client, db_session, seed_accounts, role, how
):
    c = _customer(client, "Exempt Owner", taxable=False)
    p = _permit(client, c["id"], "PE-8", "public_entity")
    _name_certificate(client, c["id"], p["id"])
    job = _job(client, c["id"])
    inv = _invoice(client, c["id"], [_line(100)])
    who = _who(client, db_session, role, how)
    assert who.get("/api/reseller-permits").json()[0]["kind"] == "public_entity"
    assert (
        who.get(f"/api/customers/{c['id']}").json()["exemption_certificate_id"]
        == p["id"]
    )
    assert who.get(f"/api/jobs/{job['id']}").json()["is_taxable"] is False
    assert who.get(f"/api/invoices/{inv['id']}").json()["tax_exempt"] is True
    assert who.get(f"/api/reports/sales-tax?{PERIOD}").json()["exempt_sales"]
    writes = (
        who.post(
            "/api/reseller-permits",
            json={
                "entity_type": "customer",
                "entity_id": c["id"],
                "jurisdiction": "IL",
                "permit_number": "NO",
                "kind": "nonprofit",
            },
        ),
        who.put(f"/api/customers/{c['id']}", json={"exemption_certificate_id": None}),
        who.put(f"/api/jobs/{job['id']}", json={"is_taxable": True}),
        who.put(f"/api/invoices/{inv['id']}", json={"tax_exempt": False}),
    )
    for r in writes:
        assert r.status_code == 403, r.text
        assert r.json()["detail"]  # the role's own refusal, unchanged


@pytest.mark.parametrize("how", ["session", "token"])
def test_a_bookkeeper_writes_the_exemption(client, db_session, seed_accounts, how):
    c = _customer(client, "Bookkept Owner")
    who = _who(client, db_session, "bookkeeper", how)
    p = who.post(
        "/api/reseller-permits",
        json={
            "entity_type": "customer",
            "entity_id": c["id"],
            "jurisdiction": "IL",
            "permit_number": "NP-2",
            "kind": "nonprofit",
        },
    )
    assert p.status_code == 201, p.text
    r = who.put(
        f"/api/customers/{c['id']}", json={"exemption_certificate_id": p.json()["id"]}
    )
    assert r.status_code == 200, r.text
    job = who.post(
        "/api/jobs",
        json={"customer_id": c["id"], "name": "Shelter", "is_taxable": False},
    )
    assert job.status_code == 201 and job.json()["is_taxable"] is False
    inv = who.post(
        "/api/invoices",
        json={
            "customer_id": c["id"],
            "date": "2026-07-02",
            "tax_rate": 0.1,
            "job_id": job.json()["id"],
            "lines": [_line(100)],
        },
    )
    assert inv.status_code == 201, inv.text
    assert inv.json()["tax_exempt"] is True


# ── imports ──────────────────────────────────────────────────────────────


def test_a_quickbooks_customer_marked_non_taxable_stays_so(db_session, seed_accounts):
    from app.models.contacts import Customer
    from app.services.iif_import import import_customers

    import_customers(
        db_session,
        [
            {"NAME": "Exempt From QB", "TAXABLE": "N"},
            {"NAME": "Taxed From QB", "TAXABLE": "Y"},
            {"NAME": "Silent From QB"},
        ],
    )
    db_session.commit()
    got = {c.name: c.is_taxable for c in db_session.query(Customer).all()}
    assert got["Exempt From QB"] is False
    assert got["Taxed From QB"] is True and got["Silent From QB"] is True


# ── the migrations, each its own step (#770) ─────────────────────────────


def _cfg(db: Path):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def _cols(db, table):
    with sqlite3.connect(db) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_the_certificate_kind_migration(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "kind.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("b8640a1c2e3f").down_revision == "a4c8e2f6b0d3"
    command.upgrade(cfg, "a4c8e2f6b0d3")  # the step below it (#795)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO customers (id, name, is_active, is_taxable) VALUES "
            "(1, 'One Permit', 1, 0), (2, 'Two Permits', 1, 0), (3, 'Taxed', 1, 1)"
        )
        for pid, cust in ((1, 1), (2, 2), (3, 2), (4, 3)):
            conn.execute(
                "INSERT INTO reseller_permits (id, entity_type, entity_id,"
                " jurisdiction, permit_number, is_active) VALUES"
                " (?, 'customer', ?, 'IL', ?, 1)",
                (pid, cust, f"P-{pid}"),
            )
    command.upgrade(cfg, "b8640a1c2e3f")  # its own step, not the chain (#770)
    with sqlite3.connect(db) as conn:
        kinds = {r[0] for r in conn.execute("SELECT kind FROM reseller_permits")}
        named = dict(conn.execute("SELECT id, exemption_certificate_id FROM customers"))
    assert kinds == {"resale"}
    assert named == {1: 1, 2: None, 3: None}
    command.downgrade(cfg, "a4c8e2f6b0d3")
    assert "kind" not in _cols(db, "reseller_permits")
    assert "exemption_certificate_id" not in _cols(db, "customers")


def test_the_job_is_taxable_migration(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "job.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("b8640b2d3f4a").down_revision == "b8640a1c2e3f"
    command.upgrade(cfg, "b8640a1c2e3f")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        conn.execute(
            "INSERT INTO jobs (id, customer_id, name, status, is_active)"
            " VALUES (1, 1, 'J', 'in_progress', 1)"
        )
    command.upgrade(cfg, "b8640b2d3f4a")  # its own step (#770)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT is_taxable FROM jobs").fetchone()[0] == 1
    command.downgrade(cfg, "b8640a1c2e3f")
    assert "is_taxable" not in _cols(db, "jobs")


def test_the_document_tax_exempt_migration(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "doc.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("b8640c3e4a5b").down_revision == "b8640b2d3f4a"
    assert script.get_heads() == ["b8640c3e4a5b"]
    command.upgrade(cfg, "b8640b2d3f4a")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO customers (id, name, is_active, is_taxable) VALUES "
            "(1, 'Exempt', 1, 0), (2, 'Taxed', 1, 1)"
        )
        for n, cust, tax in ((1, 1, "0"), (2, 1, "5.00"), (3, 2, "0")):
            conn.execute(
                "INSERT INTO invoices (id, invoice_number, customer_id, date,"
                " status, tax_amount) VALUES (?, ?, ?, '2026-07-01', 'DRAFT', ?)",
                (n, f"I-{n}", cust, tax),
            )
            conn.execute(
                "INSERT INTO estimates (id, estimate_number, customer_id, date,"
                " status, tax_amount) VALUES (?, ?, ?, '2026-07-01', 'PENDING', ?)",
                (n, f"E-{n}", cust, tax),
            )
    command.upgrade(cfg, "b8640c3e4a5b")  # its own step (#770)
    with sqlite3.connect(db) as conn:
        for table in ("invoices", "estimates"):
            got = dict(conn.execute(f"SELECT id, tax_exempt FROM {table}"))
            # exempt where it already was: a non-taxable customer, no tax on it
            assert got == {1: 1, 2: 0, 3: 0}, table
    command.downgrade(cfg, "b8640b2d3f4a")
    assert "tax_exempt" not in _cols(db, "invoices")
    assert "tax_exempt" not in _cols(db, "estimates")


def test_a_credit_memo_on_an_exempt_invoice_gives_back_no_tax(client, seed_accounts):
    c = _customer(client, "Returning Exempt Buyer")
    inv = _invoice(client, c["id"], [_line(100)], tax_exempt=True)
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": c["id"],
            "date": "2026-07-09",
            "original_invoice_id": inv["id"],
            "tax_rate": 0.10,
            "lines": [_line(40)],
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["tax_amount"])) == 0
