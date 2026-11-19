"""#1045 / #1046 (2.32.0 round 21): what is long leaves the last group, and a
construction estimate keeps its Total with the acceptance block.

#1039 made the last line row, the totals, the notes and the footer one
unbreakable group (``tbody.doc-tail``). A group taller than a page moved whole
to a new page and broke there (W-185): long notes now follow the line table as
a block of their own, and a last line with a long description is not bound to
the totals. A construction estimate's commercial bid had no group at all and
its acceptance block ("accepts this bid at the Total above") could stand on a
page with no Total (W-184): both construction layouts now close their last
table with the group, the acceptance block in it. The Chromium half of this is
tests/test_print_last_sheet_browser.py."""

import io
import re

import pytest

from tests.test_pdf_footer_margin_1028 import MARK
from tests import test_pdf_last_page_w183 as w183
from tests.test_pdf_last_page_w183 import MAKERS

# 1,500 distinct words, each findable on its own
LONG_NOTES = " ".join(f"n{i:04d}" for i in range(1, 1501))
LONG_LINE = " ".join(f"d{i:04d}" for i in range(1, 501))
SHORT_TERMS = "Includes permits.\nExcludes rock excavation."
LONG_TERMS = "\n".join(
    f"{i}. The contractor provides item {i} as drawn and specified."
    for i in range(1, 41)
)
SHORT_NOTES = "Pricing holds for 30 days."
HEADINGS = re.compile(
    r"DESCRIPTION QTY|ALLOWANCES AMOUNT|ALTERNATES ADD|UNIT PRICES UNIT"
)
# a row, or the rest of a long one carried over
ROW = re.compile(r"\b(Line|Item|Allowance|Alternate|Unit price) \d+|\bd\d{4}\b")
GRAND = re.compile(r"\bTotal \$")


def pdf_pages(content):
    pypdf = pytest.importorskip("pypdf")
    return [
        " ".join((p.extract_text() or "").split())
        for p in pypdf.PdfReader(io.BytesIO(content)).pages
    ]


def heading_problems(pages):
    """A page that shows a table's column headings shows a row under them."""
    return [
        f"page {i} has the column headings over no row"
        for i, page in enumerate(pages, 1)
        if HEADINGS.search(page) and not ROW.search(page)
    ]


def words_once(pages, words):
    text = " ".join(pages)
    return [f"{w} printed {text.count(w)} times" for w in words if text.count(w) != 1]


def long_notes_problems(pages):
    first = pages[0]
    said = [
        f"page 1 lacks {s}" for s in ("Line 1", "Line 2", "Subtotal") if s not in first
    ]
    if "n0001" not in first:
        said.append("page 1 does not start the notes")
    said += heading_problems(pages)
    said += words_once(pages, LONG_NOTES.split())
    if MARK not in pages[-1] or sum(p.count(MARK) for p in pages) != 1:
        said.append("the footer is not once, on the last page")
    return said


def long_line_problems(pages):
    said = [] if len(pages) == 2 else [f"{len(pages)} pages, not 2"]
    said += heading_problems(pages)
    said += words_once(pages, LONG_LINE.split())
    if "Subtotal" not in pages[-1] or MARK not in pages[-1]:
        said.append("the totals and the footer are not on the last page")
    return said


def make_long_notes(client, accounts, customer, kind, monkeypatch):
    """A two-line document of the kind with 1,500-word notes."""
    monkeypatch.setattr(w183, "NOTES", LONG_NOTES)
    return MAKERS[kind](client, accounts, customer, 2, True)


