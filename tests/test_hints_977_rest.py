"""#977, payroll and HR, first-run setup, companies, tax, nonprofit and the
shared pieces: each screen's teaching text went behind the tortoise. The
words are the same; they sit in a hint() after a label or heading, or, where
a screen would show three or more tortoises, in one hintList() beside its
heading. State, warnings, disclaimers and the privacy lines stay in the
open."""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js"

# (file, text as written in the source, what the note is about)
MOVED = [
    # First-run setup: one note beside the form's heading
    (
        "auth.js",
        "Just enough to get you in. You can configure everything else later.",
        '"Set up SlowBooks Pro 2026"',
    ),
    (
        "auth.js",
        '"You can add your address, phone, tax ID, payment defaults, " +',
        '"Set up SlowBooks Pro 2026"',
    ),
    (
        "auth.js",
        "Picks the tax return and the equity accounts (owner's equity and draw;",
        '"Legal entity"',
    ),
    # Companies
    (
        "companies.js",
        "Each company is stored in its own separate database.",
        "'Company Files'",
    ),
    (
        "companies.js",
        "'Switch company takes you back to the company picker; this company is signed out.'",
        "'Company Files'",
    ),
    (
        "companies.js",
        "A copy of these books, encrypted under a passphrase shown once",
        "'Send to accountant'",
    ),
    (
        "companies.js",
        "The .sbacopy file your client sent, and the passphrase they sent separately.",
        "'Copy file'",
    ),
    (
        "companies.js",
        "The .sbchanges file your accountant sent back.",
        "'Changes file'",
    ),
    # Payroll and HR
    (
        "payroll.js",
        "What each employee was paid this year before the first SlowBooks pay run: the",
        "'File'",
    ),
    (
        "payroll.js",
        "Other: state taxes withheld besides income tax (disability, family leave, transit) and garnishments.",
        "'Other'",
    ),
    (
        "employees.js",
        "Record-keeping only — submit cases via the federal E-Verify portal.",
        "'E-Verify'",
    ),
    (
        "employees.js",
        "opens in the employee's browser — the address must be reachable from where they are",
        "'Portal URL'",
    ),
    (
        "employees.js",
        "What the old payroll paid this year before the first SlowBooks pay run, quarter by quarter,",
        "'Opening YTD'",
    ),
    (
        "benefits.js",
        "A benefit is a code with a rule. Pre-tax codes apply in sequence order",
        "'Benefits & Deductions'",
    ),
    (
        "benefits.js",
        "A group is a template: every employee in it gets the group's codes.",
        "'Group'",
    ),
    (
        "deductions.js",
        "Orders apply to disposable earnings under CCPA limits, in priority order.",
        "'Garnishments'",
    ),
    (
        "tax_forms.js",
        'Vendors marked "1099 Vendor: Yes" with type NEC on the Vendors page.',
        "'1099-NEC / 1096 (Contractors)'",
    ),
    (
        "tax_forms.js",
        "Every W-2, W-3, 940 and 941 generated from these books, newest first.",
        "'Form history'",
    ),
    ("time_entries.js", "Clock times work out Regular Hours", "'Clock Out'"),
    # Tax
    (
        "reseller_permits.js",
        'Reseller permit verification is manual — click "Open WA lookup"',
        "'Reseller Permits'",
    ),
    (
        "reseller_permits.js",
        "Round-trip: click <strong>Open lookup</strong>, the official state site opens",
        "'Step 1'",
    ),
    # Nonprofit
    (
        "in_kind.js",
        "Donated property and services. The value you enter is the donor's estimate",
        "'In-Kind Gifts'",
    ),
    (
        "nonprofit.js",
        "When a restricted fund spends for its purpose, release that much",
        "'Releases from Restriction'",
    ),
    (
        "nonprofit.js",
        "Posted as a debit to Net Assets With Donor Restrictions and a credit to Net Assets Without",
        "'Amount'",
    ),
    (
        "nonprofit.js",
        "Rules split a shared cost across program, management and fundraising (and across ${T('classes')}).",
        "'Functional Allocations'",
    ),
    (
        "nonprofit.js",
        "Each share lands in a ${T('class')}, a function, or both.",
        "'Targets'",
    ),
    # Dashboard, support and shared pieces
    (
        "dashboard.js",
        "Use the arrows to reorder, × to hide. Your layout is remembered for your login.",
        "'Customize'",
    ),
    (
        "audit.js",
        "All create, update, and delete operations are logged here",
        "'Audit Log'",
    ),
    (
        "support.js",
        "Send opens your own mail program with the message written for you",
        "'Contact support'",
    ),
    (
        "ocr_canvas.js",
        "Drag a box around a value, then tap what it is below — or tap a field first, then drag.",
        "'Review scan'",
    ),
]

