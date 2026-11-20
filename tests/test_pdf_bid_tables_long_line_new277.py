"""Round 26 (NEW-277): a bid with tables whose scope ends in a long line.

The gate's "bid with tables" is a construction estimate of base lines
"Line 1" … "Line 49" (1 × 33.00), Allowance 1 and 2, Alternate 1 (accepted)
and 2, Unit price 1 and 2, and a long base line typed last: 200 words
"D0001" … "D0200" (1,199 characters, 23 lines of the layout). It printed
16 of those lines at the foot of page 2 and 7 at the top of page 3, where
17 shapes of 48-50 lines × 1,000-1,200 characters, words or sentences,
carried 3-7.

What the layout says (Letter, 96 px to the inch): page 2's content ends at
998.4 px; the line's row starts at 721.9 and its 16th line ends at 996.2,
2.2 px from the foot, with every line 16.8 px. The first part fills the
page; across the 17 shapes it ends 2.2-13.2 px from the foot, never a
line's height. To carry 2 the page would need 1-5 more lines (17-84 px).

And this is not the long last line the cut rule is about: a bid with
tables prints its scope table first, then the allowances, the pending
alternates and the unit prices, and the group kept with the totals is the
last unit price (pdf_service.choose_last_line is never asked; the HTML has
no data-last-line). The scope's long line is a row like any other, broken
where the page ends: its rest opens the next page, above the allowances
table, and the totals follow those tables. So nothing better exists under
the rule, and this pins it: the first part fills its page, the rest opens
the next above the allowances, the totals' group holds no word of it, and
every word prints once and in order.
"""

import re

import pytest

from app.services import pdf_service
from tests.test_pdf_last_page_1045_1046 import _construction

# the 17 shapes the round's sweep found, each (lines, characters, style)
SHAPES = [
    *((n, c, "sentences") for n, c in ((48, 1200), (49, 1100), (49, 1200))),
    *((50, c, "sentences") for c in (1000, 1100, 1200)),
    *((48, c, "words") for c in (1150, 1200)),
    *((49, c, "words") for c in (1050, 1100, 1150, 1200)),
    *((50, c, "words") for c in (1000, 1050, 1100, 1150, 1200)),
]


def gate_text(chars, style):
    """The sweep's last line: numbered five-character words, single spaces;
    as sentences, a full stop after every eighth."""
    words = [f"D{i:04d}" for i in range(1, chars // 6 + 1)]
    if style == "sentences":
        words = [w + ("." if i % 8 == 7 else "") for i, w in enumerate(words)]
    return " ".join(words)


def bid_with_tables(n, last):
    lines = [
        {"description": f"Line {i}", "quantity": 1, "rate": 33} for i in range(1, n)
    ]
    lines += [
        {"description": f"Allowance {i}", "quantity": 1, "rate": 500}
        | {"line_kind": "allowance"}
        for i in (1, 2)
    ]
    lines += [
        {"description": f"Alternate {i}", "quantity": 1, "rate": 700}
        | {"line_kind": "alternate", "alternate_accepted": i == 1}
        for i in (1, 2)
    ]
    lines += [
        {"description": f"Unit price {i}", "quantity": 1, "rate": 45}
        | {"unit": "hr", "line_kind": "unit_price"}
        for i in (1, 2)
    ]
    return lines + [{"description": last, "quantity": 1, "rate": 33}]


def _where(html):
    """[(page, its content's foot, the long row's line boxes there)], the
    pages of the totals' group and whether it holds a word of the row, and
    the page and top of the Allowances table."""
    from weasyprint.formatting_structure import boxes as wb

    parts, tail, allowances = [], set(), None
    for number, page in enumerate(pdf_service._layout(html).pages):
        box = page._page_box
        foot = box.content_box_y() + box.height
        for d in box.descendants():
            classes, element = pdf_service._box_classes(d)
            if element is None:
                continue
            if isinstance(d, wb.TableRowGroupBox) and "doc-tail" in classes:
                words = " ".join(
                    " ".join(pdf_service._line_texts(c))
                    for c in d.descendants()
                    if isinstance(c, wb.TableCellBox)
                )
                tail.add((number, "D0" in words))
            if isinstance(d, wb.TableBox) and allowances is None:
                head = " ".join(
                    t.text for t in d.descendants() if isinstance(t, wb.TextBox)
                )
                if head.lower().startswith("allowances"):
                    allowances = (number, d.position_y)
            if not isinstance(d, wb.TableRowBox):
                continue
            cells = [c for c in d.children if isinstance(c, wb.TableCellBox)]
            if cells and any("D0" in t for t in pdf_service._line_texts(cells[0])):
                lines = [x for x in cells[0].descendants() if isinstance(x, wb.LineBox)]
                parts.append((number, foot, lines, d.position_y + d.margin_height()))
    return parts, tail, allowances


def test_a_long_scope_line_breaks_where_its_page_ends(
    client, seed_customer, monkeypatch
):
    pytest.importorskip("weasyprint")
    pypdf = pytest.importorskip("pypdf")
    import io

    _construction(client)
    made = []
    real = pdf_service._write_pdf
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: made.append(html) or real(html)
    )
    wrong = []
    for n, chars, style in SHAPES:
        text = gate_text(chars, style)
        said = f"{n} lines, {chars} characters, {style}"
        r = client.post(
            "/api/estimates",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-11-18",
                "tax_rate": 0,
                "lines": bid_with_tables(n, text),
            },
        )
        assert r.status_code == 201, r.text
        pdf = client.get(f"/api/estimates/{r.json()['id']}/pdf").content
        html = made[-1]
        # the group kept with the totals is the last unit price's: no cut
        assert "data-last-line" not in html, said
        parts, tail, allowances = _where(html)
        if len(parts) != 2 or parts[1][0] != parts[0][0] + 1:
            # whole on one page: nothing to carry (another font's layout)
            if len(parts) != 1:
                wrong.append(f"{said}: the line is on pages {[p[0] for p in parts]}")
            continue
        (page, foot, first, _bottom), (rest_page, _f, rest, rest_bottom) = parts
        # the first part fills its page: less than a line's room under it
        room = foot - (first[-1].position_y + first[-1].height)
        if room >= first[-1].height:
            wrong.append(f"{said}: {room:.1f} px under the first part")
        # the rest opens the next page, above the allowances
        if allowances is None or allowances[0] != rest_page:
            wrong.append(f"{said}: the allowances are not under the line's rest")
        elif rest_bottom > allowances[1]:
            wrong.append(f"{said}: the line's rest is not above the allowances")
        # and the totals' group carries none of it
        if any(holds for _p, holds in tail):
            wrong.append(f"{said}: the totals' group holds the line")
        seen = re.findall(
            r"\bD\d{4}\b",
            " ".join(
                p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
            ),
        )
        if seen != re.findall(r"\bD\d{4}\b", text):
            wrong.append(f"{said}: a word missing, twice or out of order")
    assert not wrong, "\n".join(wrong)
