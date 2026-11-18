"""W-181 / W-183 on Chromium's print, which is how Windows prints a document
(and what a browser's own Print does): no last sheet carries only the footer,
only the totals and the footer, or only the notes and the footer. Chromium
does not honour ``break-after: avoid`` on a table row, which is why the last
row, the totals, the notes and the footer are one unbreakable row group
(tests/test_pdf_last_page_w183.py has the PDF half and the helpers)."""

import io

import pytest

pytest.importorskip("playwright.sync_api")
pypdf = pytest.importorskip("pypdf")

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_pdf_last_page_w183 import (  # noqa: E402
    LENGTHS,
    MAKERS,
    last_page_problems,
)


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # no browser installed on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        yield browser
        browser.close()


def test_chromium_prints_no_orphaned_totals_notes_or_footer(
    client, seed_accounts, seed_customer, chromium
):
    wrong = []
    page = chromium.new_page()
    for kind in ("invoice", "bill"):
        for notes in (False, True):
            for n in LENGTHS:
                url = MAKERS[kind](client, seed_accounts, seed_customer, n, notes)
                html = client.get(f"{url}/print-preview").text
                # the sheet's own print-on-load would open no dialog here
                page.set_content(html.replace("window.print()", "void 0"))
                pdf = page.pdf(prefer_css_page_size=True)
                pages = [
                    " ".join((p.extract_text() or "").split())
                    for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
                ]
                for problem in last_page_problems(pages, n, notes):
                    wrong.append(f"{kind}, {n} lines, notes={notes}: {problem}")
    page.close()
    assert not wrong, "\n".join(wrong)
