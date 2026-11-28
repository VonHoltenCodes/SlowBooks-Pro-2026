"""#977, Settings: the teaching lines go behind one tortoise per section.

The Settings tabs carried about ninety grey lines under their fields, ten to
nineteen a tab; a tortoise on every row would litter the page as much. Each
section heading gets one button (hintList in hint.js) whose note lists the
section's fields, each name in bold over its description, in the page's
order. Every field still points at its own entry with aria-describedby, so a
screen reader reads the description at the field with the note shut. State
lines, warnings and the lines that say who may change a thing stay in the
open. The dialogs opened from Settings have no section heading; their lines
sit behind a field's own tortoise (hint()).

tests/js/settings_hints_977_probe.js draws the page (and the parts drawn
later) the way the app does; this reads what it drew.
"""

import functools
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@functools.lru_cache(maxsize=None)
def drawn():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_hints_977_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-4000:]
    return json.loads(out.stdout)


class Page(HTMLParser):
    """The text in the open and the text in the notes, the ids, and every
    element's aria-describedby; per Settings group, its section tortoises."""

    VOID = {"input", "img", "br", "hr", "meta", "link", "source", "col", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (tag, in_note, group)
        self.open_text = []
        self.note_text = []
        self.ids = set()
        self.described = []  # (tag, attrs, [ids])
        self.list_buttons = {}  # group -> count
        self.field_hints = 0
        self.feed(html)
        self.open_text = _norm(" ".join(self.open_text))
        self.note_text = _norm(" ".join(self.note_text))

    def _in_note(self):
        return bool(self.stack) and self.stack[-1][1]

    def _group(self):
        return self.stack[-1][2] if self.stack else None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        in_note = self._in_note() or "hint-pop" in cls
        group = a.get("data-group") if "settings-section" in cls else self._group()
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("aria-describedby"):
            self.described.append((tag, a, a["aria-describedby"].split()))
        if "hint-wrap--list" in cls:
            self.list_buttons[group] = self.list_buttons.get(group, 0) + 1
        elif "hint-wrap" in cls:
            self.field_hints += 1
        if tag not in self.VOID:
            self.stack.append((tag, in_note, group))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        (self.note_text if self._in_note() else self.open_text).append(data)


def _norm(text):
    return re.sub(r"\s+", " ", text)


@functools.lru_cache(maxsize=None)
def page(name):
    return Page(drawn()[name])


# The moved lines (#977 inventory, group Settings, MOVE), by inventory id:
# where each is drawn and a piece of its words (the vocabulary words T()
# puts in are left out of the piece).
MOVED = {
    11: (
        "business",
        "Shown on invoices, estimates, statements and receipts. Tax and payroll forms keep the company name.",
    ),
    12: (
        "business",
        "Nonprofit shows donors, pledges, donations and funds in place of",
    ),
    13: ("business", "Your company's legal form. It decides which tax return applies"),
    14: (
        "business",
        "How this LLC is taxed. A single-member LLC taxed as a sole proprietor files Schedule C",
    ),
    15: ("business", "On, the chart has a Number column, accounts list by number"),
    16: ("business", "Reports' This/Last Fiscal Year presets, the dashboard's profit"),
    17: ("business", "PNG, JPG, GIF, WebP, or SVG · max 5 MB · 200×80 px recommended."),
    19: ("business", "Applies to PDF, Print, and emailed invoice attachments."),
    20: ("business", "Print the logo without the company name heading on invoices"),
    21: ("business", "Prevent modifications to transactions before this date."),
    22: (
        "business",
        "With a password set, a change dated on or before the closing date asks for it",
    ),
    23: ("construction", "Printed in the header of the estimate PDF."),
    24: (
        "construction",
        "A new estimate starts with this text; edit it on any estimate.",
    ),
    25: (
        "construction",
        "How a new estimate is laid out; an estimate that already uses packages",
    ),
    26: (
        "construction",
        "A new estimate line starts with this markup on its cost; change it on any line.",
    ),
    27: (
        "business",
        "For a company that charges no sales tax. A line still takes its item's own setting",
    ),
    28: (
        "business",
        "or sales receipt that sells more of a stock item than is on hand is saved",
    ),
    29: (
        "business",
        "Checks, mailing labels and the IRS forms print on Letter whatever this says",
    ),
    30: (
        "business",
        'Automatically apply late fees to overdue invoices. Use "Apply Late Fees" on the AR Aging report.',
    ),
    31: ("business", "is coming due or is overdue. Run them from the Collections page"),
    32: ("business", "Once a day, when SlowBooks Pro is opened. Needs email set up."),
    33: (
        "business",
        "A tenant billed with its property, say. Whoever is written to must have an email",
    ),
    34: (
        "business",
        "Days from due: negative is before the due date, 0 is the day, positive is after.",
    ),
    35: (
        "business",
        "and vendor form offers, each with its rule. A standard term is due a number of days",
    ),
    36: ("business", "form: how to pay, a thank-you, a season's greeting."),
    37: ("nonprofit", "The ministry's own words on the year-end giving statement."),
    38: (
        "business",
        "Saved. Shown as a placeholder; type a new value only to change it.",
    ),
    39: ("business", "Configure SMTP for sending invoices by email."),
    40: ("business", "Copied on every email sent. Separate addresses with commas."),
    41: ("business", "Blind-copied on every email sent; recipients don't see it."),
    42: (
        "business",
        "Every invoice, statement, collection letter, portal invite and test email, newest first",
    ),
    43: (
        "business",
        "Customize email templates for invoices, estimates, payment receipts, and collection notices.",
    ),
    44: ("business", "What the bank prints on its ACH origination agreement."),
    45: ("business", "The account the net pay is paid from. Stored encrypted."),
    46: (
        "business",
        "Where a pay run posts. Each piece goes to its usual account unless another is chosen here.",
    ),
    47: ("business", "paid from the bank, or held in a liability"),
    50: (
        "business",
        "Washington PFML and Paid Leave Oregon charge the employer share only from a size of employer.",
    ),
    51: (
        "business",
        "ESD tells the employer each year whether it owes the employer share.",
    ),
    52: (
        "business",
        "OED tells the employer each year whether it owes the employer share.",
    ),
    53: (
        "business",
        'Pre-printed stock already carries the "Pay to the order of", memo and signature lines.',
    ),
    54: (
        "business",
        "Where the check sits on the sheet. Check at the bottom prints two stubs and then the check.",
    ),
    55: ("business", "What Pay Bills puts on the memo line of the check"),
    56: (
        "business",
        "For check stock that prints its own dollar sign beside the amount box",
    ),
    57: (
        "business",
        "Prints everything a pre-printed check carries: the company logo, name and address",
    ),
    58: (
        "business",
        "The amount field at the right end of the MICR line is normally encoded by the bank",
    ),
    59: ("business", "Each bank account you write checks on keeps its own details."),
    60: ("business", "Prints a sample check with placeholder values on plain paper."),
    61: (
        "business",
        "Move, size or bold one field of the check without moving the others",
    ),
    62: (
        "business",
        "Pre-printed slips already carry the bank's labels, boxes and MICR line.",
    ),
    63: (
        "business",
        "Where each figure prints on your bank's slip, in inches from the slip's top-left corner",
    ),
    64: (
        "business",
        "Prints a sample deposit (two checks and some cash, made-up figures)",
    ),
    65: (
        "business",
        "Track income and expenses by department, location, or line of business.",
    ),
    66: (
        "business",
        "chosen and asks before saving a transaction that has none on its header or its lines",
    ),
    67: (
        "construction",
        "How job costs roll up: labor, material, subcontract, equipment, other",
    ),
    68: (
        "construction",
        '("03 Concrete", "26 Electrical"), independent of the account it posts to.',
    ),
    69: (
        "construction",
        "The recovery account is the credit side (defaults to the equipment cost type's offset).",
    ),
    70: (
        "construction",
        "Bid packages a general contractor awards — what a sub bids and gets awarded.",
    ),
    71: (
        "business",
        "Where Make Deposits puts the money. A deposit or a payment already saved keeps its own account.",
    ),
    72: (
        "business",
        "Payments wait in the clearing account until the payout reaches your bank",
    ),
    73: (
        "business",
        "Make Deposits opens on this account. On Receive Payment and Enter Sales Receipt",
    ),
    75: (
        "business",
        "Make Deposits, and Deposit To on Receive Payment and Enter Sales Receipt, offer only the accounts ticked here",
    ),
    76: ("business", "Accept online payments on emailed invoice links."),
    77: ("business", "Bring-your-own-key access to Anthropic Claude, xAI Grok, Groq"),
    81: ("business", "Deploy cloudflare/worker.js in your own Cloudflare account"),
    82: (
        "business",
        "Point SlowBooks at any OpenAI-compatible chat API on the public internet",
    ),
    84: (
        "business",
        "SlowBooks Switchboard lets Claude Desktop, Claude Code and other agents",
    ),
    85: (
        "business",
        "The token stays on this computer, and Switchboard sends nothing anywhere",
    ),
    86: (
        "business",
        "Everything runs on this computer — no cloud, no data leaves the machine.",
    ),
    87: ("business", "Optional: Tesseract can sharpen box re-reads on faded receipts."),
    89: (
        "business",
        "macOS and Windows normally use the engine built into the OS; installing Tesseract enables scanning anywhere.",
    ),
    93: (
        "business",
        "Yours alone: kept with your sign-in, saved as soon as you change one.",
    ),
    94: (
        "business",
        "Makes the text larger on this computer, for every company and sign-in.",
    ),
    95: (
        "business",
        "Questions every form asks before it saves, for everyone using these books.",
    ),
    96: (
        "business",
        "A typing slip in the year, or January’s work landing in the old year.",
    ),
    97: (
        "business",
        "A check, register payment or bill payment whose number is already used on that bank account asks",
    ),
    98: (
        "business",
        "Saving changes to a transaction that was already saved asks first.",
    ),
    99: (
        "business",
        "A personal preference, kept per sign-in: it follows you to another computer",
    ),
    100: ("business", "A short chime when a transaction posts"),
    101: (
        "business",
        "A ticked item shows in the menu; untick the ones this company doesn't use",
    ),
    103: ("business", "The buttons in the top bar, in their order: up to 8."),
    104: (
        "business",
        "With Yes, the next start asks for the password even if you did not sign out.",
    ),
    105: ("business", "Add a second user and this deployment becomes Server Edition"),
    106: (
        "business",
        "Scoped credentials for non-humans: AI agents, the receipt service, scripts.",
    ),
    110: ("business", "Backups of this company only, named for it."),
    113: (
        "legacy",
        "Before version 2.18, every company here kept its logo, attachments and employee documents",
    ),
    115: ("dialogs", "Variables: {{ invoice.invoice_number }}"),
    116: ("dialogs", "Renders what you have typed, without saving or sending."),
    120: ("dialogs", "The sign-in gets a one-time password, shown once"),
    121: ("dialogs", "Leave it empty for a sign-in with no end date."),
    124: ("dialogs", "The password you signed in with, not a new one"),
    128: (
        "dialogs",
        "Paste CSV rows as code,name,cost_type,parent_code (header optional).",
    ),
}

# where each was drawn before #977, when that is not the page itself
DRAWN_THERE = {79: "ai", 81: "ai", 82: "ai", 89: "ocr_unavailable", 103: "fast_rack"}


@pytest.mark.parametrize("item", sorted(MOVED), ids=lambda i: f"inventory-{i}")
def test_each_line_is_in_a_note_and_not_in_the_open(item):
    where, words = MOVED[item]
    p = page(where)
    assert words in p.note_text, f"#{item} not in a note: {words}"
    assert words not in p.open_text, f"#{item} still in the open: {words}"
    if item in DRAWN_THERE:
        there = page(DRAWN_THERE[item])
        assert words not in there.open_text, f"#{item} still drawn where it was"


def test_the_provider_line_is_the_ai_notes_and_follows_the_choice():
    # 79: the chosen provider's line, written into the AI Insights note
    assert 'id="ai-settings-hint"' not in drawn()["ai"]
    assert "{ name: 'Provider', id: 'ai-settings-hint', hidden: true, text: '' }" in JS
    follow = JS[
        JS.index("    _aiHintFollow(spec) {") : JS.index("    _renderAiConfig(cfg) {")
    ]
    assert "hintEl.innerHTML" in follow and "Get a key" in follow
    assert "Hints.showEntry('ai-worker-help', !!spec.needs_worker_url);" in follow
    wire = JS[JS.index("    _wireAiConfig(cfg) {") :]
    wire = wire[: wire.index("providerSel.addEventListener('change'") + 400]
    assert "SettingsPage._aiHintFollow(spec);" in wire


# Lines that stay in the open: state, warnings, who may change a thing, and
# every line the owner left visible (UNSURE).
STAY = {
    "business": [
        "Company settings are changed by an administrator.",
        "Backups are made, downloaded and restored by an administrator.",
        "Copy this token now — it will never be shown again:",
        "Last ran 2026-11-18.",
    ],
    "plain": [
        "The payroll accounts could not be loaded; the pay run posts to its usual accounts.",
    ],
    "ai": [
        "AI settings are changed by an administrator.",
        "Security: only https:// URLs are accepted; private/loopback IPs, embedded credentials,",
        "The key saved before 2.22.1 couldn't be matched to a provider",
    ],
    "ocr_unavailable": [
        "No OCR engine is available — scanning is disabled.",
        "Ubuntu: sudo apt-get install tesseract-ocr",
    ],
    "dialogs": [
        "Exactly as Settings shows it:",
        "This rewrites the company file encrypted.",
        "Nobody else should be using the company while this runs.",
    ],
}


@pytest.mark.parametrize("where", sorted(STAY))
def test_state_and_warning_lines_stay_in_the_open(where):
    p = page(where)
    for words in STAY[where]:
        assert words in p.open_text, f"{where}: {words}"
        assert words not in p.note_text, f"{where}: {words}"


@pytest.mark.parametrize(
    "where", ["business", "construction", "nonprofit", "plain", "legacy", "dialogs"]
)
def test_every_aria_describedby_points_at_something_on_the_page(where):
    p = page(where)
    known = p.ids | (page("business").ids if where == "legacy" else set())
    for tag, attrs, refs in p.described:
        for ref in refs:
            assert (
                ref in known
            ), f"{where}: <{tag} {attrs.get('name') or attrs.get('id')}> -> {ref}"


def test_the_ai_forms_fields_point_at_the_ai_notes_entries():
    ids = page("business").ids
    for tag, attrs, refs in page("ai").described:
        for ref in refs:
            assert ref in ids or ref in page("ai").ids, ref


# A field and its own entry (name= or id= on the field -> the entry's id).
FIELDS = {
    "settings-display-name": "settings-display-name-help",
    "company-type": "company-type-help",
    "entity-type": "entity-type-help",
    "llc-tax-election": "llc-tax-election-help",
    "use-account-numbers": "use-account-numbers-help",
    "fiscal-year-start-month": "fiscal-year-start-month-help",
    "logo-upload": "logo-upload-help",
    "invoice-show-logo": "invoice-show-logo-help",
    "invoice-logo-only": "invoice-logo-only-help",
    "new-lines-taxable": "new-lines-taxable-help",
    "inventory-refuse-oversell": "inventory-refuse-oversell-help",
    "pdf_paper_size": "pdf-paper-size-help",
    "ask-password-on-start": "ask-password-on-start-help",
    "closing-date": "closing-date-help",
    "closing_date_password": "closing-date-password-help",
    "warn-date-past": "warn-date-help",
    "warn-date-future": "warn-date-help",
    "warn-duplicate-check": "warn-duplicate-check-help",
    "warn-edit": "warn-edit-help",
    "smtp_password": "smtp-password-help",
    "smtp_always_cc": "smtp-always-cc-help",
    "smtp_always_bcc": "smtp-always-bcc-help",
    "ach_company_account": "ach-company-account-help",
    "payroll_net_pay_account_id": "settings-payroll-hint-payroll_net_pay_account_id",
    "settings-wa-pfml-employer-share": "settings-wa-pfml-employer-share-help",
    "settings-or-paid-leave-employer-share": "settings-or-paid-leave-employer-share-help",
    "settings-default-deposit-account": "settings-default-deposit-account-help",
    "stripe_secret_key": "stripe-secret-key-help",
    "stripe_webhook_secret": "stripe-webhook-secret-help",
    "paypal_client_secret": "paypal-client-secret-help",
    "square_access_token": "square-access-token-help",
    "square_webhook_signature_key": "square-webhook-signature-key-help",
    "stripe_clearing_enabled": "settings-clearing-help",
    "paypal_clearing_enabled": "settings-clearing-help",
    "square_clearing_enabled": "settings-clearing-help",
    "ocr-engine-pref": "ocr-engine-pref-help",
    "post-sound-pref": "post-sound-pref-help",
    "invoice-reminders-auto": "invoice-reminders-auto-help",
    "invoice-reminders-billed-sub-to": "invoice-reminders-billed-sub-to-help",
    "reminder-rules-list": "reminder-rules-help",
    "hidden-menu-choices": "settings-menu-help",
    "class-warn-blank": "class-warn-blank-help",
    "pref-text-size": "pref-text-size-hint",
    "fr-list": "fr-hint",
    "check_stock": "settings-check-stock-help",
    "settings-check-position": "settings-check-position-help",
    "settings-check-memo": "settings-check-memo-help",
    "settings-check-amount-no-dollar-sign": "settings-check-amount-no-dollar-sign-help",
    "settings-check-amount-asterisk-fill": "settings-check-amount-asterisk-fill-help",
    "settings-check-blank-stock": "settings-check-blank-stock-help",
    "settings-check-micr-amount": "settings-check-micr-amount-help",
    "settings-check-bank-details": "settings-check-bank-details-help",
    "settings-deposit-slip-stock": "settings-deposit-slip-stock-help",
}


def _describers(p):
    out = {}
    for _tag, attrs, refs in p.described:
        for key in (attrs.get("id"), attrs.get("name")):
            if key:
                out.setdefault(key, set()).update(refs)
    return out


def test_each_field_points_at_its_own_entry():
    found = _describers(page("business"))
    for field, entry in FIELDS.items():
        assert entry in found.get(field, set()), f"{field} -> {entry}"
    construction = _describers(page("construction"))
    for field, entry in {
        "company-license": "company-license-help",
        "estimate-terms": "estimate-terms-help",
        "estimate-style": "estimate-style-help",
        "estimate-default-markup": "estimate-default-markup-help",
    }.items():
        assert entry in construction.get(field, set()), f"{field} -> {entry}"


def test_every_named_entry_is_some_fields_description():
    html = drawn()["business"] + drawn()["construction"]
    described = set()
    for name in ("business", "construction", "ai"):
        for _tag, _attrs, refs in page(name).described:
            described.update(refs)
    for entry in re.findall(r'<dd id="([^"]+)"', html):
        assert entry in described, entry


def test_the_ids_tests_and_fields_already_used_are_the_entries_ids():
    html = drawn()["business"]
    for kept in (
        "invoice-logo-only-help",
        "new-lines-taxable-help",
        "settings-check-fields-help",
        "settings-deposit-slip-fields-help",
        "settings-default-deposit-account-help",
        "settings-deposit-accounts-help",
        "pref-text-size-hint",
        "ai-settings-hint",
        "fr-hint",
    ):
        assert f'<dd id="{kept}">' in html, kept


def test_one_tortoise_per_section_and_none_per_row():
    p = page("business")
    assert p.field_hints == 0, "a Settings section has no tortoise on a row"
    sections = re.findall(
        r'<div class="settings-section"[^>]*data-group="([a-z]+)"', drawn()["business"]
    )
    for group, count in p.list_buttons.items():
        assert count <= sections.count(group), group
    # the densest tab: four sections, four tortoises
    assert p.list_buttons["payroll"] == 4
    assert p.list_buttons["company"] == 3
    # every heading's tortoise sits beside it, in the heading's row
    rows = re.findall(
        r'<div class="settings-h-row">\s*<h3[^>]*>[^<]*</h3>(.{0,80})',
        drawn()["business"],
        re.S,
    )
    assert rows and all(
        r.startswith('<div class="hint-wrap hint-wrap--list">') for r in rows
    )


def test_the_last_reminder_run_is_state_and_stays_under_its_field():
    at = JS.index('id="invoice-reminders-last-run"')
    assert (
        "Last ran ${escapeHtml(formatDate(s.invoice_reminders_last_run))}."
        in JS[at : at + 300]
    )


def test_the_shown_or_hidden_entries_follow_their_fields():
    llc = JS[JS.index("    _onEntityTypeChange(sel) {") :][:400]
    assert "Hints.showEntry('llc-tax-election-help', sel.value === 'llc');" in llc
    style = JS[JS.index("    _onEstimateStyleChange(sel) {") :][:400]
    assert (
        "Hints.showEntry('estimate-default-markup-help', sel.value === 'line_markup');"
        in style
    )
    ocr = JS[JS.index("    async loadOcrStatus() {") :][:2500]
    assert "Hints.showEntry('ocr-status-help', false);" in ocr
    assert "Hints.showEntry('ocr-status-help', true);" in ocr


def test_the_dialogs_lines_are_behind_their_fields_own_tortoise():
    p = page("dialogs")
    assert p.field_hints >= 6
    assert not p.list_buttons
    ids = p.ids
    for entry in (
        "invite-expires-help",
        "encryption-password-help",
        "email-template-variables-help",
        "email-template-preview-help",
        "cc-import-csv-help",
    ):
        assert entry in ids, entry


def test_find_a_setting_reads_the_notes_behind_the_tortoises():
    """A description that moved off the page still finds its setting: the
    search reads each section's notes with its headings and labels, and
    still not the tortoises' own "About ..." names."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    body = src[src.index("SettingsPage._matches = function") :][:700]
    assert "querySelectorAll('h3, h4, label, legend, .hint-pop')" in body
    assert "[aria-label]:not([data-hint]):not(.hint-pop)" in body
