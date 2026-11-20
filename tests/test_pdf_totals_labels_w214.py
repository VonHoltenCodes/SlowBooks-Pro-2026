"""Round 24 (W-214): no label in a totals block wraps.

The credit memo's "Remaining Credit" wrapped onto two lines in the 240px
totals block, its figure between them. Every document's totals block is
laid out here (WeasyPrint) with every label any document prints there, at
its longest wording (the nonprofit, pledge, retainage and construction
bid ones) beside a figure in the millions: each label is on one line with
its figure, nothing runs past the page's right edge, and an ordinary
document's block keeps its width.
"""

import re

import pytest

from tests.test_pdf_last_page_w183 import MAKERS
from tests.test_pdf_last_page_w187_w188 import long_last_line

FIGURE = "-$9,999,999.99"
LABELS = (
    ("Subtotal", ""),
    ("Tax (8.875%)", ""),
    ("Total", "grand"),
    ("Contribution", "grand"),
    ("Pledge Amount", "grand"),
    ("Retainage withheld this period", ""),
    ("Retainage reduction this period", ""),
    ("Retainage released", ""),
    ("Current payment due", "grand"),
    ("Received", ""),
    ("Paid", ""),
    ("Balance", "grand"),
    ("Balance Due", "grand"),
    ("Total Credit", "grand"),
    ("Applied", ""),
    ("Remaining Credit", "grand"),
    ("General conditions (12.5%)", ""),
    ("Insurance (1.25%)", ""),
    ("Fee (12.5%)", ""),
    ("Bond (1.25%)", ""),
    ("Bid total", ""),
)


def _rows():
    return "".join(
        f'<tr><td class="label {cls}">{text}</td>'
        f'<td class="r {cls}">{FIGURE}</td></tr>'
        for text, cls in LABELS
    )


def _layout(html):
    """[(label text, its line positions, figure text, its line positions,
    the figure's right edge)] of each row of the totals block, the block's
    table width, and the page content's right edge."""
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    def cell(c):
        lines = [x for x in c.descendants() if isinstance(x, wb.LineBox)]
        text = " ".join(
            "".join(t.text for t in x.descendants() if isinstance(t, wb.TextBox))
            for x in lines
        )
        text = " ".join(text.split())
        return text, {round(x.position_y, 1) for x in lines}

    rows, width, right = [], None, None
    for page in HTML(string=html).render().pages:
        box = page._page_box
        right = box.content_box_x() + box.width
        for block in box.descendants():
            el = getattr(block, "element", None)
            if not isinstance(block, wb.BlockBox) or el is None:
                continue
            if "totals" not in (el.get("class") or "").split():
                continue
            for d in block.descendants():
                if isinstance(d, wb.TableBox) and width is None:
                    width = d.border_width()
                if isinstance(d, wb.TableRowBox):
                    cells = [c for c in d.children if isinstance(c, wb.TableCellBox)]
                    if len(cells) == 2:
                        (lt, ly), (ft, fy) = cell(cells[0]), cell(cells[1])
                        edge = cells[1].position_x + cells[1].border_width()
                        rows.append((lt, ly, ft, fy, edge))
    return rows, width, right


def _check(kind, html, base):
    # an ordinary document's block keeps its width
    _rows_, width, _right = _layout(html)
    assert width is not None and abs(width - base) < 0.5, (kind, width)
    # every label, at its longest, in this document's totals block
    widened = re.sub(
        r'(<div class="totals">\s*<table>)',
        lambda m: m[1] + _rows(),
        html,
        count=1,
    )
    assert widened != html, kind
    rows, _width, right = _layout(widened)
    wrong = []
    ours = [r for r in rows if r[2] == FIGURE]
    for label, label_ys, _fig, fig_ys, edge in ours:
        if len(label_ys) != 1 or label_ys != fig_ys:
            wrong.append(f"{kind}: {label!r} wraps or sits off its figure")
        if edge > right + 0.5:
            wrong.append(f"{kind}: {label!r}'s figure runs past the page")
    assert {r[0] for r in ours} == {t for t, _c in LABELS}, (kind, ours)
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("kind", list(MAKERS))
def test_every_totals_label_sits_on_one_line_with_its_figure(
    client, seed_accounts, seed_customer, kind
):
    pytest.importorskip("weasyprint")
    url = long_last_line(client, seed_accounts, seed_customer, kind, 2, 20)
    _check(kind, client.get(f"{url}/print-preview").text, 240)


