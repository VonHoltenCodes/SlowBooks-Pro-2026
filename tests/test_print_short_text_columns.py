"""A column of short text is as wide as its text, up to the room it is given.

"A text cell never widens its column" was first decided by counting
characters: who a deposit is from wrapped past 20 of them, a reference past
10, a report's cell past 40, and a description stopped breaking inside its
words past 200. A count of characters is not a width. Twenty capital W's are
wider than the column twenty ordinary letters were given, so a deposit slip
with text at those counts still printed its Amount column off the paper; a
38-character payment reference kept the General Ledger's Balance past the
margin; and a pay application's item number ("03-3000") was broken inside,
its column starved to the width of its heading.

The rule, with no count in it (app/templates/_print_text.html):

* a column of short text beside a description is as wide as its widest value
  set on one line, and never wider than the width the template gives it:
  text that fits is on one line, as it always was, and longer text wraps
  there, a word breaking inside only when it alone is wider than the column;
* the description has the rest of the table, however little, and its words
  break inside when they are wider than that;
* a report's cell is a figure (on one line) or it is words (which wrap), by
  what is in it, and no word claims more than its share of the sheet;
* a word too long for any line is set in pieces that keep every letter with
  its marks (a Thai vowel or tone mark, an accent typed after its letter)
  and every joined emoji whole.

Each case here printed past the margin, or broke an ordinary item number,
before the rule.
"""

import re
import unicodedata
from decimal import Decimal

import pytest

from tests import test_print_text_never_widens as text

W = "W"


@pytest.fixture
def pages(monkeypatch):
    from app.services import pdf_service

    seen = []
    monkeypatch.setattr(
        pdf_service, "_write_pdf", lambda html: seen.append(html) or b"%PDF-"
    )
    return seen


def ok(r):
    assert r.status_code in (200, 201), r.text[:400]
    return r.json()


def page_of(pages, client, url):
    pages.clear()
    r = client.get(url)
    assert r.status_code == 200, r.text[:300]
    return pages[-1]


