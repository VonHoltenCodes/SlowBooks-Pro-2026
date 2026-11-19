"""#977: the sales and purchase forms' teaching text sits behind the tortoise.

Each line below used to be a grey line in the open under (or over) its
field. It is now the text of a hint() placed after the field's label, word
for word, and the old line is gone. Errors, warnings and state lines are not
hints and are not listed here. hint() itself is tested in
tests/js/hint_977.test.cjs.
"""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app/static/js"

# (inventory id, file, the text as the source spells it, the field it names)
MOVED = [
    (
        170,
        "invoices.js",
        "Applies to all invoices: PDF, Print, and emailed attachments. Changes save immediately.",
        "Show company logo on invoices",
    ),
    (177, "invoices.js", "Optional. Separate addresses with commas.", "CC"),
    (178, "invoices.js", "Optional. Hidden from the recipient.", "BCC"),
    (
        179,
        "invoices.js",
        "Appears at the top of the email. Leave it blank to send your template as it is.",
        "Message",
    ),
    (183, "estimates.js", "Optional. Separate addresses with commas.", "CC"),
    (184, "estimates.js", "Optional. Hidden from the recipient.", "BCC"),
    (
        185,
        "estimates.js",
        "Appears at the top of the email, above the attached PDF.",
        "Message",
    ),
    (
        187,
        "estimates.js",
        "On the owner\\'s PDF the markup rows are hidden: each package\\'s price"
        " carries its share and one subtotal is printed.",
        "Markup rows",
    ),
    (
        188,
        "estimates.js",
        "Off, the owner's PDF spreads the markups into each package's price and"
        " prints one subtotal. The totals here do not change.",
        "Show the markup rows",
    ),
    (190, "estimates.js", "(taken, at cost; markups in the rows above)", None),
    (
        191,
        "payments.js",
        "Where the discounts the ${T('customer')} took post, as a credit memo"
        " applied to each ${T('invoice')}.",
        "Discount account",
    ),
    (
        192,
        "payments.js",
        "Enter the amount received in ${cur}. The rate converts it for the books;"
        " a difference from the invoices' own rates posts as an exchange gain or loss.",
        "Exchange rate",
    ),
    (
        197,
        "deposits.js",
        "Select payments to deposit from Undeposited Funds to a bank account",
        "Make Deposits",
    ),
    (
        198,
        "deposits.js",
        "Money that was never invoiced (a reimbursement, a vendor's refund, a"
        " rebate) goes on the same slip: choose the account it goes to.",
        "Other deposits",
    ),
    (
        199,
        "deposits.js",
        "To take a payment out of a deposit, void the deposit: its payments go"
        " back on the list above to deposit again.",
        "Recent deposits",
    ),
    (
        202,
        "sales_receipts.js",
        "Leave blank for a pure gift. The receipt states the deductible portion"
        " (IRS Pub. 1771).",
        "Goods or services provided in exchange",
    ),
    (
        204,
        "app.js",
        "Enter invoice details and press <strong>Save &amp; Next</strong> (or"
        " Ctrl+Enter) to save and immediately start a new invoice.",
        "Quick Entry Mode",
    ),
    (
        208,
        "bills.js",
        "Where the discounts we take post, as a vendor credit applied to each bill.",
        "Discount account",
    ),
    (
        211,
        "vendor_credits.js",
        "Enter the credit as a positive amount. It reduces what you owe this"
        " vendor, and you choose which bill it settles afterwards.",
        "Amount",
    ),
    (
        214,
        "purchase_orders.js",
        "The ${formatCurrency(po.tax_amount)} of tax on this order is part of what"
        " these goods cost: it is added to each line's account, not to Sales Tax Payable.",
        "Tax on this order",
    ),
    (
        215,
        "purchase_orders.js",
        "${po.retainage_pct}% retainage is held back on this bill and posted to"
        " Retainage Payable.",
        "Retainage",
    ),
    (
        216,
        "purchase_orders.js",
        "Choose where each line is recorded. The bill takes ${escapeHtml(vendor.name)}'s"
        " terms (${escapeHtml(vendor.terms || 'Net 30')}) from the date above.",
        "Lines",
    ),
    (
        217,
        "expenses.js",
        "Enter a receipt you've already paid — by card, cash, or check. Scan it"
        " to fill the form.",
        "Expenses",
    ),
    (
        218,
        "bill_quick_entry.js",
        "Enter the bill and press <strong>Save &amp; Next</strong> (or Ctrl+Enter)"
        " to save it and start the next one. Each line is an account and an amount.",
        "Bill Quick Entry",
    ),
]

