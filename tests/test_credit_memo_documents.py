"""Credit memos can be viewed, saved as a PDF and printed (2.17.3
exploratory, skytech W-L19).

A credit memo could be created and applied, but the list had no View and
there was no PDF or print: nothing could be sent to the customer. It now
has a View window with Save PDF and Print, printed the way an invoice is.
"""

from pathlib import Path

import pytest

from app.services import pdf_service

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


@pytest.fixture
def memo(client, seed_accounts):
    cust = client.post(
        "/api/customers",
        json={
            "name": "Tidewater Cafe",
            "bill_address1": "1 Harbor Way",
            "bill_city": "Port Alder",
            "bill_state": "OR",
            "bill_zip": "97000",
        },
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-09-01",
            "tax_rate": 0.0825,
            "lines": [{"description": "Sourdough Loaf", "quantity": 10, "rate": 8.5}],
        },
    ).json()
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": cust["id"],
            "original_invoice_id": inv["id"],
            "date": "2026-09-26",
            "notes": "Two loaves came back stale.",
            "lines": [{"description": "Sourdough Loaf", "quantity": 2, "rate": 8.5}],
        },
    )
    assert r.status_code == 201, r.text
    return {"cm": r.json(), "invoice": inv}


def test_the_credit_memo_prints_like_an_invoice(client, memo, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    cm = memo["cm"]
    r = client.get(f"/api/credit-memos/{cm['id']}/pdf")
    assert r.status_code == 200, r.text
    assert f"CreditMemo_{cm['memo_number']}.pdf" in r.headers["content-disposition"]
    html = r.text
    for text in (
        "CREDIT MEMO",
        f"#{cm['memo_number']}",
        "Tidewater Cafe",
        "1 Harbor Way",
        "Port Alder, OR 97000",
        memo["invoice"]["invoice_number"],
        "Sourdough Loaf",
        "Tax (8.25%)",
        "$17.00",
        "$1.40",
        "Total Credit",
        "$18.40",
        "Two loaves came back stale.",
    ):
        assert text in html, text

    preview = client.get(f"/api/credit-memos/{cm['id']}/print-preview")
    assert preview.status_code == 200
    assert "CREDIT MEMO" in preview.text and "window.print()" in preview.text


def test_a_void_memo_says_so(client, memo, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    cm = memo["cm"]
    assert client.post(f"/api/credit-memos/{cm['id']}/void").status_code == 200
    html = client.get(f"/api/credit-memos/{cm['id']}/pdf").text
    assert "VOID" in html and "Remaining Credit" not in html


def test_the_pdf_renders(client, memo):
    r = client.get(f"/api/credit-memos/{memo['cm']['id']}/pdf")
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_an_unknown_memo_is_a_404(client, seed_accounts):
    assert client.get("/api/credit-memos/999999/pdf").status_code == 404
    assert client.get("/api/credit-memos/999999/print-preview").status_code == 404


def test_the_list_has_view_with_save_pdf_and_print():
    src = (JS / "credit_memos.js").read_text(encoding="utf-8")
    assert 'onclick="CreditMemosPage.view(${m.id})">View</button>' in src
    view = src[src.index("async view(id) {") :]
    view = view[: view.index("\n    },")]
    assert "window.open('/api/credit-memos/${cm.id}/pdf','_blank')" in view
    assert "window.open('/api/credit-memos/${cm.id}/print-preview','_blank')" in view