def rows(html, table_class):
    """Each row of a table as the PDF lays it out: for every cell, where it
    starts, how wide it is and the lines of text printed in it."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    out = []
    for page in HTML(string=html, url_fetcher=_get_fetcher()).render().pages:
        for table in page._page_box.descendants():
            if not isinstance(table, wb.TableBox):
                continue
            if table_class not in (table.element.get("class") or "").split():
                continue
            for row in table.descendants():
                if not isinstance(row, wb.TableRowBox):
                    continue
                cells = []
                for cell in row.children:
                    lines = []
                    for line in cell.descendants():
                        if not isinstance(line, wb.LineBox):
                            continue
                        said = "".join(
                            t.text
                            for t in line.descendants()
                            if isinstance(t, wb.TextBox)
                            and t.style["visibility"] == "visible"
                        )
                        if said.strip():
                            lines.append(said.strip())
                    cells.append(
                        (
                            round(cell.position_x, 1),
                            round(cell.border_width(), 1),
                            lines,
                        )
                    )
                out.append(cells)
    return out


def past_the_margin(html):
    return text.survey(html)[0]


# --- the pay application's item number --------------------------------------


def _pay_application(client, item, desc, value):
    c = ok(client.post("/api/customers?force=true", json={"name": "Owner " + item}))
    job = ok(
        client.post("/api/jobs", json={"customer_id": c["id"], "name": "Job " + item})
    )
    ok(
        client.put(
            f"/api/jobs/{job['id']}/contract",
            json={
                "contract_type": "lump_sum",
                "contract_amount": value,
                "retainage_pct": 10,
            },
        )
    )
    ok(
        client.put(
            f"/api/jobs/{job['id']}/sov",
            json={
                "lines": [
                    {
                        "item_no": item,
                        "description": desc,
                        "kind": "work",
                        "scheduled_value": value,
                    }
                ]
            },
        )
    )
    app = ok(
        client.post(
            "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
        )
    )
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    half = str((Decimal(value) / 2).quantize(Decimal("0.01")))
    app = ok(
        client.put(
            f"/api/pay-applications/{app['id']}",
            json={
                "lines": [
                    {"sov_line_id": sov[0]["id"], "this_period": half, "stored": "0"}
                ]
            },
        )
    )
    return f"/api/pay-applications/{app['id']}/pdf"


def test_a_pay_application_item_number_is_on_one_line(client, seed_accounts, pages):
    """ "03-3000" printed as "03-3" over "000": the Item column had the width
    of its heading and no more."""
    pytest.importorskip("weasyprint")
    wrong = []
    for item, desc, value in (
        ("03-3000", "Cast-in-place concrete", "345678.90"),
        (
            "03-3000-A",
            "Cast-in-place concrete footings and grade beams, including formwork",
            "3456789.01",
        ),
        (
            "DIV 26-A",
            "Electrical rough-in and telecommunications conduit",
            "34567890.12",
        ),
        ("A-1", "Mobilization", "12.34"),
    ):
        html = page_of(pages, client, _pay_application(client, item, desc, value))
        line = [r for r in rows(html, "cont") if r and r[0][2] == [item]]
        if not line:
            first = [r[0][2] for r in rows(html, "cont")]
            wrong.append(f"item {item!r} is not on one line: {first}")
        wrong += [f"item {item!r}: {p}" for p in past_the_margin(html)]
    assert not wrong, "\n".join(wrong)


def test_a_pay_application_item_number_wider_than_its_column_wraps_there(
    client, seed_accounts, pages
):
    """The column is never wider than the room the template gives it: a
    long item number wraps in it, and the ten columns of figures end where
    they end with a short one."""
    pytest.importorskip("weasyprint")
    for paper in ("letter", "a4"):
        ok(client.put("/api/settings", json={"pdf_paper_size": paper}))
        ends = {}
        for item in ("A-1", W * 10, "@" * 10):
            html = page_of(
                pages,
                client,
                _pay_application(client, item, W * 19 + " " + W * 19, "34567890.12"),
            )
            assert not past_the_margin(html), (paper, item, past_the_margin(html))
            table = rows(html, "cont")
            ends[item] = [round(x + w, 1) for x, w, _ in table[0]][2:]
        assert ends[W * 10] == ends["@" * 10], (paper, ends)
        assert ends[W * 10][-1] == ends["A-1"][-1], (paper, ends)


# --- the deposit slip ---------------------------------------------------------


def _deposit(client, acc, frm, ref, method, memo, amount):
    doc = ok(
        client.post(
            "/api/deposits",
            json={
                "deposit_to_account_id": acc["1000"].id,
                "date": "2026-06-12",
                "line_ids": [],
                "total": amount,
                "other_lines": [
                    {
                        "received_from": frm,
                        "account_id": acc["4000"].id,
                        "check_number": ref,
                        "method": method,
                        "amount": amount,
                        "memo": memo,
                    }
                ],
            },
        )
    )
    return f"/api/deposits/{doc['transaction_id']}/pdf"


REFS = " ".join(["MOU20260000012345AB"] * 10)
DEPOSITS = [
    # at the counts the first build decided by, and one past them
    (W * 20, W * 10, W * 10, "Rent", "12.34"),
    (W * 21, W * 11, W * 11, "Rent", "12.34"),
    (W * 20, "1042", "Check", "Rent", "12.34"),
    (W * 19, W * 9, W * 9, W * 19, "34567890.12"),
    (W * 19 + " " + W * 19, W * 19, W * 19, W * 19 + " " + W * 13, "34567890.12"),
    ("MCMAHON & MOWAT MGMT", "WM00420084", "MONEYGRAM", REFS, "345678.90"),
    (
        "MCMAHON & MOWAT MGMT",
        "WM00420084",
        "MONEYGRAM",
        "Common-area maintenance assessment for the spring quarter, see the"
        " agreement MOU20260000012345A and the board's minutes of the meeting"
        " of the twelfth, and the fall one, MOU20260000012345B, with the"
        " reserve study and the roof replacement schedule attached to it",
        "345678.90",
    ),
    ("MIDWEST HOMEOWNERS M", "WM00420084", "MONEY ORDR", "Common-area", "3456789.01"),
]


def test_a_deposit_slip_ends_at_the_margin_whatever_its_text(
    client, seed_accounts, pages
):
    """Twenty W's, ten and ten are wider than the page; a description's
    13- to 19-character words held its column open."""
    pytest.importorskip("weasyprint")
    wrong = []
    for paper in ("letter", "a4"):
        ok(client.put("/api/settings", json={"pdf_paper_size": paper}))
        plain = page_of(
            pages,
            client,
            _deposit(
                client, seed_accounts, "Dana Whitfield", "1042", "Check", "Rent", "1"
            ),
        )
        edge = max(x + w for x, w, _ in rows(plain, "items")[0])
        for frm, ref, method, memo, amount in DEPOSITS:
            html = page_of(
                pages,
                client,
                _deposit(client, seed_accounts, frm, ref, method, memo, amount),
            )
            said = (
                f"{paper}, from {frm[:24]!r}, {ref[:12]!r}, {method[:12]!r}, ${amount}"
            )
            wrong += [f"{said}: {p}" for p in past_the_margin(html)]
            table = rows(html, "items")
            now = max(x + w for x, w, _ in table[0])
            if abs(now - edge) > 0.5:
                wrong.append(
                    f"{said}: the table ends at {now}, {edge} with ordinary text"
                )
            amounts = [r[-1][2] for r in table[1:] if len(r) == 6]
            if not amounts or len(amounts[0]) != 1:
                wrong.append(f"{said}: the amount prints as {amounts}")
    assert not wrong, "\n".join(wrong)


def test_a_deposit_slip_sets_text_that_fits_on_one_line(client, seed_accounts, pages):
    """Who it is from, the reference and the method are on one line when
    the column has the room, whatever their count of characters, and the
    columns are as wide as that text: no wider."""
    pytest.importorskip("weasyprint")
    for frm, ref, method in (
        ("Dana Whitfield", "1042", "Check"),
        ("Whitfield & Sons LLC", "CHK-104233", "Wire-ACH-1"),
        ("Whitfield & Sons Roof", "CHK-1042339", "Bank transfer"),
    ):
        html = page_of(
            pages,
            client,
            _deposit(client, seed_accounts, frm, ref, method, "Rent", "345678.90"),
        )
        row = [r for r in rows(html, "items") if len(r) == 6 and r[1][2]][-1]
        assert [row[1][2], row[3][2], row[4][2]] == [[frm], [ref], [method]], row
        assert not past_the_margin(html)
    # a short name's column is narrower than a longer one's: the width is the
    # text's, not a set one
    short = page_of(
        pages,
        client,
        _deposit(client, seed_accounts, "Al", "1", "Cash", "Rent", "345678.90"),
    )
    wide = [r for r in rows(html, "items") if len(r) == 6 and r[1][2]][-1]
    narrow = [r for r in rows(short, "items") if len(r) == 6 and r[1][2]][-1]
    assert narrow[1][1] < wide[1][1] and narrow[4][1] < wide[4][1], (narrow, wide)
    assert narrow[2][1] > wide[2][1], (narrow, wide)


# --- the giving statement -----------------------------------------------------


def test_a_giving_statement_ends_at_the_margin_whatever_its_text(
    client, seed_accounts, pages
):
    """A description with a word of 20 characters and one of 19 kept the
    Running total past the margin."""
    pytest.importorskip("weasyprint")
    wrong = []
    for paper in ("letter", "a4"):
        ok(
            client.put(
                "/api/settings",
                json={
                    "company_type": "nonprofit",
                    "company_name": "Riverbend Arts",
                    "pdf_paper_size": paper,
                },
            )
        )
        client.post("/api/nonprofit/setup-accounts")
        for n, (desc, amount) in enumerate(
            (
                (W * 19, "3456789.01"),
                ("Internationalization " + W * 19, "3456789.01"),
                ("Internationalization " + W * 19, "34567890.12"),
                ("desconeXXXXXXXXXXXX, Deliver desctwoXXXXXXXXXXXX", "757702.53"),
                ("https://example.org/pledge/2026 Infrastructure", "34567890.12"),
                (" ".join([W * 19] * 14), "3456789.01"),
            )
        ):
            c = ok(
                client.post("/api/customers?force=true", json={"name": f"Donor {n}"})
            )
            ok(
                client.post(
                    "/api/sales-receipts",
                    json={
                        "customer_id": c["id"],
                        "date": "2026-05-09",
                        "tax_rate": "0",
                        "method": "Check",
                        "reference": (W * 19) if n % 2 else "1042",
                        "lines": [{"description": desc, "quantity": 1, "rate": amount}],
                    },
                )
            )
            html = page_of(
                pages, client, f"/api/donors/{c['id']}/giving-statement/pdf?year=2026"
            )
            wrong += [
                f"{paper}, {desc[:40]!r}, ${amount}: {p}" for p in past_the_margin(html)
            ]
    assert not wrong, "\n".join(wrong)


# --- a report -----------------------------------------------------------------

GL = "/api/reports/general-ledger/pdf?start_date=2026-01-01&end_date=2026-12-31"


def _payment(client, name, reference, amount):
    c = ok(client.post("/api/customers?force=true", json={"name": name}))
    ok(
        client.post(
            "/api/payments",
            json={
                "customer_id": c["id"],
                "date": "2026-02-02",
                "amount": amount,
                "method": "Wire",
                "reference": reference,
                "allocations": [],
            },
        )
    )


def test_the_general_ledger_ends_at_the_margin_with_ordinary_text(
    client, seed_accounts, pages
):
    """A 38-character reference beside a 39-character description, each on
    one line, were wider than the page with the three columns of figures."""
    pytest.importorskip("weasyprint")
    _payment(client, "Whitfield Roofing", "1042", "345678.90")
    html = page_of(pages, client, GL)
    assert not past_the_margin(html)
    # with room, a reference and a description are on one line each
    row = [r for r in rows(html, "report") if ["1042"] in [c[2] for c in r]]
    assert row and all(len(c[2]) <= 1 for c in row[0]), row
    wrong = []
    for name, reference, amount in (
        (
            "Whitfield and Sons Roofing",
            "Wire 2026-02-02 Chase conf 88213-A4471",
            "345678.90",
        ),
        (
            "Whitfield & Sons Roofing Co",
            "Wire 2026-02-03 Chase conf 88213-A4472",
            "3456789.01",
        ),
        (
            "Whitfield & Sons Roofing LP",
            "Wire 2026-02-02 Chase conf 88213",
            "34567890.12",
        ),
    ):
        _payment(client, name, reference, amount)
        html = page_of(pages, client, GL)
        wrong += [f"after {reference!r}, ${amount}: {p}" for p in past_the_margin(html)]
        # every figure is whole, on one line
        for row in rows(html, "report"):
            for _x, _w, lines in row:
                if any(text.MONEY.match(line) for line in lines) and len(lines) != 1:
                    wrong.append(f"after {reference!r}: a figure prints as {lines}")
    assert not wrong, "\n".join(wrong[:20])


def test_a_report_ends_at_the_margin_with_wide_words_in_every_column(
    client, seed_accounts, pages
):
    """Words of under 20 characters, as wide as letters come, in a
    customer's name and a payment's reference: each column of words has its
    share of the sheet and its words break there."""
    pytest.importorskip("weasyprint")
    _payment(client, W * 19 + " " + W * 19, W * 19, "34567890.12")
    _payment(client, "M" * 19, "@" * 19, "3456789.01")
    wrong = []
    span = "start_date=2026-01-01&end_date=2026-12-31"
    for url in (
        GL,
        f"/api/reports/income-by-customer/pdf?{span}",
        f"/api/reports/profit-loss-detail/pdf?{span}",
        "/api/reports/ar-aging/pdf?as_of_date=2026-10-01",
    ):
        html = page_of(pages, client, url)
        wrong += [f"{url.split('?')[0]}: {p}" for p in past_the_margin(html)]
    assert not wrong, "\n".join(wrong[:20])


def test_a_report_cell_is_a_figure_or_it_is_words():
    """What is a figure is on one line; what is words wraps. The count of
    its characters has no say."""
    from app.services.pdf_service import _is_figure

    for figure in (
        "",
        "—",
        "$12.34",
        "-$34,567,890.12",
        "($1,234.00)",
        "USD -1,234.00",
        "12.5%",
        "02/02/2026",
        "2026-02-02",
        "Feb 2, 2026",
        "September 30, 2026",
        "30 Sep 2026",
        "1,204",
        "12.5 hrs",
        1234.5,
        None,
    ):
        assert _is_figure(figure), figure
    for words in (
        "Wire 2026-02-02 Chase conf 88213-A4471",
        "Payment from Whitfield and Sons Roofing",
        "Whitfield Roofing",
        "Wire",
        "INV-1042",
        W * 19,
        "Net income",
    ):
        assert not _is_figure(words), words


# --- a word set in pieces -----------------------------------------------------

THAI = (
    "งานก่อสร้างฐานรากคอนกรีตเสริมเหล็กรวมค่าแรงและวัสดุทั้งหมดตามแบบที่แนบมาพร้อม"
    "ค่าขนส่งและค่าติดตั้งนั่งร้านรวมทั้งค่าทดสอบวัสดุทุกชนิด"
)
ZWJ = "‍"
FAMILY = ZWJ.join(["\U0001f468", "\U0001f469", "\U0001f467", "\U0001f466"])
ACCENTED = unicodedata.normalize(
    "NFD",
    "Société-générale-d'électricité-et-d'ingénierie-hydroélectrique-québécoise"
    "-des-régions-éloignées-référence-n°-12345-é",
)
FLAGS = "\U0001f1fa\U0001f1f8\U0001f1e8\U0001f1e6\U0001f1f2\U0001f1fd" * 20
SKIN = "\U0001f44d\U0001f3fd" * 60
HINDI = "क्षत्रियविद्यार्थीस्त्रीप्रार्थनाश्रद्धाञ्जलि" * 4
KEYCAPS = "1️⃣" * 40


def _pieces(word):
    from app.services.pdf_service import break_long_words

    out = break_long_words(word)
    pieces = re.findall(r'<span class="long-word">(.*?)</span>', out)
    assert "".join(pieces) == word
    return pieces


def _joins_back(char):
    """Whether a character belongs with the one before it."""
    return (
        unicodedata.category(char) in ("Mn", "Mc", "Me")
        or char in (ZWJ, "‌", "ำ", "ຳ")
        or "\U0001f3fb" <= char <= "\U0001f3ff"
    )


@pytest.mark.parametrize(
    "word",
    [THAI, FAMILY * 15, ACCENTED, FLAGS, SKIN, HINDI, KEYCAPS],
    ids=["thai", "family emoji", "accents", "flags", "skin tones", "hindi", "keycaps"],
)
def test_a_piece_of_a_long_word_keeps_a_letter_with_its_marks(word):
    """A word of more than 100 characters was cut every 10 code points: a
    Thai vowel or tone mark started a piece and printed on a dotted circle,
    a family printed as its members, an accent apart from its letter."""
    assert len(word) > 100
    pieces = _pieces(word)
    assert len(pieces) > 1
    bad = [
        p
        for p in pieces
        if _joins_back(p[0]) or p[-1] == ZWJ or unicodedata.combining(p[-1]) == 9
    ]
    assert not bad, [[f"U+{ord(c):04X}" for c in p[:3]] for p in bad[:5]]
    # a flag is two letters: no piece has half of one
    if word is FLAGS:
        assert all(len(p) % 2 == 0 for p in pieces), [len(p) for p in pieces]
    # and no piece is much longer than the ten characters it is cut at
    assert max(len(p) for p in pieces) <= 24, [len(p) for p in pieces]


def test_a_long_word_of_plain_letters_is_cut_as_it_was():
    """Nothing changes for a word with no marks: ten characters a piece."""
    word = "".join(chr(ord("a") + i % 26) for i in range(137))
    assert [len(p) for p in _pieces(word)] == [10] * 13 + [7]
    # a character reference is one character: no piece has half of one
    pieces = _pieces("A&amp;B" * 40)
    assert all(p.count("&") == p.count("&amp;") for p in pieces), pieces


def test_thai_in_a_narrow_column_prints_no_mark_apart_from_its_letter(tmp_path):
    """As the PDF is laid out: no line of a long Thai word starts with a
    vowel or tone mark, in pieces or under the length that is set in them."""
    pytest.importorskip("weasyprint")
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import break_long_words

    page = (
        "<html><head><style>@page{size:5in 3in;margin:.2in}"
        "body{font:11pt sans-serif} html{overflow-wrap:break-word}"
        ".long-word{overflow-wrap:anywhere}"
        ".long-word-gap{display:inline-block;width:0}</style></head><body>"
        '<table style="width:100%"><tr><td>WORD</td>'
        '<td style="white-space:nowrap">$12.34</td></tr></table></body></html>'
    )
    for word in (THAI, THAI[:98]):
        doc = HTML(string=page.replace("WORD", break_long_words(word))).render()
        starts = [
            t.text
            for t in doc.pages[0]._page_box.descendants()
            if isinstance(t, wb.TextBox) and t.text.strip()
        ]
        assert "".join(starts) == word + "$12.34"
        assert not [s[:3] for s in starts if _joins_back(s[0])]


# --- a long number in a report -------------------------------------------------


def leaves_its_cell(html):
    """The text of a table that is printed outside its own cell, over the
    cell beside it: ``(page, text, where it ends, where its cell ends)``."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    out = []
    document = HTML(string=html, url_fetcher=_get_fetcher()).render()
    for number, page in enumerate(document.pages, 1):
        for cell in page._page_box.descendants():
            if not isinstance(cell, wb.TableCellBox):
                continue
            lo = cell.position_x
            hi = lo + cell.border_width()
            for t in cell.descendants():
                if not isinstance(t, wb.TextBox) or not t.text.strip():
                    continue
                if t.style["visibility"] != "visible":
                    continue
                end = t.position_x + t.width
                if end > hi + 1 or t.position_x < lo - 1:
                    out.append((number, t.text[:40], round(end, 1), round(hi, 1)))
    return out


