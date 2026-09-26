"""A foreign-currency invoice says which currency it is in (2.17.3
exploratory, skytech W-M2).

A EUR invoice at 1.10 showed "$850.00" in the form, the invoices list and
the PDF, with no currency anywhere; only the ledger (correctly $935.00) knew.
Every amount on a foreign-currency document now carries its ISO code
("EUR 850.00") on screen, on the PDF and print preview, and in the covering
email. Home-currency documents are unchanged, and so is the posting.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import pdf_service

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _invoice(client, customer_id, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-09-10",
        "tax_rate": 0,
        "lines": [
            {"description": "Graphic design, per hour", "quantity": 10, "rate": 85}
        ],
    }
    body.update(extra)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def html_pdfs(monkeypatch):
    # capture the HTML the PDF engine would have rendered
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def test_a_euro_invoice_prints_in_euros(
    client, db_session, seed_accounts, seed_customer, html_pdfs
):
    inv = _invoice(client, seed_customer.id, currency="EUR", exchange_rate=1.1)
    for page in ("pdf", "print-preview"):
        html = client.get(f"/api/invoices/{inv['id']}/{page}").text
        assert "EUR 850.00" in html, page
        assert "$850.00" not in html and "$85.00" not in html, page

    # the ledger was right all along and is not touched: A/R holds $935.00
    from app.models.transactions import TransactionLine

    debit = (
        db_session.query(TransactionLine)
        .filter_by(
            transaction_id=inv_txn(db_session, inv["id"]),
            account_id=seed_accounts["1100"].id,
        )
        .one()
        .debit
    )
    assert debit == Decimal("935.00")


def inv_txn(db_session, invoice_id):
    from app.models.invoices import Invoice

    return db_session.get(Invoice, invoice_id).transaction_id


def test_a_home_currency_invoice_still_prints_dollars(
    client, seed_accounts, seed_customer, html_pdfs
):
    for extra in ({}, {"currency": "USD"}):
        inv = _invoice(client, seed_customer.id, **extra)
        html = client.get(f"/api/invoices/{inv['id']}/pdf").text
        assert "$850.00" in html and "USD 850.00" not in html, extra


def test_the_covering_email_names_the_currency(client, seed_accounts, seed_customer):
    inv = _invoice(client, seed_customer.id, currency="EUR", exchange_rate=1.1)
    r = client.post(f"/api/invoices/{inv['id']}/email-preview", json={})
    assert r.status_code == 200, r.text
    assert "Amount Due: EUR 850.00" in r.json()["html_body"]


def test_the_screens_carry_the_document_currency():
    inv = (JS / "invoices.js").read_text(encoding="utf-8")
    assert "${SalesLines.money(inv.total, inv.currency)}" in inv
    assert "${SalesLines.money(inv.balance_due, inv.currency)}" in inv
    assert "const money = (v) => SalesLines.money(v, inv.currency);" in inv
    recalc = inv[inv.index("    recalc() {") :]
    recalc = recalc[: recalc.index("\n    },")]
    assert "const cur = $('#invoice-form [name=\"currency\"]')?.value;" in recalc
    assert (
        "SalesLines.show(t, ['inv-subtotal', 'inv-tax', 'inv-total'], cur);" in recalc
    )
    sr = (JS / "sales_receipts.js").read_text(encoding="utf-8")
    assert "${SalesLines.money(sr.total, sr.currency)}" in sr
    assert "SalesLines.show(t, ['sr-subtotal', 'sr-tax', 'sr-total'], cur);" in sr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_screen_formats_money_the_way_the_pdf_does():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "sales_lines_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["money"] == ["EUR 850.00", "$850.00", "$850.00", "CAD 10,824,891.75"]
    assert got["eurLine"] == "EUR 850.00"
    assert pdf_service._format_currency(850, "EUR") == "EUR 850.00"
    assert pdf_service._format_currency(10824891.75, "CAD") == "CAD 10,824,891.75"
    assert pdf_service._format_currency(850) == "$850.00"
