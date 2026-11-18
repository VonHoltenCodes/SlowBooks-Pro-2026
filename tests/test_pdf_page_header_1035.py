"""#1035 (2.32.0 round 19, NEW-256): every page after the first names its
document and its page in the top margin, "Invoice 1001 · Page 2 of 3", so a
loose sheet still says where it came from. Page 1 is unchanged, the column
headings still repeat, and the footer is still once at the end (#1030)."""

import re

import pytest

from tests.test_pdf_footer_margin_1028 import MARK, _long_invoice, _rows_by_page

LINES = 45


def _lines(n=LINES, **extra):
    return [
        {"description": f"Line {i}", "quantity": 1, "rate": 33, **extra}
        for i in range(1, n + 1)
    ]


def _invoice(client, seed_accounts, seed_customer):
    inv = _long_invoice(client, seed_customer, lines=LINES)
    return f"/api/invoices/{inv['id']}", "Invoice", inv["invoice_number"]


def _estimate(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-01",
            "lines": _lines(),
        },
    )
    assert r.status_code in (200, 201), r.text
    est = r.json()
    return f"/api/estimates/{est['id']}", "Estimate", est["estimate_number"]


def _credit_memo(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-01",
            "tax_rate": 0,
            "lines": _lines(),
        },
    )
    assert r.status_code == 201, r.text
    cm = r.json()
    return f"/api/credit-memos/{cm['id']}", "Credit memo", cm["memo_number"]


def _purchase_order(client, seed_accounts, seed_customer):
    v = client.post("/api/vendors", json={"name": "Ace Concrete"}).json()
    r = client.post(
        "/api/purchase-orders",
        json={"vendor_id": v["id"], "date": "2026-11-01", "lines": _lines()},
    )
    assert r.status_code == 201, r.text
    po = r.json()
    return f"/api/purchase-orders/{po['id']}", "Purchase order", po["po_number"]


def _bill(client, seed_accounts, seed_customer):
    v = client.post("/api/vendors", json={"name": "Harbor Supply"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v["id"],
            "bill_number": "HS-1035",
            "date": "2026-11-01",
            "lines": _lines(account_id=seed_accounts["6000"].id),
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    return f"/api/bills/{bill['id']}", "Bill", bill["bill_number"]


def _header_rows(rows):
    # pypdf (WeasyPrint 70) hands the header back as one text run, e.g.
    # "Invoice 1001 · Page 2 of 2"; another engine or pypdf version may split
    # it at the "·" or around the counters, and the pieces of one row share
    # its baseline, so every run on a baseline is joined before matching
    by_y = {}
    for y, t in rows:
        by_y.setdefault(y, []).append(t)
    return [re.sub(r"\s+", " ", " ".join(ts)).strip() for ts in by_y.values()]


def _assert_headed(pdf_bytes, word, number):
    pages = _rows_by_page(pdf_bytes)
    total = len(pages)
    assert total >= 2, "the fixture must fill more than one page"
    joined = [_header_rows(rows) for rows in pages]
    assert not any("Page 1 of" in t for t in joined[0]), joined[0]
    assert not any("· Page" in t for t in joined[0]), "page 1 carries a header"
    for n in range(2, total + 1):
        want = f"{word} {number} · Page {n} of {total}"
        assert want in joined[n - 1], (n, want, joined[n - 1][:4])
    # the footer is still once, on the last page (#1030)
    with_mark = [
        n for n, rows in enumerate(pages, 1) if any(MARK in t for _, t in rows)
    ]
    assert with_mark == [total], with_mark
    return joined


@pytest.mark.parametrize(
    "make", [_invoice, _estimate, _credit_memo, _purchase_order, _bill]
)
def test_every_page_after_the_first_names_its_document_and_page(
    client, seed_accounts, seed_customer, make
):
    url, word, number = make(client, seed_accounts, seed_customer)
    r = client.get(f"{url}/pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-", r.text[:200]
    _assert_headed(r.content, word, number)


def test_a_sales_receipt_is_headed_sales_receipt(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-01",
            "method": "Cash",
            "lines": _lines(),
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    assert inv["is_sales_receipt"] is True
    r = client.get(f"/api/invoices/{inv['id']}/pdf")
    assert r.status_code == 200
    joined = _assert_headed(r.content, "Sales receipt", inv["invoice_number"])
    assert not any(
        t.startswith("Invoice ") and "· Page" in t for p in joined for t in p
    )


def test_the_print_page_carries_the_header_rule_and_skips_page_one(
    client, seed_accounts, seed_customer
):
    inv = _long_invoice(client, seed_customer, lines=3)
    page = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    style = page.split("<style>")[1].split("</style>")[0]
    assert "@top-center" in style
    assert (
        f'content: "Invoice {inv["invoice_number"]}" " \\00B7  Page " counter(page)'
        in style
    )
    assert "counter(pages)" in style
    assert "@page :first { @top-center { content: none; } }" in style
    # the column headings are still a <thead>, so they repeat on every page
    assert "<thead>" in page


def test_a_number_with_quotes_cannot_break_out_of_the_css_string():
    from app.services.pdf_service import _css_string

    got = str(_css_string('7"} body{x:1} </style><b>\\'))
    assert got.startswith('"') and got.endswith('"')
    inner = got[1:-1]
    assert '"' not in inner and "<" not in inner and ">" not in inner
    assert "&quot;" not in got and "&#34;" not in got
    assert inner == "7\\22 } body{x:1} \\3c /style\\3e \\3c b\\3e \\5c "
