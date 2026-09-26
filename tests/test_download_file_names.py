"""A file named after something a person typed survives the trip.

The customer statement put the raw customer name into Content-Disposition
(``inline; filename=Statement_Łódź Signs.pdf``). A header goes out as
Latin-1, so an accented name reached the browser as bytes read the wrong
way, and a letter Latin-1 lacks made the request fail with a 500 (found
while integrating the 2.17.3 exploratory fixes). The same was true of every
PDF named after a document number a person can type — a vendor's bill
number, an invoice prefix, a check number, a donor's name — and of the
statement a customer is emailed. RFC 6266: ``filename*=UTF-8''…`` carries
the exact name and ``filename="…"`` an ASCII stand-in.
"""

import email
import re
import smtplib
from urllib.parse import unquote

import pytest

from app.models.contacts import Customer, Vendor
from tests.conftest import WEASYPRINT_AVAILABLE


def _exact(header: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", header)
    assert m, header
    return unquote(m.group(1), encoding="utf-8", errors="strict")


def _fallback(header: str) -> str:
    m = re.search(r'filename="([^"]*)"', header)
    assert m, header
    return m.group(1)


def _check(r, exact: str, fallback: str):
    assert r.status_code == 200, r.text[:300]
    assert r.content[:5] == b"%PDF-"
    header = r.headers["content-disposition"]
    header.encode("ascii")  # nothing a header can't carry
    assert header.startswith("inline; ")
    assert _exact(header) == exact
    assert _fallback(header) == fallback


def test_the_header_carries_the_exact_name_and_a_plain_one():
    from app.services.request_utils import content_disposition

    h = content_disposition("Statement_Łódź Signs.pdf")
    assert h == (
        'inline; filename="Statement_Lodz Signs.pdf"; '
        "filename*=UTF-8''Statement_%C5%81%C3%B3d%C5%BA%20Signs.pdf"
    )
    assert content_disposition("x.csv", "attachment").startswith("attachment; ")
    # what would end the quoted name, or break a reader that percent-decodes
    # it (the desktop shell), never reaches it; a folder separator is a dash
    h = content_disposition('Invoice_100% "Olé"; x/y\\z\r\n.pdf')
    assert _fallback(h) == "Invoice_100_ _Ole__ x-y-z.pdf"
    assert _exact(h) == 'Invoice_100% "Olé"; x-y-z.pdf'
    assert _fallback(content_disposition("Statement_名古屋.pdf")) == "Statement____.pdf"


@pytest.mark.parametrize(
    "name, fallback",
    [("Łódź Signs", "Lodz Signs"), ("Café Olé & Søn", "Cafe Ole & Son")],
)
def test_a_statement_for_any_customer_name(
    client, db_session, seed_accounts, name, fallback
):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    r = client.get(f"/api/reports/customer-statement/{c.id}/pdf?as_of_date=2026-09-30")
    _check(r, f"Statement_{name}.pdf", f"Statement_{fallback}.pdf")


def _sale(client, customer_id):
    return {
        "customer_id": customer_id,
        "date": "2026-09-01",
        "lines": [{"description": "Sign", "quantity": 1, "rate": 100}],
    }


def test_sales_documents_numbered_with_any_letters(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.credit_memos import CreditMemo
    from app.models.estimates import Estimate
    from app.models.invoices import Invoice

    inv = client.post("/api/invoices", json=_sale(client, seed_customer.id)).json()
    est = client.post("/api/estimates", json=_sale(client, seed_customer.id)).json()
    cm = client.post(
        "/api/credit-memos", json=dict(_sale(client, seed_customer.id), tax_rate=0)
    ).json()
    # a numbering prefix is whatever the company typed in Settings
    db_session.get(Invoice, inv["id"]).invoice_number = "FAKTURA-Ł/1"
    db_session.get(Estimate, est["id"]).estimate_number = "OFERTA-Ł-1"
    db_session.get(CreditMemo, cm["id"]).memo_number = "KOREKTA-Ł-1"
    db_session.commit()

    _check(
        client.get(f"/api/invoices/{inv['id']}/pdf"),
        "Invoice_FAKTURA-Ł-1.pdf",
        "Invoice_FAKTURA-L-1.pdf",
    )
    _check(
        client.get(f"/api/estimates/{est['id']}/pdf"),
        "Estimate_OFERTA-Ł-1.pdf",
        "Estimate_OFERTA-L-1.pdf",
    )
    _check(
        client.get(f"/api/credit-memos/{cm['id']}/pdf"),
        "CreditMemo_KOREKTA-Ł-1.pdf",
        "CreditMemo_KOREKTA-L-1.pdf",
    )


def test_purchase_documents_named_by_the_vendor(client, db_session, seed_accounts):
    from app.models.purchase_orders import PurchaseOrder

    v = Vendor(
        name="Młyn Łódź",
        is_active=True,
        default_expense_account_id=seed_accounts["5100"].id,
    )
    db_session.add(v)
    db_session.commit()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "bill_number": "Faktura Łódź/7",
            "date": "2026-09-02",
            "lines": [{"description": "mąka", "quantity": 2, "rate": 50}],
        },
    )
    assert bill.status_code == 201, bill.text
    _check(
        client.get(f"/api/bills/{bill.json()['id']}/pdf"),
        "Bill_Faktura Łódź-7.pdf",
        "Bill_Faktura Lodz-7.pdf",
    )

    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v.id,
            "date": "2026-09-02",
            "lines": [{"description": "mąka", "quantity": 2, "rate": 50}],
        },
    )
    assert po.status_code == 201, po.text
    db_session.get(PurchaseOrder, po.json()["id"]).po_number = "ZAM-Ł-1"
    db_session.commit()
    _check(
        client.get(f"/api/purchase-orders/{po.json()['id']}/pdf"),
        "PurchaseOrder_ZAM-Ł-1.pdf",
        "PurchaseOrder_ZAM-L-1.pdf",
    )

    paid = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2026-09-03",
            "amount": 100,
            "check_number": "№ 1001",
            "allocations": [{"bill_id": bill.json()["id"], "amount": 100}],
        },
    )
    assert paid.status_code == 201, paid.text
    _check(
        client.get(f"/api/checks/print?bill_payment_id={paid.json()['id']}"),
        "Check_№ 1001.pdf",
        "Check_No 1001.pdf",
    )


