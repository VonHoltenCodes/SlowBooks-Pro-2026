"""W-187 / W-188 on Chromium's print, which is how the window prints a
document and what Server Edition's browser print does. The lengths that end
at a sheet's foot are searched for with the cut turned off (#1045's markup),
then the same lengths, cut, must keep a row above the totals (W-187) and
something above the footer (W-188). The WeasyPrint half and the helpers are
tests/test_pdf_last_page_w187_w188.py.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import io

import pytest

pytest.importorskip("playwright.sync_api")
pypdf = pytest.importorskip("pypdf")

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_pdf_last_page_w183 import MAKERS  # noqa: E402
from tests.test_pdf_last_page_w187_w188 import (  # noqa: E402
    LONG_LINES,
    NOTE_WORDS,
    ending_problems,
    long_last_line,
    search,
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


def _sheets(page, html):
    page.set_content(html.replace("window.print()", "void 0"))
    pdf = page.pdf(prefer_css_page_size=True)
    return [
        " ".join((p.extract_text() or "").split())
        for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
    ]


def test_chromium_keeps_a_row_above_the_totals_and_text_above_the_footer(
    client, seed_accounts, seed_customer, chromium, monkeypatch
):
    page = chromium.new_page()
    wrong = []

    def printed(url):
        return _sheets(page, client.get(f"{url}/print-preview").text)

    # W-187: each kind's long last line, across the band
    for kind in MAKERS:

        def render(length, kind=kind):
            return printed(
                long_last_line(client, seed_accounts, seed_customer, kind, 50, length)
            )

        found = search(render, LONG_LINES, monkeypatch)
        if not found:
            wrong.append(f"{kind}: no length in the band ends at a sheet's foot")
        for length in found:
            wrong += [
                f"{kind}, a {length}-character last line: {p}"
                for p in ending_problems(render(length))
            ]

    # W-188: a 50-line invoice's long notes, across the band the gate found
    def notes(length):
        return printed(
            long_last_line(
                client,
                seed_accounts,
                seed_customer,
                "invoice",
                50,
                20,
                notes=NOTE_WORDS[:length].rstrip(),
            )
        )

    found = search(notes, range(5900, 6081, 20), monkeypatch)
    if not found:
        wrong.append("no notes length in the band leaves the footer alone")
    for length in found:
        wrong += [
            f"{length}-character notes: {p}" for p in ending_problems(notes(length))
        ]
    page.close()
    assert not wrong, "\n".join(wrong)