TRACE = "9120260211004471883305526671102938475611"


def test_a_long_number_in_a_report_stays_in_its_cell(client, seed_accounts, pages):
    """A payment's reference that is one long number (a wire's trace number)
    was set on one line, as a figure is, in a column that counted for less
    than its width: it printed over the description beside it."""
    pytest.importorskip("weasyprint")
    wrong = []
    for paper in ("letter", "a4"):
        ok(client.put("/api/settings", json={"pdf_paper_size": paper}))
        for reference in (
            TRACE[:16],
            TRACE[:22],
            TRACE[:26],
            TRACE[:30],
            TRACE,
            "0000-1111-2222-3333-4444-5555-6666-7777",
            "2026 " + TRACE[:30],
        ):
            _payment(
                client,
                "Whitfield & Sons Roofing Company of Ohio",
                reference,
                "34567890.12",
            )
            html = page_of(pages, client, GL)
            said = f"{paper}, after {reference!r}"
            wrong += [f"{said}: {o} is outside its cell" for o in leaves_its_cell(html)]
            wrong += [f"{said}: {p}" for p in past_the_margin(html)]
            for row in rows(html, "report"):
                for _x, _w, lines in row:
                    if any(text.MONEY.match(ln) for ln in lines) and len(lines) != 1:
                        wrong.append(f"{said}: a figure prints as {lines}")
    assert not wrong, "\n".join(wrong[:20])


