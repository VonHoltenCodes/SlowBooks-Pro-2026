"""PDFs (#807): a voided invoice or bill prints VOID as a void deposit slip
does, the customer statement follows the Letter / A4 paper setting, and a
PDF route on an install without WeasyPrint answers a sentence, never a 500."""

import io
import sys
from datetime import date
from decimal import Decimal

import pytest

from app.services.pdf_service import WEASYPRINT_MISSING
from app.services.settings_service import set_setting


def _pdf_reader(content: bytes):
    pypdf = pytest.importorskip("pypdf")
    assert content[:5] == b"%PDF-"
    return pypdf.PdfReader(io.BytesIO(content))


def _pdf_text(client, url: str) -> str:
    r = client.get(url)
    assert r.status_code == 200, r.text[:200]
    reader = _pdf_reader(r.content)
    return "\n".join(page.extract_text() for page in reader.pages)


def _invoice(client, customer_id: int) -> int:
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-01",
            "terms": "Net 30",
            "lines": [{"description": "Vinyl Banner 3x6", "quantity": 2, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_voided_invoice_pdf_says_void_and_an_unvoided_one_does_not(
    client, seed_accounts, seed_customer
):
    kept = _invoice(client, seed_customer.id)
    voided = _invoice(client, seed_customer.id)
    r = client.post(f"/api/invoices/{voided}/void")
    assert r.status_code == 200, r.text

    assert "VOID" not in _pdf_text(client, f"/api/invoices/{kept}/pdf")
    assert "VOID" in _pdf_text(client, f"/api/invoices/{voided}/pdf")


def test_a_voided_invoice_print_preview_says_void_as_its_pdf_does(
    client, seed_accounts, seed_customer
):
    kept = _invoice(client, seed_customer.id)
    voided = _invoice(client, seed_customer.id)
    assert client.post(f"/api/invoices/{voided}/void").status_code == 200

    page = client.get(f"/api/invoices/{voided}/print-preview")
    assert page.status_code == 200
    assert '<span class="void">VOID</span>' in page.text
    page = client.get(f"/api/invoices/{kept}/print-preview")
    assert '<span class="void">VOID</span>' not in page.text


def test_a_voided_bill_pdf_says_void_and_an_unvoided_one_does_not(
    client, db_session, seed_accounts
):
    from app.models.bills import Bill, BillStatus
    from app.models.contacts import Vendor

    vendor = Vendor(name="Harbor Sign Supply", is_active=True)
    db_session.add(vendor)
    db_session.flush()
    bills = {}
    for number, status in (("B-1", BillStatus.UNPAID), ("B-2", BillStatus.VOID)):
        bill = Bill(
            bill_number=number,
            vendor_id=vendor.id,
            status=status,
            date=date(2026, 9, 1),
            total=Decimal("50.00"),
            balance_due=Decimal("50.00"),
        )
        db_session.add(bill)
        db_session.flush()
        bills[number] = bill.id
    db_session.commit()

    assert "VOID" not in _pdf_text(client, f"/api/bills/{bills['B-1']}/pdf")
    assert "VOID" in _pdf_text(client, f"/api/bills/{bills['B-2']}/pdf")


@pytest.mark.parametrize("setting, size", [("letter", (612, 792)), ("a4", (595, 842))])
def test_the_customer_statement_prints_on_the_paper_the_setting_names(
    client, db_session, seed_accounts, seed_customer, setting, size
):
    _invoice(client, seed_customer.id)
    set_setting(db_session, "pdf_paper_size", setting)
    db_session.commit()

    r = client.get(
        f"/api/reports/customer-statement/{seed_customer.id}/pdf"
        "?as_of_date=2026-09-30"
    )
    assert r.status_code == 200, r.text[:200]
    box = _pdf_reader(r.content).pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == size


def test_a_pdf_route_without_weasyprint_answers_a_sentence_not_a_500(
    client, seed_accounts, seed_customer, monkeypatch
):
    inv = _invoice(client, seed_customer.id)
    # None in sys.modules makes `from weasyprint import ...` raise ImportError
    monkeypatch.setitem(sys.modules, "weasyprint", None)

    r = client.get(f"/api/invoices/{inv}/pdf")
    assert r.status_code == 503
    assert r.json() == {"detail": WEASYPRINT_MISSING}
    assert "INSTALL.md" in r.json()["detail"]

    r = client.get(
        f"/api/reports/customer-statement/{seed_customer.id}/pdf"
        "?as_of_date=2026-09-30"
    )
    assert r.status_code == 503
    assert r.json() == {"detail": WEASYPRINT_MISSING}

    # the print preview never needs WeasyPrint
    page = client.get(f"/api/invoices/{inv}/print-preview")
    assert page.status_code == 200
    assert "Vinyl Banner 3x6" in page.text


# ── Every document follows the paper setting (#807 follow-up) ─────────────

PAGE = {"letter": (612, 792), "a4": (595, 842)}


def _page_size(content: bytes) -> tuple:
    box = _pdf_reader(content).pages[0].mediabox
    return (round(float(box.width)), round(float(box.height)))


def _vendor_id(client) -> int:
    r = client.post("/api/vendors", json={"name": "Harbor Sign Supply"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _doc_invoice(client, db, cid):
    return f"/api/invoices/{_invoice(client, cid)}/pdf"


def _doc_sales_receipt(client, db, cid):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "method": "Cash",
            "lines": [{"description": "Decal", "quantity": 1, "rate": 15}],
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/invoices/{r.json()['invoice']['id']}/pdf"


def _doc_estimate(client, db, cid):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Decal", "quantity": 1, "rate": 15}],
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/estimates/{r.json()['id']}/pdf"


def _doc_credit_memo(client, db, cid):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": cid,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [{"description": "Decal", "quantity": 1, "rate": 15}],
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/credit-memos/{r.json()['id']}/pdf"


def _doc_purchase_order(client, db, cid):
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": _vendor_id(client),
            "date": "2026-09-01",
            "lines": [{"description": "Vinyl roll", "quantity": 1, "rate": 80}],
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/purchase-orders/{r.json()['id']}/pdf"


def _doc_bill(client, db, cid):
    from app.models.bills import Bill, BillStatus

    bill = Bill(
        bill_number="B-7",
        vendor_id=_vendor_id(client),
        status=BillStatus.UNPAID,
        date=date(2026, 9, 1),
        total=Decimal("80.00"),
        balance_due=Decimal("80.00"),
    )
    db.add(bill)
    db.commit()
    return f"/api/bills/{bill.id}/pdf"


def _doc_deposit(client, db, cid):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": "40.00",
            "method": "Check",
            "check_number": "4420",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    from app.models.accounts import Account

    checking = db.query(Account).filter(Account.account_number == "1000").one()
    pending = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": checking.id,
            "date": "2026-09-12",
            "line_ids": [p["transaction_line_id"] for p in pending],
        },
    )
    assert r.status_code in (200, 201), r.text
    return f"/api/deposits/{r.json()['transaction_id']}/pdf"


def _doc_change_order(client, db, cid):
    job = client.post("/api/jobs", json={"customer_id": cid, "name": "Storefront"})
    assert job.status_code == 201, job.text
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job.json()["id"],
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Steel",
                    "cost_type": "material",
                    "quantity": 1,
                    "unit_cost": 1000,
                    "markup_pct": 10,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/change-orders/{r.json()['id']}/pdf"


def _doc_statement(client, db, cid):
    _invoice(client, cid)
    return f"/api/reports/customer-statement/{cid}/pdf?as_of_date=2026-09-30"


def _doc_analytics(client, db, cid):
    return "/api/analytics/export.pdf"


DOCUMENT_ROUTES = {
    "invoice": _doc_invoice,
    "sales receipt": _doc_sales_receipt,
    "estimate": _doc_estimate,
    "credit memo": _doc_credit_memo,
    "purchase order": _doc_purchase_order,
    "bill": _doc_bill,
    "deposit slip": _doc_deposit,
    "change order": _doc_change_order,
    "customer statement": _doc_statement,
    "analytics": _doc_analytics,
}


@pytest.mark.parametrize("setting", ["letter", "a4"])
@pytest.mark.parametrize("document", sorted(DOCUMENT_ROUTES))
def test_each_document_pdf_prints_on_the_paper_the_setting_names(
    client, db_session, seed_accounts, seed_customer, document, setting
):
    url = DOCUMENT_ROUTES[document](client, db_session, seed_customer.id)
    set_setting(db_session, "pdf_paper_size", setting)
    db_session.commit()

    r = client.get(url)
    assert r.status_code == 200, r.text[:200]
    assert _page_size(r.content) == PAGE[setting]


# The documents whose records take a long build (pay application, lien
# waiver, payroll stub, the donor letters) are rendered from the template
# itself, every missing value blank, and drawn by WeasyPrint the same way.
TEMPLATES_ONLY = [
    "pay_application_pdf.html",
    "lien_waiver_pdf.html",
    "paystub_pdf.html",
    "giving_statement_pdf.html",
    "collection_letter.html",
    "acknowledgment_letter.html",
]


def _blank_env():
    from jinja2 import ChainableUndefined

    from app.services import pdf_service

    class Blank(ChainableUndefined):
        __slots__ = ()

        def _same(self, *args, **kwargs):
            return self

        __add__ = __radd__ = __sub__ = __rsub__ = _same
        __mul__ = __rmul__ = __truediv__ = __rtruediv__ = __neg__ = _same

        def __lt__(self, other):
            return False

        __le__ = __gt__ = __ge__ = __lt__

        def __float__(self):
            return 0.0

        def __int__(self):
            return 0

    return pdf_service._jinja_env.overlay(undefined=Blank)


@pytest.mark.parametrize("setting", ["letter", "a4"])
@pytest.mark.parametrize("template", TEMPLATES_ONLY)
def test_each_document_template_prints_on_the_paper_the_setting_names(
    template, setting
):
    from app.services.pdf_service import paper_size_of, render_pdf

    html = (
        _blank_env()
        .get_template(template)
        .render(
            company={}, ytd={}, paper_size=paper_size_of({"pdf_paper_size": setting})
        )
    )
    assert _page_size(render_pdf(html)) == PAGE[setting]


# Checks and labels print on Letter stock, and the tax forms are the IRS's
# Letter pages, whatever the setting; every other template reads it.
LETTER_STOCK = {
    "check_pdf.html",
    "labels_5160_pdf.html",
    "w2.html",
    "w3.html",
    "form_940.html",
    "form_941.html",
    "form_1099nec.html",
    "form_1096.html",
    "new_hire_report.html",
}


def test_no_document_template_but_the_letter_stock_hard_codes_letter():
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    hard_coded = {
        p.name for p in templates.glob("*.html") if "size: letter" in p.read_text()
    }
    assert hard_coded == LETTER_STOCK
