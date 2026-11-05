"""The Budgets grid in a real browser (#702): its twelve months scroll
sideways under a frozen account column, and the month row stays at the top
while the accounts scroll under it, so a late month still says which account
each row is. The same for Budget vs Actual. Tab into a month off screen
brings it in whole, clear of the frozen column. The frozen cells meet AA in
both themes. The API is stubbed; the page is the real one
(tests/test_browser_ui.py's fixtures)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402
    AA,
    _theme,
    browser_fixture,  # noqa: F401  (the fixture)
    contrast,
)

# 26 accounts: more than one screen of rows, with a long name or two
ACCOUNTS = [
    {
        "id": i,
        "name": name,
        "account_number": str(6000 + i * 10),
        "account_type": kind,
        "is_active": True,
    }
    for i, (name, kind) in enumerate(
        [
            ("Sales", "income"),
            ("Service Income", "income"),
            ("Materials", "cogs"),
            ("Subcontractors", "cogs"),
            ("Advertising and Promotion", "expense"),
            ("Automobile", "expense"),
            ("Bank Service Charges", "expense"),
            ("Computer and Internet Expenses", "expense"),
            ("Depreciation Expense", "expense"),
            ("Insurance Expense", "expense"),
            ("Meals and Entertainment", "expense"),
            ("Office Supplies", "expense"),
            ("Professional Fees", "expense"),
            ("Rent Expense", "expense"),
            ("Repairs and Maintenance", "expense"),
            ("Telephone Expense", "expense"),
            ("Travel Expense", "expense"),
            ("Utilities", "expense"),
            ("Dues and Subscriptions", "expense"),
            ("Freight and Delivery", "expense"),
            ("Licenses and Permits", "expense"),
            ("Postage and Delivery", "expense"),
            ("Small Tools and Equipment", "expense"),
            ("Uniforms", "expense"),
            ("Waste Removal", "expense"),
            ("Workers Compensation", "expense"),
        ],
        start=1,
    )
]
BUDGETS = [
    {"account_id": a["id"], "month": m, "amount": 1000 + a["id"] * 10 + m}
    for a in ACCOUNTS
    for m in range(1, 13)
]


def _variance():
    accounts = []
    for a in ACCOUNTS[:4]:
        months = [
            {"month": m, "budget": 1000.0, "actual": 900.0 + m, "variance": 100.0 - m}
            for m in range(1, 13)
        ]
        accounts.append(
            {
                "account_id": a["id"],
                "account_name": a["name"],
                "account_number": a["account_number"],
                "months": months,
                "total_budget": 12000.0,
                "total_actual": sum(x["actual"] for x in months),
                "total_variance": sum(x["variance"] for x in months),
            }
        )
    return {"year": 2026, "accounts": accounts}


def _budgets(browser, width, height=800):
    page = _open(browser, width, height, "#/")
    page.route("**/api/accounts*", lambda r: r.fulfill(json=ACCOUNTS))
    page.route("**/api/budgets/variance*", lambda r: r.fulfill(json=_variance()))
    page.route(
        "**/api/budgets?*",
        lambda r: r.fulfill(json=BUDGETS),
    )
    page.goto(page.url.split("#")[0] + "#/budgets")
    page.wait_for_selector(".budget-grid tbody tr:nth-child(15)")
    return page


# Where the frozen cells are, once the box is scrolled as asked: every first
# cell inside the box's left edge with a ground of its own (no figure shows
# through it), every month header inside its top edge, the corner over both.
FROZEN = """([sel, toEnd]) => {
    const box = document.querySelector(sel);
    if (toEnd) { box.scrollLeft = box.scrollWidth; box.scrollTop = box.scrollHeight; }
    const b = box.getBoundingClientRect();
    const opaque = (el) => {
        const c = getComputedStyle(el).backgroundColor;
        const m = c.match(/[\\d.]+/g) || [];
        return getComputedStyle(el).backgroundImage !== 'none'
            || (m.length >= 3 && (m.length < 4 || +m[3] === 1));
    };
    const first = [...box.querySelectorAll('tbody tr')]
        .map(tr => tr.cells[0])
        .filter(td => { const r = td.getBoundingClientRect();
                        return r.bottom > b.top && r.top < b.bottom; });
    const heads = [...box.querySelectorAll('thead th')]
        .filter(th => { const r = th.getBoundingClientRect();
                        return r.right > b.left && r.left < b.right; });
    const corner = box.querySelector('thead th:first-child');
    const cr = corner.getBoundingClientRect();
    const atCorner = document.elementFromPoint(cr.left + 4, cr.top + 4);
    return {
        scrollsX: box.scrollWidth > box.clientWidth,
        scrollsY: box.scrollHeight > box.clientHeight,
        scrolledX: box.scrollLeft, scrolledY: box.scrollTop,
        first: first.map(td => ({ text: td.textContent.trim(),
            left: td.getBoundingClientRect().left, opaque: opaque(td) })),
        heads: heads.map(th => ({ top: th.getBoundingClientRect().top, opaque: opaque(th) })),
        boxLeft: b.left + box.clientLeft, boxTop: b.top + box.clientTop,
        cornerOnTop: corner === atCorner || corner.contains(atCorner),
    };
}"""


def _assert_frozen(view, where):
    assert view["scrolledX"] > 0, where
    assert view["first"], where
    for cell in view["first"]:
        assert abs(cell["left"] - view["boxLeft"]) <= 1, (where, cell)
        assert cell["opaque"], (where, cell)
    for th in view["heads"]:
        assert th["opaque"], (where, th)
    assert view["cornerOnTop"], where


@pytest.mark.parametrize("width", [1280, 900])
def test_the_account_column_stays_put_while_the_months_scroll(browser, width):
    page = _budgets(browser, width)
    try:
        view = page.evaluate(FROZEN, [".table-container--frozen", True])
        assert view["scrollsX"], f"twelve months should be wider than {width}"
        assert view["scrollsY"], "twenty-six accounts should scroll under the months"
        _assert_frozen(view, width)
        # the last rows are in view, still named
        assert any(c["text"].endswith("Workers Compensation") for c in view["first"])
        # and the month row is held at the top of the box
        assert view["scrolledY"] > 0
        for th in view["heads"]:
            assert abs(th["top"] - view["boxTop"]) <= 1, th
        # striped and hovered rows keep a ground of their own too
        page.hover(".budget-grid tbody tr:last-child td:first-child")
        view = page.evaluate(FROZEN, [".table-container--frozen", False])
        _assert_frozen(view, f"{width} hover")
    finally:
        page.close()


# A late month's box after Tab: wholly in the box, right of the frozen column
# and below the month row.
FOCUSED = """() => {
    const a = document.activeElement, box = a.closest('.table-container--frozen');
    const r = a.getBoundingClientRect(), b = box.getBoundingClientRect();
    const col = box.querySelector('thead th:first-child').getBoundingClientRect();
    const head = box.querySelector('thead').getBoundingClientRect();
    return { month: a.dataset.month, acct: a.dataset.acct,
             left: r.left, right: r.right, top: r.top, bottom: r.bottom,
             colRight: col.right, headBottom: head.bottom,
             boxRight: b.left + box.clientLeft + box.clientWidth,
             boxBottom: b.top + box.clientTop + box.clientHeight };
}"""


@pytest.mark.parametrize("width", [1280, 900])
def test_tab_brings_a_late_month_in_clear_of_the_frozen_column(browser, width):
    page = _budgets(browser, width)
    try:
        # forward: from November of a row low in the grid to December
        page.focus('.budget-cell[data-acct="16"][data-month="11"]')
        page.keyboard.press("Tab")
        # then back across the row to January, and up to the row above
        stops = [page.evaluate(FOCUSED)]
        for _ in range(12):
            page.keyboard.press("Shift+Tab")
            stops.append(page.evaluate(FOCUSED))
        assert stops[0]["month"] == "12" and stops[0]["acct"] == "16"
        assert stops[11]["month"] == "1" and stops[12]["month"] == "12"
        assert stops[12]["acct"] == "15"
        for s in stops:
            assert s["left"] >= s["colRight"] - 0.5, (width, s)
            assert s["right"] <= s["boxRight"] + 0.5, (width, s)
            assert s["top"] >= s["headBottom"] - 0.5, (width, s)
            assert s["bottom"] <= s["boxBottom"] + 0.5, (width, s)
    finally:
        page.close()


# The frozen cells' words against their own ground (the cell's, else its
# row's, else the box's), plain and hovered.
FROZEN_TEXT = """(sel) => {
    const box = document.querySelector(sel);
    const ground = (el) => {
        for (let e = el; e; e = e.parentElement) {
            const c = getComputedStyle(e).backgroundColor;
            if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
        }
        return 'rgb(255, 255, 255)';
    };
    return [...box.querySelectorAll('tbody tr')].slice(0, 4).map(tr => tr.cells[0])
        .map(td => ({ text: td.textContent.trim(), color: getComputedStyle(td).color,
                      ground: getComputedStyle(td).backgroundColor,
                      row: ground(td.parentElement) }));
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_frozen_cells_meet_aa_in_both_themes(browser, theme):
    page = _budgets(browser, 1280)
    try:
        _theme(page, theme)
        page.evaluate(
            "() => { const b = document.querySelector("
            "'.table-container--frozen'); b.scrollLeft = b.scrollWidth; }"
        )
        cells = page.evaluate(FROZEN_TEXT, ".table-container--frozen")
        page.hover(".budget-grid tbody tr:nth-child(2) td:first-child")
        cells += page.evaluate(FROZEN_TEXT, ".table-container--frozen")
        for c in cells:
            assert contrast(c["color"], c["ground"]) >= AA, (theme, c)
            # the frozen cell's ground is its row's, striped or hovered
            assert c["ground"] == c["row"], (theme, c)

        # Budget vs Actual: the same frozen column, and its words at AA
        page.evaluate("() => BudgetsPage.showVariance()")
        page.wait_for_selector("#modal-body .budget-variance__table")
        _theme(page, theme)
        view = page.evaluate(FROZEN, ["#modal-body .table-container--frozen", True])
        assert view["scrollsX"]
        _assert_frozen(view, f"variance {theme}")
        for c in page.evaluate(FROZEN_TEXT, "#modal-body .table-container--frozen"):
            assert contrast(c["color"], c["ground"]) >= AA, (theme, c)
        # each account's heading holds to the box's left edge too
        lefts = page.evaluate("""() => {
            const box = document.querySelector('#modal-body .table-container--frozen');
            const b = box.getBoundingClientRect().left + box.clientLeft;
            return [...box.querySelectorAll('.budget-variance__acct')]
                .map(h => h.getBoundingClientRect().left - b); }""")
        assert lefts and all(abs(x) <= 1 for x in lefts), lefts
    finally:
        page.close()


# Printing (W-115, #702): 49 accounts, the size of a real chart's budget
# list. The shell (body, #app, #content) let the page flow in print, no
# 1100px cap on the page, every month and the last account inside the
# document; and the PDF Chromium prints is more than one landscape page,
# December and the last account on it.
PRINT_ACCOUNTS = [
    {
        "id": i,
        "name": f"Budget Line {i:02d}" if i < 49 else "Zebra Mussel Removal",
        "account_number": str(6000 + i * 10),
        "account_type": "expense" if i > 4 else "income",
        "is_active": True,
    }
    for i in range(1, 50)
]
PRINT_BUDGETS = [
    {"account_id": a["id"], "month": m, "amount": 10000 + a["id"] * 10 + m}
    for a in PRINT_ACCOUNTS
    for m in range(1, 13)
]

PRINT_SHELL = """() => {
    const cs = (sel) => getComputedStyle(document.querySelector(sel));
    const doc = document.documentElement;
    const W = Math.max(doc.scrollWidth, document.body.scrollWidth);
    const H = Math.max(doc.scrollHeight, document.body.scrollHeight);
    const inside = (el) => { const r = el.getBoundingClientRect();
        return r.right <= W + 0.5 && r.bottom + window.scrollY <= H + 0.5
            && r.width > 0 && r.height > 0; };
    const heads = [...document.querySelectorAll('.budget-grid thead th')];
    const last = document.querySelector('.budget-grid tbody tr:last-child td:first-child');
    return {
        body: [cs('body').overflowY, cs('body').height],
        app: [cs('#app').overflowY, cs('#app').height],
        content: [cs('#content').overflowY, cs('#content').height],
        pageMax: cs('#page-content').maxWidth,
        box: [cs('.table-container--frozen').overflowY,
              cs('.table-container--frozen').maxHeight],
        sidebar: cs('#sidebar').display, topbar: cs('#topbar').display,
        heads: heads.map(th => [th.textContent.trim(), inside(th)]),
        last: [last.textContent.trim(), inside(last)],
        viewport: window.innerHeight,
        H,
    };
}"""


def _pdf_text(pdf):
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        import io  # noqa: PLC0415

        reader = PdfReader(io.BytesIO(pdf))
        return len(reader.pages), [p.extract_text() or "" for p in reader.pages]
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    if not shutil.which("pdftotext"):
        return None
    out = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True
    ).stdout.decode()
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return len(pages), pages


