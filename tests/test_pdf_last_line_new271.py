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


# ---------------------------------------------------------------------------
# Round 25 (W-220, W-221): the shapes the round's sweep found carrying more
# than two lines, or cut at two lines.
#
# W-220: a last line written with line breaks is 34-51 lines of the layout
# tall. On a bid, a bid with tables and a credit memo it starts 59-87 %
# down a page and runs over the page break, 17-38 of its lines on the
# totals' page. Kept whole with the totals it does not fit on one page (on
# a bid, the room under the totals is 83-369 pt; the 22 lines the page
# before holds of it need 370 pt). A cut may fall at any line of the layout,
# inside a paragraph or at a break (_cut_words counts words, not breaks),
# but the totals' group fits on that page under the k lines the break
# leaves there, so a first part of all but two lines puts k - 2 of its own
# lines on the totals' page: the first part ends at a page's foot only by
# holding exactly the lines the break leaves above it. So the cut carries
# the k lines and none of the first part is on the totals' page; two is
# possible only by forcing the totals onto a page of their own after a
# page holding k - 2 lines of the first part alone (and a page more).
#
# W-221: a 2-line bid with every line in a package, at 1,200 characters,
# does not fit on one page: laid out whole, its totals' group falls to the
# next page (with the line whole above them it overruns by a few points).
# Kept whole with the totals would leave the first page under half full
# (the line starts 29 % down it), so it is cut and two lines are carried.
# Each shape below is checked against the uncut layout that decided it.
# ---------------------------------------------------------------------------

BREAK_PARTS = "\n".join(
    " ".join(f"w{j:04d}" for j in range(i, i + 4)) for i in range(1, 2000, 4)
)
# the Mac's detail: bids and bids with tables as plain words and sentences
SENTENCES = " ".join(
    " ".join(f"w{j:04d}" for j in range(i, i + 8)) + "." for i in range(1, 2000, 8)
)
BREAK_KINDS = ["bid", "bid tables", "credit memo", "line_markup"]
SHAPES = [(kind, BREAK_PARTS) for kind in BREAK_KINDS] + [
    (kind, text) for kind in ("bid", "bid tables") for text in (WORDS, SENTENCES)
]
BREAK_CASES = ((47, 1200), (48, 900), (49, 1100), (50, 1000))


def _shaped(client, accounts, customer, kind, n, length, text, packaged=False):
    """A document of ``kind`` whose last line is ``text`` cut to ``length``."""
    if kind in MAKERS:
        import tests.test_pdf_last_page_w187_w188 as w187

        saved = w187.WORDS
        w187.WORDS = text
        try:
            return long_last_line(client, accounts, customer, kind, n, length)
        finally:
            w187.WORDS = saved
    c1046._construction(client)
    lines, _last = c1046.construction_lines(client, kind, n)
    if packaged:
        pkgs = [
            c1046._package(client, "Concrete"),
            c1046._package(client, "Electrical"),
        ]
        for i, line in enumerate(lines):
            line["trade_package_id"] = pkgs[i % 2]
    lines[-1]["description"] = (f"Item {n}\n" + text)[:length].rstrip()
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
    return f"/api/estimates/{r.json()['id']}"


def _decided(monkeypatch):
    """The uncut layout's facts each print decided on, newest last."""
    real = pdf_service.choose_last_line
    seen = []

    def spy(html, keep=True):
        document = pdf_service._layout(html)
        found, start, tail, _pages = pdf_service._last_line_layout(document)
        while found and not found[-1][1].strip():
            found = found[:-1]
        on_totals = sum(1 for p, _t in found if tail and p == tail[0])
        seen.append({"start": start, "tail": tail, "on_totals": on_totals})
        return real(html, keep)

    monkeypatch.setattr(pdf_service, "choose_last_line", spy)
    return seen


def _sweep_one(client, url, seen, said):
    """What is wrong with one printed document: the first part on the
    totals' page, or more than two lines carried where two were possible."""
    seen.clear()
    html = client.get(f"{url}/print-preview").text
    head, end, tail, stretched = _where(html)
    if len(tail) != 1:
        return [f"{said}: the totals' group is on pages {sorted(tail)}"], None
    page = tail.pop()
    wrong = [f"{said}: a line of the cut row is set full"] if stretched else []
    if not end:
        if set(head) - {page}:
            wrong.append(f"{said}: uncut, and not on the totals' page")
        return wrong, 0
    if page in head:
        wrong.append(
            f"{said}: {head.count(page)} lines of the first part on the totals' page"
        )
    if set(end) != {page}:
        wrong.append(f"{said}: the end is on pages {sorted(set(end))}")
    if len(end) != 2:
        # more than two only when the uncut line ran over the page break
        # with that many of its lines on the totals' page, starting on the
        # page before it
        first = seen[0]
        if not (
            first["on_totals"] == len(end) and first["start"][0] == first["tail"][0] - 1
        ):
            wrong.append(f"{said}: {len(end)} lines carried; {first}")
    return wrong, len(end)


def test_a_last_line_with_line_breaks_carries_what_the_page_break_leaves(
    client, seed_accounts, seed_customer, monkeypatch
):
    seen = _decided(monkeypatch)
    wrong, counts = [], {}
    for kind, text in SHAPES:
        for n, length in BREAK_CASES:
            url = _shaped(client, seed_accounts, seed_customer, kind, n, length, text)
            found, carried = _sweep_one(
                client, url, seen, f"{kind}, {n} lines, {length} characters"
            )
            wrong += found
            counts[carried] = counts.get(carried, 0) + 1
    assert any(c and c > 2 for c in counts), counts  # the shape W-220 found
    assert not wrong, "\n".join(wrong)


def test_a_two_line_packaged_bid_prints_whole_when_it_fits(
    client, seed_accounts, seed_customer, monkeypatch
):
    seen = _decided(monkeypatch)
    wrong, whole = [], 0
    for length in (590, 700, 800, 900, 1000, 1100, 1200):
        url = _shaped(
            client, seed_accounts, seed_customer, "bid", 2, length, WORDS, True
        )
        said = f"2-line packaged bid, {length} characters"
        found, carried = _sweep_one(client, url, seen, said)
        wrong += found
        if carried == 0:
            whole += 1
        elif seen:
            first = seen[0]
            # cut only when it does not fit: the totals' group fell to the
            # page after the line, and keeping the line with them would have
            # left the page before under half full
            if not (
                first["tail"][0] > first["start"][0]
                and first["on_totals"] == 0
                and first["start"][1] < 0.5
            ):
                wrong.append(f"{said}: cut though it fits; {first}")
    assert whole >= 5, whole
    assert not wrong, "\n".join(wrong)
