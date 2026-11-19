"""#1041: the dates and terms sit beside a document's title.

The meta table (Date, Due Date, Terms, PO #...) was a band of its own across
the page under the heading, so with the company block and the addresses the
line table started about 35% down the sheet and page 1 of a long invoice held
far fewer rows than page 2 (2.32.0 round 20, the owner: "the top half is
massive"). It is now a short list under the document number, a label and a
value on each row, in the five line documents, and the gaps are tighter."""

import io

import pytest

from tests.test_pdf_last_page_w183 import MAKERS


@pytest.mark.parametrize("kind", list(MAKERS))
def test_the_dates_and_terms_are_in_the_title_block(
    client, seed_accounts, seed_customer, kind
):
    url = MAKERS[kind](client, seed_accounts, seed_customer, 3, False)
    page = client.get(f"{url}/print-preview").text
    body = page.split("<body")[1]
    assert body.count('<table class="meta-table">') == 1
    title = body.index('-number">#')
    meta = body.index('<table class="meta-table">')
    after = min(
        body.index(marker)
        for marker in ('class="addresses"', 'class="line-items')
        if marker in body
    )
    assert title < meta < after, "the meta table follows the number, in the heading"
    rows = body[meta : body.index("</table>", meta)]
    assert '<th scope="row">Date' in rows
    assert '<th scope="col"' not in rows and "meta-label" not in rows
    # label and value on a row each, right aligned beside the title
    assert ".meta-table { margin: 6px 0 0 auto;" in page


def test_a_bills_discount_line_is_a_sentence_under_the_heading(
    client, seed_accounts, seed_customer
):
    from app.services.pdf_service import _jinja_env

    src = _jinja_env.loader.get_source(_jinja_env, "bill_pdf.html")[0]
    table = src[src.index('<table class="meta-table">') : src.index("</table>")]
    assert "discount_line" not in table
    assert '<div class="terms-discount">{{ discount_line }}</div>' in src


def test_page_one_of_a_long_invoice_holds_more_rows(
    client, seed_accounts, seed_customer
):
    pypdf = pytest.importorskip("pypdf")
    # a company with its address, as a real one has: the title block then
    # sits beside it and the list of dates adds no height
    r = client.put(
        "/api/settings",
        json={
            "company_name": "Harbor Light Electric LLC",
            "company_address1": "1420 Marine Drive",
            "company_address2": "Suite 200",
            "company_city": "Astoria",
            "company_state": "OR",
            "company_zip": "97103",
            "company_phone": "(503) 555-0142",
            "company_email": "billing@harborlight.example",
        },
    )
    assert r.status_code == 200, r.text
    url = MAKERS["invoice"](client, seed_accounts, seed_customer, 53, False)
    r = client.get(f"{url}/pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    first = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    rows = [line for line in first.splitlines() if line.startswith("Line ")]
    # 22 with the band across the page
    assert len(rows) >= 24, len(rows)
