"""#977, the lists, journal and memorized pages: each form's teaching text
went behind the tortoise. The words are the same; they now sit in a hint()
after the field's label, not in a grey line under it, and a field that
named the old line with aria-describedby names the note instead."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js"

# (file, text as written in the source, the field it is about)
MOVED = [
    (
        "app.js",
        "An account's name opens its register: the bank register",
        "'Chart of Accounts'",
    ),
    (
        "app.js",
        "Every new company starts with a seeded chart of accounts.",
        "'Seeded accounts'",
    ),
    (
        "app.js",
        "A bank or card account gets a register. A sub-account shares",
        "'Type'",
    ),
    (
        "app.js",
        "The books post to it, so it stays a top-level account.",
        "'Sub-account'",
    ),
    (
        "app.js",
        "Accounts of the same type. A sub-account is listed under its parent.",
        "'Sub-account'",
    ),
    (
        "app.js",
        "A CSV in the columns the export writes (Number, Name, Type, optional Parent and",
        "'File'",
    ),
    (
        "customers.js",
        "A tenant under a property, say: the sub-customer is invoiced",
        "'Sub-customer'",
    ),
    (
        "vendors.js",
        "A bank rule: a statement line whose payee has this vendor",
        "'Bank feed'",
    ),
    ("vendors.js", "Choose the default account first.", "'Bank feed'"),
    (
        "items.js",
        "A line on this item can be entered at a negative price",
        "'Discount or concession'",
    ),
    ("items.js", "An inactive item stays on the documents that use it", "'Active'"),
    (
        "items.js",
        "Posting an invoice or bill that uses this item will adjust quantity",
        "'Track inventory'",
    ),
    ("items.js", "Weighted, computed from movements.", "'Avg Cost'"),
    ("items.js", "Enter a quantity to see the resulting balance.", "'Quantity'"),
    ("items.js", "A unit cost applies to stock added; left blank", "'Unit cost'"),
    (
        "classes.js",
        "column for this ${T('class').toLowerCase()}; every",
        "T('Net Income')",
    ),
    (
        "classes.js",
        "Every account, balance-sheet lines included. A line opens its document.",
        "'Lines'",
    ),
    (
        "bank_rules.js",
        "Rules auto-match imported bank transactions by payee name.",
        "'Bank Rules'",
    ),
    (
        "bank_rules.js",
        "the pattern is its payee, matched by Contains in any case.",
        "'Payee Pattern'",
    ),
    ("bank_rules.js", "A line this rule matches is added for this vendor.", "'Vendor'"),
    (
        "bank_rules.js",
        "A deposit this rule matches is added for this customer.",
        "T('Customer')",
    ),
    ("bank_rules.js", "The memo a line this rule matches is added with", "'Memo'"),
    (
        "journal.js",
        "An accountant's correction to the books: marked on the entry",
        "'Adjusting entry'",
    ),
    (
        "journal.js",
        "Accrual only: counts on accrual reports, e.g. an opening A/R balance.",
        "'Basis'",
    ),
    (
        "memorized.js",
        "An auto-enter memorized bill or journal entry is entered on its date",
        "'Auto-enter'",
    ),
    (
        "memorized.js",
        "Twice a month is the next date's day and fifteen days later",
        "'How often'",
    ),
    (
        "memorized.js",
        "Left blank, each bill is numbered the date and the vendor's initials",
        "'Bill Number'",
    ),
    ("fixed_assets.js", "No more than the purchase price.", "'Salvage Value'"),
]

# hint() calls per file, after this page group moved
TORTOISES = {
    "app.js": 5,
    "customers.js": 1,
    "vendors.js": 1,
    "items.js": 6,
    "classes.js": 2,
    "bank_rules.js": 5,
    "journal.js": 2,
    "memorized.js": 3,
    "fixed_assets.js": 1,
}

# a field that pointed at the old line still points at the note
DESCRIBED = [
    ("app.js", 'name="detail_type"', "account-type-hint"),
    ("app.js", 'id="account-parent"', "account-parent-hint"),
    ("vendors.js", 'id="vendor-bank-rule"', "vendor-bank-rule-hint"),
    ("bank_rules.js", 'id="bank-rule-vendor"', "bank-rule-vendor-hint"),
    ("bank_rules.js", 'id="bank-rule-customer"', "bank-rule-customer-hint"),
    ("bank_rules.js", 'id="bank-rule-memo"', "bank-rule-memo-hint"),
    ("memorized.js", 'name="bill_number"', "mem-bill-number-hint"),
]


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def _hint_calls(src):
    """Each hint( ... ) call's argument text, balanced on brackets and
    template literals well enough for these files."""
    calls = []
    for m in re.finditer(r"\bhint\(", src):
        i, depth = m.end(), 1
        while depth:
            c = src[i]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            i += 1
        calls.append(src[m.end() : i - 1])
    return calls


def test_each_moved_text_is_in_a_hint_about_its_field():
    for name, text, about in MOVED:
        calls = [c for c in _hint_calls(_src(name)) if text in c]
        assert len(calls) == 1, (name, text)
        assert about in calls[0], (name, text, about)


def test_no_moved_text_is_left_in_the_open():
    for name, text, _ in MOVED:
        src = _src(name)
        outside = src
        for call in _hint_calls(src):
            outside = outside.replace(call, "")
        if name == "vendors.js" and text in (
            "Choose the default account first.",
            "A bank rule: a statement line whose payee has this vendor",
        ):
            # the account picker swaps the note's words, through Hints.setText
            setter = src[src.index("bankRuleAccountChanged(sel) {") :]
            setter = setter[: setter.index("\n    },")]
            assert "Hints.setText('vendor-bank-rule-hint'" in setter
            outside = outside.replace(setter, "")
        assert text not in outside, (name, text)


def test_each_page_has_its_count_of_tortoises():
    for name, count in TORTOISES.items():
        assert len(_hint_calls(_src(name))) == count, name


def test_a_described_field_still_names_its_note():
    for name, field, note in DESCRIBED:
        src = _src(name)
        at = src.index(field)
        tag = src[src.rindex("<", 0, at) : src.index(">", at)]
        assert f'aria-describedby="{note}"' in tag, (name, field)
        assert f"'{note}')" in src, (name, note)
        assert f'id="{note}"' not in src, (name, "the old line is gone")


def test_the_grey_lines_went_with_their_words():
    assert "class-net-note" in _src("classes.js")
    assert "(the ${T('P&L by Class')}" not in _src("classes.js")
    items = _src("items.js")
    assert "(weighted, computed from movements)" not in items
    # the preview box shows once there is a quantity to preview
    assert "preview.style.display = '';" in items
    assert '<p class="hint" style="margin-top:8px;">An auto-enter' not in _src(
        "memorized.js"
    )
