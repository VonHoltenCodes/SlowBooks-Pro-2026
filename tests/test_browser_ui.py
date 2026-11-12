"""The page in a real browser: what a node probe cannot see (layout, focus,
scrolling, the address bar), checked in playwright's Chromium.

The page is the real index.html with the real scripts and stylesheets; the
API answers from the fixtures below. Skipped, as one module, where playwright
or its Chromium is not installed.

- F22, the remainder (2.18.0 gate, macbase1): a customer's 120-character
  name sized the Customer select, and the Edit Invoice form's second column
  (Date, Due Date, Class, Exchange Rate) went past the dialog's right edge,
  behind a sideways scrollbar. The statement dialog had been fixed alone; the
  form grid now lets its columns shrink, for every form. Checked on the
  invoice, estimate and sales receipt forms at 1280 x 800.
- NEW-3 (2.18.0 gate, macbase1): the Pay Run view opened scrolled to the
  first Stub PDF, with the Employee column out of sight, so no button said
  whose stub it was. Checked in the gate's 1280 x 800 window and a narrower
  one.
- NEW-9 (2.18.0 gate, macbase1): after the toolbar's Home, the sidebar link
  of the page just left did nothing. Clicked through with the real toolbar
  and sidebar, and Back.
- #197: an IIF or report-CSV import that came back with errors said
  "Imported 0 records" in green and "Import complete"; the red box below was
  the only sign of them. Driven through the real page with the file input.
- #194 (@cnbarry1): on Add Reseller Permit the state's format note was drawn
  over the State and Permit number boxes, and a click on either box's lower
  part landed on the note. Checked with a long note and a short one, typed
  and empty, in a wide window and a narrow one.
- W-36 (2.29.0 gate, skytech): an employee's Opening YTD quarter with a
  state row was 852 px wide in a 682 px dialog: the state table's seven
  fixed-width inputs pushed Q2's Federal withholding through Net past the
  edge, behind a sideways scrollbar. Opened for an employee with a state in
  Q1 and Q2 at 1280 x 800, in both themes; the state inputs still take the
  keyboard in order.
- #662: reading an employee's onboarding checklist no longer saves the
  default one; the read answers it with no task ids. The Onboarding list
  still reads 0 of 8, and View Checklist saves it before showing it, so
  every Complete button names a saved task.
- #663: a Transaction Detail by Account file with problems posts nothing on
  its first upload; the page lists them by kind with a box to tick each,
  and Import sends the same file again with the answers. Driven through
  the real page, its text checked at AA in both themes.
"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import _theme, new_page  # noqa: E402  (a Linux browser)

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://slowbooks.test"

LONG_NAME = (
    "Harbor District Community Events Association: Weddings, Memorials, "
    "Graduations and Seasonal Market Catering, Astoria, OR"
)
assert len(LONG_NAME) == 120

CUSTOMERS = [
    {
        "id": 1,
        "name": "Salt & Pine Catering Co.",
        "terms": "Net 15",
        "is_taxable": True,
    },
    {"id": 2, "name": LONG_NAME, "terms": "Net 30", "is_taxable": True},
]
LINE = {
    "item_id": None,
    "description": "Rounding probe",
    "quantity": 3,
    "rate": 0.335,
    "amount": 1.01,
    "is_taxable": False,
}
API = {
    "/health": {"status": "ok", "version": "2.18.0"},
    "/api/auth/status": {
        "authenticated": True,
        "setup_needed": False,
        "multi_user": False,
        "company_name": "Harbor Light Bakery Two",
    },
    "/api/system": {"version": "2.18.0", "desktop": False, "server_mode": False},
    "/api/settings": {
        "company_name": "Harbor Light Bakery Two",
        "company_type": "business",
        "default_terms": "Net 15",
        "default_tax_rate": "8.25",
        "invoice_notes": "Thank you for choosing Harbor Light!",
        "walk_in_customer_id": "9",
    },
    "/api/customers": CUSTOMERS,
    "/api/items": [{"id": 1, "name": "Sourdough Loaf", "rate": 8.5}],
    "/api/classes": [{"id": 1, "name": "Unassigned", "is_system_default": True}],
    "/api/accounts": [
        {"id": 1, "name": "Checking", "account_number": "1000", "bank_kind": "bank"}
    ],
    "/api/employees/1": {
        "id": 1,
        "first_name": "Lena",
        "last_name": "Ortiz",
        "email": "lena@harborlight.test",
        "pay_type": "salary",
        "pay_rate": 52000,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "hire_date": "2024-03-04",
        "work_state": "GA",
        "is_active": True,
    },
    "/api/employees/1/ytd": {
        "gross": 30000,
        "federal": 3100,
        "state": 1140,
        "ss": 1798,
        "medicare": 420.5,
        "net": 22000,
    },
    # a May switch: Q1 and Q2 each carry the old payroll, one state apiece
    "/api/employees/1/opening-ytd": {
        "locked": False,
        "first_pay_date": None,
        "opening": {
            "id": 1,
            "employee_id": 1,
            "year": 2026,
            "as_of": "2026-05-15",
            "gross": 30000,
            "federal_tax": 3100,
            "ss_tax": 1798,
            "medicare_tax": 420.5,
            "net": 22000,
            "states": [{"state": "GA", "wages": 30000, "withholding": 1140}],
            "quarters": [
                {
                    "quarter": q,
                    "gross": gross,
                    "federal_tax": round(gross * 0.1033, 2),
                    "ss_wages": gross,
                    "ss_tax": round(gross * 0.062, 2),
                    "employer_ss_tax": round(gross * 0.062, 2),
                    "medicare_wages": gross,
                    "medicare_tax": round(gross * 0.0145, 2),
                    "employer_medicare_tax": round(gross * 0.0145, 2),
                    "futa_wages": futa,
                    "futa_tax": round(futa * 0.006, 2),
                    "pretax_deductions": 0,
                    "posttax_deductions": 0,
                    "garnishments": 0,
                    "net": round(gross * 0.7333, 2),
                    "states": [
                        {
                            "state": "GA",
                            "wages": gross,
                            "withholding": round(gross * 0.038, 2),
                            "suta_wages": futa,
                            "suta_tax": round(futa * 0.027, 2),
                            "other_employee": 0,
                            "other_employer": 0,
                        }
                    ],
                }
                for q, gross, futa in ((1, 18000, 7000), (2, 12000, 0))
            ],
            "benefits": [],
        },
    },
    "/api/invoices/1": {
        "id": 1,
        "invoice_number": "HLB-2005",
        "customer_id": 1,
        "date": "2026-09-26",
        "due_date": "2026-10-11",
        "terms": "Net 15",
        "po_number": "",
        "tax_rate": 0.0825,
        "notes": "Thank you for choosing Harbor Light!",
        "currency": "USD",
        "exchange_rate": 1,
        "class_id": 1,
        "job_id": None,
        "status": "draft",
        "total": 1.01,
        "amount_paid": 0,
        "lines": [LINE],
    },
    "/api/estimates/1": {
        "id": 1,
        "estimate_number": "E-1001",
        "customer_id": 1,
        "date": "2026-09-26",
        "expiration_date": "2026-10-26",
        "tax_rate": 0.0825,
        "notes": "",
        "class_id": 1,
        "job_id": None,
        "lines": [LINE],
    },
    "/api/payroll/1": {
        "id": 1,
        "period_start": "2026-09-13",
        "period_end": "2026-09-26",
        "status": "processed",
        "total_gross": 3726.67,
        "total_taxes": 751.54,
        "total_employer_taxes": 352.17,
        "total_employer_benefits": 0,
        "total_net": 2975.13,
        "stubs": [
            {
                "id": 1,
                "employee_id": 1,
                "employee_name": "Lena Ortiz",
                "hours": 0,
                "gross_pay": 2166.67,
                "federal_tax": 91.67,
                "state_tax": 144.47,
                "state_other_employee": 2.17,
                "ss_tax": 134.33,
                "medicare_tax": 31.42,
                "pretax_deductions": 0,
                "posttax_deductions": 0,
                "garnishments": 0,
                "reimbursements": 0,
                "net_pay": 1762.61,
                "benefits": [],
            },
            {
                "id": 2,
                "employee_id": 2,
                "employee_name": "Jonah Pike",
                "hours": 80,
                "gross_pay": 1560,
                "federal_tax": 60.89,
                "state_tax": 115.69,
                "state_other_employee": 1.56,
                "ss_tax": 96.72,
                "medicare_tax": 22.62,
                "pretax_deductions": 0,
                "posttax_deductions": 0,
                "garnishments": 0,
                "reimbursements": 0,
                "net_pay": 1212.52,
                "benefits": [],
            },
        ],
    },
}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # the package without its browser
            pytest.skip(f"playwright's Chromium is not installed: {exc}")
        yield chromium
        chromium.close()


def _serve(route):
    url = urlsplit(route.request.url)
    if f"{url.scheme}://{url.netloc}" != ORIGIN:
        return route.abort()  # web fonts and the like: not part of the layout
    if url.path == "/":
        return route.fulfill(path=str(ROOT / "index.html"))
    if url.path.startswith("/static/"):
        f = ROOT / "app" / url.path.lstrip("/")
        return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404)
    body = API.get(url.path, [])  # a list the page does not need: empty
    return route.fulfill(
        status=200, content_type="application/json", body=json.dumps(body)
    )


def _open_raw(browser, width, height):
    """A page with the app served, before any navigation: for tests of what
    the first load itself does (the splash)."""
    page = new_page(browser, viewport={"width": width, "height": height})
    page.route("**/*", _serve)
    return page


def _open(browser, width, height, page_hash):
    page = _open_raw(browser, width, height)
    page.goto(f"{ORIGIN}/{page_hash}")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.click("#splash-dismiss")  # a fresh profile: the terms keep the splash up
    return page


def _open_dialog(page, call, ready):
    page.evaluate(f"() => {call}")
    page.wait_for_selector(ready)
    # openModal focuses the dialog's first control on the next tick, which
    # scrolls it into view; measure after that
    page.evaluate("() => new Promise((done) => setTimeout(done, 0))")


HEADER_FIELDS_OUTSIDE_THE_DIALOG = """() => {
    const modal = document.getElementById('modal');
    const body = document.getElementById('modal-body');
    const box = body.getBoundingClientRect();
    const edge = box.right - parseFloat(getComputedStyle(body).paddingRight);
    const out = [];
    for (const group of body.querySelectorAll('.form-grid > .form-group')) {
        const label = ((group.querySelector('label') || {}).textContent || '').trim();
        for (const el of group.querySelectorAll('input, select, textarea')) {
            if (el.offsetParent === null) continue;  // the hidden quick-add form
            const r = el.getBoundingClientRect();
            if (r.right > edge + 0.5) out.push(`${label}: right ${r.right} > ${edge}`);
        }
    }
    if (modal.scrollWidth > modal.clientWidth) {
        out.push(`the dialog scrolls sideways: ${modal.scrollWidth} > ${modal.clientWidth}`);
    }
    return out;
}"""


SALES_FORMS = {
    "invoice": ("InvoicesPage.showForm(1)", "#invoice-form"),
    "estimate": ("EstimatesPage.showForm(1)", "#est-form"),
    "sales receipt": ("SalesReceiptsPage.showForm()", "#sales-receipt-form"),
}


def test_a_long_customer_name_keeps_each_sales_form_header_in_the_dialog(browser):
    problems = {}
    for form, (call, ready) in SALES_FORMS.items():
        page = _open(browser, 1280, 800, "#/invoices")
        try:
            _open_dialog(page, call, ready)
            # the long name is in the list the Customer select sizes itself to
            assert page.evaluate(
                "(n) => [...document.querySelectorAll('#modal-body select option')]"
                ".some(o => o.textContent === n)",
                LONG_NAME,
            ), form
            outside = page.evaluate(HEADER_FIELDS_OUTSIDE_THE_DIALOG)
            if outside:
                problems[form] = outside
        finally:
            page.close()
    assert problems == {}


PAY_RUN_ROWS = """(scrollToEnd) => {
    const wrap = document.querySelector('#modal-body .table-container');
    if (scrollToEnd) wrap.scrollLeft = wrap.scrollWidth;
    const box = wrap.getBoundingClientRect();
    const rows = [...wrap.querySelectorAll('tbody tr')].map(tr => {
        const cell = tr.cells[0];
        const r = cell.getBoundingClientRect();
        // what is painted at the middle of the name: the name, not a
        // column scrolled over it
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const button = tr.querySelector('button');
        return {
            name: cell.textContent.trim(),
            name_in_view: r.left >= box.left - 0.5 && r.right <= box.right + 0.5
                && !!hit && cell.contains(hit),
            label: button.getAttribute('aria-label'),
            text: button.textContent.trim(),
        };
    });
    return { rows, scrolls: wrap.scrollWidth > wrap.clientWidth };
}"""


def test_each_pay_run_row_says_whose_stub_it_is(browser):
    # 1280: the window of the report, where the table now fits the dialog;
    # 900: a narrower one, where it scrolls and the Employee column stays
    for width, height in ((1280, 800), (900, 700)):
        page = _open(browser, width, height, "#/payroll")
        try:
            _open_dialog(page, "PayrollPage.view(1)", "#modal-body table")
            seen = [page.evaluate(PAY_RUN_ROWS, False)]
            if width < 1280:
                assert seen[0]["scrolls"], "the table should be wider than 900"
                seen.append(page.evaluate(PAY_RUN_ROWS, True))  # all the way right
            for view in seen:
                rows = view["rows"]
                assert [r["name"] for r in rows] == ["Lena Ortiz", "Jonah Pike"]
                assert all(r["name_in_view"] for r in rows), (width, rows)
                for r in rows:
                    assert r["text"] == f"Stub PDF — {r['name']}"
                    assert r["label"] == f"Stub PDF — {r['name']}"
        finally:
            page.close()


OPENING_IMPORT_BUTTONS = """() => {
    const shown = (el) => el.checkVisibility() && el.getBoundingClientRect().width > 0;
    const actions = document.querySelector('#modal-body .form-actions');
    return { apply: shown(document.getElementById('opening-import-apply')),
        preview: shown(actions.querySelector('button[type=submit]')),
        buttons: [...actions.querySelectorAll('button')].filter(shown).map(b => b.textContent.trim()) }; }"""


def test_the_opening_import_offers_apply_only_after_preview(browser):
    """Payroll -> Import opening balances opens with Cancel and Preview; Apply
    is drawn `hidden` until a Preview says what there is to write. The
    attribute lost to .btn's display and the button showed, inviting a press
    that did nothing (2.29.0 gate, macbase1 NEW-175)."""
    page = _open(browser, 1280, 800, "#/payroll")
    try:
        _open_dialog(
            page, "PayrollPage.showOpeningImport()", "#modal-body button[type=submit]"
        )
        seen = page.evaluate(OPENING_IMPORT_BUTTONS)
        assert seen == {
            "apply": False,
            "preview": True,
            "buttons": ["Cancel", "Preview"],
        }
        # a preview with something to write shows it
        page.evaluate(
            "() => { document.getElementById('opening-import-apply').hidden = false; }"
        )
        assert page.evaluate(OPENING_IMPORT_BUTTONS)["apply"] is True
    finally:
        page.close()


OPENING_QUARTERS_FIT = """() => {
    const body = document.getElementById('modal-body');
    const box = body.getBoundingClientRect();
    const edge = box.right - parseFloat(getComputedStyle(body).paddingRight);
    const inBody = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.left >= box.left - 0.5 && r.right <= edge + 0.5;
    };
    const net = document.getElementById('emp-opening-1-q2-net');
    const states = [...body.querySelectorAll('[data-opening-state] [data-k]')];
    // the quarter's table wrapper may scroll if a wider row ever appears;
    // at the dialog's own size it has no reason to
    const wraps = [...body.querySelectorAll('[data-opening-quarter] table')]
        .map(t => t.parentElement).filter(w => w !== null);
    return {
        body_scroll: [body.scrollWidth, body.clientWidth],
        q2_net_in_body: inBody(net),
        state_inputs_outside: states.filter(el => !inBody(el)).map(el => el.getAttribute('aria-label')),
        wraps_scroll: wraps.filter(w => w.scrollWidth > w.clientWidth).length,
        quarters: body.querySelectorAll('[data-opening-quarter]').length,
        state_rows: body.querySelectorAll('[data-opening-state]').length,
    }; }"""

TAB_THROUGH_STATE_ROW = """(n) => {
    const row = document.querySelector(`#emp-opening-states-1-q${n} [data-opening-state]`);
    row.querySelector('[data-k=state]').focus();
    return document.activeElement.getAttribute('aria-label'); }"""


def test_the_opening_quarters_fit_the_employee_dialog(browser):
    """Employees -> Details -> Opening YTD: a quarter with a state row was
    852 px wide in a 682 px dialog body, the state table's seven fixed-width
    inputs pushing Q2's Federal withholding through Net past the edge behind
    a sideways scrollbar (2.29.0 gate, skytech W-36). The state inputs now
    size to their cells; the dialog does not scroll sideways, and the state
    row still takes the keyboard in order, State through Remove."""
    page = _open(browser, 1280, 800, "#/employees")
    try:
        _open_dialog(page, "EmployeesPage.viewDetails(1)", "#emp-opening-1-q2-net")
        for theme in ("light", "dark"):
            _theme(page, theme)
            seen = page.evaluate(OPENING_QUARTERS_FIT)
            assert seen["quarters"] == 4 and seen["state_rows"] == 2, (theme, seen)
            assert seen["body_scroll"][0] == seen["body_scroll"][1], (theme, seen)
            assert seen["q2_net_in_body"] is True, (theme, seen)
            assert seen["state_inputs_outside"] == [], (theme, seen)
            assert seen["wraps_scroll"] == 0, (theme, seen)
        # a seven-figure amount shows whole in a state cell: no stepper
        # over its last digit (2.29.0 gate, #813)
        fits = page.evaluate(
            """() => [...document.querySelectorAll('[data-opening-state] input[type=number]')]
                .map(el => { el.value = '1234567.89';
                    return [el.scrollWidth <= el.clientWidth, getComputedStyle(el).appearance]; })"""
        )
        assert fits and all(ok for ok, _ in fits), fits
        assert {look for _, look in fits} == {"textfield"}, fits
        # Tab walks each state row left to right: State, the six amounts,
        # then its Remove
        for n in (1, 2):
            order = [page.evaluate(TAB_THROUGH_STATE_ROW, n)]
            for _ in range(7):
                page.keyboard.press("Tab")
                order.append(
                    page.evaluate(
                        "() => document.activeElement.getAttribute('aria-label')"
                        " || document.activeElement.textContent.trim()"
                    )
                )
            assert order == [
                f"Q{n} State",
                f"Q{n} GA Wages",
                f"Q{n} GA Withholding",
                f"Q{n} GA SUTA wages",
                f"Q{n} GA SUTA tax",
                f"Q{n} GA Other (employee)",
                f"Q{n} GA Other (employer)",
                "Remove",
            ]
    finally:
        page.close()


ONBOARDING_TASKS = (
    "w4",
    "i9_section1",
    "i9_section2",
    "everify",
    "direct_deposit",
    "state_new_hire_report",
    "policy_acknowledgment",
    "emergency_contact",
)
FIRST_ROW = """() => [...document.querySelector('#page-content tbody tr').cells]
    .map(c => c.textContent.trim())"""
COMPLETE_CALLS = """() => [...document.querySelectorAll('#modal-body tbody button')]
    .map(b => b.getAttribute('onclick'))"""


def _onboarding_checklist(saved):
    return {
        "employee_id": 1,
        "employee_name": "Lena Ortiz",
        "complete": 0,
        "total": len(ONBOARDING_TASKS),
        "percent_complete": 0.0,
        "tasks": [
            {
                "id": n if saved else None,
                "employee_id": 1,
                "task_type": task,
                "status": "pending",
                "signed": False,
            }
            for n, task in enumerate(ONBOARDING_TASKS, start=101)
        ],
    }


def test_an_unsaved_onboarding_checklist_is_saved_when_it_is_opened(browser):
    """The read answers an employee's default checklist without saving it,
    its tasks without ids (#662). The list shows it as 0 of 8; View
    Checklist saves it (POST /onboarding/1/seed) and then shows it, every
    Complete button naming a saved task."""
    seeded = []

    def hr(route):
        req = route.request
        path = urlsplit(req.url).path
        if path == "/api/employees":
            body = [{"id": 1, "first_name": "Lena", "last_name": "Ortiz"}]
        elif path == "/api/onboarding/1/seed" and req.method == "POST":
            seeded.append(path)
            body = _onboarding_checklist(saved=True)
        elif path == "/api/onboarding/1" and req.method == "GET":
            body = _onboarding_checklist(saved=bool(seeded))
        else:
            return route.fallback()
        return route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page = _open_raw(browser, 1280, 800)
    page.route("**/api/employees*", hr)
    page.route("**/api/onboarding/**", hr)
    try:
        page.goto(f"{ORIGIN}/#/hr/onboarding")
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.click("#splash-dismiss")
        page.wait_for_selector("#page-content tbody tr")
        assert page.evaluate(FIRST_ROW)[:4] == ["Lena Ortiz", "0", "8", "0%"]
        assert seeded == []  # the list only reads
        page.click("#page-content tbody button:has-text('View Checklist')")
        page.wait_for_selector("#modal-body tbody tr")
        assert seeded == ["/api/onboarding/1/seed"]
        assert page.evaluate(COMPLETE_CALLS) == [
            f"OnboardingPage.completeTask({n}, 1)" for n in range(101, 109)
        ]
    finally:
        page.close()


def test_a_sidebar_link_works_after_a_toolbar_button(browser):
    page = _open(browser, 1280, 800, "#/settings")
    try:
        page.wait_for_selector("#settings-form")
        page.click('#topbar .tb-btn[data-nav="#/"]')  # Home
        page.wait_for_selector('.nav-link.active[data-page="dashboard"]')
        assert page.evaluate("location.hash") == "#/"
        page.click('#sidebar a.nav-link[data-page="settings"]')
        page.wait_for_selector("#settings-form", timeout=5000)
        # and Back goes to the Dashboard the toolbar opened
        page.go_back()
        page.wait_for_selector('.nav-link.active[data-page="dashboard"]')
        assert page.evaluate("location.hash") == "#/"
    finally:
        page.close()


LINE_TABLE_FITS = """() => {
    const wrap = document.querySelector('#modal-body .table-container');
    const box = wrap.getBoundingClientRect();
    const out = [];
    if (wrap.scrollWidth > wrap.clientWidth + 1) {
        out.push(`the line table scrolls sideways: ${wrap.scrollWidth} > ${wrap.clientWidth}`);
    }
    for (const cell of wrap.querySelectorAll('tbody td.col-amount')) {
        const r = cell.getBoundingClientRect();
        if (r.right > box.right + 0.5) out.push(`Amount past the edge: ${r.right} > ${box.right}`);
        if (cell.scrollWidth > cell.clientWidth + 1) out.push(`Amount clipped: ${cell.textContent}`);
    }
    return out;
}"""


def test_the_estimate_line_table_fits_the_dialog_at_1280(browser):
    # 2.18.0 gate, macbase1 NEW-12: a 1,180px floor meant for the job cost
    # grid made the estimate's table scroll sideways, its Amount cut off
    # at "$1,0…".
    page = _open(browser, 1280, 800, "#/estimates")
    try:
        _open_dialog(page, "EstimatesPage.showForm(1)", "#est-form")
        page.fill("#est-lines tr:first-child .line-qty", "1")
        page.fill("#est-lines tr:first-child .line-rate", "12345.67")
        amount = page.inner_text("#est-lines tr:first-child .line-amount")
        assert amount.startswith("$12,345.67"), amount
        assert page.evaluate(LINE_TABLE_FITS) == []
    finally:
        page.close()


PERMIT_NOTE_CLEAR = """() => {
    const note = document.getElementById('permit-format-hint');
    const n = note.getBoundingClientRect();
    const out = [];
    if (!note.textContent.trim()) out.push('the note is empty');
    for (const el of document.querySelectorAll('#modal-body input, #modal-body select, #modal-body textarea')) {
        if (el.offsetParent === null || el.type === 'checkbox') continue;
        // a type-ahead picker's select is out of sight: its box is the field
        if (el.closest('[aria-hidden="true"]')) continue;
        const r = el.getBoundingClientRect();
        if (n.top < r.bottom - 0.5 && n.bottom > r.top + 0.5 && n.left < r.right && n.right > r.left) {
            out.push(`the note covers ${el.name || el.id}`);
        }
        // a click on the box's lower part reaches the box
        const hit = document.elementFromPoint(r.left + r.width / 2, r.bottom - 3);
        if (hit !== el) out.push(`a click low on ${el.name || el.id} lands on ${hit && (hit.id || hit.tagName)}`);
    }
    return out;
}"""


def test_the_permit_format_note_covers_no_box(browser):
    # #194: WA's note is the long one; TX's is short; ZZ has no rule
    problems = {}
    for width, height in ((1280, 800), (600, 800)):
        page = _open(browser, width, height, "#/reseller-permits")
        try:
            _open_dialog(page, "ResellerPermitsPage.showForm()", "#permit-format-hint")
            for state, number in (
                ("WA", ""),
                ("WA", "603-123"),
                ("TX", ""),
                ("ZZ", "1"),
            ):
                page.fill('#modal-body [name="jurisdiction"]', state)
                page.fill('#modal-body [name="permit_number"]', number)
                page.evaluate("() => ResellerPermitsPage._checkFormat()")
                found = page.evaluate(PERMIT_NOTE_CLEAR)
                if found:
                    problems[(width, state, number)] = found
        finally:
            page.close()
    assert problems == {}


IIF_VALID = {
    "valid": True,
    "sections_found": ["TRNS"],
    "record_counts": {"TRNS": 1},
    "warnings": [],
    "errors": [],
    "caps_names": 0,
    "caps_name_examples": [],
}
IIF_RESULT = {
    "classes": 0,
    "accounts": 0,
    "customers": 0,
    "vendors": 0,
    "items": 0,
    "invoices": 0,
    "payments": 0,
    "sales_receipts": 0,
    "estimates": 0,
    "bills": 0,
    "deposits": 0,
    "duplicates_skipped": 0,
    "names_changed": 0,
    "warnings": [],
}
PAYMENT_404 = (
    "PAYMENT P-404: customer 'NOBODY HERE' not found. Import the "
    "customer list first, or correct the NAME in the IIF file."
)


def _answer(body):
    # one parameter: Playwright hands a two-parameter handler the request too
    return lambda route: route.fulfill(json=body)


def _last_toast_and_status(page):
    page.wait_for_selector("#toast-container .toast")
    return page.evaluate("""() => {
        const t = [...document.querySelectorAll('#toast-container .toast')].pop();
        return [t.className, t.textContent, document.getElementById('status-text').textContent];
    }""")


def test_an_import_with_errors_says_so(browser):
    # #197
    cases = {
        "errors": (
            {**IIF_RESULT, "errors": [{"row": 1, "message": PAYMENT_404}]},
            [
                "toast toast-error",
                "Nothing imported, 1 error: see the list below",
                "Interoperability — Import finished with errors",
            ],
        ),
        "clean": (
            {**IIF_RESULT, "payments": 1, "errors": []},
            [
                "toast toast-success",
                "Imported 1 transaction",
                "Interoperability — Import complete",
            ],
        ),
        # the owner's walkthrough file (2.23.1 gate, NEW-106): the toast
        # counts what the panel counts, not "Imported 4 records"
        "walkthrough": (
            {
                **IIF_RESULT,
                "classes": 1,
                "invoices": 1,
                "bills": 1,
                "deposits": 1,
                "transaction_blocks": 3,
                "errors": [],
            },
            [
                "toast toast-success",
                "Imported 1 class and 3 transactions",
                "Interoperability — Import complete",
            ],
        ),
    }
    for name, (result, expected) in cases.items():
        page = _open(browser, 1280, 800, "#/iif")
        try:
            page.route("**/api/iif/validate", lambda r: r.fulfill(json=IIF_VALID))
            page.route("**/api/iif/import", _answer(result))
            page.wait_for_selector("#iif-file-input", state="attached")
            page.set_input_files(
                "#iif-file-input",
                files=[
                    {"name": "p.iif", "mimeType": "text/plain", "buffer": b"!TRNS\n"}
                ],
            )
            page.click("#iif-import-actions button:has-text('Validate')")
            page.wait_for_selector("#iif-import-btn:not([disabled])")
            page.evaluate(
                "() => document.getElementById('toast-container').replaceChildren()"
            )
            page.click("#iif-import-btn")
            assert _last_toast_and_status(page) == expected, name
            if name == "errors":
                assert PAYMENT_404 in page.inner_text("#iif-import-result")
            if name == "walkthrough":
                # the file's blocks, accounted for in one sentence on a line
                # of its own: as a label beside a count it wrapped after
                # "the", the count's "3:" reading as a superscript (NEW-106)
                rows = page.evaluate(
                    """() => [...document.querySelectorAll('#iif-import-result .result-row')]
                        .map(r => [r.textContent.replace(/\\s+/g, ' ').trim(),
                                   Math.round(r.getBoundingClientRect().height),
                                   r.querySelectorAll('span').length])"""
                )
                first, last = rows[0], rows[-1]
                assert first[0] == "Classes 1 imported", rows
                assert last[0] == (
                    "3 transaction blocks in the file: 3 imported, 0 already here, "
                    "0 not imported, 0 failed"
                ), rows
                # one line: as tall as a plain row (the last row has no bottom border)
                assert last[2] == 0 and abs(last[1] - first[1]) <= 1, rows
        finally:
            page.close()


def test_a_report_csv_import_with_errors_says_so(browser):
    # the same page's other import had the same green message (#197)
    page = _open(browser, 1280, 800, "#/iif")
    try:
        page.route(
            "**/api/csv/import/qb-report",
            lambda r: r.fulfill(
                json={
                    "detected": "deposits",
                    "sales_receipts": 0,
                    "deposits": 0,
                    "checks": 0,
                    "duplicates_skipped": 0,
                    "warnings": [],
                    "errors": ["Row 4: account 'Checking 2' not found"],
                }
            ),
        )
        page.wait_for_selector("#qbcsv-file-input", state="attached")
        page.set_input_files(
            "#qbcsv-file-input",
            files=[
                {
                    "name": "deposits.csv",
                    "mimeType": "text/csv",
                    "buffer": b"Type,Date\n",
                }
            ],
        )
        page.evaluate("() => IIFPage.importQbReportCsv()")
        assert _last_toast_and_status(page) == [
            "toast toast-error",
            "Imported 0 records, 1 error: see the list below",
            "Interoperability — Import finished with errors",
        ]
    finally:
        page.close()


# ── #663: a ledger file's problems, answered before anything posts ───────

LEDGER_PROBLEMS = {
    "detected": "ledger",
    "journals": 0,
    "duplicates_skipped": 2,
    "errors": [],
    "warnings": [],
    "ready": 5,
    "file_sha256": "5e" * 32,
    "problems": [
        {
            "kind": "cut_off",
            "key": "file",
            "details": [
                "The lines under Rent come to 1,000.00 debit and 0.00 credit, and "
                "QuickBooks' total says 1,100.00 and 0.00."
            ],
            "text": "The lines under Rent come to 1,000.00 debit and 0.00 credit.",
        },
        {
            "kind": "missing_account",
            "key": "Card Fees",
            "path": "Card Fees",
            "transactions": 2,
            "text": "Card Fees is not in the chart (2 transactions)",
        },
        {
            "kind": "missing_account",
            "key": "Office Expense:Postage",
            "path": "Office Expense:Postage",
            "transactions": 1,
            "text": "Office Expense:Postage is not in the chart (1 transaction)",
        },
    ]
    + [
        {
            "kind": "unbalanced",
            "key": f"Check {1001 + n} · 2024-01-{n + 2:02d} · Harbor Properties",
            "date": f"2024-01-{n + 2:02d}",
            "type": "Check",
            "num": str(1001 + n),
            "name": "Harbor Properties",
            "amount": "1010.00",
            "off": "10.00",
            "rows": [8 + n],
            "text": f"Check {1001 + n} does not balance",
        }
        for n in range(3)
    ],
    "problem_notes": {
        "cut_off": "The file looks cut short or edited. QuickBooks' Save as CSV "
        "stops a report at 32,768 rows without a warning.",
        "missing_account": "These accounts are in the report and not in the chart. "
        "Import the lists first (in QuickBooks: File > Utilities > Export > Lists "
        "to IIF Files), then this report.",
        "unbalanced": "These transactions do not balance in the file. Ticked, one "
        "posts with its difference on 3950 Import Differences.",
    },
    "account_types": [
        {"value": "bank", "label": "Bank", "account_type": "asset"},
        {"value": "equity", "label": "Equity", "account_type": "equity"},
        {"value": "expense", "label": "Expense", "account_type": "expense"},
        {"value": "other_expense", "label": "Other Expense", "account_type": "expense"},
    ],
}
LEDGER_ANSWERED = {
    "detected": "ledger",
    "journals": 7,
    "duplicates_skipped": 2,
    "errors": [],
    "warnings": [],
    "accepted": {"unbalanced": 1, "accounts": ["Card Fees"], "cut_off": True},
    "left_out": {"unbalanced": 2, "missing_account": 1},
    "differences_account": "3950 Import Differences",
}

PROBLEM_LIST = """() => {
    const box = document.getElementById('qbr');
    const group = (kind) => box.querySelector(`[data-kind="${kind}"]`);
    const picks = (kind) => [...box.querySelectorAll(`input.qbr-pick[data-kind="${kind}"]`)];
    return {
        groups: [...box.querySelectorAll('section.qbr-group h5')].map(h => h.textContent.trim()),
        notes: ['cut_off', 'missing_account', 'unbalanced'].map(k => group(k).querySelector('.qbr-note').textContent.trim().slice(0, 40)),
        ticked: Object.fromEntries(['cut_off', 'missing_account', 'unbalanced'].map(k => [k, picks(k).map(b => b.checked)])),
        disabled: picks('missing_account').map(b => b.disabled || b.getAttribute('aria-disabled') === 'true'),
        counts: [...box.querySelectorAll('.qbr-count')].map(c => c.textContent),
        importable: !document.getElementById('qbr-import').disabled,
        hint: document.getElementById('qbr-hint').textContent,
        scrolls: [...box.querySelectorAll('.qbr-scroll')].map(s => getComputedStyle(s).overflowY),
    };
}"""


def _problem_page(browser, sent, problems=None, answered=None):
    def answer(route):
        sent.append(route.request.post_data_buffer or b"")
        first = problems or LEDGER_PROBLEMS
        route.fulfill(json=first if len(sent) == 1 else answered or LEDGER_ANSWERED)

    page = _open(browser, 1280, 800, "#/iif")
    page.route("**/api/csv/import/qb-report", answer)
    page.wait_for_selector("#qbcsv-file-input", state="attached")
    page.set_input_files(
        "#qbcsv-file-input",
        files=[
            {
                "name": "Transaction Detail by Account.csv",
                "mimeType": "text/csv",
                "buffer": b"Type,Date,Num\r\n",
            }
        ],
    )
    page.evaluate("() => IIFPage.importQbReportCsv()")
    page.wait_for_selector("#qbr")
    return page


def _form_field(body: bytes, name: str) -> str:
    import re

    m = re.search(
        rb'name="'
        + name.encode()
        + rb'"(?:; filename="[^"]*")?\r\n(?:[^\r\n]+\r\n)*\r\n(.*?)\r\n--',
        body,
        re.S,
    )
    return m.group(1).decode() if m else None


def test_a_ledger_files_problems_are_listed_and_answered(browser):
    sent = []
    page = _problem_page(browser, sent)
    try:
        seen = page.evaluate(PROBLEM_LIST)
        assert seen["groups"] == [
            "The file may be cut off",
            "2 accounts not in the chart",
            "3 transactions that do not balance",
        ]
        assert seen["notes"] == [
            "The file looks cut short or edited. Quic",
            "These accounts are in the report and not",
            "These transactions do not balance in the",
        ]
        assert seen["scrolls"] == ["auto", "auto"]
        # nothing ticked; an account cannot be without a type; the notice
        # unticked holds Import, and says why
        assert seen["ticked"] == {
            "cut_off": [False],
            "missing_account": [False, False],
            "unbalanced": [False, False, False],
        }
        assert seen["disabled"] == [True, True]
        assert seen["counts"] == ["0 of 2 ticked", "0 of 3 ticked"]
        assert not seen["importable"] and "Import what is there" in seen["hint"]
        assert _last_toast_and_status(page) == [
            "toast toast-warning",
            "Nothing imported yet: 6 problems to decide below",
            "Interoperability — Choose what to import",
        ]
        # every box is named, the type picker too
        named = page.get_by_role("checkbox", name="Import what is there")
        assert named.count() == 1
        account = page.get_by_role("checkbox", name="Make the account Card Fees")
        page.get_by_role("combobox", name="Type for Card Fees").select_option("expense")
        assert account.is_enabled() and account.is_checked()
        # Select all and none, per group: the account with no type stays out
        unbalanced = page.locator("#qbr [data-kind=unbalanced]")
        unbalanced.get_by_role("button", name="Select all").click()
        page.locator("#qbr [data-kind=missing_account]").get_by_role(
            "button", name="Select all"
        ).click()
        seen = page.evaluate(PROBLEM_LIST)
        assert seen["ticked"]["unbalanced"] == [True, True, True]
        assert seen["ticked"]["missing_account"] == [True, False]
        assert seen["counts"] == ["1 of 2 ticked", "3 of 3 ticked"]
        unbalanced.get_by_role("button", name="Select none").click()
        assert page.evaluate(PROBLEM_LIST)["ticked"]["unbalanced"] == [False] * 3
        page.get_by_role(
            "checkbox",
            name="Import Check 1002 of Jan 3, 2024, Harbor Properties, off by $10.00 more in debits",
        ).check()
        page.get_by_role("checkbox", name="Import what is there").check()
        assert page.evaluate(PROBLEM_LIST)["importable"]
        page.evaluate(
            "() => document.getElementById('toast-container').replaceChildren()"
        )
        page.click("#qbr-import")
        page.wait_for_selector("#qbcsv-import-result .iif-results:not(.qbr)")
        # focus goes to what was imported, not to the page (#663)
        assert page.evaluate("() => document.activeElement.id") == "qbcsv-result-title"
        # the same file again, with the answers
        assert len(sent) == 2
        assert _form_field(sent[0], "answers") is None
        assert _form_field(sent[1], "file") == _form_field(sent[0], "file")
        assert json.loads(_form_field(sent[1], "answers")) == {
            "file_sha256": "5e" * 32,
            "unbalanced": ["Check 1002 · 2024-01-03 · Harbor Properties"],
            "replaces": [],
            "accounts": {"Card Fees": "expense"},
            "cut_off": True,
        }
        rows = page.evaluate(
            """() => [...document.querySelectorAll('#qbcsv-import-result .result-row')]
                .map(r => r.children.length
                    ? [...r.children].map(c => c.textContent.trim()).join(' | ')
                    : r.textContent.trim())"""
        )
        assert rows == [
            "Detected report | Transaction Detail by Account",
            "Journal entries | 7 imported",
            "Accepted, the difference on 3950 Import Differences | 1",
            "Accounts made | 1",
            "Card Fees",
            "The file looked cut off; what was in it was imported.",
            "Left out: did not balance | 2",
            "Left out: an account not in the chart | 1",
            "Duplicates skipped | 2",
        ]
        assert _last_toast_and_status(page) == [
            "toast toast-success",
            "Imported 7 records",
            "Interoperability — Import complete",
        ]
    finally:
        page.close()


FOCUSED = """() => {
    const el = document.activeElement;
    return el.getAttribute('aria-label') || el.textContent.trim();
}"""


def test_a_long_problem_list_is_one_stop_for_the_keyboard(browser):
    """Thousands of transactions must not be thousands of Tab presses on
    the way to Import: Tab enters the list and leaves it, the arrow keys,
    Home and End move in it, Space ticks."""
    sent = []
    page = _problem_page(browser, sent)
    try:
        # Off by says which side is over (#663), here the debits
        assert page.inner_text(
            "#qbr [data-kind=unbalanced] tbody tr td:last-child"
        ) == ("$10.00 more in debits")
        group = page.locator("#qbr [data-kind=unbalanced]")
        group.get_by_role("button", name="Select none").focus()
        page.keyboard.press("Tab")
        first = "Import Check 1001 of Jan 2, 2024, Harbor Properties, off by $10.00 more in debits"
        assert page.evaluate(FOCUSED) == first
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Space")
        third = "Import Check 1003 of Jan 4, 2024, Harbor Properties, off by $10.00 more in debits"
        assert page.evaluate(FOCUSED) == third
        assert page.evaluate(PROBLEM_LIST)["ticked"]["unbalanced"] == [
            False,
            False,
            True,
        ]
        page.keyboard.press("Home")
        assert page.evaluate(FOCUSED) == first
        page.keyboard.press("End")
        assert page.evaluate(FOCUSED) == third
        page.keyboard.press("ArrowUp")
        # out of the list in one press (Import waits on the file's notice),
        # and back in at the row last used
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Cancel"
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(FOCUSED) == (
            "Import Check 1002 of Jan 3, 2024, Harbor Properties, off by $10.00 more in debits"
        )
    finally:
        page.close()


def test_the_arrow_keys_draw_the_ring_after_a_click_in_the_list(browser):
    """2.31.2 gate, NEW-190: on the Mac, a click on a box and then the
    arrow keys moved the focus with no ring, WebKit keeping :focus-visible
    off for a focus() after a pointer. A move by the keys marks the list, and
    the marked list's focused box draws the keyboard's ring; a pointer in
    the list clears the mark."""
    sent = []
    page = _problem_page(browser, sent)
    try:
        boxes = page.locator("#qbr [data-kind=unbalanced] tbody input.qbr-pick")
        boxes.nth(0).click()
        page.keyboard.press("ArrowDown")
        state = page.evaluate("""() => {
                const list = document.querySelector('#qbr [data-kind=unbalanced] .qbr-roving');
                const box = document.activeElement;
                const css = getComputedStyle(box);
                const ring = getComputedStyle(document.documentElement).getPropertyValue('--focus-ring').trim();
                return {keys: list.dataset.keys || null, style: css.outlineStyle,
                        width: css.outlineWidth, ring: ring, color: css.outlineColor,
                        second: box === document.querySelectorAll('#qbr [data-kind=unbalanced] tbody input.qbr-pick')[1]};
            }""")
        assert state["second"]
        assert state["keys"] == "1"
        assert state["style"] == "solid" and state["width"] == "2px"
        boxes.nth(2).dispatch_event("pointerdown")
        assert (
            page.evaluate(
                "() => document.querySelector('#qbr [data-kind=unbalanced] .qbr-roving').dataset.keys || null"
            )
            is None
        )
    finally:
        page.close()


# A file that has whole two transactions the books hold as partial entries
# (#663): one ticked replaces its entry, one in a reconciliation cannot.
LEDGER_REPLACES = {
    "detected": "ledger",
    "journals": 0,
    "duplicates_skipped": 7,
    "errors": [],
    "warnings": [],
    "ready": 0,
    "file_sha256": "ab" * 32,
    "problems": [
        {
            "kind": "closed_period",
            "key": "closed_period",
            "closing_date": "2023-12-31",
            "transactions": 2,
            "dates": ["2023-12-04", "2023-12-29"],
            "text": "2 transactions dated on or before the closing date, "
            "12/31/2023: 12/04/2023 and 12/29/2023.",
        },
        {
            "kind": "replaces",
            "key": "General Journal 31 · 2024-01-31",
            "date": "2024-01-31",
            "type": "General Journal",
            "num": "31",
            "name": "Smith",
            "names": ["Smith"],
            "amount": "100.00",
            "off": "0.00",
            "rows": [4, 7],
            "journals": 1,
            "partial": [
                {
                    "id": 2,
                    "date": "2024-01-31",
                    "num": "31",
                    "name": "",
                    "amount": "100.00",
                    "difference": "-100.00",
                    "reconciled": False,
                }
            ],
            "voidable": True,
            "text": "General Journal 31 of 01/31/2024 is in the books as a partial "
            "entry of 100.00",
        },
        {
            "kind": "replaces",
            "key": "Check 101 · 2024-01-05",
            "date": "2024-01-05",
            "type": "Check",
            "num": "101",
            "name": "Acme",
            "names": ["Acme"],
            "amount": "60.00",
            "off": "0.00",
            "rows": [3, 10],
            "journals": 1,
            "partial": [
                {
                    "id": 1,
                    "date": "2024-01-05",
                    "num": "101",
                    "name": "Acme",
                    "amount": "60.00",
                    # its rent line alone: 60.00 more in debits, so the
                    # import credited Import Differences 60.00
                    "difference": "60.00",
                    "reconciled": True,
                }
            ],
            "voidable": False,
            "text": "Check 101 of 01/05/2024 is in the books as a partial entry",
        },
    ],
    "problem_notes": {
        "closed_period": "The books are closed through 12/31/2023, so a "
        "transaction dated on or before it is left out.",
        "replaces": "An earlier file had these transactions in part. Ticked, the "
        "partial entry is voided and this file's posts in its place.",
    },
}
REPLACES_ANSWERED = {
    "detected": "ledger",
    "journals": 1,
    "duplicates_skipped": 7,
    "errors": [],
    "warnings": [],
    "accepted": {"unbalanced": 0, "accounts": [], "cut_off": False, "replaced": 1},
    "left_out": {
        "unbalanced": 0,
        "missing_account": 0,
        "replaces": 1,
        "closed_period": 2,
    },
}


def test_a_transaction_that_replaces_a_partial_entry_is_listed_beside_it(browser):
    sent = []
    page = _problem_page(browser, sent, LEDGER_REPLACES, REPLACES_ANSWERED)
    try:
        # the closing date's notice comes first, with no box to tick
        closed = page.locator("#qbr [data-kind=closed_period]")
        assert closed.locator("h5").inner_text() == (
            "2 transactions in a closed period, left out"
        )
        assert closed.locator("input").count() == 0
        assert "12/04/2023 and 12/29/2023" in closed.inner_text()
        group = page.locator("#qbr [data-kind=replaces]")
        assert group.locator("h5").inner_text() == (
            "2 transactions that replace a partial entry in the books"
        )
        cells = page.evaluate(
            """() => [...document.querySelectorAll('#qbr [data-kind=replaces] tbody tr')]
                .map(r => [...r.cells].slice(1).map(c => c.innerText.trim()))"""
        )
        assert cells == [
            [
                "Jan 31, 2024",
                "General Journal",
                "31",
                "Smith",
                "$100.00",
                "Jan 31, 2024 · $100.00",
                "$100.00 debit",
            ],
            [
                "Jan 5, 2024",
                "Check",
                "101",
                "Acme",
                "$60.00",
                "Jan 5, 2024 · $60.00\nin a completed reconciliation",
                "$60.00 credit",
            ],
        ]
        ok = page.get_by_role(
            "checkbox",
            name="Replace the partial entry: General Journal 31 of Jan 31, 2024, "
            "Smith, $100.00 in this file; in the books Jan 31, 2024 · $100.00, "
            "with a $100.00 debit on Import Differences",
        )
        # the column says what was posted to Import Differences, on which
        # side: the file's 100.00 more in credits was a debit there
        header = group.locator("thead th").last.text_content().strip()
        assert header == "On Import Differences"
        held = group.locator("input.qbr-pick").nth(1)
        assert held.get_attribute("aria-disabled") == "true"
        # the held one takes no tick, by click, by Space or by Select all
        # (forced: Playwright waits for an aria-disabled box to be enabled)
        held.click(force=True)
        assert not held.is_checked()
        group.get_by_role("button", name="Select all").click()
        assert ok.is_checked() and not held.is_checked()
        held.focus()
        page.keyboard.press("Space")
        assert not held.is_checked()
        assert page.locator("#qbr [data-count=replaces]").inner_text() == (
            "1 of 2 ticked"
        )
        page.click("#qbr-import")
        page.wait_for_selector("#qbcsv-import-result .iif-results:not(.qbr)")
        assert json.loads(_form_field(sent[1], "answers")) == {
            "file_sha256": "ab" * 32,
            "unbalanced": [],
            "replaces": ["General Journal 31 · 2024-01-31"],
            "accounts": {},
            "cut_off": False,
        }
        rows = page.evaluate(
            """() => [...document.querySelectorAll('#qbcsv-import-result .result-row')]
                .map(r => [...r.children].map(c => c.textContent.trim()).join(' | '))"""
        )
        assert rows == [
            "Detected report | Transaction Detail by Account",
            "Journal entries | 1 imported",
            "Replaced: partial entries voided, this file's posted in their place | 1",
            "Left out: a partial entry in the books kept | 1",
            "Left out: in a closed period | 2",
            "Duplicates skipped | 7",
        ]
    finally:
        page.close()


# Accounts the chart lacks (#663): a sub-account offered its parent's types
# only, starting on the parent's, and one under an inactive parent none.
LEDGER_ACCOUNTS = {
    "detected": "ledger",
    "journals": 0,
    "duplicates_skipped": 0,
    "errors": [],
    "warnings": [],
    "ready": 3,
    "file_sha256": "cd" * 32,
    "problems": [
        {
            "kind": "missing_account",
            "key": "Office Supplies:Toner",
            "path": "Office Supplies:Toner",
            "transactions": 1,
            "types": ["expense"],
            "type": "expense",
            "why": None,
            "text": "Office Supplies:Toner is not in the chart (1 transaction)",
        },
        {
            "kind": "missing_account",
            "key": "Checking:Petty Cash",
            "path": "Checking:Petty Cash",
            "transactions": 2,
            "types": ["bank", "other_current_asset"],
            "type": "bank",
            "why": None,
            "text": "Checking:Petty Cash is not in the chart (2 transactions)",
        },
        {
            "kind": "missing_account",
            "key": "Old Expenses:Postage",
            "path": "Old Expenses:Postage",
            "transactions": 1,
            "types": [],
            "type": None,
            "why": "6990 Old Expenses is inactive and cannot take sub-accounts. "
            "Reactivate it, or choose another parent.",
            "text": "Old Expenses:Postage is not in the chart (1 transaction)",
        },
    ],
    "problem_notes": {"missing_account": "These accounts are not in the chart."},
    "account_types": LEDGER_PROBLEMS["account_types"]
    + [
        {
            "value": "other_current_asset",
            "label": "Other Current Asset",
            "account_type": "asset",
        },
        {"value": "income", "label": "Income", "account_type": "income"},
    ],
}


def test_a_sub_account_is_offered_the_types_its_parent_takes(browser):
    sent = []
    page = _problem_page(browser, sent, LEDGER_ACCOUNTS)
    try:
        choices = page.evaluate(
            """() => [...document.querySelectorAll('#qbr select.qbr-type')].map(s => ({
                value: s.value,
                disabled: s.disabled,
                offered: [...s.options].filter(o => o.value).map(o => o.value),
            }))"""
        )
        assert choices == [
            {"value": "expense", "disabled": False, "offered": ["expense"]},
            {
                "value": "bank",
                "disabled": False,
                "offered": ["bank", "other_current_asset"],
            },
            {"value": "", "disabled": True, "offered": []},
        ]
        boxes = page.locator("#qbr input.qbr-pick[data-kind=missing_account]")
        # a type to start on is not a tick: nothing is ticked to start with
        assert [boxes.nth(i).is_checked() for i in range(3)] == [False] * 3
        assert [boxes.nth(i).is_enabled() for i in range(3)] == [True, True, False]
        assert "is inactive and cannot take sub-accounts" in page.inner_text(
            "#qbr [data-kind=missing_account] tbody tr:nth-child(3)"
        )
        page.get_by_role(
            "checkbox", name="Make the account Office Supplies:Toner"
        ).check()
        page.click("#qbr-import")
        page.wait_for_selector("#qbcsv-import-result .iif-results:not(.qbr)")
        assert json.loads(_form_field(sent[1], "answers"))["accounts"] == {
            "Office Supplies:Toner": "expense"
        }
    finally:
        page.close()


def test_a_long_problem_list_draws_without_work_per_row_on_the_page(browser):
    """15,000 rows took 105 s to draw and 134 ms an arrow key (#663). The
    causes, each now absent: the app's field namer read each new box's
    `labels` (a walk of the whole document) before its aria-label; every
    date and amount built an Intl formatter; a box went from static to
    relative positioning as it took focus (the focus ring's rule), which
    relaid the whole table out; and each move rewrote every row's
    tabIndex."""
    import copy

    big = copy.deepcopy(LEDGER_PROBLEMS)
    big["problems"] = [p for p in big["problems"] if p["kind"] != "unbalanced"] + [
        {
            "kind": "unbalanced",
            "key": f"Check {1001 + i} · 2024-01-02 · Northwind",
            "date": "2024-01-02",
            "type": "Check",
            "num": str(1001 + i),
            "name": "Northwind",
            "amount": "1010.00",
            "off": "10.00",
            "rows": [8 + i],
            "text": f"Check {1001 + i} does not balance",
        }
        for i in range(200)
    ]
    page = _open(browser, 1280, 800, "#/iif")
    page.route("**/api/csv/import/qb-report", lambda route: route.fulfill(json=big))
    page.wait_for_selector("#qbcsv-file-input", state="attached")
    page.set_input_files(
        "#qbcsv-file-input",
        files=[{"name": "t.csv", "mimeType": "text/csv", "buffer": b"x\r\n"}],
    )
    try:
        counts = page.evaluate("""async () => {
                const counts = { labels: 0, numberFormats: 0, dateStrings: 0 };
                const labels = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'labels');
                Object.defineProperty(HTMLInputElement.prototype, 'labels', {
                    configurable: true,
                    get() {
                        // a box named by its aria-label (the notice's is
                        // named by its own <label>, and read as before)
                        if (this.classList.contains('qbr-pick') && this.hasAttribute('aria-label')) counts.labels++;
                        return labels.get.call(this);
                    },
                });
                const NumberFormat = Intl.NumberFormat;
                Intl.NumberFormat = function (...a) { counts.numberFormats++; return new NumberFormat(...a); };
                const toDate = Date.prototype.toLocaleDateString;
                Date.prototype.toLocaleDateString = function (...a) { counts.dateStrings++; return toDate.apply(this, a); };
                await IIFPage.importQbReportCsv();
                await new Promise(r => setTimeout(r, 0));  // the namer's turn
                return counts;
            }""")
        assert counts == {"labels": 0, "numberFormats": 0, "dateStrings": 0}
        assert page.evaluate("""() => getComputedStyle(document.querySelector(
                '#qbr input.qbr-pick[data-kind=unbalanced]')).position""") == "relative"
        group = page.locator("#qbr [data-kind=unbalanced]")
        group.get_by_role("button", name="Select none").focus()
        page.keyboard.press("Tab")
        page.evaluate("""() => {
                window.__touched = new Set();
                new MutationObserver(records => records.forEach(r => window.__touched.add(r.target)))
                    .observe(document.getElementById('qbr'), { attributes: true, attributeFilter: ['tabindex'], subtree: true });
            }""")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("End")
        touched = page.evaluate(
            """() => new Promise(r => setTimeout(() => r(
                [...window.__touched].map(b => b.closest('tr').sectionRowIndex)), 0))"""
        )
        assert sorted(touched) == [0, 1, 199]
        assert page.evaluate("() => document.activeElement.tabIndex") == 0
    finally:
        page.close()


def test_the_missing_accounts_are_one_stop_for_the_keyboard(browser):
    """As the long lists are (#663): Tab enters the accounts at the row in
    use, goes on to its type and then out; the arrow keys move between the
    rows, a box with no type among them."""
    sent = []
    page = _problem_page(browser, sent, LEDGER_ACCOUNTS)
    try:
        group = page.locator("#qbr [data-kind=missing_account]")
        group.get_by_role("button", name="Select none").focus()
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Make the account Office Supplies:Toner"
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Type for Office Supplies:Toner"
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Import"  # out of the list
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(FOCUSED) == "Type for Office Supplies:Toner"
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        # the third row's box cannot be ticked, and is still a stop
        assert page.evaluate(FOCUSED) == "Make the account Old Expenses:Postage"
        page.keyboard.press("Space")
        assert not page.locator(
            "#qbr input[data-key='Old Expenses:Postage']"
        ).is_checked()
        page.keyboard.press("ArrowUp")
        assert page.evaluate(FOCUSED) == "Make the account Checking:Petty Cash"
        page.keyboard.press("Space")
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Type for Checking:Petty Cash"
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUSED) == "Import"
        assert group.locator("[data-count=missing_account]").inner_text() == (
            "1 of 3 ticked"
        )
    finally:
        page.close()


def test_focus_stays_on_import_when_the_answers_are_refused(browser):
    """The answers refused (the file changed, a type the chart's rules
    refuse): the list stays, and so does focus, on Import."""
    sent = []

    def answer(route):
        sent.append(route.request.post_data_buffer or b"")
        if len(sent) == 1:
            route.fulfill(json=LEDGER_ACCOUNTS)
        else:
            route.fulfill(
                status=400,
                json={
                    "detail": "The file changed since the list was made; upload it again."
                },
            )

    page = _open(browser, 1280, 800, "#/iif")
    page.route("**/api/csv/import/qb-report", answer)
    page.wait_for_selector("#qbcsv-file-input", state="attached")
    page.set_input_files(
        "#qbcsv-file-input",
        files=[{"name": "t.csv", "mimeType": "text/csv", "buffer": b"x\r\n"}],
    )
    try:
        page.evaluate("() => IIFPage.importQbReportCsv()")
        page.wait_for_selector("#qbr")
        page.click("#qbr-import")
        # the click itself focuses Import, while the answers are still out
        # and the button disabled: wait for the refusal to come back (2.31.2
        # gate: on LabStudio's slower CPU the check ran before it did)
        page.wait_for_function(
            "() => document.body.innerText.includes('The file changed since the list was made')"
            " && !document.getElementById('qbr-import').disabled"
            " && document.activeElement.id === 'qbr-import'"
        )
        assert page.is_visible("#qbr") and page.is_enabled("#qbr-import")
        assert len(sent) == 2
    finally:
        page.close()


def test_the_problem_list_reads_at_aa_in_both_themes(browser):
    from tests.test_theme_contrast import _theme, below_threshold, sweep_of

    sent = []
    page = _problem_page(browser, sent)
    try:
        page.get_by_role("combobox", name="Type for Card Fees").select_option("expense")
        sweeps = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            sweeps[(theme, "#/iif problems")] = page.evaluate(
                sweep_of("document.getElementById('qbr')")
            )
        assert all(sweeps.values()), sweeps
        assert below_threshold(sweeps) == []
        # Cancel takes the list away; nothing was sent again, and focus goes
        # back to the button that began it, not to the page (#663)
        page.get_by_role("button", name="Cancel").click()
        assert page.inner_html("#qbcsv-import-result") == ""
        assert len(sent) == 1
        assert page.evaluate(FOCUSED) == "Import Report CSV"
    finally:
        page.close()


# ── #213: P&L by Class, account by account, and the way in ───────────────

BY_CLASS = {
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "classes": [
        {
            "class_id": 1,
            "class_name": "Uncategorized",
            "income": 50,
            "cogs": 0,
            "gross_profit": 50,
            "expenses": 50,
            "net_income": 0,
        },
        {
            "class_id": 7,
            "class_name": "Side Gig",
            "income": 300,
            "cogs": 100,
            "gross_profit": 200,
            "expenses": 300,
            "net_income": -100,
        },
    ],
    "accounts": {
        "income": [
            {
                "account_id": 40,
                "account_number": "4000",
                "account_name": "Service Income",
                "amounts": [50, 300],
                "total": 350,
            }
        ],
        "cogs": [
            {
                "account_id": 53,
                "account_number": "5300",
                "account_name": "Subcontractor Costs",
                "amounts": [0, 100],
                "total": 100,
            }
        ],
        "expenses": [
            {
                "account_id": 60,
                "account_number": "6000",
                "account_name": "Advertising",
                "amounts": [50, 300],
                "total": 350,
            }
        ],
    },
    "total_income": 350,
    "total_cogs": 100,
    "total_gross_profit": 250,
    "total_expenses": 350,
    "total_net_income": -100,
}

DRILL = {
    "account": {"id": 40, "number": "4000", "name": "Service Income", "type": "income"},
    "class_id": 7,
    "class_name": "Side Gig",
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "opening_balance": 0,
    "period_debit": 0,
    "period_credit": 300,
    "period_net": 300,
    "entries": [
        {
            "date": "2026-07-10",
            "reference": "",
            "description": "side gig",
            "source_type": "journal",
            "source_id": 1,
            "source_link": None,
            "debit": 0,
            "credit": 300,
            "running_balance": 300,
        }
    ],
}

CLASS_PL = {
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "class_id": 7,
    "class_name": "Side Gig",
    "income": [
        {
            "account_id": 40,
            "account_name": "Service Income",
            "account_number": "4000",
            "amount": 300,
        }
    ],
    "cogs": [
        {
            "account_id": 53,
            "account_name": "Subcontractor Costs",
            "account_number": "5300",
            "amount": 100,
        }
    ],
    "expenses": [
        {
            "account_id": 60,
            "account_name": "Advertising",
            "account_number": "6000",
            "amount": 300,
        }
    ],
    "total_income": 300,
    "total_cogs": 100,
    "gross_profit": 200,
    "total_expenses": 300,
    "net_income": -100,
}


def test_p_and_l_by_class_opens_the_transactions_behind_an_amount(browser):
    asked = []

    def _record(body):
        def handler(route):
            asked.append(route.request.url)
            route.fulfill(json=body)

        return handler

    page = _open(browser, 1280, 800, "#/reports")
    try:
        page.route("**/api/reports/profit-loss-by-class*", _answer(BY_CLASS))
        page.route("**/api/reports/account-transactions*", _record(DRILL))
        page.route("**/api/reports/profit-loss?*", _record(CLASS_PL))
        _open_dialog(page, "ReportsPage.profitLossByClass()", "#report-content table")

        heads = page.eval_on_selector_all(
            "#report-content thead th", "els => els.map(e => e.textContent.trim())"
        )
        assert heads == ["Account", "Uncategorized", "Side Gig", "Total"]
        assert "4000 - Service Income" in page.inner_text("#report-content tbody")
        # an empty cell is not a link; a filled one is
        assert (
            page.locator("#report-content tbody tr:has-text('Subcontractor') a").count()
            == 1
        )

        page.click(
            "#report-content tbody tr:has-text('Service Income') a:has-text('$300.00')"
        )
        page.wait_for_selector("#drilldown-body table")
        assert "class_id=7" in asked[-1] and "account_id=40" in asked[-1]
        assert "Side Gig" in page.inner_text("#modal-title")
        assert "side gig" in page.inner_text("#drilldown-body")

        # back to the report, on the dates the drill-down was opened with:
        # through the browser's history since R7, so the report comes back
        # as it was left (its period preset, the same dates), with the
        # drill-down's own address behind it
        q = parse_qs(urlsplit(asked[-1]).query)
        start, end = q["start_date"][0], q["end_date"][0]
        assert page.evaluate("location.hash").startswith(
            "#/reports/account-transactions?account_id=40"
        )
        page.click("#modal button:has-text('Back to P&L by Class')")
        page.wait_for_selector("#report-content table")
        assert page.input_value("#report-period-select") == "this_year_to_date"
        assert page.input_value("#report-custom-start") == start
        assert page.input_value("#report-custom-end") == end
        assert page.evaluate("location.hash").startswith(
            "#/reports/profit-loss-by-class?"
        )

        page.click("#report-content thead a:has-text('Side Gig')")
        page.wait_for_selector("#class-pl-body table")
        assert "class_id=7" in asked[-1]
        assert "Side Gig" in page.inner_text("#modal-title")
        page.click("#class-pl-body a:has-text('Service Income')")
        page.wait_for_selector("#drilldown-body table")
        assert "class_id=7" in asked[-1]
    finally:
        page.close()


# ── #220: the splash once per version, not every load ────────────────────


def _splash_state(page):
    return page.evaluate("""() => ({
            shown: !document.getElementById('splash').classList.contains('hidden'),
            notes: !document.getElementById('splash-whatsnew').hidden,
            terms: !document.getElementById('splash-terms').hidden,
            ack: localStorage.getItem('slowbooks.license_ack'),
            seen: localStorage.getItem('slowbooks.whatsnew_ack'),
        })""")


def test_the_splash_opens_once_per_version(browser):
    # #220: it opened on every load. The terms were gated on their version;
    # the release notes weren't. Now both are.
    notes = {"9.9.9": {"title": "A test release", "items": ["One thing"]}}
    page = _open_raw(browser, 1280, 800)
    try:
        page.route("**/static/whats-new.json", _answer(notes))
        page.route("**/health", _answer({"status": "ok", "version": "9.9.9"}))
        page.goto(f"{ORIGIN}/")
        page.wait_for_function(
            "() => !document.getElementById('splash-whatsnew').hidden"
        )
        first = _splash_state(page)
        assert first["shown"] and first["notes"] and first["terms"], first
        page.click("#splash-dismiss")
        after_ok = _splash_state(page)
        assert (
            not after_ok["shown"] and after_ok["ack"] and after_ok["seen"] == "9.9.9"
        ), after_ok

        # a reload: nothing new, so no splash
        page.goto(f"{ORIGIN}/")
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.wait_for_timeout(300)
        reloaded = _splash_state(page)
        assert not reloaded["shown"], reloaded

        # About still shows it in full, notes included
        page.evaluate("() => App.showAbout()")
        about = _splash_state(page)
        assert about["shown"] and about["notes"], about
        page.click("#splash-dismiss")

        # a new version: its notes open the splash again, the terms stay acknowledged
        page.route(
            "**/static/whats-new.json",
            _answer({"9.9.10": {"title": "Next", "items": ["Another"]}}),
        )
        page.route("**/health", _answer({"status": "ok", "version": "9.9.10"}))
        page.goto(f"{ORIGIN}/")
        page.wait_for_function(
            "() => !document.getElementById('splash').classList.contains('hidden')"
        )
        again = _splash_state(page)
        assert again["notes"] and not again["terms"], again
        assert "9.9.10" in page.inner_text("#splash-whatsnew-title")
        page.click("#splash-dismiss")
        assert _splash_state(page)["seen"] == "9.9.10"
    finally:
        page.close()


# ── #224: an account in the General Ledger opens its register ────────────

GENERAL_LEDGER = {
    "start_date": "2026-07-01",
    "end_date": "2026-07-31",
    "accounts": [
        {
            "account_id": 40,
            "account_number": "4000",
            "account_name": "Service Income",
            "account_type": "income",
            "normal_balance": "credit",
            "opening_balance": 0,
            "entries": [
                {
                    "date": "2026-07-10",
                    "description": "side gig",
                    "reference": "",
                    "debit": 0,
                    "credit": 300,
                    "running_balance": 300,
                    "source_type": "journal",
                    "class_name": "Side Gig",
                },
            ],
            "total_debit": 0,
            "total_credit": 300,
            "closing_balance": 300,
        },
        {
            "account_id": 60,
            "account_number": "6000",
            "account_name": "Advertising",
            "account_type": "expense",
            "normal_balance": "debit",
            "opening_balance": 0,
            "entries": [],
            "total_debit": 0,
            "total_credit": 0,
            "closing_balance": 0,
        },
    ],
}


def test_a_general_ledger_account_opens_its_register(browser):
    # #224: the headings were plain text; the P&L's names were links.
    asked = []

    def _record(body):
        def handler(route):
            asked.append(route.request.url)
            route.fulfill(json=body)

        return handler

    page = _open(browser, 1280, 800, "#/reports")
    try:
        page.route("**/api/reports/general-ledger?*", _answer(GENERAL_LEDGER))
        page.route(
            "**/api/reports/account-transactions*",
            _record({**DRILL, "class_id": None, "class_name": None}),
        )
        _open_dialog(page, "ReportsPage.generalLedger()", "#report-content table")

        links = page.locator("#report-content h3 a")
        assert links.count() == 2
        assert links.first.inner_text() == "Service Income"
        assert "4000 — Service Income" in page.inner_text("#report-content h3 >> nth=0")
        start = page.input_value("#report-custom-start")

        links.first.click()
        page.wait_for_selector("#drilldown-body table")
        q = parse_qs(urlsplit(asked[-1]).query)
        assert q["account_id"] == ["40"] and "class_id" not in q
        assert q["start_date"] == [start]
        assert "Service Income" in page.inner_text("#modal-title")
        assert "side gig" in page.inner_text("#drilldown-body")
        # opened from the ledger: the way back is to the ledger, not to P&L by Class
        assert (
            page.locator("#modal button:has-text('Back to P&L by Class')").count() == 0
        )

        # back through the browser's history (R7): the ledger as it was
        # left, its period preset and the same dates
        page.click("#modal button:has-text('Back to General Ledger')")
        page.wait_for_selector("#report-content h3 a")
        assert page.input_value("#report-period-select") == "this_year_to_date"
        assert page.input_value("#report-custom-start") == start
        assert page.evaluate("location.hash").startswith("#/reports/general-ledger?")
    finally:
        page.close()
