"""Round 24, NEW-271: a long last line's cut holds whatever the fonts are.

Round 23 (#1137) laid the document out with the line whole and carried its
last two lines with the totals. When the line itself already ran over onto
the totals' page, the rest of its first part went there too: the gate's
900-1,200-character last lines at 47-50 lines had 3 to 11 of its lines on
the totals' page, the first part's last line set full in mid-page over a
seam. It was not the Mac's fonts (Linux printed the same documents the same
way); the product's test only counted the end's own lines.

Now that layout decides the whole of it (pdf_service.choose_last_line): a
line that runs over onto the totals' page prints whole with them when they
fit on one page ("kept"), and a cut leaves the first part off the totals'
page, its last line set as the layout set it. Checked here from the layout
of the HTML that prints, with the fonts as shipped and with a wider family
forced on every element (as a Mac's or Windows' fonts measure differently),
on the five line documents and the construction estimate's three layouts.
"""

import re

import pytest

from app.services import pdf_service
from tests import test_pdf_last_page_1045_1046 as c1046
from tests.test_pdf_last_page_w183 import MAKERS
from tests.test_pdf_last_page_w187_w188 import WORDS, long_last_line, page_texts

# the gate's band (GATE.md 176): 900-1,200 characters at 47-50 lines
CASES = ((47, 1200), (48, 1000), (49, 900), (50, 1100))
KINDS = [*MAKERS, "line_markup", "bid", "bid tables"]
FONTS = {"as shipped": None, "a wider family": "DejaVu Sans"}


def _force(monkeypatch, family):
    """Every layout, the deciding one and the printed one, in ``family``."""
    if family is None:
        return
    real = pdf_service._weasyprint

    def forced():
        HTML, fetcher = real()

        class Forced(HTML):
            def __init__(self, *args, string=None, **kw):
                if string is not None:
                    string = string.replace(
                        "</head>",
                        f"<style>* {{ font-family: '{family}' !important; }}"
                        "</style></head>",
                        1,
                    )
                super().__init__(*args, string=string, **kw)

        return Forced, fetcher

    monkeypatch.setattr(pdf_service, "_weasyprint", forced)


def _document(client, accounts, customer, kind, n, length):
    """The address of a document whose last line is ``length`` characters,
    and that line's text."""
    if kind in MAKERS:
        url = long_last_line(client, accounts, customer, kind, n, length)
        return url, (f"Line {n} " + WORDS)[:length].rstrip()
    c1046._construction(client)
    lines, _last = c1046.construction_lines(client, kind, n)
    text = (f"Item {n} " + WORDS)[:length].rstrip()
    lines[-1]["description"] = text
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer.id,
            "date": "2026-11-01",
            "lines": lines,
            "terms": "",
        },
    )
    assert r.status_code == 201, r.text
    return f"/api/estimates/{r.json()['id']}", text


def _where(html):
    """The pages of the printed layout: the first part's lines, the end's,
    and the totals' group's, with whether a cut row's cell is stretched."""
    from weasyprint.formatting_structure import boxes as wb

    document = pdf_service._layout(html)
    head, end, tail, stretched = [], [], set(), False
    for number, page in enumerate(document.pages):
        for box in page._page_box.descendants():
            classes, element = pdf_service._box_classes(box)
            if element is None:
                continue
            if isinstance(box, wb.TableRowGroupBox) and "doc-tail" in classes:
                tail.add(number)
            if not isinstance(box, wb.TableRowBox):
                continue
            into = (
                head if "row-head" in classes else end if "row-end" in classes else None
            )
            if into is None:
                continue
            cell = next(c for c in box.children if isinstance(c, wb.TableCellBox))
            stretched |= cell.style["text_align_last"] == "justify"
            into += [number for t in pdf_service._line_texts(cell) if t.strip()]
    return head, end, tail, stretched


@pytest.mark.parametrize("font", list(FONTS))
def test_the_totals_page_carries_the_end_alone_and_two_lines_of_it(
    client, seed_accounts, seed_customer, monkeypatch, font
):
    pytest.importorskip("pypdf")
    _force(monkeypatch, FONTS[font])
    wrong, cuts, kept = [], 0, 0
    for kind in KINDS:
        for n, length in CASES:
            url, text = _document(client, seed_accounts, seed_customer, kind, n, length)
            said = f"{font}, {kind}, {n} lines, {length} characters"
            html = client.get(f"{url}/print-preview").text
            head, end, tail, stretched = _where(html)
            if len(tail) != 1:
                wrong.append(f"{said}: the totals' group is on pages {sorted(tail)}")
                continue
            page = tail.pop()
            if stretched:
                wrong.append(f"{said}: a line of the cut row is set full")
            if end:
                cuts += 1
                if page in head:
                    wrong.append(
                        f"{said}: {head.count(page)} lines of the first part "
                        "are on the totals' page"
                    )
                if set(end) != {page} or len(end) != 2:
                    wrong.append(f"{said}: the totals' page carries {len(end)} lines")
            else:
                kept += "data-last-line" not in html
                if set(head) - {page}:
                    wrong.append(f"{said}: uncut, and not on the totals' page")
            # every word once, in order, none cut, on the PDF itself
            pdf = client.get(f"{url}/pdf").content
            seen = re.findall(r"\bw\d+\b", " ".join(page_texts(pdf)))
            if seen != re.findall(r"\bw\d+\b", text):
                wrong.append(f"{said}: a word missing, twice, cut or out of order")
    assert cuts + kept, f"{font}: every line printed whole with room"
    assert not wrong, "\n".join(wrong)