def test_a_report_figure_is_on_one_line_while_it_is_no_wider_than_a_column():
    """A figure keeps its width and its line; a run of digits wider than a
    column of words may be wraps there as words do. By its width as digits
    are set, at the narrowest and the widest a column of words counts for."""
    from app.services.pdf_service import (
        _CAP_LEAST_EM,
        _CAP_MOST_EM,
        _capped,
        _one_line,
    )

    for cap in (_CAP_LEAST_EM, _CAP_MOST_EM):
        for figure in (
            "",
            "—",
            "$12.34",
            "-$34,567,890.12",
            "($34,567,890.12)",
            "USD -34,567,890.12",
            "12.5%",
            "2026-02-02",
            "September 30, 2026",
            "1042",
            "4111111111111",
            # an amount of eleven figures and more is an amount, never words
            "-$12,345,678,901.23",
            "($123,456,789,012.34)",
            "-$9,999,999,999,999.99",
            "USD -9,999,999,999,999.99",
            "12345678901.23",
            1234.5,
            None,
        ):
            assert _one_line(figure, cap), (figure, cap)
            # and it is never set in an element narrower than itself
            assert "<span" not in str(_capped(figure, cap)), (figure, cap)
        for words in (
            TRACE[:26],
            TRACE,
            "0000-1111-2222-3333-4444-5555-6666-7777",
            "2026 " + TRACE[:30],
            "Wire",
            "INV-1042",
        ):
            assert not _one_line(words, cap), (words, cap)
    assert 'class="w"' in str(_capped(TRACE[:26], _CAP_MOST_EM))


