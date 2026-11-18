"""W-183 / #1039 (2.32.0 round 20): the last page of a line document never holds the
totals with no line row above them, nor the notes without the totals.

With the totals box a block (#1032), an invoice whose rows filled a sheet
ended on a sheet of Subtotal, Total and the footer alone, and one with notes
on a sheet of the notes and the footer alone (the owner: "this is something
that can't happen"). The last line row, the totals, the notes and the footer
are now one unbreakable group of the line table (``tbody.doc-tail``), so
whatever starts the last page starts with a row under the column headings.
The Chromium half of this is tests/test_print_last_sheet_browser.py."""

import io

import pytest

from tests.test_pdf_footer_margin_1028 import MARK

NOTES = "Net 30. Thank you for your business."
# the lengths the round-20 laps found (50-56), a little either side
LENGTHS = range(48, 59)


def _lines(n, **extra):
    return [
        {"description": f"Line {i}", "quantity": 1, "rate": 33, **extra}
        for i in range(1, n + 1)
    ]


def _post(client, url, body, notes):
    if notes:
        body["notes"] = NOTES
    r = client.post(url, json=body)
    assert r.status_code in (200, 201), r.text
    return f"{url}/{r.json()['id']}"


def _invoice(client, accounts, customer, n, notes):
    body = {"customer_id": customer.id, "date": "2026-11-01", "tax_rate": 0}
    return _post(client, "/api/invoices", {**body, "lines": _lines(n)}, notes)


def _estimate(client, accounts, customer, n, notes):
    body = {"customer_id": customer.id, "date": "2026-11-01", "lines": _lines(n)}
    return _post(client, "/api/estimates", body, notes)


def _credit_memo(client, accounts, customer, n, notes):
    body = {"customer_id": customer.id, "date": "2026-11-01", "tax_rate": 0}
    return _post(client, "/api/credit-memos", {**body, "lines": _lines(n)}, notes)


def _vendor(client, name):
    found = [v for v in client.get("/api/vendors").json() if v["name"] == name]
    return (found or [client.post("/api/vendors", json={"name": name}).json()])[0]


def _purchase_order(client, accounts, customer, n, notes):
    v = _vendor(client, "Ace Concrete")
    body = {"vendor_id": v["id"], "date": "2026-11-01", "lines": _lines(n)}
    return _post(client, "/api/purchase-orders", body, notes)


def _bill(client, accounts, customer, n, notes):
    v = _vendor(client, "Harbor Supply")
    body = {
        "vendor_id": v["id"],
        "bill_number": f"HS-{n}-{int(notes)}",
        "date": "2026-11-01",
        "lines": _lines(n, account_id=accounts["6000"].id),
    }
    return _post(client, "/api/bills", body, notes)


MAKERS = {
    "invoice": _invoice,
    "estimate": _estimate,
    "credit memo": _credit_memo,
    "purchase order": _purchase_order,
    "bill": _bill,
}


def last_page_problems(pages, n, notes):
    """What is wrong with a document's last page, as sentences (none when
    it is right): it carries the last line row, the totals, the notes when
    there are any, and the footer, and no earlier page has the totals."""
    last = pages[-1]
    said = []
    if f"Line {n}" not in last:
        said.append("the last line row is not on the last page")
    if "Total" not in last:
        said.append("the totals are not on the last page")
    if notes and NOTES not in last:
        said.append("the notes are not on the last page")
    if MARK not in last:
        said.append("the footer is not on the last page")
    if any("Subtotal" in page or MARK in page for page in pages[:-1]):
        said.append("an earlier page carries the totals or the footer")
    return said


@pytest.mark.parametrize("kind", list(MAKERS))
def test_the_last_page_starts_with_a_row_and_ends_the_document(
    client, seed_accounts, seed_customer, kind
):
    pypdf = pytest.importorskip("pypdf")
    wrong = []
    for notes in (False, True):
        for n in LENGTHS:
            url = MAKERS[kind](client, seed_accounts, seed_customer, n, notes)
            r = client.get(f"{url}/pdf")
            assert r.status_code == 200 and r.content[:5] == b"%PDF-", r.text[:200]
            pages = [
                " ".join((p.extract_text() or "").split())
                for p in pypdf.PdfReader(io.BytesIO(r.content)).pages
            ]
            for problem in last_page_problems(pages, n, notes):
                wrong.append(f"{kind}, {n} lines, notes={notes}: {problem}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("kind", list(MAKERS))
def test_the_tail_is_one_group_of_the_line_table(
    client, seed_accounts, seed_customer, kind
):
    url = MAKERS[kind](client, seed_accounts, seed_customer, 3, True)
    page = client.get(f"{url}/print-preview").text
    assert "table.line-items > tbody.doc-tail { break-inside: avoid;" in page
    body = page.split("<body")[1]
    assert body.count('<tbody class="doc-tail">') == 1
    tail = body.split('<tbody class="doc-tail">')[1].split("</tbody>")[0]
    # the last row, then the totals, the notes and the footer, in that order
    spots = [
        tail.index("Line 3"),
        tail.index('class="doc-end"'),
        tail.index('class="totals'),
        tail.index('class="notes"'),
        tail.index('class="footer"'),
    ]
    assert spots == sorted(spots)
    assert "Line 2" not in tail
    assert body.count('class="footer"') == 1 and body.count('class="notes"') == 1


def test_a_document_with_no_lines_still_closes_its_table(
    client, seed_accounts, seed_customer
):
    """The tail group opens on the last row; with no row it opens on its own."""
    from app.services.pdf_service import _jinja_env

    src = _jinja_env.loader.get_source(_jinja_env, "invoice_pdf.html")[0]
    assert '{% if not inv.lines and not rl %}</tbody><tbody class="doc-tail">' in src
    for name, var in (
        ("credit_memo", "cm"),
        ("purchase_order", "po"),
        ("bill", "bill"),
    ):
        src = _jinja_env.loader.get_source(_jinja_env, f"{name}_pdf.html")[0]
        assert f'{{% if not {var}.lines %}}</tbody><tbody class="doc-tail">' in src