# what stays in the open: state, warnings, disclaimers, privacy and
# security lines, checkbox labels, empty states and keyboard hints
STAY = [
    ("auth.js", "<strong>Use account numbers.</strong>"),
    ("auth.js", "<strong>Encrypt this company file.</strong>"),
    ("auth.js", "The administrator signs in with this password from now on"),
    ("auth.js", "This recovery key is shown once."),
    ("companies.js", "This is an accountant's copy of <strong>"),
    ("companies.js", "New company files are created by an administrator."),
    ("companies.js", "The passphrase is shown here once and kept nowhere."),
    ("companies.js", "Copies sent to an accountant: "),
    ("payroll.js", "<strong>Disclaimer:</strong> Tax calculations are approximate."),
    ("payroll.js", "Reading the file…"),
    (
        "employees.js",
        "Only an administrator can see or make an employee\\'s portal link.",
    ),
    (
        "employees.js",
        "Locked: a pay run dated ${escapeHtml(data.first_pay_date)} is processed",
    ),
    ("benefits.js", "set a remittance vendor on the code to bill it"),
    ("tax_forms.js", "<strong>Note:</strong> Tax forms are for reference."),
    ("reseller_permits.js", "No active permits expire within 30 days."),
    ("tax.js", "<strong>Disclaimer:</strong> This report is for reference only."),
    ("nonprofit.js", "Pick a rule to preview what would move."),
    ("dashboard.js", "Nothing waiting. Scan a receipt from a Bill or Expense form."),
    (
        "support.js",
        "Support emails, including any logs you attach, are stored in our private project tracker (Basecamp).",
    ),
    ("support.js", "Found a security issue? Report it through"),
    # a caution, not teaching: read the message before it leaves
    (
        "support.js",
        "Redaction is best-effort — please read the message before you send it.",
    ),
    ("ocr_canvas.js", "Drag a box, then tap a field:"),
    ("utils.js", "Enter or = puts it in"),
]

# (file, hint() calls, hintList() calls): no screen shows three or more
# tortoises; the two that had three lines carry one list each
TORTOISES = {
    "auth.js": (0, 1),
    "companies.js": (4, 0),
    "payroll.js": (2, 0),
    "employees.js": (0, 1),
    "benefits.js": (2, 0),
    "deductions.js": (1, 0),
    "tax_forms.js": (2, 0),
    "time_entries.js": (1, 0),
    "reseller_permits.js": (2, 0),
    "in_kind.js": (1, 0),
    "nonprofit.js": (4, 0),
    "dashboard.js": (1, 0),
    "audit.js": (1, 0),
    "support.js": (1, 0),
    "ocr_canvas.js": (1, 0),
}


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def _calls(src, fn):
    """Each fn( ... ) call's argument text, balanced on brackets well enough
    for these files."""
    calls = []
    for m in re.finditer(rf"\b{fn}\(", src):
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


def _noted(src):
    return _calls(src, "hint") + _calls(src, "hintList")


def test_each_moved_text_is_in_a_note_about_its_field():
    for name, text, about in MOVED:
        calls = [c for c in _noted(_src(name)) if text in c]
        assert len(calls) == 1, (name, text)
        assert about in calls[0], (name, text, about)


