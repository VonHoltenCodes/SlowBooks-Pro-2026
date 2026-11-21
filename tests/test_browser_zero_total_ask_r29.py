"""Every line form asks before it saves a document that adds up to $0.00,
in a real browser against the real routes.

The forms had three rules for a document with nothing on it: the invoice
asked "This invoice adds up to $0.00. Save it anyway?", the estimate and the
purchase order saved an empty document without a word, and the expense
form's Remove line did nothing on its only line. The rule is now one: the
last line comes off like any other, and a save with no lines, or lines that
add up to $0.00, asks the invoice's question with the kind's own name. No
leaves the form as it stands and sends nothing; Yes goes on to the save,
and a kind the server will not take that way answers in a sentence."""

import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402, F401
    browser_fixture,
    company_fixture,
    settle,
)

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TOASTS = (
    "() => [...document.querySelectorAll('#toast-container > *')]"
    ".map(t => t.textContent.trim())"
)
# A grid's Remove line control, by any of the ways the grids draw one.
REMOVE_ONE = """(sel) => {
    const b = document.querySelector(sel).querySelector(
        '[aria-label^="Remove line"], [data-line-order="remove"],'
        + ' button[onclick*="emoveLine"]');
    if (!b || b.disabled) return false;
    b.click();
    return true;
}"""
# A grid with no Remove line of its own (the bill's): its amounts are cleared,
# which is the other way a document comes to $0.00.
HAS_REMOVE = """(sel) => !!document.querySelector(sel).querySelector(
    '[aria-label^="Remove line"], [data-line-order="remove"],'
    + ' button[onclick*="emoveLine"]')"""
CLEAR_AMOUNTS = """(sel) => {
    for (const box of document.querySelectorAll(
            sel + ' .line-rate, ' + sel + ' .line-amt')) {
        box.value = '';
        box.dispatchEvent(new Event('input', { bubbles: true }));
    }
}"""
ROWS = "(sel) => document.querySelectorAll(sel + ' > tr').length"
# What a person fills in before Save is allowed to run: every required box.
FILL_REQUIRED = """(sel) => {
    const form = document.querySelector(sel);
    if (!form) return;
    for (const el of form.querySelectorAll('[required]')) {
        if (el.value) continue;
        if (el.tagName === 'SELECT') {
            const o = [...el.options].find(o => o.value && !o.disabled && /^\\d+$/.test(o.value));
            if (o) el.value = o.value;
        } else if (el.type === 'date') el.value = '2026-09-21';
        else if (el.type !== 'number') el.value = 'Zero total';
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }
}"""
SUBMIT = """(sel) => {
    const form = document.querySelector(sel);
    form.noValidate = true;  // the question is the save path's, not the browser's
    form.requestSubmit();
}"""
QUESTION = "This {noun} adds up to $0.00. Save it anyway?"
FIELD_TICKET_YES = "Field ticket saved"


PICK_NEW_CUSTOMER = """() => {
    const pick = document.querySelector('#qe-form [name="customer_id"]');
    pick.value = '__new__';
    pick.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector('#qe-new-cust-name').value = 'Zero Street Diner';
    document.querySelector('#qe-lines .line-desc').value = 'Site visit, no charge';
}"""


def _first(client, path):
    rows = client.get(path).json()
    return (rows.get("items") if isinstance(rows, dict) else rows)[0]["id"]