def test_a_change_orders_totals_labels_sit_on_one_line(
    client, seed_customer, monkeypatch
):
    pytest.importorskip("weasyprint")
    from app.services import pdf_service
    from tests.test_co_effective_date_allowance import _owner_co, _setup

    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000)
    html = client.get(f"/api/change-orders/{co['id']}/pdf").text
    _check("change order", html, 260)


# Round 25 (NEW-273): the giving statement's "Less goods and services
# received" wrapped in its 300px summary. Every other printed totals block
# #1158 did not cover takes the same treatment and is checked here from its
# own template's stylesheet, with its own labels beside a figure in the
# millions: the statement's and the deposit slip's summary, the pay
# application's certificate. (A paystub's net box is one label in a flex
# row, the 1096's totals are the form's boxes, and the reports' totals are a
# row of their full-width table: none is a narrow block of labels.)
BLOCKS = {
    "giving_statement_pdf.html": (
        "summary",
        300,
        (
            "Total contributions",
            "Less goods and services received",
            "Deductible contributions",
        ),
    ),
    "statement_pdf.html": (
        "summary",
        260,
        (
            "Total Invoiced",
            "Total Payments",
            "Total Credits",
            "Balance Due",
            "Retainage held, not yet due",
        ),
    ),
    "deposit_pdf.html": (
        "summary",
        300,
        ("12 checks", "Cash", "Payments", "Other deposits", "Total deposit"),
    ),
    "pay_application_pdf.html": (
        "cert",
        300,
        (
            "Original contract sum",
            "Net change by change orders",
            "Retainage held, previous applications",
            "Less previous payment applications",
            "Total earned less retainage",
            "Current payment due",
        ),
    ),
}


def _block_html(template, cls, labels):
    from app.services.pdf_service import TEMPLATE_DIR

    source = (TEMPLATE_DIR / template).read_text()
    style = re.search(r"<style>(.*?)</style>", source, re.S)[1]
    style = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", "", style, flags=re.S)
    rows = "".join(
        f'<tr><td class="label">{text}</td><td class="r">{FIGURE}</td></tr>'
        for text in labels
    )
    return (
        f"<html><head><style>{style}</style></head><body>"
        f'<div class="{cls}"><table>{rows}</table></div></body></html>'
    )


def _block_layout(html, cls):
    from weasyprint import HTML
    from weasyprint.formatting_structure import boxes as wb

    rows, right = [], None
    for page in HTML(string=html).render().pages:
        box = page._page_box
        right = box.content_box_x() + box.width
        for d in box.descendants():
            if not isinstance(d, wb.TableRowBox):
                continue
            cells = [c for c in d.children if isinstance(c, wb.TableCellBox)]
            ys = [
                {
                    round(x.position_y, 1)
                    for x in c.descendants()
                    if isinstance(x, wb.LineBox)
                }
                for c in cells
            ]
            edge = cells[1].position_x + cells[1].border_width()
            rows.append((len(ys[0]), ys[0] == ys[1], edge))
    return rows, right


def test_the_other_printed_totals_blocks_never_wrap_a_label():
    pytest.importorskip("weasyprint")
    wrong = []
    for template, (cls, _width, labels) in BLOCKS.items():
        rows, right = _block_layout(_block_html(template, cls, labels), cls)
        assert len(rows) == len(labels), template
        for label, (lines, beside, edge) in zip(labels, rows):
            if lines != 1 or not beside:
                wrong.append(f"{template}: {label!r} wraps or sits off its figure")
            if edge > right + 0.5:
                wrong.append(f"{template}: {label!r}'s figure runs past the page")
    assert not wrong, "\n".join(wrong)
