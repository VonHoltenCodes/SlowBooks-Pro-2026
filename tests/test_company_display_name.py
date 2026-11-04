"""A display name (DBA) on the customer's documents, the legal name on the
tax and payroll forms (#679).

A corporation that trades under another name had one name setting,
company_name, and every document printed it. Settings -> Company ->
Display name now goes on the invoice, sales receipt, estimate, statement,
credit memo, the invoice and statement emails and the public pay page;
the W-2, W-3, 940, 941, 1099 and pay stubs keep the company name.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.models.credit_memos import CreditMemo, CreditMemoLine
from app.models.estimates import Estimate, EstimateLine
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.settings import DEFAULT_SETTINGS
from app.services import pdf_service
from app.services.settings_service import get_all_settings, set_setting

ROOT = Path(__file__).resolve().parents[1]
LEGAL = "Pike Holdings Incorporated"
DBA = "Pike's Plumbing"


@pytest.fixture
def named(db_session, monkeypatch):
    set_setting(db_session, "company_name", LEGAL)
    set_setting(db_session, "display_name", DBA)
    db_session.commit()
    # the HTML is what is under test, not WeasyPrint
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    customer = Customer(name="Ada Customer", email="ada@example.test", is_active=True)
    db_session.add(customer)
    db_session.flush()
    return customer


def _heading(html: str) -> str:
    m = re.search(r'<div class="company-name">(.*?)</div>', html)
    return m.group(1) if m else ""


def _invoice(db_session, customer, **over):
    inv = Invoice(
        invoice_number=over.pop("invoice_number", "DBA-1"),
        customer_id=customer.id,
        date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("40"),
        total=Decimal("40"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("40"),
        lines=[InvoiceLine(description="Services", quantity=1, rate=40, amount=40)],
        **over,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _assert_display(html: str):
    assert _heading(html) == DBA.replace("'", "&#39;")
    assert LEGAL not in html


def test_display_name_is_empty_by_default():
    assert DEFAULT_SETTINGS["display_name"] == ""


def test_without_a_display_name_the_company_name_prints(db_session, named):
    set_setting(db_session, "display_name", "")
    db_session.commit()
    inv = _invoice(db_session, named)
    html = pdf_service.render_invoice_html(inv, get_all_settings(db_session))
    assert _heading(html) == LEGAL


@pytest.mark.parametrize("kind", ["invoice", "sales_receipt"])
def test_the_invoice_and_the_sales_receipt_print_the_display_name(
    db_session, named, kind
):
    from app.routes.invoices import documents

    inv = _invoice(db_session, named, is_sales_receipt=kind == "sales_receipt")
    html = documents.invoice_pdf(inv.id, db_session).body.decode()
    _assert_display(html)
    assert ("SALES RECEIPT" if kind == "sales_receipt" else "INVOICE") in html


def test_the_estimate_prints_the_display_name(db_session, named):
    from app.routes.estimates import estimate_pdf

    est = Estimate(
        estimate_number="E-DBA",
        customer_id=named.id,
        date=date(2026, 10, 1),
        subtotal=Decimal("40"),
        total=Decimal("40"),
        lines=[EstimateLine(description="Bid", quantity=1, rate=40, amount=40)],
    )
    db_session.add(est)
    db_session.commit()
    _assert_display(estimate_pdf(est.id, db_session).body.decode())


def test_the_credit_memo_prints_the_display_name(db_session, named):
    from app.routes.credit_memos import credit_memo_pdf

    cm = CreditMemo(
        memo_number="CM-DBA",
        customer_id=named.id,
        date=date(2026, 10, 1),
        subtotal=Decimal("10"),
        total=Decimal("10"),
        balance_remaining=Decimal("10"),
        lines=[CreditMemoLine(description="Refund", quantity=1, rate=10, amount=10)],
    )
    db_session.add(cm)
    db_session.commit()
    _assert_display(credit_memo_pdf(cm.id, db_session).body.decode())


def test_the_statement_prints_the_display_name(db_session, named):
    from app.routes.reports.receivables import customer_statement_pdf

    _invoice(db_session, named)
    html = customer_statement_pdf(named.id, date(2026, 10, 31), db_session)
    _assert_display(html.body.decode())


def test_the_invoice_email_signs_with_the_display_name(db_session, named):
    from app.services.email_service import render_invoice_email_parts

    inv = _invoice(db_session, named)
    _, body = render_invoice_email_parts(inv, get_all_settings(db_session))
    assert DBA.replace("'", "&#39;") in body
    assert LEGAL not in body


def test_the_seeded_invoice_email_template_names_the_display_name(db_session, named):
    from app.models.email_templates import EmailTemplate
    from app.routes.email_templates import DEFAULT_TEMPLATES
    from app.services.email_service import render_invoice_email_parts

    tpl = next(t for t in DEFAULT_TEMPLATES if t["name"] == "invoice_email")
    db_session.add(EmailTemplate(**tpl))
    db_session.commit()
    inv = _invoice(db_session, named)
    subject, body = render_invoice_email_parts(
        inv, get_all_settings(db_session), db=db_session
    )
    # a subject is a header, not HTML: the apostrophe arrives as typed
    assert subject == f"Invoice #DBA-1 from {DBA}"
    assert LEGAL not in subject and LEGAL not in body


def test_the_default_subject_names_the_display_name_without_a_saved_template(
    db_session, named
):
    """A company that never loaded the default email templates sent
    "Invoice #1001" with no company name (2.31.5 gate, NEW-200 / C-1): the
    built-in subject names the display name, else the company name."""
    from app.services.email_service import render_invoice_email_parts

    inv = _invoice(db_session, named)
    subject, _ = render_invoice_email_parts(
        inv, get_all_settings(db_session), db=db_session
    )
    assert subject == f"Invoice #DBA-1 from {DBA}"
    set_setting(db_session, "display_name", "")
    db_session.commit()
    subject, _ = render_invoice_email_parts(
        inv, get_all_settings(db_session), db=db_session
    )
    assert subject == f"Invoice #DBA-1 from {LEGAL}"


def test_a_template_saved_before_the_setting_reads_the_display_name(db_session, named):
    """A book that seeded its templates on an earlier release has
    `{{ company.company_name }}` in the saved rows. On a customer's email
    that reads the display name once one is set; the legal name is under
    `legal_name` for a template that wants it."""
    from app.models.email_templates import EmailTemplate
    from app.services.email_service import render_invoice_email_parts

    db_session.add(
        EmailTemplate(
            name="invoice_email",
            template_type="invoice",
            subject_template="Invoice from {{ company.company_name }}",
            body_template="<p>{{ company.company_name }} / {{ company.legal_name }}</p>",
        )
    )
    db_session.commit()
    inv = _invoice(db_session, named)
    subject, body = render_invoice_email_parts(
        inv, get_all_settings(db_session), db=db_session
    )
    assert subject == f"Invoice from {DBA}"
    assert f"{DBA.replace(chr(39), '&#39;')} / {LEGAL}" in body

    set_setting(db_session, "display_name", "")
    db_session.commit()
    subject, body = render_invoice_email_parts(
        inv, get_all_settings(db_session), db=db_session
    )
    assert subject == f"Invoice from {LEGAL}"
    assert f"{LEGAL} / {LEGAL}" in body


def test_the_statement_email_names_the_display_name(
    client, db_session, seed_accounts, monkeypatch
):
    import app.routes.reports.receivables as rcv
    import app.services.email_service as es

    monkeypatch.setattr(rcv, "generate_statement_pdf", lambda *a, **k: b"%PDF-")
    sent = []
    monkeypatch.setattr(
        es,
        "send_email",
        lambda db, to_email, subject, html_body, **kw: sent.append((subject, html_body))
        or True,
    )
    client.put(
        "/api/settings",
        json={
            "smtp_host": "smtp.example.com",
            "company_name": LEGAL,
            "display_name": DBA,
        },
    )
    c = Customer(name="Acme Diner", email="acme@example.com", is_active=True)
    db_session.add(c)
    db_session.commit()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": c.id,
            "date": "2026-01-01",
            "due_date": "2026-01-31",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 100}],
        },
    )
    assert client.post(f"/api/invoices/{r.json()['id']}/send").status_code == 200
    assert client.post("/api/reports/batch-email-statements").json()["sent"] == 1
    subject, body = sent[0]
    assert subject == f"Account Statement — {DBA}"
    assert "Pike&#x27;s Plumbing" in body
    assert LEGAL not in subject + body


def test_the_public_pay_page_names_the_display_name(db_session, named, client):
    _invoice(db_session, named, payment_token="tok-dba-1")
    html = client.get("/pay/tok-dba-1").text
    assert f"<h1>{DBA.replace(chr(39), '&#39;')}</h1>" in html
    assert LEGAL not in html


@pytest.mark.parametrize(
    "path",
    [
        "/api/payroll/forms/w2/{emp}/pdf?year=2026",
        "/api/payroll/forms/941/2026/1/pdf",
    ],
)
def test_tax_forms_keep_the_company_name(
    client, db_session, seed_accounts, monkeypatch, path
):
    from app.services.tax_forms import form_941, w2_w3

    for mod in (w2_w3, form_941):
        monkeypatch.setattr(mod, "render_pdf", lambda html, **kw: html.encode())
    client.put("/api/settings", json={"company_name": LEGAL, "display_name": DBA})
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Jonah",
            "last_name": "Pike",
            "pay_type": "salary",
            "pay_rate": 52000,
            "work_state": "TX",
        },
    ).json()
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-03-20",
            "period_end": "2026-03-20",
            "pay_date": "2026-03-20",
            "stubs": [{"employee_id": emp["id"], "gross_override": 2000}],
        },
    ).json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200
    r = client.get(path.format(emp=emp["id"]))
    assert r.status_code == 200, r.text
    assert LEGAL in r.text
    assert "Pike&#39;s Plumbing" not in r.text and DBA not in r.text


def test_the_settings_field_is_named_and_marked_data_write():
    js = (ROOT / "app" / "static" / "js" / "settings.js").read_text()
    assert '<label for="settings-display-name">Display name</label>' in js
    tag = re.search(r'<input id="settings-display-name"[^>]*>', js).group(0)
    assert 'name="display_name"' in tag
    assert "data-write" in tag
    assert (
        "Shown on invoices, estimates, statements and receipts. Tax and payroll "
        "forms keep the company name." in js
    )
