"""A long last line is cut only when the layout needs it, and then at a line
boundary: its last two lines go with the totals (W-200, W-201, NEW-266,
2.32.0 gate round 23).

Round 22 (#1120) cut every last line over 600 characters at 240 characters
from its end, about four or five lines of a 60-character column (seven at a
sentence's end), even on a one-page document, so the first part's last line
stopped short, and Qty, Rate and Amount sat beside the first part only.
Now the document is laid out with the line whole first
(pdf_service.choose_last_line): when its end and the totals share a page
with room to spare it prints whole; otherwise the cut falls between two of
the layout's own lines, so the end is exactly DOC_TAIL_LINES lines and the
first part's last line is a full one. Every row's figures are on its first
line. The Chromium sweep of the same lengths is in the PR."""

import re
import shutil
import subprocess
import tempfile

import pytest

from tests.test_pdf_last_page_w183 import MAKERS
from tests.test_pdf_last_page_w187_w188 import (
    ending_problems,
    long_last_line,
    page_texts,
)


def _words(pdf):
    """[(page, y, x0, x1, word)] of every wNNNN word, from pdftotext."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        out = subprocess.run(
            ["pdftotext", "-bbox", f.name, "-"], capture_output=True, text=True
        ).stdout
    found = []
    for number, page in enumerate(out.split("<page ")[1:]):
        for m in re.finditer(
            r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">'
            r"(w\d{4})<",
            page,
        ):
            found.append((number, round(float(m[2])), float(m[1]), float(m[3]), m[4]))
    return found


def carried_problems(tail, every):
    """What is wrong with the end a cut carries (NEW-271), as sentences:
    ``tail`` is the (page, y) of the end's lines, ``every`` of all the
    line's lines. The page with the end holds no line of the first part,
    and the end is two lines, or more only where the line ran over onto
    that page by itself: the cut is then at the page break, the first
    part ending on the page before."""
    if not tail:
        return ["no end carried"]
    pages = {p for p, _y in tail}
    head = every - tail
    said = []
    if len(pages) != 1:
        said.append(f"the end is on pages {sorted(pages)}")
    page = max(pages)
    if any(p == page for p, _y in head):
        said.append("the first part runs onto the totals' page")
    if len(tail) != 2 and (not head or max(p for p, _y in head) != page - 1):
        said.append(f"the end carried is {len(tail)} lines")
    return said


def _end_words(html):
    m = re.search(r'class="[^"]*row-end[^"]*"><td>(.*?)</td>', html, re.S)
    return re.findall(r"\bw\d{4}\b", re.sub(r"<[^>]+>", " ", m[1])) if m else []


@pytest.mark.parametrize("kind", list(MAKERS))
def test_a_long_last_line_is_whole_when_it_fits_and_else_ends_on_two_lines(
    client, seed_accounts, seed_customer, kind
):
    pytest.importorskip("pypdf")
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    wrong, cut_seen = [], 0
    for n, length in (
        (2, 900),
        (3, 1500),
        (47, 700),
        (48, 700),
        (50, 1000),
        (50, 1100),
        (50, 3000),
    ):
        url = long_last_line(client, seed_accounts, seed_customer, kind, n, length)
        html = client.get(f"{url}/print-preview").text
        pdf = client.get(f"{url}/pdf").content
        pages = page_texts(pdf)
        said = f"{kind}, {n} lines, {length} characters"
        wrong += [f"{said}: {p}" for p in ending_problems(pages)]
        words = _words(pdf)
        seen = [w for *_, w in words]
        if seen != sorted(seen) or len(seen) != len(set(seen)):
            wrong.append(f"{said}: a word missing, twice or out of order")
        end = set(_end_words(html))
        # with room on the page the line prints whole; a page that only just
        # holds it is cut (DOC_TAIL_SLACK, for a browser's print), and that
        # cut must not show, as below
        if n == 2 and len(pages) == 1 and end:
            wrong.append(f"{said}: cut on a one-page document with room")
        if not end:
            continue
        cut_seen += 1
        tail = {(p, y) for p, y, _a, _b, w in words if w in end}
        wrong += [
            f"{said}: {p}"
            for p in carried_problems(tail, {(p, y) for p, y, *_x, w in words})
        ]
        head = {}
        left = min(a for _p, _y, a, _b, _w in words)
        for p, y, _a, b, w in words:
            if w not in end:
                head[(p, y)] = max(head.get((p, y), 0), b)
        full = max(head.values()) - left
        last = head[max(head)] - left
        if last < 0.9 * full:
            wrong.append(
                f"{said}: the first part's last line is short ({last / full:.0%})"
            )
    assert cut_seen, f"{kind}: no length needed the cut"
    assert not wrong, "\n".join(wrong)


def test_every_rows_figures_are_on_its_first_line():
    from app.services.pdf_service import _jinja_env

    for name in (
        "invoice_pdf.html",
        "bill_pdf.html",
        "purchase_order_pdf.html",
        "credit_memo_pdf.html",
        "estimate_pdf.html",
    ):
        src = _jinja_env.loader.get_source(_jinja_env, name)[0]
        assert "table.line-items > tbody > tr > td { vertical-align: top; }" in src
        assert "tail_split(line.description" not in src, name
        assert src.count("data-last-line") >= 1, name