@pytest.mark.parametrize("kind", list(MAKERS))
def test_long_notes_leave_the_group_and_page_one_keeps_the_lines(
    client, seed_accounts, seed_customer, kind, monkeypatch
):
    pytest.importorskip("pypdf")
    url = make_long_notes(client, seed_accounts, seed_customer, kind, monkeypatch)
    r = client.get(f"{url}/pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-", r.text[:200]
    assert not (problems := long_notes_problems(pdf_pages(r.content))), problems


def long_line_invoice(customer):
    """A two-line invoice whose last line has a 500-word description."""
    return {
        "customer_id": customer.id,
        "date": "2026-11-01",
        "tax_rate": 0,
        "lines": [
            {"description": "Line 1", "quantity": 1, "rate": 33},
            {"description": f"Line 2 {LONG_LINE}", "quantity": 1, "rate": 33},
        ],
    }


def test_a_long_last_line_is_not_bound_to_the_totals(
    client, seed_accounts, seed_customer
):
    pytest.importorskip("pypdf")
    r = client.post("/api/invoices", json=long_line_invoice(seed_customer))
    assert r.status_code == 201, r.text
    pdf = client.get(f"/api/invoices/{r.json()['id']}/pdf").content
    assert not (problems := long_line_problems(pdf_pages(pdf))), problems


def test_the_group_markup_follows_the_length_of_the_notes(
    client, seed_accounts, seed_customer, monkeypatch
):
    url = make_long_notes(client, seed_accounts, seed_customer, "invoice", monkeypatch)
    body = client.get(f"{url}/print-preview").text.split("<body")[1]
    # the notes and the footer come after the line table closes
    table_end = body.index("</table>", body.index('class="doc-end"'))
    assert body.index('class="notes"') > table_end
    assert body.index('class="footer"') > table_end
    assert body.count('class="notes"') == 1 and body.count('class="footer"') == 1
    # notes of a few lines stay in the group, as #1039 has them
    monkeypatch.setattr(w183, "NOTES", LONG_NOTES[:990])
    url = MAKERS["invoice"](client, seed_accounts, seed_customer, 2, True)
    body = client.get(f"{url}/print-preview").text.split("<body")[1]
    tail = body.split('<tbody class="doc-tail">')[1].split("</tbody>")[0]
    assert 'class="notes"' in tail and 'class="footer"' in tail


# --- construction estimates (#1046) ------------------------------------------


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _package(client, name):
    r = client.post("/api/trade-packages", json={"name": name, "section_ids": []})
    if r.status_code == 409:  # setup seeds some of the common names
        return next(
            p["id"]
            for p in client.get("/api/trade-packages").json()
            if p["name"] == name
        )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def construction_lines(client, layout, n):
    """n lines and the name of the row the document prints last.

    ``line_markup`` carries a markup on each line; ``bid`` is the commercial
    bid's scope table only; ``bid tables`` spreads the lines over two
    packages, allowances, pending alternates and unit prices."""
    if layout == "line_markup":
        lines = [
            {
                "description": f"Item {i}",
                "quantity": 1,
                "unit_cost": 40,
                "markup_pct": 10,
            }
            for i in range(1, n + 1)
        ]
        return lines, f"Item {n}"
    if layout == "bid":
        lines = [
            {"description": f"Item {i}", "quantity": 1, "unit_cost": 40}
            for i in range(1, n + 1)
        ]
        return lines, f"Item {n}"
    pkgs = [_package(client, "Concrete"), _package(client, "Electrical")]
    base = n - 6
    lines = [
        {
            "description": f"Item {i}",
            "quantity": 1,
            "unit_cost": 40,
            "trade_package_id": pkgs[i * 2 // (base + 1)],
        }
        for i in range(1, base + 1)
    ]
    for i in (1, 2):
        lines.append(
            {
                "description": f"Allowance {i}",
                "quantity": 1,
                "rate": 500,
                "line_kind": "allowance",
            }
        )
        lines.append(
            {
                "description": f"Alternate {i}",
                "quantity": 1,
                "unit_cost": 300,
                "line_kind": "alternate",
            }
        )
        lines.append(
            {
                "description": f"Unit price {i}",
                "quantity": 1,
                "rate": 12,
                "unit": "CY",
                "line_kind": "unit_price",
            }
        )
    return lines, "Unit price 2"


def construction_estimate(client, customer, layout, n, terms, notes):
    """A construction estimate's id and the name of the row it prints last."""
    lines, last = construction_lines(client, layout, n)
    body = {
        "customer_id": customer.id,
        "date": "2026-11-01",
        "lines": lines,
        "terms": terms,
    }
    if notes:
        body["notes"] = notes
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"], last


def construction_problems(pages, last):
    """The page with the totals has the last row above them and the Total;
    the acceptance block is on that page; the footer is once, at the end."""
    said = []
    with_totals = [i for i, p in enumerate(pages) if "Subtotal" in p]
    if len(with_totals) != 1:
        return [f"the totals are on pages {[i + 1 for i in with_totals]}"]
    page = pages[with_totals[0]]
    if last not in page:
        said.append(
            f"the totals' page (page {with_totals[0] + 1}) lacks the last row, {last}"
        )
    if not GRAND.search(page):
        said.append("the totals' page lacks the Total")
    for i, p in enumerate(pages):
        if "Owner signature" in p and not GRAND.search(p):
            said.append(f"page {i + 1} has the acceptance block and no Total")
    if MARK not in pages[-1] or sum(p.count(MARK) for p in pages) != 1:
        said.append("the footer is not once, on the last page")
    return said + heading_problems(pages)


# The lengths the round-21 gate found wrong on main (20-23 and 46-55 lines,
# the bid's 50-51 and 55-57), all of them with no terms or notes, a few of
# them with each other combination, and two lengths elsewhere: sampled, not
# every one, so the suite stays quick.
GATE_LENGTHS = [*range(20, 24), *range(46, 58)]
VARIANTS = [
    # (terms, notes, the lengths rendered)
    ("", None, [*GATE_LENGTHS, 33, 64]),
    (SHORT_TERMS, SHORT_NOTES, [22, 47, 50, 53, 55, 57]),
    (LONG_TERMS, SHORT_NOTES, [20, 50, 55]),
    (SHORT_TERMS, LONG_NOTES, [20, 50, 55]),
]


LAYOUTS = ["line_markup", "bid", "bid tables"]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_a_construction_estimate_keeps_its_total_with_a_row_and_the_acceptance(
    client, seed_accounts, seed_customer, layout
):
    pytest.importorskip("pypdf")
    _construction(client)
    wrong = []
    for terms, notes, lengths in VARIANTS:
        for n in lengths:
            est_id, last = construction_estimate(
                client, seed_customer, layout, n, terms, notes
            )
            pdf = client.get(f"/api/estimates/{est_id}/pdf")
            assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-", pdf.text[
                :200
            ]
            for problem in construction_problems(pdf_pages(pdf.content), last):
                wrong.append(
                    f"{layout}, {n} lines, terms={len(terms)}, notes={len(notes or '')}: {problem}"
                )
    assert not wrong, "\n".join(wrong)


def test_long_terms_follow_the_acceptance(client, seed_accounts, seed_customer):
    _construction(client)
    for terms, after in ((SHORT_TERMS, False), (LONG_TERMS, True)):
        lines, _ = construction_lines(client, "bid", 3)
        body = {
            "customer_id": seed_customer.id,
            "date": "2026-11-01",
            "lines": lines,
            "terms": terms,
        }
        est = client.post("/api/estimates", json=body).json()
        html = client.get(f"/api/estimates/{est['id']}/print-preview").text.split(
            "<body"
        )[1]
        tail = html.split('<tbody class="doc-tail">')[1].split("</tbody>")[0]
        assert 'class="totals' in tail and 'class="acceptance"' in tail
        assert ('class="terms-block"' in tail) is not after
        assert ("on the terms below" in html) is after
        if after:
            assert html.index('class="terms-block"') > html.index('class="acceptance"')
        else:
            assert tail.index('class="terms-block"') < tail.index('class="acceptance"')
        assert html.count('class="footer"') == 1