def test_a_giving_statement_for_any_donor_name(client, db_session, seed_accounts):
    c = Customer(name="Fundacja Łódź", is_active=True)
    db_session.add(c)
    db_session.commit()
    _check(
        client.get(f"/api/donors/{c.id}/giving-statement/pdf?year=2026"),
        "GivingStatement_2026_Fundacja Łódź.pdf",
        "GivingStatement_2026_Fundacja Lodz.pdf",
    )


@pytest.mark.skipif(
    not WEASYPRINT_AVAILABLE,
    reason=(
        "emails a statement, which renders a PDF; the email route turns the "
        "missing native stack into a failed send the exception hook cannot see"
    ),
)
def test_an_emailed_statement_keeps_the_customers_name(
    client, db_session, seed_accounts, monkeypatch
):
    """A mail program reads the attachment's name from its own header;
    set as one string, the whole value went out as an encoded-word that
    is not read as a file name at all."""
    from app.services.settings_service import set_setting

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, _to, message):
            sent.append(message)

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for key, value in (
        ("smtp_host", "mail.example.com"),
        ("smtp_from_email", "books@example.com"),
    ):
        set_setting(db_session, key, value)
    c = Customer(name="Łódź Signs", is_active=True, email="ap@lodz.example")
    db_session.add(c)
    db_session.commit()
    inv = client.post(
        "/api/invoices",
        json=dict(_sale(client, c.id), due_date="2026-09-02"),
    ).json()
    assert client.post(f"/api/invoices/{inv['id']}/send").status_code == 200

    r = client.post("/api/reports/batch-email-statements")
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == 1, r.json()
    msg = email.message_from_string(sent[0])
    names = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert names == ["Statement_Łódź Signs.pdf"]
    part = next(p for p in msg.walk() if p.get_filename())
    assert part.get_content_disposition() == "attachment"