def _print_budgets(browser):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/accounts*", lambda r: r.fulfill(json=PRINT_ACCOUNTS))
    page.route("**/api/budgets?*", lambda r: r.fulfill(json=PRINT_BUDGETS))
    page.goto(page.url.split("#")[0] + "#/budgets")
    page.wait_for_selector(".budget-grid tbody tr:nth-child(49)")
    return page


def test_the_budgets_grid_prints_every_month_and_every_account(browser):
    page = _print_budgets(browser)
    try:
        page.emulate_media(media="print")
        view = page.evaluate(PRINT_SHELL)
        for part in ("body", "app", "content"):
            overflow, height = view[part]
            assert overflow == "visible", (part, view[part])
        # no screen's height: as tall as what is on it, past the viewport
        assert view["H"] > view["viewport"], view
        for part in ("app", "content"):
            assert float(view[part][1].rstrip("px")) > view["viewport"], (part, view)
        assert view["pageMax"] == "none", view
        assert view["box"] == ["visible", "none"], view
        assert view["sidebar"] == "none" and view["topbar"] == "none", view
        months = [h for h, _ in view["heads"][1:]]
        assert [m.upper() for m in months][-1] == "DEC" and len(months) == 12, months
        assert all(ok for _, ok in view["heads"]), view["heads"]
        assert view["last"][0].endswith("Zebra Mussel Removal"), view["last"]
        assert view["last"][1], view["last"]

        pdf = page.pdf(landscape=True, format="Letter")
        got = _pdf_text(pdf)
        if got is None:
            pytest.skip("neither pypdf nor pdftotext is installed")
        count, texts = got
        assert count > 1, count
        text = "\n".join(texts)
        assert "DEC" in text.upper(), text[:400]
        assert "Zebra Mussel Removal" in text
        # the month row repeats on every page
        for t in texts:
            assert "JAN" in t.upper() and "DEC" in t.upper(), t[:300]
        # every account, and December's figure for the last one
        for a in PRINT_ACCOUNTS:
            assert a["name"] in text, a["name"]
    finally:
        page.close()