def _made(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


class _Form:
    """One line form of the sweep: where it is, how it opens, where its
    lines are, the name its question uses, a box whose typed words No must
    leave alone, and the words Yes ends in (the save's own, or the sentence
    that says why this kind is not saved empty)."""

    def __init__(self, noun, route, opener, form, lines, mark, yes, **more):
        self.noun = noun
        self.route = route
        self.opener = opener
        self.form = form
        self.lines = lines
        self.mark = mark
        self.yes = yes
        self.saved = more.get("saved", False)  # Yes saves it
        self.prep = more.get("prep")  # typed before the lines come off
        self.submit = more.get("submit")  # a page with no form of its own
        self.ready = more.get("ready", MODAL_SHOWN)
        self.still = more.get("still", self.ready)  # the form is still open
        self.before = more.get("before")  # set up just before it opens
        self.keep = more.get("keep", False)  # its one line stays, at $0.00


def _forms(client):
    cust = _first(client, "/api/customers")
    vend = _first(client, "/api/vendors")
    accounts = client.get("/api/accounts").json()
    expense = next(a["id"] for a in accounts if a.get("account_type") == "expense")
    sold = [
        {"description": "Demo", "quantity": 1, "rate": 100},
        {"description": "Haul away", "quantity": 1, "rate": 60},
    ]
    day = "2026-09-21"
    sale = {"customer_id": cust, "date": day, "tax_rate": 0, "lines": sold}
    inv = _made(client, "/api/invoices", sale)
    est = _made(client, "/api/estimates", sale)
    po = _made(
        client,
        "/api/purchase-orders",
        {"vendor_id": vend, "date": day, "tax_rate": 0, "lines": sold},
    )
    bill = _made(
        client,
        "/api/bills",
        {
            "vendor_id": vend,
            "bill_number": "ZERO-1",
            "date": day,
            "lines": [dict(ln, account_id=expense) for ln in sold],
        },
    )
    rec = _made(
        client,
        "/api/recurring",
        {
            "customer_id": cust,
            "start_date": day,
            "frequency": "monthly",
            "tax_rate": 0,
            "lines": sold,
        },
    )
    job = _made(client, "/api/jobs", {"customer_id": cust, "name": "Birch Lane"})

    def construction():
        r = client.put("/api/settings", json={"company_type": "construction"})
        assert r.status_code == 200, r.text
        r = client.put(
            f"/api/jobs/{job['id']}/contract",
            json={"contract_type": "lump_sum", "contract_amount": 36000},
        )
        assert r.status_code == 200, r.text

    def nonprofit():
        r = client.put("/api/settings", json={"company_type": "nonprofit"})
        assert r.status_code == 200, r.text

    modal = "#modal-body form"
    return [
        _Form(
            "invoice",
            "#/invoices",
            f"InvoicesPage.showForm({inv['id']})",
            "#invoice-form",
            "#inv-lines",
            "#invoice-form [name=notes]",
            "Invoice updated",
            saved=True,
        ),
        # Quick Entry, the other form that makes invoices: one described
        # line with no rate
        _Form(
            "invoice",
            "#/quick-entry",
            "null",
            "#qe-form",
            "#qe-lines",
            "#qe-po",
            re.compile(r"Invoice #\S+ created"),
            saved=True,
            prep="() => { document.querySelector('#qe-lines .line-desc').value"
            " = 'Site visit, no charge'; }",
            ready="() => !!document.querySelector('#qe-lines tr')",
        ),
        _Form(
            "estimate",
            "#/estimates",
            f"EstimatesPage.showForm({est['id']})",
            modal,
            "#est-lines",
            modal + " [name=notes]",
            "Estimate updated",
            saved=True,
        ),
        _Form(
            "purchase order",
            "#/purchase-orders",
            f"PurchaseOrdersPage.showForm({po['id']})",
            modal,
            "#po-lines",
            modal + " [name=notes]",
            "PO updated",
            saved=True,
        ),
        _Form(
            "sales receipt",
            "#/sales-receipts",
            "SalesReceiptsPage.showForm()",
            "#sales-receipt-form",
            "#sr-lines",
            "#sales-receipt-form [name=notes]",
            "Sales receipt must have at least one line.",
        ),
        _Form(
            "credit memo",
            "#/credit-memos",
            "CreditMemosPage.showForm()",
            modal,
            "#cm-lines",
            modal + " [name=notes]",
            "Credit memo must have at least one line.",
        ),
        _Form(
            "vendor credit",
            "#/vendor-credits",
            "VendorCreditsPage.showForm()",
            modal,
            "#vc-lines",
            modal + " [name=notes]",
            "Enter the amount of the credit: a quantity and rate on at least one line.",
        ),
        _Form(
            "bill",
            "#/bills",
            f"BillsPage.showForm({bill['id']})",
            modal,
            "#bill-lines",
            modal + " [name=notes]",
            "Enter what the vendor charged: an amount, or a quantity and rate,"
            " on at least one line.",
        ),
        _Form(
            "journal entry",
            "#/journal",
            "JournalPage.showForm()",
            modal,
            "#je-lines",
            modal + " [name=description]",
            "At least 2 lines required",
        ),
        _Form(
            "expense",
            "#/expenses",
            "ExpensesPage.showForm()",
            "#expense-form",
            "#expense-lines",
            "#expense-form [name=memo]",
            "The lines must add up to the amount paid: $25.00 left to assign.",
            prep="() => { const f = document.querySelector('#expense-form');"
            " f.amount.value = '25.00'; ExpensesPage.amountChanged(); }",
        ),
        _Form(
            "recurring invoice",
            "#/recurring",
            f"RecurringPage.showForm({rec['id']})",
            modal,
            "#rec-lines",
            modal + " [name=notes]",
            "This recurring invoice adds up to $0.00. Enter a rate on at least"
            " one line before saving it.",
        ),
        _Form(
            "memorized bill",
            "#/memorized",
            "MemorizedPage.showBillForm()",
            modal,
            "#bill-lines",
            modal + " [name=notes]",
            "Enter what the vendor charges: an amount, or a quantity and rate,"
            " on at least one line.",
        ),
        _Form(
            "memorized journal entry",
            "#/memorized",
            "MemorizedPage.showJournalForm()",
            modal,
            "#je-lines",
            modal + " [name=description]",
            "At least 2 lines required",
        ),
        _Form(
            "deposit",
            "#/deposits",
            "DepositsPage.addOtherLine()",
            None,
            "#dep-other-rows",
            "#deposit-ref",
            "Tick the payments to deposit, or add a line for money that was"
            " never invoiced.",
            submit="() => DepositsPage.makeDeposit()",
            ready="() => !!document.querySelector('#dep-other-rows')",
        ),
        _Form(
            "change order",
            f"#/jobs/{job['id']}",
            "(async () => { await JobsPage.setTab('changeorders');"
            " JobsPage.coNew(); JobsPage.coAddLine(); })()",
            "#co-form",
            "#co-line-body",
            "#co-title",
            "Change order saved",
            saved=True,
            ready="() => !!document.querySelector('#co-line-body .co-line')",
            still="() => !!document.querySelector('#co-form')",
            before=construction,
        ),
        # the forms that are never saved for nothing ask first all the
        # same, then say what they need
        _Form(
            "job cost entry",
            "#/job-costs",
            "JobCostsPage.showForm()",
            modal,
            "#jc-lines",
            modal + " [name=memo]",
            "Add at least one line with a quantity and rate",
        ),
        _Form(
            "bill",
            "#/bill-entry",
            "null",
            "#bqe-form",
            "#bqe-lines",
            "#bqe-number",
            "Enter what the vendor charged: an amount on at least one line.",
            ready="() => !!document.querySelector('#bqe-lines tr')",
        ),
        _Form(
            "field ticket",
            f"#/jobs/{job['id']}",
            "(async () => { await JobsPage.setTab('fieldtickets');"
            " FieldTickets.newTicket('tm'); FieldTickets.addLine(); })()",
            "#ft-form",
            "#ft-line-body",
            "#ft-by",
            FIELD_TICKET_YES,
            saved=True,
            ready="() => !!document.querySelector('#ft-line-body tr')",
            still="() => !!document.querySelector('#ft-form')",
        ),
        # a nonprofit's in-kind gift: one described line with no fair value
        _Form(
            "in-kind gift",
            "#/in-kind-gifts",
            "InKindPage.showForm()",
            modal,
            "#ik-lines",
            modal + " [name=memo]",
            re.compile(r"IK-\d+ posted"),
            saved=True,
            keep=True,
            before=nonprofit,
        ),
    ]


def test_every_line_form_asks_before_saving_nothing(browser, company):
    for f in _forms(company):
        question = [QUESTION.format(noun=f.noun)]
        if f.before:
            f.before()
        page, handled = _open_at(browser, company, f.route)
        asked, answer, writes, errors = [], [False], [], []
        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))

        def on_dialog(d, asked=asked, answer=answer):
            asked.append(d.message)
            d.accept() if answer[0] else d.dismiss()

        page.on("dialog", on_dialog)
        page.on(
            "request",
            lambda r, writes=writes: (
                writes.append(f"{r.method} {r.url.split('/api/')[-1]}")
                if r.method != "GET" and "/api/" in r.url
                else None
            ),
        )
        submit = f.submit or f"() => ({SUBMIT})({f.form!r})"
        try:
            page.evaluate(f"async () => {{ await {f.opener}; }}")
            page.wait_for_function(f.ready)
            settle(page, handled)
            if f.form:
                page.evaluate(FILL_REQUIRED, f.form)
            if f.prep:
                page.evaluate(f.prep)
            page.evaluate(
                "(box) => { document.querySelector(box).value = 'kept as typed'; }",
                f.mark,
            )
            # the last line comes off like any other
            assert page.evaluate(ROWS, f.lines) >= 1, f.noun
            if f.keep:
                assert page.evaluate(ROWS, f.lines) == 1, f.noun
            elif page.evaluate(HAS_REMOVE, f.lines):
                for _ in range(12):
                    if not page.evaluate(REMOVE_ONE, f.lines):
                        break
                assert page.evaluate(ROWS, f.lines) == 0, f.noun
            else:
                page.evaluate(CLEAR_AMOUNTS, f.lines)
            settle(page, handled)
            writes.clear()

            # No: asked once, nothing sent, the form as it was
            page.evaluate(submit)
            settle(page, handled)
            page.wait_for_timeout(150)
            assert asked == question, f.noun
            assert writes == [], f.noun
            assert page.evaluate(f.still), f.noun
            assert page.evaluate(TOASTS) == [], f.noun
            assert (
                page.evaluate("(box) => document.querySelector(box).value", f.mark)
                == "kept as typed"
            ), f.noun

            # Yes: asked once more and the save goes on; a kind that is
            # not saved empty says why, in a sentence
            asked.clear()
            answer[0] = True
            page.evaluate(submit)
            settle(page, handled)
            page.wait_for_timeout(300)
            assert asked == question, f.noun
            toasts = page.evaluate(TOASTS)
            if isinstance(f.yes, str):
                assert toasts == [f.yes], f.noun
            else:
                assert len(toasts) == 1 and f.yes.fullmatch(toasts[0]), (
                    f.route,
                    toasts,
                )
            if f.saved:
                assert len(writes) == 1, (f.noun, writes)
            assert errors == [], f.noun
        finally:
            page.close()

    # Quick Entry with "+ New Customer" open and a name typed: the question
    # comes before anything is sent, so No adds no customer either.
    r = company.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    page, handled = _open_at(browser, company, "#/quick-entry")
    asked, writes = [], []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.on(
        "request",
        lambda r: (
            writes.append(f"{r.method} {r.url.split('/api/')[-1]}")
            if r.method != "GET" and "/api/" in r.url
            else None
        ),
    )
    try:
        page.wait_for_function("() => !!document.querySelector('#qe-lines tr')")
        settle(page, handled)
        page.evaluate(PICK_NEW_CUSTOMER)
        settle(page, handled)
        writes.clear()
        page.evaluate(SUBMIT, "#qe-form")
        settle(page, handled)
        page.wait_for_timeout(300)
        assert asked == [QUESTION.format(noun="invoice")]
        assert writes == []
        assert page.evaluate(TOASTS) == []
        assert page.evaluate(
            "() => [document.querySelector('#qe-new-cust-name').value,"
            " document.querySelector('#qe-lines .line-desc').value]"
        ) == ["Zero Street Diner", "Site visit, no charge"]
    finally:
        page.close()
    found = company.get("/api/customers", params={"search": "Zero Street"}).json()
    found = found.get("items") if isinstance(found, dict) else found
    assert not [c for c in found if c["name"] == "Zero Street Diner"]

    # A nonprofit's invoice form is titled, and answers, as a pledge whether
    # or not the box that prints PLEDGE is ticked: it asks about a pledge too.
    edit_invoice = _forms_invoice_opener(company)
    assert (
        company.put("/api/settings", json={"company_type": "nonprofit"}).status_code
        == 200
    )
    page, handled = _open_at(browser, company, "#/invoices")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    try:
        page.evaluate(f"async () => {{ await {edit_invoice}; }}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert (
            page.evaluate(
                "() => document.getElementById('modal-title').textContent.trim()"
            )
            == "Edit Pledge"
        )
        assert not page.evaluate(
            "() => document.querySelector('#invoice-form [name=is_pledge]').checked"
        )
        page.evaluate(CLEAR_AMOUNTS, "#inv-lines")
        settle(page, handled)
        page.evaluate(SUBMIT, "#invoice-form")
        settle(page, handled)
        page.wait_for_timeout(150)
        assert asked == [QUESTION.format(noun="pledge")]
        assert page.evaluate(MODAL_SHOWN)
    finally:
        page.close()
        r = company.put("/api/settings", json={"company_type": "construction"})
        assert r.status_code == 200, r.text


