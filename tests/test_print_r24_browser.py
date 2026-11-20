"""Round 24 on Chromium's print (the window's Print and Server Edition's
browser print). W-213: a description's line breaks print as line breaks,
and the long last line cut with them reads as on the saved PDF (the checks
and the cases are tests/test_pdf_line_breaks_w213.py's). W-214: no totals
label wraps (the labels are tests/test_pdf_totals_labels_w214.py's).

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


def test_chromium_sets_every_totals_label_on_one_line(
    client, seed_accounts, seed_customer, chromium
):
    import re

    from tests.test_pdf_totals_labels_w214 import FIGURE, _rows

    page = chromium.new_page()
    wrong = []
    try:
        for kind in MAKERS:
            url = long_last_line(client, seed_accounts, seed_customer, kind, 2, 20)
            html = client.get(f"{url}/print-preview").text
            html = re.sub(
                r'(<div class="totals">\s*<table>)', lambda m: m[1] + _rows(), html
            )
            page.set_content(html.replace("window.print()", "void 0"))
            page.emulate_media(media="print")
            said = page.evaluate(
                """(figure) => [...document.querySelectorAll('.totals tr')]
                .filter(tr => tr.cells.length === 2 && tr.cells[1].textContent.trim() === figure)
                .map(tr => {
                    const line = (td) => { const r = document.createRange();
                        r.selectNodeContents(td);
                        return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; };
                    const right = tr.cells[1].getBoundingClientRect().right;
                    return [tr.cells[0].textContent.trim(), line(tr.cells[0]), line(tr.cells[1]),
                            right <= document.documentElement.clientWidth];
                })""",
                FIGURE,
            )
            for label, lines, fig_lines, inside in said:
                if lines != 1 or fig_lines != 1 or not inside:
                    wrong.append(f"{kind}: {label!r} wraps or runs past the page")
            if len(said) < 20:
                wrong.append(f"{kind}: only {len(said)} labels laid out")
    finally:
        page.close()
    assert not wrong, "\n".join(wrong)
