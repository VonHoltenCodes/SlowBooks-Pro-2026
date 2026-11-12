"""Sales tax exemption on the job (#864).

A job is charged sales tax or exempt. An exempt job holds its own exemption
certificate: number, state (or jurisdiction) and kind are required, the
expiry is optional; switching the job back to charged clears it, so a
certificate lives and dies with its job. Estimates, invoices and sales
receipts carry their own Tax exempt switch, which unticks every line; one
made exempt copies its job's certificate into its own columns, and a later
edit of the job's certificate never changes that copy, so an old PDF and an
old report reason stay as they were. The PDF prints "Sales tax exempt —
certificate <number> (<state>)" from the copy, "(expired)" when the copy
expired before the document's date, plain "Sales tax exempt" with no copy.
The Sales Tax report lists the exempt sales by reason, from the copies.

What was there stays: a customer marked not taxable is never charged and
its documents are exempt, the "new lines taxable" setting starts new lines
as before, a line's own Tax box wins on a taxed document, the customer
record and the Reseller Permits page are as they were. A read-only or
accountant role reads all of it and writes none; a bookkeeper writes it.
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
REFUSAL_START = "A tax-exempt job needs its exemption certificate, so enter"


# ── helpers ──────────────────────────────────────────────────────────────


def _customer(client, name, taxable=True):
    r = client.post(
        "/api/customers?force=true", json={"name": name, "is_taxable": taxable}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _job(client, customer_id, name="Library", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _certificate(number="E-1234-5678", state="IL", kind="public_entity", expires=None):
    cert = {
        "exempt_certificate_number": number,
        "exempt_certificate_state": state,
        "exempt_certificate_kind": kind,
    }
    if expires is not None:
        cert["exempt_certificate_expires"] = expires
    return cert


def _exempt_job(client, customer_id, name="Public Work", **cert):
    return _job(
        client, customer_id, name=name, is_taxable=False, **_certificate(**cert)
    )


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


def _estimate(client, customer_id, lines, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-07-01",
        "tax_rate": 0.10,
        "lines": lines,
        **extra,
    }
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _line(rate, taxable=True, description="Work"):
    return {
        "description": description,
        "quantity": 1,
        "rate": rate,
        "is_taxable": taxable,
    }


def _copy(doc):
    return (
        doc["exempt_certificate_number"],
        doc["exempt_certificate_state"],
        doc["exempt_certificate_kind"],
        doc["exempt_certificate_expires"],
    )


def _invoice_html(db_session, invoice_id):
    from app.models.invoices import Invoice
    from app.services.pdf_service import render_invoice_html
    from app.services.settings_service import get_all_settings as get_settings

    db_session.expire_all()
    return render_invoice_html(
        db_session.get(Invoice, invoice_id), get_settings(db_session)
    )


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


# ── 1. the job holds the certificate ─────────────────────────────────────


def test_an_exempt_job_holds_its_certificate(client, seed_accounts):
    c = _customer(client, "Water District")
    job = _exempt_job(client, c["id"], state=" il ", expires="2027-06-30")
    assert job["is_taxable"] is False
    assert _copy(job) == ("E-1234-5678", "IL", "public_entity", "2027-06-30")
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert _copy(got) == _copy(job)
    for kind in ("nonprofit", "resale", "other"):
        j = _exempt_job(client, c["id"], name=f"K {kind}", kind=kind)
        assert j["exempt_certificate_kind"] == kind


@pytest.mark.parametrize(
    "left_out, named",
    [
        (("exempt_certificate_number",), "number."),
        (("exempt_certificate_state",), "state or jurisdiction."),
        (
            ("exempt_certificate_kind",),
            "kind (public entity, nonprofit, resale or other).",
        ),
        (
            ("exempt_certificate_number", "exempt_certificate_kind"),
            "number and kind (public entity, nonprofit, resale or other).",
        ),
        (
            (
                "exempt_certificate_number",
                "exempt_certificate_state",
                "exempt_certificate_kind",
            ),
            "number, state or jurisdiction, and kind (public entity, "
            "nonprofit, resale or other).",
        ),
    ],
)
def test_exempt_without_the_certificate_is_refused_in_one_sentence(
    client, seed_accounts, left_out, named
):
    c = _customer(client, "Park District")
    body = {"customer_id": c["id"], "name": "Pavilion", "is_taxable": False}
    body.update({k: v for k, v in _certificate().items() if k not in left_out})
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail == f"{REFUSAL_START} the certificate's {named}"
    # a blank one is missing too
    body.update({k: "  " for k in left_out})
    assert client.post("/api/jobs", json=body).json()["detail"] == detail


def test_a_certificate_of_no_known_kind_is_refused_in_a_sentence(client, seed_accounts):
    c = _customer(client, "Odd Kind")
    r = client.post(
        "/api/jobs",
        json={
            "customer_id": c["id"],
            "name": "Embassy",
            "is_taxable": False,
            **_certificate(kind="diplomat"),
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "A certificate's kind is public entity, nonprofit, resale or other, "
        "so choose one of those."
    )


def test_switching_a_job_exempt_needs_the_certificate_and_charged_clears_it(
    client, seed_accounts
):
    c = _customer(client, "School Board")
    job = _job(client, c["id"])
    assert job["is_taxable"] is True and _copy(job) == (None, None, None, None)
    r = client.put(f"/api/jobs/{job['id']}", json={"is_taxable": False})
    assert r.status_code == 422
    assert r.json()["detail"].startswith(REFUSAL_START)
    r = client.put(
        f"/api/jobs/{job['id']}",
        json={"is_taxable": False, **_certificate(kind="nonprofit")},
    )
    assert r.status_code == 200, r.text
    # an edit of the rest keeps it; clearing the number alone is refused
    r = client.put(f"/api/jobs/{job['id']}", json={"notes": "renewed"})
    assert r.json()["exempt_certificate_number"] == "E-1234-5678"
    r = client.put(f"/api/jobs/{job['id']}", json={"exempt_certificate_number": None})
    assert r.status_code == 422
    assert r.json()["detail"] == f"{REFUSAL_START} the certificate's number."
    r = client.put(f"/api/jobs/{job['id']}", json={"is_taxable": True})
    assert r.status_code == 200
    assert r.json()["is_taxable"] is True
    assert _copy(r.json()) == (None, None, None, None)
    # charged with a certificate sent keeps none
    r = client.put(
        f"/api/jobs/{job['id']}", json={"is_taxable": True, **_certificate()}
    )
    assert _copy(r.json()) == (None, None, None, None)


def test_the_customer_and_the_reseller_permits_are_as_they_were(client, seed_accounts):
    c = _customer(client, "Plain Customer")
    assert (
        "exemption_certificate_id" not in client.get(f"/api/customers/{c['id']}").json()
    )
    r = client.put(f"/api/customers/{c['id']}", json={"exemption_certificate_id": 1})
    assert r.status_code == 422
    body = {
        "entity_type": "customer",
        "entity_id": c["id"],
        "jurisdiction": "IL",
        "permit_number": "R-1",
    }
    p = client.post("/api/reseller-permits", json=body)
    assert p.status_code == 201, p.text
    assert "kind" not in p.json() and "kind_label" not in p.json()
    r = client.post(
        "/api/reseller-permits", json={**body, "permit_number": "R-2", "kind": "x"}
    )
    assert r.status_code == 422
    from app.models.contacts import Customer
    from app.models.reseller_permit import ResellerPermit

    assert "exemption_certificate_id" not in Customer.__table__.columns
    assert "kind" not in ResellerPermit.__table__.columns
    import app.services.tax_exemption as te

    assert not hasattr(te, "check_certificate")
    assert not hasattr(te, "certificate_for")


# ── 2. the job ───────────────────────────────────────────────────────────


def test_a_new_job_is_charged_unless_it_says_exempt(client, seed_accounts):
    taxed = _customer(client, "Homeowner")
    exempt = _customer(client, "County", taxable=False)
    assert _job(client, taxed["id"])["is_taxable"] is True
    # a non-taxable customer's job is charged too: the customer's own flag
    # keeps its documents exempt, and no certificate is asked for
    assert _job(client, exempt["id"])["is_taxable"] is True
    assert _exempt_job(client, taxed["id"])["is_taxable"] is False


def test_a_job_switched_exempt_changes_new_documents_only(client, seed_accounts):
    gc = _customer(client, "General Contractor")
    job = _job(client, gc["id"])
    before = _invoice(client, gc["id"], [_line(100)], job_id=job["id"])
    assert before["tax_amount"] == "10.00" and before["tax_exempt"] is False
    r = client.put(
        f"/api/jobs/{job['id']}", json={"is_taxable": False, **_certificate()}
    )
    assert r.status_code == 200 and r.json()["is_taxable"] is False
    still = client.get(f"/api/invoices/{before['id']}").json()
    assert still["tax_amount"] == "10.00" and still["tax_exempt"] is False
    assert still["exempt_certificate_number"] is None
    after = _invoice(client, gc["id"], [_line(100)], job_id=job["id"])
    assert after["tax_exempt"] is True
    assert after["tax_amount"] == "0.00"
    assert [ln["is_taxable"] for ln in after["lines"]] == [False]


def test_an_exempt_jobs_new_schedule_lines_start_untaxed(client, seed_accounts):
    gc = _customer(client, "Builder")
    job = _exempt_job(client, gc["id"])
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
    job = _exempt_job(client, gc["id"])
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
    job = _exempt_job(client, c["id"])
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
    job = _exempt_job(client, c["id"])
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


# ── 3b. the document's own copy of the certificate ─────────────────────


def test_a_document_copies_its_jobs_certificate_when_it_is_made(client, seed_accounts):
    c = _customer(client, "Library Board")
    job = _exempt_job(client, c["id"], kind="nonprofit", expires="2027-06-30")
    want = ("E-1234-5678", "IL", "nonprofit", "2027-06-30")
    inv = _invoice(client, c["id"], [_line(100)], job_id=job["id"])
    assert inv["tax_exempt"] is True and _copy(inv) == want
    est = _estimate(client, c["id"], [_line(100)], job_id=job["id"])
    assert est["tax_exempt"] is True and _copy(est) == want
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": c["id"],
            "date": "2026-07-05",
            "tax_rate": 0.10,
            "job_id": job["id"],
            "lines": [_line(80)],
        },
    )
    assert r.status_code == 201, r.text
    assert _copy(r.json()["invoice"]) == want
    # a document taxed on purpose on the exempt job keeps no copy
    taxed = _invoice(client, c["id"], [_line(100)], job_id=job["id"], tax_exempt=False)
    assert _copy(taxed) == (None, None, None, None)


def test_a_later_job_edit_leaves_the_old_document_as_it_was(
    client, db_session, seed_accounts
):
    c = _customer(client, "Township")
    job = _exempt_job(client, c["id"], number="OLD-1", kind="public_entity")
    inv = _invoice(client, c["id"], [_line(100)], job_id=job["id"])
    # the certificate renewed, then the job switched back to charged
    client.put(
        f"/api/jobs/{job['id']}",
        json={"exempt_certificate_number": "NEW-2", "exempt_certificate_kind": "other"},
    )
    later = _invoice(client, c["id"], [_line(100)], job_id=job["id"])
    assert later["exempt_certificate_number"] == "NEW-2"
    client.put(f"/api/jobs/{job['id']}", json={"is_taxable": True})
    old = client.get(f"/api/invoices/{inv['id']}").json()
    assert _copy(old) == ("OLD-1", "IL", "public_entity", None)
    # an edit of the old document itself keeps its copy too
    r = client.put(f"/api/invoices/{inv['id']}", json={"notes": "re-sent"})
    assert r.status_code == 200, r.text
    assert r.json()["exempt_certificate_number"] == "OLD-1"
    html = _invoice_html(db_session, inv["id"])
    assert "Sales tax exempt — certificate OLD-1 (IL)" in html
    data = client.get(f"/api/reports/sales-tax?{PERIOD}&basis=accrual").json()
    assert {e["reason"]: e["amount"] for e in data["exempt_sales"]} == {
        "public_entity": 100.0,
        "other": 100.0,
    }


def test_an_exempt_document_takes_a_copy_when_it_gets_a_job(client, seed_accounts):
    c = _customer(client, "Fire District")
    # an estimate before its job exists: exempt, no copy
    est = _estimate(client, c["id"], [_line(100)], tax_exempt=True)
    assert est["tax_exempt"] is True and est["exempt_certificate_number"] is None
    job = _exempt_job(client, c["id"], number="FD-9")
    r = client.put(f"/api/estimates/{est['id']}", json={"job_id": job["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["exempt_certificate_number"] == "FD-9"
    # an invoice on the job switched on later takes it then
    inv = _invoice(client, c["id"], [_line(100)], job_id=job["id"], tax_exempt=False)
    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_exempt": True})
    assert r.status_code == 200, r.text
    assert r.json()["exempt_certificate_number"] == "FD-9"
    # switched off, it keeps none
    r = client.put(f"/api/invoices/{inv['id']}", json={"tax_exempt": False})
    assert r.json()["exempt_certificate_number"] is None


def test_a_converted_estimate_and_a_duplicate_copy_the_job(client, seed_accounts):
    c = _customer(client, "Sanitary District")
    job = _exempt_job(client, c["id"], number="SD-1")
    est = _estimate(client, c["id"], [_line(400)], job_id=job["id"])
    inv = client.post(f"/api/estimates/{est['id']}/convert").json()
    assert inv["exempt_certificate_number"] == "SD-1"
    dup = client.post(f"/api/invoices/{inv['id']}/duplicate").json()
    assert dup["tax_exempt"] is True and dup["exempt_certificate_number"] == "SD-1"


def test_an_import_or_a_run_copies_the_jobs_certificate_on_insert(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice

    c = _customer(client, "Imported Owner")
    job = _exempt_job(client, c["id"], number="IMP-1", kind="resale")
    inv = Invoice(
        invoice_number="IMP-0001",
        customer_id=c["id"],
        job_id=job["id"],
        date=date(2026, 7, 3),
        subtotal=Decimal("50"),
        tax_amount=Decimal("0"),
        total=Decimal("50"),
        balance_due=Decimal("50"),
    )
    db_session.add(inv)
    db_session.commit()
    assert inv.tax_exempt is True
    assert inv.exempt_certificate_number == "IMP-1"
    assert inv.exempt_certificate_kind == "resale"


def test_a_non_taxable_customers_document_without_a_job_carries_no_copy(
    client, db_session, seed_accounts
):
    c = _customer(client, "Exempt Buyer", taxable=False)
    inv = _invoice(client, c["id"], [_line(100)])
    assert inv["tax_exempt"] is True and inv["exempt_certificate_number"] is None
    html = _invoice_html(db_session, inv["id"])
    assert "Sales tax exempt" in html
    assert "certificate" not in html.split("Sales tax exempt")[1].split("<")[0]


def test_the_pdf_says_exempt_under_the_totals_with_the_certificate(
    client, db_session, seed_accounts
):
    c = _customer(client, "Water Reclamation District")
    job = _exempt_job(client, c["id"], expires="2026-12-31")
    inv = _invoice(client, c["id"], [_line(500)], job_id=job["id"])
    other = _customer(client, "Plain Exempt")
    bare = _invoice(client, other["id"], [_line(100)], tax_exempt=True)
    taxed = _invoice(client, other["id"], [_line(100)])
    html = _invoice_html(db_session, inv["id"])
    assert "Sales tax exempt — certificate E-1234-5678 (IL)<" in html
    html = _invoice_html(db_session, bare["id"])
    assert (
        "Sales tax exempt" in html
        and "certificate" not in html.split("Sales tax exempt")[1].split("<")[0]
    )
    assert "Sales tax exempt" not in _invoice_html(db_session, taxed["id"])


def test_the_pdf_notes_a_copy_that_expired_before_the_documents_date(
    client, db_session, seed_accounts
):
    from app.services.tax_exemption import exemption_line

    c = _customer(client, "Lapsed Owner")
    job = _exempt_job(client, c["id"], number="EX-1", expires="2026-07-01")
    on_the_day = _invoice(
        client, c["id"], [_line(100)], job_id=job["id"], date="2026-07-01"
    )
    after = _invoice(client, c["id"], [_line(100)], job_id=job["id"], date="2026-07-02")
    assert "certificate EX-1 (IL)<" in _invoice_html(db_session, on_the_day["id"])
    html = _invoice_html(db_session, after["id"])
    assert "Sales tax exempt — certificate EX-1 (IL) (expired)" in html
    est = _estimate(client, c["id"], [_line(100)], job_id=job["id"], date="2026-08-01")
    page = client.get(f"/api/estimates/{est['id']}/print-preview")
    assert page.status_code == 200
    assert "certificate EX-1 (IL) (expired)" in page.text
    from types import SimpleNamespace

    assert (
        exemption_line(
            SimpleNamespace(
                tax_exempt=True,
                date=date(2026, 7, 2),
                exempt_certificate_number="N",
                exempt_certificate_state="WI",
                exempt_certificate_kind="resale",
                exempt_certificate_expires=None,
            )
        )
        == "Sales tax exempt — certificate N (WI)"
    )


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
    resale exempt      200.00 (an exempt job, resale certificate)
    public entity      300.00 (an exempt job, public-entity certificate)
    customer exempt    400.00 (not taxable, no job, so no certificate)
    line by line        75.00, 50.00 taxed (tax 5.00), 25.00 untaxed

    Sales 1,075.00; taxable 150.00; non-taxable 925.00; tax 15.00.
    """
    shop = _customer(client, "Corner Shop")
    _invoice(client, shop["id"], [_line(100)])
    reseller = _customer(client, "Wholesale Buyer")
    stock = _exempt_job(client, reseller["id"], name="Stock", kind="resale")
    _invoice(client, reseller["id"], [_line(200)], job_id=stock["id"])
    gc = _customer(client, "GC With Public Work")
    job = _exempt_job(client, gc["id"], name="City Hall", number="PE-1")
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
    c = _customer(client, "Exempt Owner")
    job = _exempt_job(client, c["id"], number="PE-8")
    inv = _invoice(client, c["id"], [_line(100)], job_id=job["id"])
    who = _who(client, db_session, role, how)
    got = who.get(f"/api/jobs/{job['id']}").json()
    assert got["is_taxable"] is False and got["exempt_certificate_number"] == "PE-8"
    got = who.get(f"/api/invoices/{inv['id']}").json()
    assert got["tax_exempt"] is True and got["exempt_certificate_number"] == "PE-8"
    assert who.get(f"/api/reports/sales-tax?{PERIOD}").json()["exempt_sales"]
    writes = (
        who.put(f"/api/jobs/{job['id']}", json={"is_taxable": True}),
        who.put(f"/api/jobs/{job['id']}", json={"exempt_certificate_number": "X"}),
        who.put(f"/api/invoices/{inv['id']}", json={"tax_exempt": False}),
    )
    for r in writes:
        assert r.status_code == 403, r.text
        assert r.json()["detail"]  # the role's own refusal, unchanged


