"""#1047 on Chromium's print, which is how Windows prints (and what a
browser's own Print does): the change order, the deposit, the customer
statement, the pay application, the collection letter and the giving
statement end on a sheet that carries a row above their closing figures,
with no sheet of the footer alone and no summary split across sheets.
Chromium does not honour ``break-after: avoid`` on a table row, which is why
the last row and the closing blocks are one unbreakable row group
(tests/test_pdf_last_page_1047.py has the PDF half, the makers and the
check)."""

import io

import pytest

pytest.importorskip("playwright.sync_api")
pypdf = pytest.importorskip("pypdf")

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_pdf_last_page_1047 import (  # noqa: E402
    CHANGE_ORDER,
    COLLECTION_LETTER,
    DEPOSIT,
    GIVING_STATEMENT,
    PAY_APPLICATION,
    SPECS,
    STATEMENT,
    last_page_problems,
)

COUNTS = {
    "change order": CHANGE_ORDER,
    "deposit": DEPOSIT,
    "statement": STATEMENT,
    "pay application": PAY_APPLICATION,
    "collection letter": COLLECTION_LETTER,
    "giving statement": GIVING_STATEMENT,
}


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # no browser installed on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def pages_drawn(monkeypatch):
    """The HTML of every document the app draws, in order; WeasyPrint is
    not needed here (the PDF half is the other module's)."""
    from app.services import pdf_service

    seen = []

    def keep(html):
        seen.append(html)
        return b"%PDF-1.7\n"

    monkeypatch.setattr(pdf_service, "_write_pdf", keep)
    return seen


def test_chromium_prints_each_document_ending_with_a_row(
    client, seed_accounts, chromium, pages_drawn
):
    wrong = []
    page = chromium.new_page()
    for kind, counts in COUNTS.items():
        spec = SPECS[kind]
        grown = {"pdfs": pages_drawn}
        for n in counts:
            spec["make"](client, seed_accounts, n, grown)
            # the sheet's own print-on-load would open no dialog here
            page.set_content(pages_drawn[-1].replace("window.print()", "void 0"))
            pdf = page.pdf(prefer_css_page_size=True)
            pages = [
                " ".join((p.extract_text() or "").split())
                for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
            ]
            for problem in last_page_problems(pages, spec, n):
                wrong.append(f"{kind}, {n} rows: {problem}")
    page.close()
    assert not wrong, "\n".join(wrong)
