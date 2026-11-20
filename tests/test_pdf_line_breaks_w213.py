"""Round 24 (W-213): a description's line breaks print as line breaks.

A description typed as separate lines printed as one run-on paragraph on the
saved PDF and in Edge and Chrome: the templates printed the text, and HTML
folds a line break into a space. Each description now prints escaped with a
<br> for each break, on the five line documents and wherever else a line's
description prints (change orders, pay applications, statements, checks,
deposits, giving statements).

The long last line (#1137, #1138: laid out whole, cut once between two of
the layout's lines, two lines carried) is swept again here with
descriptions that have line breaks, short lines and blank lines among long
ones: nothing is lost, repeated or cut inside a word, every break starts a
new line, the end carried is two lines of the layout, and a line ended by a
break is never stretched to the column's width.
"""

import re
import shutil

import pytest

from tests import test_pdf_last_page_w187_w188 as w187
from tests.test_pdf_last_line_w200_w201 import _words
from tests.test_pdf_last_page_w183 import MAKERS
from tests.test_pdf_last_page_w187_w188 import ending_problems, long_last_line
from tests.test_pdf_last_page_w187_w188 import page_texts

# lines of 3, 14, 2, 25, 7 and 1 words, a blank line after every fifth
SIZES = (3, 14, 2, 25, 7, 1)


def _broken_words():
    out, i, k = [], 1, 0
    while i < 700:
        size = SIZES[k % len(SIZES)]
        out.append(" ".join(f"w{j:04d}" for j in range(i, min(i + size, 700))))
        i += size
        k += 1
        if k % 5 == 0:
            out.append("")
    return "\n".join(out)


BROKEN = _broken_words()


def _pairs_across_a_break(text):
    """(word before, word after) for each line break of ``text``."""
    pairs = []
    for m in re.finditer(r"(w\d{4})\s*\n\s*(w\d{4})", text):
        pairs.append((m[1], m[2]))
    return pairs


def _end_cell(html):
    m = re.search(r'class="[^"]*row-end[^"]*"><td>(.*?)</td>', html, re.S)
    return m[1] if m else ""


# (lines on the document, characters in the last line): short and long
# documents, a cut at a break and one inside a line, a blank line carried
# and one left at the first part's foot
CASES = (
    (2, 700),
    (2, 1500),
    (47, 700),
    (48, 761),
    (48, 900),
    (50, 835),
    (50, 1000),
    (50, 1100),
    (50, 3000),
)


def problems(said, n, length, html, pdf):
    """What is wrong with one printed document, as sentences, and how its
    last line was cut: None (whole), "break" (at a line break) or "line"
    (inside a paragraph, between two of the layout's lines)."""
    wrong = [f"{said}: {p}" for p in ending_problems(page_texts(pdf))]
    words = _words(pdf)
    seen = [w for *_, w in words]
    if seen != sorted(seen) or len(seen) != len(set(seen)):
        wrong.append(f"{said}: a word missing, twice or out of order")
    where = {w: (p, y) for p, y, _a, _b, w in words}
    source = (f"Line {n} " + BROKEN)[:length]
    for before, after in _pairs_across_a_break(source):
        if before in where and after in where and where[before] == where[after]:
            wrong.append(f"{said}: the break after {before} printed as a space")
    left = min(a for _p, _y, a, _b, _w in words)
    rows = {}
    for p, y, a, b, w in words:
        rows.setdefault((p, y), []).append((a, b, w))
    full = max(max(b for _a, b, _w in r) for r in rows.values()) - left
    # a line ended by a break is set as usual, never stretched
    for r in rows.values():
        last_word = max(r)[2]
        if (
            len(r) <= 3
            and re.search(rf"{last_word}[ \t]*\n", source)
            and max(b for _a, b, _w in r) - left > 0.6 * full
        ):
            wrong.append(f"{said}: the short line ending {last_word} is stretched")
    cell = _end_cell(html)
    end = set(re.findall(r"\bw\d{4}\b", re.sub(r"<[^>]+>", " ", cell)))
    if not end:
        return wrong, None
    tail = {where[w] for w in end if w in where}
    # a blank line carried opens the end; a cut word ("w01") the length
    # left on a line of its own is a line too
    blank = cell.count("&nbsp;<br>")
    stub = re.search(r"\n\s*w\d{0,3}$", source.rstrip()) is not None
    carried = len(tail) + blank + stub
    if carried != 2:
        wrong.append(f"{said}: the end carried is {carried} lines")
    first = min(end)
    head_last = max(w for w in where if w < first)
    if re.search(rf"{head_last}\s*\n", source):
        return wrong, "break"
    head_rows = [r for r in rows.values() if all(w not in end for *_x, w in r)]
    last_row = max(head_rows, key=lambda r: where[r[0][2]])
    reach = max(b for _a, b, _w in last_row) - left
    if reach < 0.9 * full:
        wrong.append(
            f"{said}: the first part's last line is short ({reach / full:.0%})"
        )
    return wrong, "line"


