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
