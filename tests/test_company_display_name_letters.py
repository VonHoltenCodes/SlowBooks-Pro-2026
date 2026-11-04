"""The display name on the other customer-facing letters (2.31.5 gate W-103,
#679).

The display name reached the invoice, estimate, statement, credit memo and
their emails; the collection letter, the donor acknowledgment letter (and
its built-in subject and body), the year-end giving statement, the change
order, the pay application and the lien waiver still printed the legal
company name. Each now reads the display name when one is set.
"""

import pytest

from app.services import pdf_service
from app.services.settings_service import get_all_settings
from tests.test_change_orders import LABOR, MATERIAL, _co
from tests.test_change_orders import _job as _co_job
from tests.test_pay_applications import (
    _approve,
    _approved_30k,
    _edit,
    _new_app,
    _pay_invoice,
    _two_line_job,
)

LEGAL = "Pike Holdings Incorporated"
DBA = "Pike's Plumbing"
DBA_HTML = "Pike&#39;s Plumbing"


@pytest.fixture
def named(client, monkeypatch):
    r = client.put("/api/settings", json={"company_name": LEGAL, "display_name": DBA})
    assert r.status_code == 200, r.text
    # the HTML is what is under test, not WeasyPrint
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def _assert_display(html: str):
    assert f'<div class="company-name">{DBA_HTML}</div>' in html, html[:400]
    assert LEGAL not in html


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-04-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_collection_letter_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.invoices import Invoice

    inv = db_session.get(Invoice, _invoice(client, seed_customer.id)["id"])
    html = pdf_service.generate_collection_letter_pdf(
        seed_customer, [inv], get_all_settings(db_session), "30", inv.balance_due
    ).decode()
    _assert_display(html)
    # the remit-to block and the signature too
    assert html.count(DBA_HTML) >= 3


def _donation(client, customer_id):
    assert (
        client.put("/api/settings", json={"company_type": "nonprofit"}).status_code
        == 200
    )
    client.post("/api/nonprofit/setup-accounts")
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": customer_id,
            "date": "2026-05-09",
            "tax_rate": "0",
            "method": "Credit Card",
            "lines": [{"description": "Gift", "quantity": 1, "rate": "150.00"}],
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["invoice"]


def test_the_acknowledgment_letter_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    inv = _donation(client, seed_customer.id)
    r = client.get(f"/api/donors/gifts/invoice/{inv['id']}/acknowledgment/pdf")
    assert r.status_code == 200, r.text
    _assert_display(r.text)
    # the built-in body signs with it, the signature line names it
    assert r.text.count(DBA_HTML) >= 3


def test_the_acknowledgment_subject_and_body_name_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.contacts import Customer
    from app.services.donor_documents import load_gift, render_acknowledgment

    inv = _donation(client, seed_customer.id)
    gift = load_gift(db_session, "invoice", inv["id"])
    subject, body = render_acknowledgment(
        db_session,
        get_all_settings(db_session),
        db_session.get(Customer, seed_customer.id),
        gift,
    )
    assert subject == f"Thank you for your gift to {DBA}"
    assert f"<p>{DBA_HTML}</p>" in body
    assert LEGAL not in subject + body


def test_the_giving_statement_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    _donation(client, seed_customer.id)
    r = client.get(f"/api/donors/{seed_customer.id}/giving-statement/pdf?year=2026")
    assert r.status_code == 200, r.text[:200]
    _assert_display(r.text)


def test_the_giving_statement_email_names_the_display_name(
    client, db_session, seed_accounts, seed_customer, named, monkeypatch
):
    import app.routes.donors as donors_routes

    client.put(f"/api/customers/{seed_customer.id}", json={"email": "d@example.org"})
    _donation(client, seed_customer.id)
    sent = []
    monkeypatch.setattr(
        donors_routes,
        "send_email",
        lambda db, to_email, subject, html_body, **kw: sent.append((subject, html_body))
        or True,
    )
    r = client.post("/api/donors/giving-statements/batch-email", json={"year": 2026})
    assert r.status_code == 200 and r.json()["sent"] == 1, r.text
    subject, body = sent[0]
    assert subject == f"Your 2026 giving statement from {DBA}"
    assert "<p>Pike&#x27;s Plumbing</p>" in body
    assert LEGAL not in subject + body


def test_the_change_order_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_totals

    job = _co_job(client, seed_customer.id)
    co = _co(client, job["id"], [LABOR, MATERIAL], title="Steel upgrade")
    row = db_session.get(ChangeOrder, co["id"])
    html = pdf_service.render_change_order_html(
        row, co_totals(row, row.job), get_all_settings(db_session)
    )
    _assert_display(html)


def test_the_pay_application_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.jobs import Job
    from app.models.pay_applications import PayApplication
    from app.routes.pay_applications import _out

    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    _approve(client, app["id"])
    pa = db_session.get(PayApplication, app["id"])
    html = pdf_service.render_pay_application_html(
        pa,
        _out(db_session, pa),
        db_session.get(Job, job["id"]),
        get_all_settings(db_session),
    )
    _assert_display(html)


def test_the_lien_waiver_prints_the_display_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.jobs import Job
    from app.models.lien_waivers import LienWaiver

    _job_, _sov_, app = _approved_30k(client, seed_customer.id)
    pay = _pay_invoice(client, seed_customer.id, app["invoice_id"], 5000)
    w = client.post(
        "/api/lien-waivers",
        json={"payment_id": pay["id"], "waiver_type": "conditional_progress"},
    ).json()
    row = db_session.get(LienWaiver, w["id"])
    html = pdf_service.render_lien_waiver_html(
        row, row.payment, db_session.get(Job, row.job_id), get_all_settings(db_session)
    )
    _assert_display(html)
    # the claimant and the signature block name it too
    assert html.count(DBA_HTML) == 3


def test_without_a_display_name_the_letters_print_the_company_name(
    client, db_session, seed_accounts, seed_customer, named
):
    from app.models.invoices import Invoice

    client.put("/api/settings", json={"display_name": ""})
    inv = db_session.get(Invoice, _invoice(client, seed_customer.id)["id"])
    html = pdf_service.generate_collection_letter_pdf(
        seed_customer, [inv], get_all_settings(db_session), "30", inv.balance_due
    ).decode()
    assert f'<div class="company-name">{LEGAL}</div>' in html