def _forms_invoice_opener(client):
    """Opens a priced invoice that is not flagged a pledge."""
    cust = _first(client, "/api/customers")
    inv = _made(
        client,
        "/api/invoices",
        {
            "customer_id": cust,
            "date": "2026-09-21",
            "tax_rate": 0,
            "lines": [{"description": "Demo", "quantity": 1, "rate": 100}],
        },
    )
    assert not inv.get("is_pledge")
    return f"InvoicesPage.showForm({inv['id']})"


# ── a tax-exempt customer's certificate, on the sales forms ──────────────

NO_CERTIFICATE = "No exemption certificate is on file for Cedar Hill School."
EXPIRED = "Cedar Hill School's exemption certificate expired on June 30, 2026."
NOTE = """(form) => {
    const n = document.querySelector(form + ' .tax-exempt-warning');
    return n ? { text: n.textContent, role: n.getAttribute('role'),
                 shown: n.offsetParent !== null || !n.textContent } : null;
}"""
PICK = """([sel, id]) => {
    const s = document.querySelector(sel);
    s.value = String(id);
    s.dispatchEvent(new Event('change', { bubbles: true }));
}"""
SET_DATE = """([form, day]) => {
    const f = document.querySelector(form);
    const d = f.querySelector('[name="date"]') || f.querySelector('[name="start_date"]');
    d.value = day;
    d.dispatchEvent(new Event('change', { bubbles: true }));
}"""


