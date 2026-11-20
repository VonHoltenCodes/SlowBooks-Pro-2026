"""The wide grid, in playwright's Chromium on the bakery's books of
tests/test_theme_contrast.py with eleven classes and a job added (R1 #231,
R2 #232, R3 #233, R5 #235, R13 #242, v2.22.0):

- P&L by Class opens in the wide dialog and its grid scrolls sideways
  inside its container while the Account column and the header row stay
  put, so the Total column is reachable with twelve classes;
- "Go to class" (the type-ahead picker), ‹ Prev / Next ›, and the status
  line that says "Column 4 of 12: Site Prep"; with the grid focused ←/→,
  Home/End move the column and Enter opens that class's own P&L;
- the class's own P&L is a report: period shell, a class picker with
  Prev/Next, Save and export; Back from its drill-down returns to it, not
  to the grid; the P&L Unclassified card; a subtotal cell opens it;
- Save PDF / Save CSV and Add to Saved Reports on the grid, a saved
  by-class report reopening with its columns;
- the column chooser: a subset says "filtered" and shows the filtered
  total, Active hides an archived class, "show empty" adds a zero column;
- P&L by Job: the same grid by job, "No job" first, a heading opening the
  job page on the report's dates with a way back;
- on a phone the wide dialog is the screen's width and the grid swipes
  with the Account column frozen.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    ORIGIN,
    SERVED,
    _served_by,
    _visit,
    browser_fixture,
    new_page,
    company_fixture,
    settle,
)

SEPT = ("2026-09-01", "2026-09-30")
BY_CLASS_URL = f"#/reports/profit-loss-by-class?start_date={SEPT[0]}&end_date={SEPT[1]}"
BY_JOB_URL = f"#/reports/profit-loss-by-job?start_date={SEPT[0]}&end_date={SEPT[1]}"

# eleven divisions; with Uncategorized (the books' untagged September
# activity) the grid has twelve columns, Site Prep the last
DIVISIONS = [
    "Concrete",
    "Drywall",
    "Electrical",
    "Flooring",
    "Framing",
    "HVAC",
    "Landscaping",
    "Painting",
    "Plumbing",
    "Roofing",
    "Site Prep",
]

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TITLE = "() => document.getElementById('modal-title').textContent"
LIVE = "() => document.getElementById('grid-live').textContent"
CURRENT_HEAD = "() => (document.querySelector('#report-content thead th.is-current') || {}).textContent"
SCROLL = """() => { const b = document.getElementById('grid-scroll');
    const first = b.querySelector('thead th:first-child').getBoundingClientRect();
    const total = b.querySelector('thead th:last-child').getBoundingClientRect();
    const box = b.getBoundingClientRect();
    return { scrollLeft: b.scrollLeft, wider: b.scrollWidth > b.clientWidth,
             firstLeft: first.left - box.left, totalVisible: total.right <= box.right + 1 && total.left >= box.left,
             sticky: getComputedStyle(b.querySelector('tbody td:first-child, tbody th:first-child')).position,
             headSticky: getComputedStyle(b.querySelector('thead th:last-child')).position }; }"""


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


@pytest.fixture(name="divisions")
def divisions_fixture(company, books):
    """Eleven classes, each with September income and an expense, and one
    September posting to the job: the twelve-column grid."""
    accounts = {a["account_number"]: a["id"] for a in _ok(company.get("/api/accounts"))}
    ids = {}
    for n, name in enumerate(DIVISIONS, start=1):
        cls = _ok(company.post("/api/classes", json={"name": name}))
        ids[name] = cls["id"]
        _ok(
            company.post(
                "/api/journal",
                json={
                    "date": f"2026-09-{n + 2:02d}",
                    "description": f"{name} work",
                    "class_id": cls["id"],
                    "lines": [
                        {
                            "account_id": accounts["1000"],
                            "debit": str(100 * n),
                            "credit": "0",
                        },
                        {
                            "account_id": accounts["4000"],
                            "debit": "0",
                            "credit": str(100 * n),
                        },
                        {
                            "account_id": accounts["6000"],
                            "debit": str(10 * n),
                            "credit": "0",
                        },
                        {
                            "account_id": accounts["1000"],
                            "debit": "0",
                            "credit": str(10 * n),
                        },
                    ],
                },
            )
        )
    _ok(
        company.post(
            "/api/journal",
            json={
                "date": "2026-09-20",
                "description": "gala staffing",
                "job_id": books["job"],
                "lines": [
                    {"account_id": accounts["1000"], "debit": "900", "credit": "0"},
                    {"account_id": accounts["4000"], "debit": "0", "credit": "900"},
                ],
            },
        )
    )
    uncategorized = next(
        c for c in _ok(company.get("/api/classes")) if c["is_system_default"]
    )
    ids["Uncategorized"] = uncategorized["id"]
    return ids


def _open_at(browser, client, page_hash, width=1280, height=800):
    """A fresh document at the address: what a bookmark, a pasted link or a
    reload does."""
    handled = []
    # The app's CSP has no 'unsafe-eval' (it is the desktop launcher's
    # alone). A wait_for_function that finds its answer on the first look
    # never evals in the page, but one that has to poll compiles its
    # predicate there and the CSP refuses it ("Evaluating a string as
    # JavaScript violates…"), which made a slow redraw a red test. The
    # page under test is the same; only Playwright's own polling is let
    # past the policy.
    page = new_page(
        browser, viewport={"width": width, "height": height}, bypass_csp=True
    )
    page.route("**/*", _served_by(client, handled, SERVED))
    page.goto(f"{ORIGIN}/{page_hash}")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    settle(page, handled)
    return page, handled


def _type_to_pick(page, box, typed, name):
    """Type into a type-ahead box and take its match with Enter. A click
    opens the whole list first, so an option being listed is not the typed
    match being listed: the wait is for the list to hold the one match,
    highlighted (aria-selected), which is what Enter takes."""
    box.click()
    page.keyboard.type(typed)
    page.wait_for_function(
        "(name) => { const l = document.querySelectorAll('#cbx-listbox [role=option]');"
        " return l.length === 1 && l[0].textContent.trim() === name"
        " && l[0].getAttribute('aria-selected') === 'true'; }",
        arg=name,
    )
    page.keyboard.press("Enter")


def _hash(page):
    return page.evaluate("location.hash")


def _history(page):
    return page.evaluate("history.length")


def _query(h):
    return dict(p.split("=", 1) for p in h.split("?", 1)[1].split("&"))


def _heads(page):
    return page.eval_on_selector_all(
        "#report-content thead th", "els => els.map(e => e.textContent.trim())"
    )


# ── R1: the wide grid ────────────────────────────────────────────────────


def test_the_grid_is_wide_and_scrolls_with_the_account_column_frozen(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        assert page.evaluate(
            "() => document.getElementById('modal').classList.contains('modal--wide')"
        )
        heads = _heads(page)
        assert heads[0] == "Account" and heads[-1] == "Total"
        assert heads[1:-1] == ["Uncategorized"] + DIVISIONS, heads
        before = page.evaluate(SCROLL)
        assert before["wider"], "twelve columns are wider than the dialog"
        assert before["sticky"] == "sticky" and before["headSticky"] == "sticky"
        assert not before["totalVisible"], "the Total column starts out of view"

        # End: the last column, and with it the Total column, into view;
        # the Account column has not moved
        page.focus("#grid-scroll")
        page.keyboard.press("End")
        after = page.evaluate(SCROLL)
        assert after["scrollLeft"] > 0
        assert after["totalVisible"]
        assert abs(after["firstLeft"]) < 1.5, "the Account column stays put"
        assert page.evaluate(LIVE) == "Column 12 of 12: Site Prep"
        assert page.evaluate(CURRENT_HEAD).strip() == "Site Prep"
        # every cell of the column is marked, the section rows included
        assert (
            page.locator(
                '#report-content .pivot-grid [data-col="11"].is-current'
            ).count()
            == page.locator('#report-content .pivot-grid [data-col="11"]').count()
        )
        # the frozen first column is a row header
        assert (
            page.locator(
                "#report-content tbody th[scope='row']:has-text('4000 - Service Income')"
            ).count()
            == 1
        )
    finally:
        page.close()


def test_go_to_class_picks_a_column_and_prev_next_step_through_them(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        picker = page.get_by_role("combobox", name="Go to class")
        assert picker.count() == 1, "the type-ahead attached to the picker, named"
        _type_to_pick(page, picker, "Site", "Site Prep")
        page.wait_for_function(f"({LIVE})() === 'Column 12 of 12: Site Prep'")
        assert page.evaluate(CURRENT_HEAD).strip() == "Site Prep"
        s = page.evaluate(SCROLL)
        assert s["scrollLeft"] > 0 and s["totalVisible"], "scrolled into view"

        nxt = page.get_by_role("button", name="Next class")
        prev = page.get_by_role("button", name="Previous class")
        assert nxt.count() == 1 and prev.count() == 1
        prev.click()
        assert page.evaluate(LIVE) == "Column 11 of 12: Roofing"
        prev.click()
        assert page.evaluate(LIVE) == "Column 10 of 12: Plumbing"
        nxt.click()
        assert page.evaluate(LIVE) == "Column 11 of 12: Roofing"
        assert picker.input_value() == "Roofing", "the picker follows"

        # a new period redraws the grid and keeps the columns' addressless
        # jump state out of the address
        assert "jump" not in _hash(page) and "col" not in _hash(page)
    finally:
        page.close()


def test_the_keyboard_moves_the_column_and_enter_opens_the_class(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        grid = page.locator("#grid-scroll")
        assert grid.get_attribute("role") == "region"
        assert grid.get_attribute("aria-label") == "P&L by Class"
        assert grid.get_attribute("tabindex") == "0"
        page.focus("#grid-scroll")
        page.keyboard.press("ArrowRight")
        assert page.evaluate(LIVE) == "Column 1 of 12: Uncategorized"
        page.keyboard.press("ArrowLeft")
        assert (
            page.evaluate(LIVE) == "Column 1 of 12: Uncategorized"
        ), "stops at the first"
        for _ in range(3):
            page.keyboard.press("ArrowRight")
        assert page.evaluate(LIVE) == "Column 4 of 12: Electrical"
        page.keyboard.press("Home")
        assert page.evaluate(LIVE) == "Column 1 of 12: Uncategorized"
        page.keyboard.press("End")
        page.keyboard.press("ArrowRight")
        assert page.evaluate(LIVE) == "Column 12 of 12: Site Prep", "stops at the last"
        page.keyboard.press("ArrowLeft")
        assert page.evaluate(LIVE) == "Column 11 of 12: Roofing"

        length = _history(page)
        page.keyboard.press("Enter")
        page.wait_for_selector("#class-pl-body table")
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Roofing'")
        h = _hash(page)
        assert h.startswith("#/reports/profit-loss-class?class_id=")
        assert _query(h)["class_id"] == str(divisions["Roofing"])
        assert (_query(h)["start_date"], _query(h)["end_date"]) == SEPT
        assert _history(page) == length + 1, "a hop pushes"

        # the class view: a class picker with Prev / Next, and where it is
        pick = page.get_by_role("combobox", name="Class")
        assert pick.count() == 1 and pick.input_value() == "Roofing"
        where = page.evaluate(
            "() => document.getElementById('class-pl-where').textContent"
        )
        assert where == "Roofing: 11 of 12"
        page.get_by_role("button", name="Next class").click()
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Site Prep'")
        assert _query(_hash(page))["class_id"] == str(divisions["Site Prep"])
        assert _history(page) == length + 1, "a step within the view replaces"
        assert (
            page.evaluate("() => document.getElementById('class-pl-where').textContent")
            == "Site Prep: 12 of 12"
        )
        page.get_by_role("button", name="Previous class").click()
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Roofing'")
        # the picker itself
        _type_to_pick(page, pick, "Conc", "Concrete")
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Concrete'")
        assert _query(_hash(page))["class_id"] == str(divisions["Concrete"])
        body = page.inner_text("#class-pl-body")
        assert "$100.00" in body and "this class only" in body

        # back to the grid through history, as it was left
        page.get_by_role("button", name="Back to P&L by Class").click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert _hash(page) == BY_CLASS_URL
        assert _history(page) == length + 1
    finally:
        page.close()


def test_on_a_phone_the_dialog_is_the_screen_and_the_grid_swipes(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL, width=390, height=800)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        width = page.evaluate(
            "() => document.getElementById('modal').getBoundingClientRect().width"
        )
        assert abs(width - 390 * 0.96) < 2, width
        s = page.evaluate(SCROLL)
        assert s["wider"] and s["sticky"] == "sticky"
        page.get_by_role("button", name="Next class").click()
        page.get_by_role("button", name="Next class").click()
        assert page.evaluate(LIVE) == "Column 2 of 12: Concrete"
        assert abs(page.evaluate(SCROLL)["firstLeft"]) < 1.5
        # the dialog and the grid's container stay inside the screen; the
        # table's overflow is the container's to scroll
        assert page.evaluate(
            "() => document.getElementById('modal').getBoundingClientRect().right <= 390"
        )
        assert page.evaluate(
            "() => document.getElementById('grid-scroll').getBoundingClientRect().right <= 390"
        )
    finally:
        page.close()


# ── R2: export and save ──────────────────────────────────────────────────


def test_the_grid_saves_as_pdf_and_csv_and_as_a_saved_report(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        opens = []
        page.expose_function("_opened", lambda url: opens.append(url))
        page.evaluate(
            "() => { window.open = (u) => { window._opened(u); return null; }; }"
        )
        page.get_by_role("button", name="Save PDF").click()
        page.get_by_role("button", name="Save CSV").click()
        assert opens == [
            f"/api/reports/profit-loss-by-class/pdf?start_date={SEPT[0]}&end_date={SEPT[1]}",
            f"/api/reports/profit-loss-by-class/csv?start_date={SEPT[0]}&end_date={SEPT[1]}",
        ]
        # the PDF and CSV the buttons open really come back
        pdf = company.get(opens[0])
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
        csv = company.get(opens[1])
        assert csv.status_code == 200 and "Site Prep" in csv.text

        page.evaluate("() => { window.prompt = () => 'Divisions, September'; }")
        page.get_by_role("button", name="Add to Saved Reports…").click()
        page.wait_for_selector("#saved-reports-list")
        saved = company.get("/api/saved-reports").json()
        mine = next(s for s in saved if s["name"] == "Divisions, September")
        assert mine["report_type"] == "profit_loss_by_class"
        assert mine["parameters"] == {
            "period": "custom",
            "start_date": SEPT[0],
            "end_date": SEPT[1],
        }
        # and it reopens on its dates, as the grid (the report stays open
        # over the refreshed Report Center; close it first)
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.get_by_role("button", name="Open").first.click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert _hash(page) == BY_CLASS_URL
        assert page.evaluate(TITLE) == "P&L by Class"
        assert len(_heads(page)) == 14
    finally:
        page.close()


# ── R5: a class's P&L as a report ────────────────────────────────────────


def test_a_subtotal_cell_opens_the_class_and_back_from_its_drill_down_returns_to_it(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        length = _history(page)
        framing = _heads(page).index("Framing") - 1
        page.locator(
            f"#report-content tbody tr:has-text('Net Income') td[data-col='{framing}'] a"
        ).click()
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Framing'")
        class_pl = _hash(page)
        assert _query(class_pl)["class_id"] == str(divisions["Framing"])
        assert _history(page) == length + 1
        # its exports carry the class
        assert (
            page.locator(
                f"#class-pl-body button[onclick*='/api/reports/profit-loss/pdf?start_date={SEPT[0]}&end_date={SEPT[1]}&class_id={divisions['Framing']}']"
            ).count()
            == 1
        )
        assert page.get_by_role("button", name="Add to Saved Reports…").count() == 1

        page.locator("#class-pl-body a:has-text('Service Income')").click()
        page.wait_for_selector("#drilldown-body table")
        q = _query(_hash(page))
        assert q["from"] == "profit-loss-class" and q["class_id"] == str(
            divisions["Framing"]
        )
        assert _history(page) == length + 2
        assert "Framing work" in page.inner_text("#drilldown-body")
        back = page.get_by_role("button", name="Back to Profit & Loss — Framing")
        assert back.count() == 1
        back.click()
        page.wait_for_selector("#class-pl-body table")
        assert _hash(page) == class_pl, "the class P&L, not the grid"
        assert _history(page) == length + 2, "through history"
        page.get_by_role("button", name="Back to P&L by Class").click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert _hash(page) == BY_CLASS_URL
    finally:
        page.close()


def test_the_class_p_and_l_saves_with_its_class_and_reopens(
    browser, company, divisions
):
    url = (
        f"#/reports/profit-loss-class?class_id={divisions['Plumbing']}"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}"
    )
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#class-pl-body table")
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Plumbing'")
        page.evaluate("() => { window.prompt = () => 'Plumbing, September'; }")
        page.get_by_role("button", name="Add to Saved Reports…").click()
        page.wait_for_selector("#saved-reports-list")
        saved = next(
            s
            for s in company.get("/api/saved-reports").json()
            if s["name"] == "Plumbing, September"
        )
        assert saved["report_type"] == "profit_loss_class"
        assert saved["parameters"] == {
            "class_id": divisions["Plumbing"],
            "period": "custom",
            "start_date": SEPT[0],
            "end_date": SEPT[1],
        }
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.get_by_role("button", name="Open").first.click()
        page.wait_for_selector("#class-pl-body table")
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Plumbing'")
        assert _hash(page) == url
        assert page.get_by_role("combobox", name="Class").input_value() == "Plumbing"

        # a plain P&L addressed with a class is that class's own, with one
        # address, not a redirect to go back through
        _visit(
            page,
            handled,
            f"#/reports/profit-loss?class_id={divisions['HVAC']}&start_date={SEPT[0]}&end_date={SEPT[1]}",
        )
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — HVAC'")
        assert _hash(page).startswith(
            f"#/reports/profit-loss-class?class_id={divisions['HVAC']}"
        )
    finally:
        page.close()


def test_the_unclassified_card_is_the_uncategorized_column_of_the_grid(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, "#/reports")
    try:
        length = _history(page)
        # under the folded All reports (#794, #936)
        page.click('#all-reports-toggle[aria-expanded="false"]')
        page.get_by_text("P&L Unclassified", exact=True).click()
        page.wait_for_selector("#class-pl-body table")
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Uncategorized'")
        h = _hash(page)
        assert h.startswith(
            f"#/reports/profit-loss-class?class_id={divisions['Uncategorized']}&period=this_year_to_date"
        )
        assert _history(page) == length + 1
        q = _query(h)
        grid = company.get(
            "/api/reports/profit-loss-by-class",
            params={"start_date": q["start_date"], "end_date": q["end_date"]},
        ).json()
        column = grid["classes"][0]
        assert column["class_name"] == "Uncategorized"
        own = company.get(
            "/api/reports/profit-loss",
            params={
                "start_date": q["start_date"],
                "end_date": q["end_date"],
                "class_id": divisions["Uncategorized"],
            },
        ).json()
        assert own["net_income"] == column["net_income"]
        shown = page.inner_text("#class-pl-body tbody tr:has-text('Net Income')")
        assert f"{column['net_income']:,.2f}".replace("-", "") in shown
        # Back is the Report Center, the report closed
        page.go_back()
        page.wait_for_function("() => location.hash === '#/reports'")
        page.wait_for_function(f"!({MODAL_SHOWN})()")

        # the card's own address opens the same view, replaced, one step
        _visit(
            page,
            handled,
            f"#/reports/profit-loss-unclassified?start_date={SEPT[0]}&end_date={SEPT[1]}",
        )
        page.wait_for_function(f"({TITLE})() === 'Profit & Loss — Uncategorized'")
        assert _hash(page) == (
            f"#/reports/profit-loss-class?class_id={divisions['Uncategorized']}&start_date={SEPT[0]}&end_date={SEPT[1]}"
        )
    finally:
        page.close()


# ── R3: choose the columns ───────────────────────────────────────────────


def test_choosing_columns_says_filtered_and_rides_on_the_address_and_exports(
    browser, company, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        assert page.locator(".grid-filtered").count() == 0
        show = page.get_by_role("combobox", name="Classes")
        assert show.count() == 1 and show.input_value() == "all"
        empty = page.get_by_role("checkbox", name="Show classes with no activity")
        assert empty.count() == 1 and not empty.is_checked()
        choose = page.get_by_role("button", name="Choose classes…")
        assert choose.get_attribute("aria-expanded") == "false"
        assert page.locator("#grid-chooser").is_hidden()

        # a subset: Framing and Roofing
        choose.click()
        assert choose.get_attribute("aria-expanded") == "true"
        page.get_by_role("checkbox", name="Framing").check()
        page.get_by_role("checkbox", name="Roofing").check()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector(".grid-filtered")
        heads = _heads(page)
        assert heads == ["Account", "Framing", "Roofing", "Total (shown)"]
        note = page.inner_text(".grid-filtered")
        assert "Filtered: 2 of 12 classes shown" in note
        assert "not the company" in note
        whole = company.get(
            "/api/reports/profit-loss-by-class",
            params={"start_date": SEPT[0], "end_date": SEPT[1]},
        ).json()
        assert f"{whole['total_net_income']:,.2f}" in note
        part = company.get(
            "/api/reports/profit-loss-by-class",
            params={
                "start_date": SEPT[0],
                "end_date": SEPT[1],
                "class_ids": f"{divisions['Framing']},{divisions['Roofing']}",
            },
        ).json()
        shown_net = page.inner_text(
            "#report-content tbody tr:has-text('Net Income') td:last-child"
        )
        assert shown_net == f"${part['total_net_income']:,.2f}"
        assert part["total_net_income"] != whole["total_net_income"]
        q = _query(_hash(page))
        assert sorted(q["class_ids"].replace("%2C", ",").split(",")) == sorted(
            [str(divisions["Framing"]), str(divisions["Roofing"])]
        )
        assert (
            page.evaluate("() => document.getElementById('grid-choice').textContent")
            == "2 chosen"
        )
        # the export links carry the choice
        href = page.locator(
            "#report-content button:has-text('Save CSV')"
        ).get_attribute("onclick")
        assert "class_ids=" in href and "profit-loss-by-class/csv" in href
        # the grid's picker lists only the columns shown
        assert page.locator("#grid-jump option").count() == 3

        # show the empty ones: a zero column for a class with nothing
        _ok(company.post("/api/classes", json={"name": "Dormant"}))
        assert page.locator("#grid-chooser").is_hidden(), "Apply folds the panel away"
        choose.click()
        page.get_by_role("button", name="Show every class").click()
        page.wait_for_function("() => !document.querySelector('.grid-filtered')")
        empty.check()
        page.wait_for_function(
            "() => [...document.querySelectorAll('#report-content thead th')].some(t => t.textContent.trim() === 'Dormant')"
        )
        assert _query(_hash(page))["include_empty"] == "true"
        assert page.locator(".grid-filtered").count() == 0

        # Active only hides an archived class with history, and says so
        _ok(
            company.put(
                f"/api/classes/{divisions['Drywall']}", json={"is_archived": True}
            )
        )
        page.select_option("#grid-show", "active")
        page.wait_for_selector(".grid-filtered")
        assert "Drywall" not in _heads(page)
        assert _query(_hash(page))["show"] == "active"
        page.select_option("#grid-show", "all")
        # the report drawn again with every class: while it reloads there is
        # no .grid-filtered and no heading either, so the wait is for the
        # heading itself (#1095: read mid-redraw on CI, the heads were [])
        page.wait_for_function(
            "() => [...document.querySelectorAll('#report-content thead th')]"
            ".some(t => t.textContent.trim() === 'Drywall (archived)')"
        )
        assert not page.locator(".grid-filtered").count()
        assert "Drywall (archived)" in _heads(page)

        # the choice reopens from a saved report
        page.get_by_role("button", name="Choose classes…").click()
        page.get_by_role("checkbox", name="Site Prep").check()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector(".grid-filtered")
        page.evaluate("() => { window.prompt = () => 'Site Prep only'; }")
        page.get_by_role("button", name="Add to Saved Reports…").click()
        page.wait_for_selector("#saved-reports-list")
        saved = next(
            s
            for s in company.get("/api/saved-reports").json()
            if s["name"] == "Site Prep only"
        )
        assert saved["parameters"]["class_ids"] == str(divisions["Site Prep"])
        assert saved["parameters"]["include_empty"] == "true"
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.get_by_role("button", name="Open").first.click()
        page.wait_for_selector(".grid-filtered")
        assert _heads(page) == ["Account", "Site Prep", "Total (shown)"]
        assert page.get_by_role(
            "checkbox", name="Show classes with no activity"
        ).is_checked()
        assert page.locator(
            f"#grid-chooser input[value='{divisions['Site Prep']}']"
        ).is_checked(), "ticked in the folded panel"
    finally:
        page.close()


# ── R13: P&L by Job ──────────────────────────────────────────────────────


def test_p_and_l_by_job_is_the_grid_by_job_and_a_heading_opens_the_job_on_its_dates(
    browser, company, books, divisions
):
    page, handled = _open_at(browser, company, "#/reports")
    try:
        # under the folded All reports (#794, #936)
        page.click('#all-reports-toggle[aria-expanded="false"]')
        page.get_by_text("P&L by Job", exact=True).click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert page.evaluate(TITLE) == "P&L by Job"
        by_job = _hash(page)
        assert by_job.startswith(
            "#/reports/profit-loss-by-job?period=this_year_to_date"
        )
        assert _heads(page) == ["Account", "No job", "Waterfront Gala", "Total"]
        assert page.get_by_role("combobox", name="Go to job").count() == 1
        assert page.get_by_role("combobox", name="Jobs").count() == 1
        assert page.get_by_role("button", name="Save PDF").count() == 1
        assert page.get_by_role("button", name="Add to Saved Reports…").count() == 1
        assert "No job" in page.inner_text("#report-content")
        q = _query(by_job)
        length = _history(page)

        # the heading: the job page, on the report's dates, with a way back
        page.locator("#report-content thead a:has-text('Waterfront Gala')").click()
        page.wait_for_selector("#job-tab-body")
        assert _hash(page) == (
            f"#/jobs/{books['job']}?start_date={q['start_date']}&end_date={q['end_date']}&from=profit-loss-by-job"
        )
        assert _history(page) == length + 1
        assert page.input_value("#job-period-start") == q["start_date"]
        assert page.input_value("#job-period-end") == q["end_date"]
        assert not page.evaluate(MODAL_SHOWN)
        back = page.get_by_role("button", name="Back to P&L by Job")
        assert back.count() == 1
        # a period set on the page is on its address (replaced)
        page.fill("#job-period-start", "2026-09-01")
        page.dispatch_event("#job-period-start", "change")
        page.wait_for_function("() => location.hash.includes('start_date=2026-09-01')")
        assert _history(page) == length + 1
        back.click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert _hash(page) == by_job
        assert _history(page) == length + 1, "through history"
        assert page.evaluate(TITLE) == "P&L by Job"

        # an amount: the lines behind it, for that job
        page.locator(
            "#report-content tbody tr:has-text('Service Income') td[data-col='1'] a"
        ).click()
        page.wait_for_selector("#drilldown-body table")
        page.wait_for_function(
            f"({TITLE})() === 'Drill-down — Service Income · Job: Waterfront Gala'"
        )
        dq = _query(_hash(page))
        assert dq["job_id"] == str(books["job"]) and dq["from"] == "profit-loss-by-job"
        assert "class_id" not in dq
        body = page.inner_text("#drilldown-body")
        assert "gala staffing" in body and "Concrete work" not in body
        assert "Job: Waterfront Gala" in body
        assert page.get_by_role("button", name="Back to P&L by Job").count() == 1
        page.go_back()
        page.wait_for_selector("#report-content .pivot-grid")

        # the No job column: job_id=0
        page.locator(
            "#report-content tbody tr:has-text('Service Income') td[data-col='0'] a"
        ).click()
        page.wait_for_selector("#drilldown-body table")
        page.wait_for_function(
            f"({TITLE})() === 'Drill-down — Service Income · Job: No job'"
        )
        assert _query(_hash(page))["job_id"] == "0"
        body = page.inner_text("#drilldown-body")
        assert "Concrete work" in body and "gala staffing" not in body

        # rebuilt from its address alone, the job's name is the server's
        _visit(page, handled, _hash(page))
        page.wait_for_selector("#drilldown-body table")
        page.wait_for_function(
            f"({TITLE})() === 'Drill-down — Service Income · Job: No job'"
        )
    finally:
        page.close()


# ── NEW-26 / NEW-28: a chosen column with no activity, and the counts ────


def test_choosing_only_classes_with_no_activity_shows_the_empty_state_and_the_counts_agree(
    browser, company, books, divisions
):
    """A chosen class with nothing in the period was drawn as a column of
    zeros under "Filtered: 1 of 12 classes shown" (NEW-26, W-1), and the
    chooser's "1 of 13 chosen" counted every class listed while the note
    counted the grid's columns (NEW-28): the chooser now counts its ticks
    alone, the note counts the columns with activity, and a chosen class
    with none is named — left out, or drawn as zeros when asked for."""
    dormant = _ok(company.post("/api/classes", json={"name": "Dormant"}))["id"]
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        choice = "() => document.getElementById('grid-choice').textContent"
        choose = page.get_by_role("button", name="Choose classes…")
        choose.click()
        page.get_by_role("checkbox", name="Dormant").check()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector(".empty-state.grid-filtered")
        state = page.inner_text(".empty-state.grid-filtered")
        assert (
            "None of the chosen classes (Dormant) has activity in this period" in state
        )
        assert page.locator("#report-content .pivot-grid").count() == 0
        assert page.evaluate(choice) == "1 chosen"

        # with one that has activity: that column, and the note says what
        # was left out and why
        choose.click()
        page.get_by_role("checkbox", name="Framing").check()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector("#report-content .pivot-grid")
        assert _heads(page) == ["Account", "Framing", "Total (shown)"]
        note = page.inner_text(".grid-filtered")
        assert (
            "Filtered: 1 of 12 classes shown; 1 chosen has no activity in this "
            "period and is left out (Dormant)"
        ) in note
        assert page.evaluate(choice) == "2 chosen"

        # asked for, the empty column is drawn; the note still counts activity
        page.get_by_role("checkbox", name="Show classes with no activity").check()
        page.wait_for_function(
            "() => [...document.querySelectorAll('#report-content thead th')].some(t => t.textContent.trim() === 'Dormant')"
        )
        assert _heads(page) == ["Account", "Dormant", "Framing", "Total (shown)"]
        note = page.inner_text(".grid-filtered")
        assert (
            "Filtered: 1 of 12 classes shown, plus 1 with no activity in this period (Dormant)"
            in note
        )
        # the CSV the Save button offers says the same
        text = company.get(
            f"/api/reports/profit-loss-by-class/csv?start_date={SEPT[0]}&end_date={SEPT[1]}"
            f"&class_ids={dormant},{divisions['Framing']}&include_empty=true"
        ).text
        assert "plus 1 with no activity in this period (Dormant)" in text

        # "Show every class" from the empty state brings the grid back
        page.get_by_role("checkbox", name="Show classes with no activity").uncheck()
        page.wait_for_function(
            "() => ![...document.querySelectorAll('#report-content thead th')].some(t => t.textContent.trim() === 'Dormant')"
        )
        choose.click()
        page.get_by_role("checkbox", name="Framing").uncheck()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector(".empty-state.grid-filtered")
        page.get_by_role("link", name="Show every class").click()
        # Navigation removes the old note before the replacement grid arrives.
        page.wait_for_selector("#report-content .pivot-grid")
        page.wait_for_function("() => !document.querySelector('.grid-filtered')")
        assert len(_heads(page)) == 14  # Account, twelve classes, Total
        assert page.evaluate(choice) == ""
    finally:
        page.close()

    # the same for a job with nothing in the period
    _ok(
        company.post(
            "/api/jobs", json={"customer_id": books["customer"], "name": "Fence"}
        )
    )
    page, handled = _open_at(browser, company, BY_JOB_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        page.get_by_role("button", name="Choose jobs…").click()
        page.get_by_role("checkbox", name="Fence").check()
        page.get_by_role("button", name="Apply").click()
        page.wait_for_selector(".empty-state.grid-filtered")
        assert (
            "None of the chosen jobs (Fence) has activity in this period"
            in page.inner_text(".empty-state.grid-filtered")
        )
        assert (
            page.evaluate("() => document.getElementById('grid-choice').textContent")
            == "1 chosen"
        )
    finally:
        page.close()
