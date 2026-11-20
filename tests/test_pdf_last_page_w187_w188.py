"""W-187 / W-188 / NEW-260 (2.32.0 round 22): the end of a long block stays
with what closes the document, and the giving statement keeps its columns.

#1045 let a last line over 600 characters, and notes over 1,000, leave the
unbreakable last group (``tbody.doc-tail``) so a block taller than a page
could break (W-185). But a long last line that ended at a page's foot then
left the totals to start the next page alone under the column headings
(W-187), and a browser's print could leave the footer alone after long
notes (W-188). Such a block is now cut in two (``tail_split``): its last
lines open the group with the totals, or close one block with the footer,
so the head may break anywhere and what follows is never alone.

The lengths that end at a page's foot are found here, not written down: the
search renders the band with the cut turned off (#1045's markup) and keeps
the lengths where the totals stand alone, then the same lengths, cut, must
keep a row above the totals. The Chromium half is
tests/test_print_last_sheet_w187_browser.py.
"""

import io
import re

import pytest

from tests import test_pdf_last_page_w183 as w183
from tests.test_pdf_footer_margin_1028 import MARK
from tests.test_pdf_last_page_w183 import MAKERS

WORDS = " ".join(f"w{i:04d}" for i in range(1, 700))
NOTE_WORDS = " ".join(f"n{i:04d}" for i in range(1, 2000))
HEADINGS = re.compile(r"DESCRIPTION QTY")
# a row of the document, or the rest of a long one carried over
ROW = re.compile(r"\bLine \d+|\bw\d{4}\b")
# the print page's running head ("Invoice 1001 · Page 3 of 3")
RUNNING_HEAD = re.compile(r"^\S+ [\w-]+ · Page \d+ of \d+")
# the band a 50-line document's long last line ends in at the foot of page 2
LONG_LINES = range(601, 852, 25)


def page_texts(pdf):
    pypdf = pytest.importorskip("pypdf")
    return [
        " ".join((p.extract_text() or "").split())
        for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
    ]


def ending_problems(pages):
    """What is wrong with how a document ends, as sentences: no page shows
    the column headings over no row, the totals are on the last page, and
    the footer is drawn once, on the last page, with something above it."""
    said = [
        f"page {i} has the column headings over no row"
        for i, page in enumerate(pages, 1)
        if HEADINGS.search(page) and not ROW.search(page)
    ]
    if "Subtotal" not in " ".join(pages):
        said.append("no totals")
    if sum(p.count(MARK) for p in pages) != 1 or MARK not in pages[-1]:
        said.append("the footer is not once, on the last page")
    rest = RUNNING_HEAD.sub("", pages[-1]).replace(MARK, "").strip()
    if not rest:
        said.append("the last page holds the footer alone")
    return said


_made = {"n": 0}


def long_last_line(client, accounts, customer, kind, n, length, notes=None):
    """A document of the kind with n lines whose last line's description is
    ``length`` characters ("Line n " counted, as the template counts it),
    and the notes given; answers its address."""
    real = w183._lines

    def lines(k, **extra):
        made = real(k, **extra)
        made[-1]["description"] = (f"Line {k} " + WORDS)[:length].rstrip()
        return made

    _made["n"] += 1
    if kind == "bill":
        body = {
            "vendor_id": w183._vendor(client, "Harbor Supply")["id"],
            "bill_number": f"R22-{_made['n']}",
            "date": "2026-11-01",
            "lines": lines(n, account_id=accounts["6000"].id),
        }
        if notes:
            body["notes"] = notes
        r = client.post("/api/bills", json=body)
        assert r.status_code in (200, 201), r.text
        return f"/api/bills/{r.json()['id']}"
    saved = w183._lines, w183.NOTES
    w183._lines, w183.NOTES = lines, notes or w183.NOTES
    try:
        return MAKERS[kind](client, accounts, customer, n, bool(notes))
    finally:
        w183._lines, w183.NOTES = saved


def uncut(monkeypatch):
    """#1045's markup: nothing of a long block kept with what follows it."""
    from app.services import pdf_service

    monkeypatch.setattr(pdf_service, "choose_last_line", lambda html: "whole")
    monkeypatch.setitem(
        pdf_service._jinja_env.globals,
        "tail_split",
        lambda text, *a, **k: ((text or "").rstrip(), ""),
    )


def search(render, lengths, monkeypatch):
    """The lengths whose document, uncut, strands its totals or its footer:
    ``render(length)`` answers the pages."""
    with monkeypatch.context() as m:
        uncut(m)
        return [
            length
            for length in lengths
            if any(
                "column headings over no row" in p or "footer alone" in p
                for p in ending_problems(render(length))
            )
        ]


