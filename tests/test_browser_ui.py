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
"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import new_page  # noqa: E402  (a Linux browser)

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