# tortoises each file ends up with, all of them from the lines above
TORTOISES = {
    "invoices.js": 4,
    "estimates.js": 6,
    "payments.js": 2,
    "deposits.js": 3,
    "sales_receipts.js": 1,
    "bills.js": 1,
    "vendor_credits.js": 1,
    "purchase_orders.js": 3,
    "expenses.js": 1,
    "bill_quick_entry.js": 1,
}


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def _calls(src):
    """Every hint(...) call: its first argument's text and its field name."""
    out = []
    for m in re.finditer(r"\bhint\((['\"`])", src):
        q = m.group(1)
        i = m.end()
        while True:
            j = src.index(q, i)
            if src[j - 1] != "\\":
                break
            i = j + 1
        text = src[m.end() : j]
        rest = src[j + 1 : j + 200]
        about = re.match(r"\s*,\s*(['\"])(.*?)\1\s*\)", rest)
        out.append((text, about.group(2) if about else rest))
    return out


@pytest.mark.parametrize("ident,name,text,about", MOVED, ids=[str(m[0]) for m in MOVED])
def test_the_text_is_behind_a_tortoise_and_no_longer_in_the_open(
    ident, name, text, about
):
    src = _src(name)
    calls = _calls(src)
    hits = [a for t, a in calls if t == text]
    assert hits, f"#{ident}: {text!r} is not a hint in {name}"
    if about is not None:
        assert about in hits, f"#{ident}: the hint names {hits}, not {about!r}"
    # word for word, and only ever as the hint
    assert src.count(text) == len(hits), f"#{ident}: {text!r} is still in the open"


@pytest.mark.parametrize("name,count", sorted(TORTOISES.items()))
def test_each_page_carries_only_the_tortoises_it_moved(name, count):
    assert len(_calls(_src(name))) == count


def test_the_quick_entry_tortoise_is_its_only_one_in_app_js_from_this_group():
    calls = _calls(_src("app.js"))
    assert [a for _, a in calls if a == "Quick Entry Mode"] == ["Quick Entry Mode"]


def test_the_old_grey_lines_are_gone():
    for name in ("invoices.js", "estimates.js"):
        src = _src(name)
        assert '<span class="form-hint">' not in src, name
    for name in ("app.js", "bill_quick_entry.js"):
        assert 'class="quick-entry-info"' not in _src(name), name
    assert '<div class="hint">' not in _src("payments.js")
    assert '<div class="hint">' not in _src("bills.js")
    assert 'class="form-hint">Enter the credit' not in _src("vendor_credits.js")
    assert '<p class="form-hint">' not in _src("purchase_orders.js")


def test_the_email_dialogs_hints_sit_in_their_labels():
    for name in ("invoices.js", "estimates.js"):
        src = _src(name)
        assert (
            "<label>CC ${hint('Optional. Separate addresses with commas.', 'CC')}</label>"
            in src
        )
        assert (
            "<label>BCC ${hint('Optional. Hidden from the recipient.', 'BCC')}</label>"
            in src
        )
        assert "<label>Message ${hint(" in src


def test_the_logo_option_keeps_the_locked_reason_in_the_open():
    # only the administrator's wording is teaching; why it is locked is state
    src = _src("invoices.js")
    assert (
        '<div class="invoice-logo-help">Applies to all invoices. Only an administrator'
        " can change this, in Settings.</div>"
    ) in src
    assert "${canChange ? hint('Applies to all invoices: PDF, Print" in src


def test_the_conditional_notes_are_still_conditional():
    po = _src("purchase_orders.js")
    assert "const taxNote = Number(po.tax_amount)\n            ? ' ' + hint(" in po
    assert (
        "const retNote = partial && Number(po.retainage_pct)\n            ? ' ' + hint("
        in po
    )
    assert "Retainage' + retNote + '</th>" in po and "Account${taxNote}</th>" in po
    est = _src("estimates.js")
    assert (
        "const hiddenNote = est.show_markup_rows === false\n            ? ' ' + hint("
        in est
    )
    assert '<span class="label">Subtotal${hiddenNote}</span>' in est
    # a taken alternate's note follows the checkbox: the panel is rewritten
    # on every recalc
    assert (
        "${a.accepted ? ' ' + hint('(taken, at cost; markups in the rows above)'" in est
    )
    pay = _src("payments.js")
    assert "per ${cur}) ${hint(`Enter the amount received in ${cur}." in pay
