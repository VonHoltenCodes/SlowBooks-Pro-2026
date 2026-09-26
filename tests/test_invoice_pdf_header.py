"""The invoice PDF's header keeps each value on one line (2.17.3
exploratory, skytech W-L1).

Date, Due Date, Terms and PO # sat side by side with an 80px label each, so
with a PO # the values were squeezed until they broke: "Sep 01, / 2026",
"Net / 30". The header is labels over values now, and a value never wraps
(a long PO # still may, inside its own column).
"""

import io

import pytest


def _render_text(client, inv_id):
    pypdf = pytest.importorskip("pypdf")
    r = client.get(f"/api/invoices/{inv_id}/pdf")
    assert r.status_code == 200, r.text[:200]
    assert r.content[:5] == b"%PDF-"
    return pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()


def test_the_header_values_stay_on_one_line_with_a_po_number(
    client, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "terms": "Net 30",
            "po_number": "PO-778",
            "tax_rate": 0.0825,
            "lines": [{"description": "Vinyl Banner 3x6", "quantity": 2, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    text = _render_text(client, r.json()["id"])
    for value in ("Sep 01, 2026", "Oct 01, 2026", "Net 30", "PO-778"):
        assert value in text, (value, text[:400])


def test_a_long_po_number_wraps_inside_its_own_column(
    client, seed_accounts, seed_customer
):
    po = "PO-" + "7" * 60
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "terms": "Due on Receipt",
            "po_number": po,
            "tax_rate": 0,
            "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    text = _render_text(client, r.json()["id"])
    assert "Sep 01, 2026" in text and "Due on Receipt" in text
    assert po[:12] in text.replace("\n", "")