@pytest.mark.parametrize(
    "amount,reference",
    [
        ("12345678901.23", TRACE[:16]),
        ("123456789012.34", TRACE[:16]),
        ("9999999999999.99", "1042"),
    ],
)
def test_an_amount_of_eleven_figures_and_more_prints_whole(
    client, seed_accounts, pages, amount, reference
):
    """An amount of eleven figures and more was judged by its width as a
    run of digits is: its cell was set as words, and a negative balance
    printed its minus sign alone on one line and the amount on the next."""
    pytest.importorskip("weasyprint")
    c = ok(
        client.post(
            "/api/customers?force=true",
            json={"name": "Whitfield & Sons Roofing Company of Ohio"},
        )
    )
    ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": c["id"],
                "date": "2026-03-03",
                "lines": [{"description": "Roof", "quantity": 1, "rate": amount}],
            },
        )
    )
    _payment(client, "Acme Supply Partners of the Midwest", reference, amount)
    wrong = []
    for url in (GL, "/api/reports/ar-aging/pdf?as_of_date=2026-12-31"):
        html = page_of(pages, client, url)
        said = f"{url.split('?')[0]}, {amount}"
        wrong += [f"{said}: {o} is outside its cell" for o in leaves_its_cell(html)]
        wrong += [f"{said}: {p}" for p in past_the_margin(html)]
        figures = 0
        for row in rows(html, "report"):
            for _x, _w, lines in row:
                money = [ln for ln in lines if text.MONEY.match(ln)]
                figures += len(money)
                # a piece of an amount: its sign alone, or digits cut short
                pieces = [ln for ln in lines if ln in "-−" or "$" in ln]
                if pieces and (len(lines) != 1 or not money):
                    wrong.append(f"{said}: a figure prints as {lines}")
        if not figures:
            wrong.append(f"{said}: no figure printed")
    assert not wrong, "\n".join(wrong[:20])