@pytest.mark.parametrize("how", ["session", "token"])
def test_a_bookkeeper_writes_the_exemption(client, db_session, seed_accounts, how):
    c = _customer(client, "Bookkept Owner")
    who = _who(client, db_session, "bookkeeper", how)
    job = who.post(
        "/api/jobs",
        json={
            "customer_id": c["id"],
            "name": "Shelter",
            "is_taxable": False,
            **_certificate(number="NP-2", kind="nonprofit"),
        },
    )
    assert job.status_code == 201, job.text
    assert job.json()["is_taxable"] is False
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
    assert inv.json()["exempt_certificate_number"] == "NP-2"


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


def test_the_job_certificate_migration(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "cert.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("b8640a1c2e3f").down_revision == "a4c8e2f6b0d3"
    command.upgrade(cfg, "a4c8e2f6b0d3")  # the step below it (#795)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        conn.execute(
            "INSERT INTO jobs (id, customer_id, name, status, is_active)"
            " VALUES (1, 1, 'J', 'in_progress', 1)"
        )
    customers_before = _cols(db, "customers")
    permits_before = _cols(db, "reseller_permits")
    command.upgrade(cfg, "b8640a1c2e3f")  # its own step, not the chain (#770)
    added = {
        "exempt_certificate_number",
        "exempt_certificate_state",
        "exempt_certificate_kind",
        "exempt_certificate_expires",
    }
    assert added <= _cols(db, "jobs")
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT exempt_certificate_number, exempt_certificate_state,"
            " exempt_certificate_kind, exempt_certificate_expires FROM jobs"
        ).fetchone()
    assert row == (None, None, None, None)
    # the customer and the permit record are untouched
    assert _cols(db, "customers") == customers_before
    assert _cols(db, "reseller_permits") == permits_before
    command.downgrade(cfg, "a4c8e2f6b0d3")
    assert not added & _cols(db, "jobs")


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
        for table in ("invoices", "estimates"):
            copies = conn.execute(
                f"SELECT exempt_certificate_number, exempt_certificate_state,"
                f" exempt_certificate_kind, exempt_certificate_expires FROM {table}"
            ).fetchall()
            assert set(copies) == {(None, None, None, None)}, table
    command.downgrade(cfg, "b8640b2d3f4a")
    for table in ("invoices", "estimates"):
        left = _cols(db, table)
        assert "tax_exempt" not in left, table
        assert not {c for c in left if c.startswith("exempt_certificate_")}, table


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