def _note(page, handled, form):
    settle(page, handled)
    page.wait_for_timeout(100)
    return page.evaluate(NOTE, form)


def test_a_sales_form_warns_about_a_missing_or_expired_certificate(
    browser, company, db_session
):
    """Every sales form, in one pass: choosing a tax-exempt customer with
    no certificate on file says so under the customer, in the open and
    announced; a taxable customer takes it away; with an expired one on
    file the sentence names the day, and follows the document's date. The
    customer's own page says it beside Tax exempt."""
    taxable = _first(company, "/api/customers")
    exempt = _made(
        company, "/api/customers", {"name": "Cedar Hill School", "is_taxable": False}
    )["id"]
    forms = [
        (
            "#/invoices",
            "InvoicesPage.showForm()",
            "#invoice-form",
            "#inv-customer-select",
        ),
        (
            "#/estimates",
            "EstimatesPage.showForm()",
            "#modal-body form",
            "#est-customer-select",
        ),
        (
            "#/sales-receipts",
            "SalesReceiptsPage.showForm()",
            "#sales-receipt-form",
            "#sr-customer-select",
        ),
        (
            "#/credit-memos",
            "CreditMemosPage.showForm()",
            "#modal-body form",
            "#cm-customer-select",
        ),
        (
            "#/recurring",
            "RecurringPage.showForm()",
            "#modal-body form",
            "#rec-customer-select",
        ),
    ]
    permit = None
    for expected in (NO_CERTIFICATE, EXPIRED):
        if expected == EXPIRED:
            permit = _made(
                company,
                "/api/reseller-permits",
                {
                    "entity_type": "customer",
                    "entity_id": exempt,
                    "jurisdiction": "IL",
                    "permit_number": "RP-2210",
                    "expires_at": "2026-06-30",
                },
            )
        for route, opener, form, picker in forms:
            page, handled = _open_at(browser, company, route)
            errors = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            try:
                page.evaluate(f"async () => {{ await {opener}; }}")
                page.wait_for_function(MODAL_SHOWN)
                settle(page, handled)
                # nothing is said until there is something to say
                assert page.evaluate(NOTE, form) is None, route
                page.evaluate(SET_DATE, [form, "2026-09-21"])
                page.evaluate(PICK, [picker, exempt])
                note = _note(page, handled, form)
                assert note == {
                    "text": expected,
                    "role": "status",
                    "shown": True,
                }, route
                if expected == EXPIRED:
                    # as of the document's date: dated while it ran, quiet
                    page.evaluate(SET_DATE, [form, "2026-06-15"])
                    assert _note(page, handled, form)["text"] == "", route
                    page.evaluate(SET_DATE, [form, "2026-09-21"])
                    assert _note(page, handled, form)["text"] == EXPIRED, route
                # a taxable customer: the warning goes
                page.evaluate(PICK, [picker, taxable])
                assert _note(page, handled, form)["text"] == "", route
                assert errors == [], route
            finally:
                page.close()
    assert permit is not None

    # A saved recurring invoice is judged as of its next invoice, as the
    # API's tax_note is, not as of the day it started: one begun while the
    # certificate ran and due again after it expired says so when opened.
    from datetime import date

    from app.models.recurring import RecurringInvoice

    schedule = {
        "customer_id": exempt,
        "start_date": "2026-06-01",
        "frequency": "monthly",
        "tax_rate": 0,
        "lines": [{"description": "Benches", "quantity": 2, "rate": 150}],
    }
    rec = _made(company, "/api/recurring", schedule)
    assert rec["tax_note"] is None
    row = db_session.get(RecurringInvoice, rec["id"])
    row.next_due = date(2026, 12, 1)  # as its runs would have left it
    db_session.commit()
    r = company.put(f"/api/recurring/{rec['id']}", json={"notes": "Monthly"})
    assert r.status_code == 200 and r.json()["tax_note"] == EXPIRED, r.text
    # and one on a job with its own current certificate is quiet, as the
    # API is: the form has no job box, so it goes by the job the schedule has
    job = _made(
        company,
        "/api/jobs",
        {
            "customer_id": exempt,
            "name": "Gym floor",
            "is_taxable": False,
            "exempt_certificate_number": "EX-4410",
            "exempt_certificate_state": "IL",
            "exempt_certificate_kind": "public_entity",
            "exempt_certificate_expires": "2027-12-31",
        },
    )
    on_job = _made(
        company,
        "/api/recurring",
        {**schedule, "start_date": "2026-09-21", "job_id": job["id"]},
    )
    assert on_job["tax_note"] is None
    for rec_id, expected in ((rec["id"], EXPIRED), (on_job["id"], "")):
        page, handled = _open_at(browser, company, "#/recurring")
        try:
            page.evaluate(f"async () => {{ await RecurringPage.showForm({rec_id}); }}")
            page.wait_for_function(MODAL_SHOWN)
            note = _note(page, handled, "#modal-body form")
            assert (note["text"] if note else "") == expected, rec_id
        finally:
            page.close()

    # the customer's own page, beside Tax exempt
    page, handled = _open_at(browser, company, "#/customers")
    try:
        page.evaluate(f"async () => {{ await CustomersPage.showDetails({exempt}); }}")
        page.wait_for_function(MODAL_SHOWN)
        note = _note(page, handled, "#modal-body")
        assert note["role"] == "status" and note["shown"]
        assert note["text"].startswith(
            "Cedar Hill School's exemption certificate expired on"
        )
        page.evaluate("() => closeModal()")
        page.evaluate(f"async () => {{ await CustomersPage.showDetails({taxable}); }}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert page.evaluate(NOTE, "#modal-body") is None
    finally:
        page.close()
