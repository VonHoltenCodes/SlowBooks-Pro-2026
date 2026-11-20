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
            if page.evaluate(HAS_REMOVE, f.lines):
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
            assert page.evaluate(TOASTS) == [f.yes], f.noun
            if f.saved:
                assert len(writes) == 1, (f.noun, writes)
            assert errors == [], f.noun
        finally:
            page.close()