# --- the running head ---------------------------------------------------------


def test_a_running_head_keeps_to_the_paper(client, seed_accounts, pages):
    """The head of a document's second page names its number: a bill number
    of 100 wide letters ran off both edges of the sheet."""
    pytest.importorskip("weasyprint")
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    v = ok(client.post("/api/vendors?force=true", json={"name": "Acme Supply"}))
    heads = {}
    for number in (W * 100, "B-1042"):
        lines = [
            {
                "account_id": seed_accounts["6000"].id,
                "description": f"Line {i}",
                "quantity": 1,
                "rate": "10.00",
                "amount": "10.00",
            }
            for i in range(60)
        ]
        b = ok(
            client.post(
                "/api/bills",
                json={
                    "vendor_id": v["id"],
                    "bill_number": number,
                    "date": "2026-01-01",
                    "lines": lines,
                },
            )
        )
        html = page_of(pages, client, f"/api/bills/{b['id']}/pdf")
        assert not past_the_margin(html), number[:12]
        document = HTML(string=html, url_fetcher=_get_fetcher()).render()
        assert len(document.pages) > 1
        page = document.pages[1]._page_box
        said = [
            t
            for box in page.children
            if isinstance(box, wb.MarginBox) and box.at_keyword == "@top-center"
            for t in box.descendants()
            if isinstance(t, wb.TextBox) and t.text.strip()
        ]
        # all of it is printed, inside the top margin
        printed = "".join(t.text for t in said).replace(" ", "")
        assert printed == f"Bill{number}·Page2of{len(document.pages)}", printed[:60]
        assert all(0 <= t.position_y for t in said)
        assert max(t.position_y + t.height for t in said) <= page.margin_top
        heads[number] = [(round(t.position_x, 1), round(t.position_y, 1)) for t in said]
    # an ordinary number's head is one line
    assert len(heads["B-1042"]) == 1, heads["B-1042"]
    # every document with a running head has the rule
    for name in sorted(p.name for p in text.TEMPLATES.glob("*.html")):
        source = (text.TEMPLATES / name).read_text(encoding="utf-8")
        for line in source.splitlines():
            if "@top-center" in line and "counter(page)" in line:
                assert "overflow-wrap: anywhere" in line, name


