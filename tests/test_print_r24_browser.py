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
                found, cut = problems(said, n, length, html, pdf, saved_pdf=False)
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


# ---------------------------------------------------------------------------
# Chromium's print of every printed page keeps a long word inside the page
# (the documents, the records and the saved PDF's half of this are
# tests/test_print_text_never_widens.py's). Each page is laid out in a
# window as wide as its sheet's printing area, as Chromium's print lays it
# out, with ordinary text, with an unbreakable word of 120 characters in
# every field a person types, and with a word of 19 characters as wide as
# letters come there.
# ---------------------------------------------------------------------------

INK = """(width) => {
    const past = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        if (!node.textContent.trim()) continue;
        const style = getComputedStyle(node.parentElement);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        // what a box cuts off at its edge is not printed
        let lo = -Infinity, hi = Infinity;
        for (let el = node.parentElement; el && el !== document.documentElement;
             el = el.parentElement) {
            if (getComputedStyle(el).overflowX !== 'visible') {
                const box = el.getBoundingClientRect();
                lo = Math.max(lo, box.left); hi = Math.min(hi, box.right);
            }
        }
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) {
            const a = Math.max(rect.left, lo), b = Math.min(rect.right, hi);
            if (a < b && (b > width + 0.5 || a < -0.5)) {
                past.push(node.textContent.trim().slice(0, 28) + ' runs from '
                    + a.toFixed(1) + ' to ' + b.toFixed(1));
                break;
            }
        }
    }
    const figures = [...document.querySelectorAll('td.r, td.num, td.amount')]
        .filter(td => /^-?(?:[A-Z]{3} -?|\\$)[\\d,]+\\.\\d{2}$/.test(td.textContent.trim()))
        .map(td => {
            const range = document.createRange();
            range.selectNodeContents(td);
            const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
            const box = td.getBoundingClientRect();
            return [td.textContent.trim(), Math.round(box.right * 10) / 10,
                    Math.round(box.left * 10) / 10, lines.size];
        });
    return [past, figures];
}"""


def _printing_width(html):
    """The width of a page's printing area in CSS pixels, from its @page."""
    from weasyprint import HTML

    from app.services.pdf_service import _get_fetcher

    box = HTML(string=html, url_fetcher=_get_fetcher()).render().pages[0]._page_box
    return box.width


def test_chromium_keeps_a_long_word_inside_the_page(
    client, seed_accounts, db_session, chromium, monkeypatch
):
    pytest.importorskip("weasyprint")
    from app.services import pdf_service
    from tests import test_print_text_never_widens as text

    printed = {"html": [], "templates": set()}
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: printed["html"].append(html) or b"%PDF-"
    )
    page = chromium.new_page()
    page.emulate_media(media="print")
    wrong = []

    def laid_out(html):
        width = _printing_width(html)
        page.set_viewport_size({"width": round(width), "height": 1056})
        page.set_content(html)
        return page.evaluate(INK, round(width))

    try:
        for kind, maker in text.MAKERS.items():
            drawn = {}
            for name, w in (
                ("ordinary", text._ordinary),
                ("long", text._long),
                ("wide words", text._wide),
            ):
                text._settings(client, w)
                drawn[name] = text._draw(
                    client, printed, maker, seed_accounts, db_session, w
                )
            plain = [laid_out(html)[1] for _what, html in drawn.pop("ordinary")]
            for name, pages in drawn.items():
                for (what, html), plain_figures in zip(pages, plain):
                    past, figures = laid_out(html)
                    said = f"{kind}, {what}, {name} (Chromium)"
                    wrong += [f"{said}: {p}" for p in past]
                    wrong += [
                        f"{said}: {t} is on {lines} lines"
                        for t, _end, _start, lines in figures
                        if lines != 1
                    ]
                    wrong += text._compare(
                        kind,
                        said,
                        [f[:3] for f in figures],
                        [f[:3] for f in plain_figures],
                    )
    finally:
        page.close()
    assert not wrong, "\n".join(wrong[:60]) + f"\n({len(wrong)} in all)"