def test_the_month_grid_prints_landscape_by_its_own_page_rule(browser):
    """With the page's own @page size honoured (as a browser's print dialog
    does), the Budgets sheet is landscape: wider than it is tall."""
    page = _print_budgets(browser)
    try:
        pdf = page.pdf(prefer_css_page_size=True)
        try:
            from pypdf import PdfReader  # noqa: PLC0415
        except ImportError:
            pytest.skip("pypdf is not installed")
        import io  # noqa: PLC0415

        box = PdfReader(io.BytesIO(pdf)).pages[0].mediabox
        assert float(box.width) > float(box.height), (box.width, box.height)
    finally:
        page.close()


def test_budget_vs_actual_prints_its_dialog_not_the_page_behind(browser):
    page = _budgets(browser, 1280)
    try:
        page.evaluate("() => BudgetsPage.showVariance()")
        page.wait_for_selector("#modal-body .budget-variance__table")
        page.emulate_media(media="print")
        view = page.evaluate("""() => {
            const cs = (sel) => getComputedStyle(document.querySelector(sel));
            return { app: cs('#app').display, overlay: cs('#modal-overlay').display,
                     modal: [cs('#modal').overflowY, cs('#modal').maxHeight],
                     box: cs('#modal-body .table-container--frozen').maxHeight,
                     toolbar: cs('#modal-body .report-toolbar').display };
        }""")
        assert view["app"] == "none", view
        assert view["overlay"] == "block", view
        assert view["modal"] == ["visible", "none"], view
        assert view["box"] == "none", view
        assert view["toolbar"] == "none", view
        got = _pdf_text(page.pdf(landscape=True, format="Letter"))
        if got is None:
            pytest.skip("neither pypdf nor pdftotext is installed")
        text = "\n".join(got[1])
        assert "Budget vs Actual" in text
        assert "TOTAL" in text.upper() and "DEC" in text.upper()
        for a in ACCOUNTS[:4]:
            assert a["name"] in text, a["name"]
    finally:
        page.close()