# --- the deposit's heading ----------------------------------------------------

ARABIC = "ت" * 60


def _slip(client, acc, reference):
    doc = ok(
        client.post(
            "/api/deposits",
            json={
                "deposit_to_account_id": acc["1000"].id,
                "date": "2026-06-12",
                "reference": reference,
                "line_ids": [],
                "total": "5",
                "other_lines": [
                    {
                        "received_from": "Dana Whitfield",
                        "account_id": acc["4000"].id,
                        "check_number": "1042",
                        "method": "Check",
                        "amount": "5",
                        "memo": "Rent",
                    }
                ],
            },
        )
    )
    return f"/api/deposits/{doc['transaction_id']}/pdf"


def _heading(html):
    """Where each label and value of a deposit's heading block is set."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    from app.services.pdf_service import _get_fetcher

    page = HTML(string=html, url_fetcher=_get_fetcher()).render().pages[0]
    out = []
    for block in page._page_box.descendants():
        element = getattr(block, "element", None)
        if element is None or not isinstance(block, wb.BlockBox):
            continue
        if (element.get("class") or "").split()[:1] not in (["label"], ["value"]):
            continue
        said = [t for t in block.descendants() if isinstance(t, wb.TextBox) and t.text]
        if said and said[0].text.strip() != "Total deposit":
            out.append(
                (
                    said[0].text.strip()[:12],
                    round(min(t.position_x for t in said), 1),
                    round(max(t.position_x + t.width for t in said), 1),
                )
            )
    return out


def test_a_deposit_heading_ends_at_the_margin_whatever_its_slip_number(
    client, seed_accounts, pages
):
    """A slip number of Latin text, a long unbroken Arabic run and another
    Arabic word was set on one line: Items printed past the margin, outside
    its box. The heading with an ordinary number is where it was."""
    pytest.importorskip("weasyprint")
    wrong = []
    for paper in ("letter", "a4"):
        ok(client.put("/api/settings", json={"pdf_paper_size": paper}))
        for reference in (
            "slipno " + ARABIC + " فاتورة",
            "slipno " + ARABIC,
            ARABIC + " فاتورة",
            W * 100,
            "DEP-1042 " + W * 19 + " " + W * 19 + " " + W * 19 + " " + W * 19,
        ):
            html = page_of(pages, client, _slip(client, seed_accounts, reference))
            said = f"{paper}, {reference[:16]!r}"
            wrong += [f"{said}: {p}" for p in past_the_margin(html)]
            heading = _heading(html)
            if [h[0] for h in heading][-2:] != ["ITEMS", "1"]:
                wrong.append(f"{said}: the heading is {heading}")
    assert not wrong, "\n".join(wrong)
    # an ordinary heading: each part beside the one before it, 24px apart
    ok(client.put("/api/settings", json={"pdf_paper_size": "letter"}))
    html = page_of(pages, client, _slip(client, seed_accounts, "DEP-1042"))
    heading = _heading(html)
    labels = [h for h in heading if h[0] in ("DEPOSITED TO", "DATE", "SLIP #", "ITEMS")]
    assert len(labels) == 4, heading
    values = {h[0]: h for h in heading}
    assert labels[1][1] - labels[0][2] == pytest.approx(24, abs=0.5), heading
    assert labels[2][1] - values["Jun 12, 2026"][2] == pytest.approx(24, abs=0.5)
    assert labels[3][1] - values["DEP-1042"][2] == pytest.approx(24, abs=0.5), heading
