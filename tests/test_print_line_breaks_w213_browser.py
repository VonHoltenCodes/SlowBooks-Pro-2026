"""W-213 on Chromium's print (the window's Print and Server Edition's
browser print): a description's line breaks print as line breaks, and the
long last line cut with them reads as on the saved PDF. The checks and the
cases are tests/test_pdf_line_breaks_w213.py's.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import shutil

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("pypdf")

from playwright.sync_api import sync_playwright  # noqa: E402

from tests import test_pdf_last_page_w187_w188 as w187  # noqa: E402
from tests.test_pdf_last_page_w183 import MAKERS  # noqa: E402
from tests.test_pdf_last_page_w187_w188 import long_last_line  # noqa: E402
from tests.test_pdf_line_breaks_w213 import BROKEN, CASES, problems  # noqa: E402


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # no browser installed on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        yield browser
        browser.close()


def test_chromium_prints_the_line_breaks_and_the_cut(
    client, seed_accounts, seed_customer, chromium, monkeypatch
):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    monkeypatch.setattr(w187, "WORDS", BROKEN)
    page = chromium.new_page()
    wrong, cuts = [], []
    try:
        for kind in MAKERS:
            for n, length in CASES:
                url = long_last_line(
                    client, seed_accounts, seed_customer, kind, n, length
                )
                html = client.get(f"{url}/print-preview").text
                page.set_content(html.replace("window.print()", "void 0"))
                pdf = page.pdf(prefer_css_page_size=True)
                said = f"{kind}, {n} lines, {length} characters (Chromium)"
                found, cut = problems(said, n, length, html, pdf)
                wrong += found
                cuts.append(cut)
    finally:
        page.close()
    assert "break" in cuts and "line" in cuts, cuts
    assert not wrong, "\n".join(wrong)