@pytest.mark.parametrize("kind", list(MAKERS))
def test_a_description_with_line_breaks_prints_them_and_cuts_on_two_lines(
    client, seed_accounts, seed_customer, kind, monkeypatch
):
    pytest.importorskip("pypdf")
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext is not installed")
    monkeypatch.setattr(w187, "WORDS", BROKEN)
    wrong, cuts = [], []
    for n, length in CASES:
        url = long_last_line(client, seed_accounts, seed_customer, kind, n, length)
        html = client.get(f"{url}/print-preview").text
        pdf = client.get(f"{url}/pdf").content
        said = f"{kind}, {n} lines, {length} characters"
        found, cut = problems(said, n, length, html, pdf)
        wrong += found
        cuts.append(cut)
    assert "break" in cuts and "line" in cuts, f"{kind}: {cuts}"
    assert not wrong, "\n".join(wrong)


def test_the_description_stays_escaped_and_each_break_is_a_br():
    from markupsafe import Markup

    from app.services.pdf_service import _desc

    assert _desc("a <b> & c\r\nd\re\nf") == Markup("a &lt;b&gt; &amp; c<br>d<br>e<br>f")
    assert _desc(None) == Markup("")
    made = _desc("x\ny")
    assert _desc(made) is made


# where a line's description prints, outside the five line documents; a
# description of the document itself (a change order's, a gift's fair value)
# is not a line's, a property line's is joined into one sentence, and the
# pay page (not a print) names a pay application's retainage in its own words
NOT_A_LINE = (
    "row.description",
    "co.description",
    "fair_value_description",
    "ln.description",
)


def test_every_line_description_a_document_prints_keeps_its_breaks():
    from app.services.pdf_service import TEMPLATE_DIR

    missed = []
    for path in sorted(TEMPLATE_DIR.glob("*.html")):
        for m in re.finditer(r"\{\{([^}]*\bdescription\b[^}]*)\}\}", path.read_text()):
            said = m[1]
            if "| desc" in said or any(k in said for k in NOT_A_LINE):
                continue
            missed.append(f"{path.name}: {{{{{said}}}}}")
    assert not missed, "\n".join(missed)


def test_a_change_orders_line_prints_its_breaks_escaped(
    client, seed_customer, monkeypatch
):
    from app.services import pdf_service
    from tests.test_co_effective_date_allowance import _setup

    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "lines": [
                {
                    "description": "Demolition\n<b>Haul</b> & dispose",
                    "cost_type": "labor",
                    "quantity": 1,
                    "unit_cost": 100,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    html = client.get(f"/api/change-orders/{r.json()['id']}/pdf").text
    assert "Demolition<br>&lt;b&gt;Haul&lt;/b&gt; &amp; dispose" in html
    assert "<b>Haul" not in html