def _pdf(client, url):
    r = client.get(f"{url}/pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-", r.text[:200]
    return page_texts(r.content)


@pytest.mark.parametrize("kind", list(MAKERS))
def test_w187_a_long_last_line_keeps_its_end_with_the_totals(
    client, seed_accounts, seed_customer, kind, monkeypatch
):
    pytest.importorskip("pypdf")

    def render(length):
        url = long_last_line(client, seed_accounts, seed_customer, kind, 50, length)
        return _pdf(client, url)

    found = search(render, LONG_LINES, monkeypatch)
    assert found, f"{kind}: no length in the band ends at a page's foot"
    wrong = [
        f"{kind}, 50 lines, a {length}-character last line: {p}"
        for length in found
        for p in ending_problems(render(length))
    ]
    # and a line far taller than a page still breaks: page 1 keeps its rows
    pages = render(3000)
    wrong += [
        f"{kind}, a 3,000-character last line: {p}" for p in ending_problems(pages)
    ]
    assert not wrong, "\n".join(wrong)


def test_w187_the_cut_markup(client, seed_accounts, seed_customer, monkeypatch):
    # the layout's choice forced: a cut carrying the line's last 30 words
    # (W-200: pdf_service.choose_last_line picks whole or the last lines)
    from app.services import pdf_service

    monkeypatch.setattr(pdf_service, "choose_last_line", lambda html: 30)
    url = long_last_line(client, seed_accounts, seed_customer, "invoice", 3, 900)
    body = client.get(f"{url}/print-preview").text.split("<body")[1]
    tail = body.split('<tbody class="doc-tail">')[1].split("</tbody>")[0]
    # the group opens on the end of the last line, then the totals
    assert tail.lstrip().startswith(
        '<tr class="alt row-end">'
    ) or tail.lstrip().startswith('<tr class="row-end">'), tail[:200]
    assert tail.index('class="row-end"') < tail.index('class="totals')
    before = body.split('<tbody class="doc-tail">')[0]
    assert "row-head" in before and "Line 3 w0001" in before
    end = re.findall(r"\bw\d{4}\b", tail.split("</td>")[0])
    assert len(end) == 30
    # every word once, in order
    words = re.findall(r"\bw\d{4}\b", re.sub(r"<[^>]+>", " ", body))
    assert words == sorted(words) and len(words) == len(set(words))


def test_tail_split_cuts_at_a_sentence_or_a_space():
    from app.services.pdf_service import _tail_split

    assert _tail_split("short") == ("", "short")
    head, end = _tail_split("x" * 50 + ". " + "y " * 200)
    assert head.endswith(".") and end.startswith("y")
    head, end = _tail_split(WORDS[:900])
    assert head + " " + end == WORDS[:900].rstrip()
    assert 240 <= len(end) < 260
    head, end = _tail_split("z" * 900)  # one word: a hard cut
    assert head + end == "z" * 900 and len(end) == 240


def test_w188_long_notes_end_with_the_footer(
    client, seed_accounts, seed_customer, monkeypatch
):
    """The notes' last lines and the footer are one block: WeasyPrint kept
    them together already, and still does across the band."""
    pytest.importorskip("pypdf")
    wrong = []
    for length in (1001, 3000, 5950, 6000, 6040):
        url = long_last_line(
            client,
            seed_accounts,
            seed_customer,
            "invoice",
            50,
            20,
            notes=NOTE_WORDS[:length].rstrip(),
        )
        wrong += [
            f"{length}-character notes: {p}" for p in ending_problems(_pdf(client, url))
        ]
    assert not wrong, "\n".join(wrong)


# --- NEW-260: the giving statement's columns --------------------------------


def _date_wraps(pdf):
    """How many gift dates print on two lines, and the pages (pdftotext's
    word boxes: a date's year below its month)."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        out = subprocess.run(
            ["pdftotext", "-bbox", f.name, "-"], capture_output=True, text=True
        ).stdout
    words = re.findall(r'yMin="([\d.]+)"[^>]*>([^<]*)<', out)
    return sum(
        1
        for i, (y, w) in enumerate(words)
        if w == "Mar"
        and i + 2 < len(words)
        and abs(float(words[i + 2][0]) - float(y)) > 2
    )


def test_new260_the_giving_statements_dates_and_figures_never_wrap(
    client, seed_accounts
):
    from tests import test_pdf_last_page_1047 as t

    pytest.importorskip("pypdf")
    grown = {"pdfs": []}
    pdf = t.giving_statement(client, seed_accounts, 45, grown)
    pages = t.page_texts(pdf)
    # round 21 printed 45 gifts on 2 pages; #1047's spanning cell made it 3
    assert len(pages) == 2, len(pages)
    assert _date_wraps(pdf) == 0
    assert not t.last_page_problems(pages, t.SPECS["giving statement"], 45)


def test_new260_the_closing_takes_no_part_in_the_column_widths(client):
    from app.services.pdf_service import _jinja_env

    src = _jinja_env.loader.get_source(_jinja_env, "giving_statement_pdf.html")[0]
    assert "table.activity .closing { width: 0; min-width: 100%; }" in src
    assert '{% set closing %}<div class="closing">' in src
    assert src.count('<td class="d">{{ g.date | fdate }}</td>') == 2