def test_no_moved_text_is_left_in_the_open():
    for name, text, _ in MOVED:
        src = _src(name)
        outside = src
        for call in _noted(src):
            outside = outside.replace(call, "")
        assert text not in outside, (name, text)


def test_state_warnings_and_disclaimers_stay_in_the_open():
    for name, text in STAY:
        src = _src(name)
        outside = src
        for call in _noted(src):
            outside = outside.replace(call, "")
        assert text in outside, (name, text)


def test_each_screen_has_its_count_of_tortoises():
    for name, (hints, lists) in TORTOISES.items():
        src = _src(name)
        assert (len(_calls(src, "hint")), len(_calls(src, "hintList"))) == (
            hints,
            lists,
        ), name


def test_the_employee_dialogs_three_lines_are_one_note():
    """Portal Access, E-Verify and Opening YTD each carried a line: one
    note beside the dialog's first heading lists the three, and the
    Portal URL entry shows while a link is."""
    src = _src("employees.js")
    view = src[src.index("async viewDetails(id) {") :]
    view = view[: view.index("\n    },")]
    assert (
        '<h4 style="margin:0;">Overview</h4>${EmployeesPage._detailHints(id)}' in view
    )
    lists = _calls(src, "hintList")
    assert "hidden: true" in lists[0] and "emp-portal-url-hint-${id}" in lists[0]
    load = src[src.index("async _loadPortal(id) {") :]
    load = load[: load.index("\n    },")]
    assert "Hints.showEntry(`emp-portal-url-hint-${id}`, !!token.portal_url);" in load
    assert 'aria-describedby="emp-portal-url-hint-${id}"' in src


def test_the_dashboard_note_is_there_only_while_customizing():
    src = _src("dashboard.js")
    assert (
        "${editing ? hint('Use the arrows to reorder, × to hide. Your layout is remembered for your login.', 'Customize') : ''}"
        in src
    )


class _Setup(HTMLParser):
    """The setup form: where each text sits (in a note or in the open), the
    ids on the page, and each aria-describedby."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.ids = set()
        self.described = {}
        self.open_text = []
        self.note_text = []
        self.note_hidden = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        is_note = "hint-pop" in (a.get("class") or "").split()
        in_note = any(self.stack) or is_note
        if is_note:
            self.note_hidden.append("hidden" in a)
        if tag not in ("input", "img", "br", "meta"):
            self.stack.append(in_note)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("aria-describedby"):
            self.described[a.get("id") or tag] = a["aria-describedby"]

    def handle_endtag(self, tag):
        if tag not in ("input", "img", "br", "meta") and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        (self.note_text if self.stack and self.stack[-1] else self.open_text).append(
            data
        )


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@needs_node
def test_the_setup_form_draws_one_note_with_the_three_lines():
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "auth_prompt_probe.js"),
            json.dumps({"status": {"setup_needed": True, "authenticated": False}}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    drawn = json.loads(out.stdout)
    assert drawn["screen"] == "setup"
    html = drawn["html"]
    assert html.count("data-hint") == 1, "one tortoise on the setup form"
    p = _Setup()
    p.feed(html)
    note = " ".join(" ".join(p.note_text).split())
    shown = " ".join(" ".join(p.open_text).split())
    for text in (
        "Just enough to get you in. You can configure everything else later.",
        "You can add your address, phone, tax ID, payment defaults, and integrations in Settings after you sign in.",
        "Picks the tax return and the equity accounts (owner's equity and draw; partner capital; common stock and shareholder distributions). You can change it in Settings.",
    ):
        assert text in note, text
        assert text not in shown, text
    assert p.note_hidden == [True], "the note is closed until asked"
    # the checkbox lines are labels and stay in the open
    assert "Use account numbers." in shown
    # Legal entity still reads its description with the note closed
    assert p.described.get("entity_choice") == "entity_choice-hint"
    for target in p.described.values():
        assert target in p.ids, target
