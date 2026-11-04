"""Print the deposit (#260, 2.22.2).

Invoices, bills, estimates, checks and statements all had a PDF; a deposit
didn't, and a bookkeeper walking checks to the bank wants a slip for the
envelope. The slip lists the account, the date, each payment the deposit
took (who, number, method, amount), the checks and cash subtotalled, and
the total, tagged like every other document; a void deposit's says VOID.
"""

import base64

import pytest

from app.services import pdf_service

# a whole 1x1 PNG: the upload decodes a raster logo before keeping it
# (NEW-201)
PNG_1PX_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"


@pytest.fixture
def acme(client):
    return client.post("/api/customers", json={"name": "Acme Diner"}).json()["id"]


def _receive(client, cid, amount, check=None, method="Check"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": method,
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def deposit(client, seed_accounts, acme):
    """Two checks and some cash, deposited to Checking."""
    _receive(client, acme, "332.30", check="4420")
    _receive(client, acme, "100.00", check="4421")
    _receive(client, acme, "25.00", method="Cash")
    pending = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-09-12",
            "reference": "SLIP-7",
            "line_ids": [p["transaction_line_id"] for p in pending],
        },
    )
    assert r.status_code in (200, 201), r.text
    out = r.json()
    return {"id": out["transaction_id"], **out}


def test_the_slip_is_a_pdf_with_a_name_the_desktop_can_save(client, deposit):
    r = client.get(f"/api/deposits/{deposit['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:5] == b"%PDF-"
    assert "Deposit_2026-09-12.pdf" in r.headers["content-disposition"]


def test_the_slip_lists_each_payment_and_the_total(client, deposit, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    html = client.get(f"/api/deposits/{deposit['id']}/pdf").text
    assert "DEPOSIT" in html and "VOID" not in html
    assert "Checking" in html and "SLIP-7" in html
    assert (
        html.count("Acme Diner") >= 3
    )  # each row names the payer (and its description)
    assert "4420" in html and "4421" in html
    assert "$332.30" in html and "$100.00" in html and "$25.00" in html
    assert "2 checks" in html and "$432.30" in html  # the checks subtotalled
    assert "Cash" in html
    assert "Total deposit" in html and "$457.30" in html
    # the print page is the same slip, printed on open
    page = client.get(f"/api/deposits/{deposit['id']}/print-preview").text
    assert "Total deposit" in page and "window.print()" in page


def test_a_void_deposits_slip_says_so(client, deposit, monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    r = client.post(f"/api/deposits/{deposit['id']}/void")
    assert r.status_code == 200, r.text
    html = client.get(f"/api/deposits/{deposit['id']}/pdf").text
    assert "VOID" in html and "went back to be deposited again" in html


def test_the_slip_carries_the_company_logo(client, deposit, monkeypatch):
    r = client.post(
        "/api/uploads/logo",
        files={"file": ("logo.png", base64.b64decode(PNG_1PX_B64), "image/png")},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    html = client.get(f"/api/deposits/{deposit['id']}/pdf").text
    assert f"data:image/png;base64,{PNG_1PX_B64}" in html


def test_a_missing_deposit_is_404(client, seed_accounts):
    assert client.get("/api/deposits/999999/pdf").status_code == 404


def test_the_deposit_page_offers_print_and_save_pdf():
    src = open("app/static/js/deposits.js", encoding="utf-8").read()
    assert "window.open('/api/deposits/${d.id}/pdf','_blank')" in src
    assert src.count("window.open('/api/deposits/${d.id}/print-preview','_blank')") == 2
