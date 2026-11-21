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


# ---------------------------------------------------------------------------
# A cut falls at a line of the layout, whatever ended the line.
#
# The layout's carried lines were counted in words, which is their length
# only when every line ends at a space. Text written without spaces
# (Japanese, Chinese, one unbroken run) was not cut at all: every line of it
# went into the group with the totals and its Qty, Rate and Amount printed
# on another page. Where the layout broke a line after a hyphen ("pre-" /
# "construction") the cut fell a word early, and the first part ended in a
# short line. The carried lines are now counted in characters, the same in
# the layout and in the description, so the row as printed in two parts has
# the lines of the row laid out whole.
# ---------------------------------------------------------------------------

HYPHENS = (
    "Furnish and install heavy-duty stainless-steel anchors (3/4-inch x 6-inch),"
    " self-tapping; includes owner-supplied fixtures, pre-construction"
    " walk-through and post-installation clean-up per section 05-50-00/2.3(b)."
    " Contractor-furnished, owner-installed items are by others. "
) * 12
NO_SPACES = "0123456789abcdefghijklmnopqrstuvwxyz" * 60
# Long enough to run past the totals' page in any font. With a Japanese font
# a line holds about 31 of these characters; a machine with none (the CI
# runner) draws each as a narrow box for a missing glyph, about 66 to a line,
# and 1200 characters then fit on the page with the totals: printed whole,
# rightly, and nothing to cut. 3000 are some 45 lines even there.
SCRIPT = (
    "見積書に記載された工事範囲は以下のとおりです。基礎工事および鉄骨工事を含みます。"
    * 80
)
SEAM_CASES = [
    ("hyphens, an invoice", "invoice", 48, 900, HYPHENS),
    ("hyphens, a purchase order", "purchase order", 10, 1900, HYPHENS),
    ("hyphens, a bid", "bid", 47, 900, HYPHENS),
    ("no spaces, an invoice", "invoice", 47, 900, NO_SPACES),
    ("no spaces, a credit memo", "credit memo", 47, 1000, NO_SPACES),
    ("no spaces, a bill", "bill", 49, 900, NO_SPACES),
    ("Japanese, an invoice", "invoice", 30, 3000, SCRIPT),
]


def _row_lines(html):
    """The lines of the last line's description as laid out: the whole
    row's, or the cut row's two parts one after the other; and the pages
    its figures are on."""
    from weasyprint.formatting_structure import boxes as wb

    lines, figures = [], []
    for number, page in enumerate(pdf_service._layout(html).pages):
        for box in page._page_box.descendants():
            classes, element = pdf_service._box_classes(box)
            if element is None or not isinstance(box, wb.TableRowBox):
                continue
            if element.get("data-last-line") is None and "row-end" not in classes:
                continue
            cells = [c for c in box.children if isinstance(c, wb.TableCellBox)]
            texts = [t.strip() for t in pdf_service._line_texts(cells[0])]
            lines += [(number, t) for t in texts if t]
            if any(t.strip() for c in cells[1:] for t in pdf_service._line_texts(c)):
                figures.append(number)
    return lines, figures


def _printed_and_whole(client, url, monkeypatch):
    """The HTML a document prints as, and the layout of its last line
    whole that decided the cut."""
    whole = []
    real = pdf_service.choose_last_line

    def spy(html, keep=True):
        whole.append(html)
        return real(html, keep)

    with monkeypatch.context() as patch:
        patch.setattr(pdf_service, "choose_last_line", spy)
        html = client.get(f"{url}/print-preview").text
    assert whole, "the last line was not long"
    return html, whole[0]


def _seam(said, html, whole):
    """What is wrong with a cut row: it is the row laid out whole, in two
    parts, the end alone on the totals' page, two lines of it or what the
    page break left there, and the figures on the row's first line."""
    head, end, tail, _stretched = _where(html)
    if not end:
        return [f"{said}: not cut"]
    if len(tail) != 1:
        return [f"{said}: the totals' group is on pages {sorted(tail)}"]
    page = tail.pop()
    wrong = []
    if not head or page in head:
        wrong.append(f"{said}: the first part is on the totals' page, or empty")
    if set(end) != {page}:
        wrong.append(f"{said}: the end is on pages {sorted(set(end))}")
    was, _f = _row_lines(whole)
    now, figures = _row_lines(html)
    # no line of the first part is cut short, none of the end is set again
    if [t for _p, t in now] != [t for _p, t in was]:
        wrong.append(f"{said}: the two parts are not the lines of the row whole")
    on_totals = sum(1 for p, _t in was if p == page)
    if len(end) != max(pdf_service.DOC_TAIL_LINES, on_totals):
        wrong.append(f"{said}: {len(end)} lines carried")
    if figures != [now[0][0]]:
        wrong.append(f"{said}: the figures are on pages {figures}")
    return wrong


def test_a_cut_falls_at_a_line_of_the_layout_whatever_ended_the_line(
    client, seed_accounts, seed_customer, monkeypatch
):
    pytest.importorskip("weasyprint")
    wrong = []
    for said, kind, n, length, text in SEAM_CASES:
        url = _shaped(client, seed_accounts, seed_customer, kind, n, length, text)
        html, whole = _printed_and_whole(client, url, monkeypatch)
        wrong += _seam(said, html, whole)
    assert not wrong, "\n".join(wrong)


def test_the_end_is_counted_in_the_characters_of_the_layouts_lines():
    cut, carry = pdf_service._cut_chars, pdf_service._carry
    # at a space, after a hyphen, inside a run with no spaces: the end is
    # the carried lines' own characters
    assert cut("one two three four", 9) == ("one two", "three four")
    assert cut("install pre-construction walk", 16) == (
        "install pre-",
        "construction walk",
    )
    assert cut("abcdefghij", 4) == ("abcdef", "ghij")
    assert cut("工事範囲は以下のとおり", 5) == ("工事範囲は以", "下のとおり")
    # a line break between the two parts stays out of both
    assert cut("first line\nsecond line", 10) == ("first line", "second line")
    # nothing to carry, or all of it: no first part
    assert cut("one two", 0) == ("", "one two")
    assert cut("one two", 6) == ("", "one two")
    assert cut("one two", 60) == ("", "one two")
    # the layout's lines, their spaces not counted; a blank first line is
    # one of the lines carried
    assert carry(["one two ", "three-", "four five"], 2) == (14, 0)
    assert carry(["one", "", "two three"], 2) == (8, 1)
    assert carry(["", " "], 2) is None


def test_a_tag_after_the_description_is_no_part_of_what_a_cut_divides(
    client, seed_accounts, seed_customer, monkeypatch
):
    """A taxed invoice's untaxed last line ends "(non-taxable)", in an
    element of its own: the layout's lines of the description do not count
    it, so the cut is at the description's own line."""
    pytest.importorskip("weasyprint")
    lines = [
        {"description": f"Line {i}", "quantity": 1, "rate": 33} for i in range(1, 49)
    ]
    text = ("Line 48 " + WORDS)[:900].rstrip()
    lines[-1] = {"description": text, "quantity": 1, "rate": 33, "is_taxable": False}
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-01",
            "tax_rate": "0.0825",
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    url = f"/api/invoices/{r.json()['id']}"
    html, whole = _printed_and_whole(client, url, monkeypatch)
    assert "(non-taxable)" in html
    assert _seam("an untaxed last line", html, whole) == []
    now, _figures = _row_lines(html)
    printed = " ".join(t for _p, t in now).replace("(non-taxable)", "").split()
    assert printed == text.split()
