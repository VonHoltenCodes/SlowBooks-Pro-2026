"""#977, Banking and Import/Export: the teaching lines go behind the tortoise.

The words are the same. A field's one or two lines sit in a hint() after
its label; a section that would carry three or more (the Mercury feed card,
Import from IIF, Import from Report CSV, the report import's review) gets
one tortoise on its heading whose note (hintList) lists them, as Settings
does. A field that named its old line with aria-describedby names the
note's entry instead. State lines (who may change a thing, what is
connected, how far a file got, what is reconciled) stay in the open.
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
JS = ROOT / "app/static/js"


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def _calls(src, fn):
    """Each fn( ... ) call's argument text, balanced on brackets well enough
    for these files (`hint` does not match `hintList` or `_hint`)."""
    calls = []
    for m in re.finditer(r"(?<![\w.])" + fn + r"\(", src):
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


def _tortoises(src):
    return _calls(src, "hint") + _calls(src, "hintList")


def _method(src, head):
    """The body of one page method, from its head to the next method."""
    at = src.index(head)
    end = src.find("\n    },\n", at)
    return src[at:end]


# (inventory id, file, words from the line as written in the source)
MOVED = [
    (219, "banking.js", "SlowBooks Pro is not affiliated with, endorsed by"),
    (222, "banking.js", "Pull transactions straight from your bank — no file exports."),
    (227, "banking.js", "See payments arriving in your Mercury account in SlowBooks."),
    (
        228,
        "banking.js",
        "Sync posted deposits and withdrawals into <em>To review</em>.",
    ),
    (229, "banking.js", "Each sync checks this date range again"),
    (230, "banking.js", "Receive current, available, and incoming-transfer balance"),
    (231, "banking.js", "Register the HTTPS URL below with Mercury"),
    (
        235,
        "banking.js",
        "Choose the account in your chart that ${escapeHtml(rowName)} is.",
    ),
    (238, "banking.js", "Paste a new setup token from your SimpleFIN provider."),
    (239, "banking.js", "How much history there is depends on your SimpleFIN provider"),
    (240, "banking.js", "What the statement says on the As-of date"),
    (242, "banking.js", "What you pick on a line is kept on it until it is added."),
    (
        243,
        "banking.js",
        "returns a line to To review, where it can be added or matched.",
    ),
    (244, "banking.js", "deposit clears the ${escapeHtml(name)} clearing"),
    (
        245,
        "banking.js",
        "Negative = a charge (owe more). Positive = a payment to the card.",
    ),
    (
        245,
        "banking.js",
        "Negative = money out (a payment). Positive = money in (a deposit).",
    ),
    (
        246,
        "banking.js",
        "(the other side of the entry; a bank or card account makes it a transfer)",
    ),
    (247, "banking.js", "(each line's share of the amount; a loan payment's principal"),
    (248, "banking.js", "Move money between bank and card accounts."),
    (249, "banking.js", "Enter the ending date and balance from your statement."),
    (252, "banking.js", "Correct the ending date or balance from your statement."),
    (364, "iif.js", "Download SlowBooks data as .iif files"),
    (365, "iif.js", "Upload .iif files exported from QuickBooks Desktop"),
    (367, "iif.js", "QuickBooks Desktop can't export transactions to IIF"),
    (
        367,
        "iif.js",
        "<strong>Deposit Detail</strong>, or <strong>Check Detail</strong>",
    ),
    (367, "iif.js", "or, for timesheets, <strong>Time by Job Detail</strong>"),
    (367, "iif.js", "Keep each report's default columns, except"),
    (367, "iif.js", "<strong>A categorized bank sheet</strong> (the Transaction Pro"),
    (368, "iif.js", "is not imported here: it goes in from the account itself"),
    (370, "iif.js", "Customer, vendor and account names; item names are kept as typed"),
    (
        371,
        "iif.js",
        "A property's tenants, say: each becomes a customer under its parent",
    ),
    (376, "iif.js", "and Tab goes on to the row's type and then out of the list."),
    (
        380,
        "iif.js",
        "In the list, the arrow keys move from row to row and Tab leaves it.",
    ),
    (384, "app.js", "Download data as CSV files."),
    (385, "app.js", "Upload CSV files to import data."),
    (387, "migration.js", "Bring your accounting history in from another system"),
]

# lines that are state, warnings or who-may-do-what: in the open, as they were
STAY = [
    ("banking.js", "An administrator connects the bank feed."),
    ("banking.js", "The administrator also sets whether a feed's signs are reversed."),
    ("banking.js", "Connected. Choose which SlowBooks bank account each feed lands in"),
    ("banking.js", "Open SlowBooks at your public HTTPS address before copying"),
    ("banking.js", "No bank or credit-card accounts yet."),
    ("banking.js", "Use <strong>+ New bank account</strong> instead."),
    ("banking.js", "Every SimpleFIN account in the list already imports somewhere."),
    ("banking.js", "This account is reconciled through ${formatDate(last)}"),
    ("banking.js", "Reading the file — this can take a moment"),
    ("iif.js", "Adjusting entries only: an accountant's Transaction Detail by Account"),
    ("iif.js", "Adding people to the Employees list needs an administrator"),
    ("iif.js", "An account is ticked once it has a type."),
    ("migration.js", "Adding employees needs an administrator"),
]

# a field that pointed at its line still points at it, now a note's entry
DESCRIBED = [
    ("banking.js", 'id="mercury-since"', "mercury-since-help"),
    ("banking.js", 'id="mercury-webhook-url"', "mercury-webhook-url-help"),
    ("iif.js", 'id="iif-retitle-names"', "iif-retitle-names-help"),
    ("iif.js", 'id="iif-sub-customers"', "iif-sub-customers-help"),
    (
        "iif.js",
        '<table aria-describedby="qbr-keys-hint-accounts"',
        "qbr-keys-hint-accounts",
    ),
    (
        "iif.js",
        '<table aria-describedby="qbr-keys-hint-replaces"',
        "qbr-keys-hint-replaces",
    ),
    ("iif.js", '<table aria-describedby="qbr-keys-hint"', "qbr-keys-hint"),
    ("app.js", 'id="csv-entity"', "csv-import-help"),
]


def _outside(src):
    for call in _tortoises(src):
        src = src.replace(call, "")
    return src


@pytest.mark.parametrize("ident,name,text", MOVED, ids=lambda v: str(v)[:40])
def test_each_moved_line_is_behind_a_tortoise_and_not_in_the_open(ident, name, text):
    src = _src(name)
    inside = [c for c in _tortoises(src) if text in c]
    assert len(inside) == 1, f"#{ident}: {text!r} is not in one hint in {name}"
    assert text not in _outside(src), f"#{ident}: {text!r} is still in the open"


@pytest.mark.parametrize("name,text", STAY, ids=lambda v: str(v)[:40])
def test_state_and_permission_lines_stay_in_the_open(name, text):
    src = _src(name)
    assert text in _outside(src), (name, text)
    assert not any(text in c for c in _tortoises(src)), (name, text)


@pytest.mark.parametrize("name,field,note", DESCRIBED)
def test_a_described_field_still_names_its_note(name, field, note):
    src = _src(name)
    at = src.index(field)
    tag = src[src.rindex("<", 0, at) : src.index(">", at)]
    assert f'aria-describedby="{note}"' in tag, (name, field)
    # the note's entry (or hint) carries the id, and no other element does
    assert any(f"'{note}'" in c for c in _tortoises(src)), (name, note)
    assert f'id="{note}"' not in src, (name, note)


def test_the_words_of_the_report_csv_paragraph_are_kept_whole():
    """One paragraph a report in the section's note, the words in their
    order: joined again they are the old paragraph."""
    src = _src("iif.js")
    [call] = [c for c in _calls(src, "hintList") if "'Import from Report CSV'" in c]
    paras = re.findall(r"\{ text: `(.*?)` \}", call, re.S)
    assert len(paras) == 8
    joined = " ".join(paras[:7])
    assert joined.startswith("QuickBooks Desktop can't export transactions to IIF")
    assert (
        "(both import as journal entries on your bank account), or <strong>Transaction Detail by Account</strong>"
        in joined
    )
    assert (
        "import the lists IIF first); or, for timesheets, <strong>Time by Job Detail</strong>"
        in joined
    )
    assert joined.endswith("payees not on the lists are added.")


# ---- how many tortoises each page, section and dialog carries -------------


def test_the_bank_feed_cards_carry_one_tortoise_each():
    src = _src("banking.js")
    mercury = _method(src, "    _renderMercurySection(feed, feeds, rows = []) {")
    assert len(_calls(mercury, "hintList")) == 1 and not _calls(mercury, "hint")
    webhook = _method(src, "    _renderMercuryWebhook(webhook) {")
    assert not _tortoises(webhook)
    simplefin = _method(src, "    _renderFeedSection(feed, feeds, rows = []) {")
    assert len(_calls(simplefin, "hint")) == 1 and not _calls(simplefin, "hintList")
    # the page itself adds none
    page = _method(src, "    async render(")
    assert not _tortoises(page)


@pytest.mark.parametrize(
    "head",
    [
        "    async showUseOwnAccount(btn) {",
        "    showSimpleFINHistory() {",
        "    showSimpleFINUpdateToken() {",
        "    async showAccountForm(",
        "    _reviewPanel(review, feedId, accountId, kind) {",
        "    _excludedPanel(excluded, accountId) {",
        "    showPayout(lineId, accountId) {",
        "    async showTransferForm(",
        "    async startReconcile(accountId) {",
        "    changeStatement(reconId, statementDate, statementBalance) {",
    ],
)
def test_each_banking_dialog_has_one_tortoise(head):
    assert len(_calls(_method(_src("banking.js"), head), "hint")) == 1, head


def test_the_register_entry_never_shows_more_than_two():
    """Amount, Category and Split across accounts each have one, but the
    Category box goes away while the split's lines show, so the dialog
    shows two at a time."""
    src = _src("banking.js")
    form = _method(src, "    async showEntryForm(accountId, entry = null) {")
    abouts = [c.rsplit(",", 1)[-1].strip() for c in _calls(form, "hint")]
    assert abouts == ["'Amount'", "'Category'", "'Split across accounts'"]
    toggle = _method(src, "    toggleSplits() {")
    assert "catGroup.style.display = open ? 'none' : '';" in toggle
    assert "box.style.display = open ? '' : 'none';" in toggle


def test_each_interoperability_section_has_one_tortoise_on_its_heading():
    page = _method(_src("iif.js"), "    async render() {")
    sections = page.split('<div class="iif-section"')[1:]
    assert len(sections) == 3
    for section in sections:
        assert len(_tortoises(section)) == 1
        # beside the heading, in the heading's row
        row = section[section.index('<div class="iif-h-row">') :]
        assert row.index("</h3>${hint") == row.index("</h3>")
    assert not _tortoises(page.split('<div class="iif-section"')[0])


def test_the_report_review_has_one_tortoise_on_its_heading():
    src = _src("iif.js")
    review = _method(src, "    _showProblems(result) {")
    assert len(_calls(review, "hintList")) == 1 and not _calls(review, "hint")
    assert '<h4 id="qbr-title">Choose what to import</h4>${keys}</div>' in review
    report = _method(src, "    _showValidationReport(report) {")
    assert not _tortoises(report)


def test_the_csv_page_sections_and_migrate_data():
    page = _method(_src("app.js"), "    async renderCSV() {")
    export, imports = page.split('<div class="settings-section" data-write>')
    assert len(_calls(export, "hint")) == 1
    assert len(_calls(imports, "hint")) == 2
    assert not _calls(page, "hintList")
    migrate = _method(_src("migration.js"), "    async render(")
    assert len(_calls(migrate, "hint")) == 1


# ---- the lines that follow a choice keep following it ---------------------


def test_the_import_columns_follow_the_entity_type_inside_its_note():
    src = _src("app.js")
    help_ = _method(src, "    csvImportHelp(entity) {")
    assert "$('#csv-import-help')" in help_  # the note's text carries hint()'s id
    assert "body.innerHTML = App._csvImportHelpHtml(entity);" in help_
    assert "body.insertBefore(head, body.firstChild)" in help_
    assert (
        "hint(App._csvImportHelpHtml('customers'), 'Entity Type', 'csv-import-help')"
        in src
    )


def test_the_validated_files_boxes_show_their_entries():
    src = _src("iif.js")
    report = _method(src, "    _showValidationReport(report) {")
    assert (
        "IIFPage._boxNotes(report.valid && report.caps_names > 0, "
        "report.valid && report.customer_job_names > 0);"
    ) in report
    notes = _method(src, "    _boxNotes(caps, sub) {")
    assert "Hints.showEntry('iif-retitle-names-help', !!caps);" in notes
    assert "Hints.showEntry('iif-sub-customers-help', !!sub);" in notes
    for head in ("    _setFile(file) {", "    clearFile() {"):
        assert "IIFPage._boxNotes(false, false);" in _method(src, head), head


# ---- drawn: the notes hold the words, the fields find their notes ---------


class Page(HTMLParser):
    VOID = {"input", "img", "br", "hr", "meta", "link", "source", "col", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.depth_note = []
        self.open_text, self.note_text = [], []
        self.ids, self.described, self.buttons = set(), [], 0
        self.feed(html)
        self.open_text = " ".join(" ".join(self.open_text).split())
        self.note_text = " ".join(" ".join(self.note_text).split())

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("aria-describedby"):
            self.described += a["aria-describedby"].split()
        if "data-hint" in a:
            self.buttons += 1
        if tag in self.VOID:
            return
        cls = (a.get("class") or "").split()
        inside = bool(self.depth_note and self.depth_note[-1]) or "hint-pop" in cls
        self.depth_note.append(inside)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.depth_note:
            self.depth_note.pop()

    def handle_data(self, data):
        (
            self.note_text
            if self.depth_note and self.depth_note[-1]
            else self.open_text
        ).append(data)


PROBE = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { console, window: {}, document: { addEventListener: () => {} },
  location: { origin: 'https://books.example.test', protocol: 'https:', hash: '#/banking' },
  setTimeout: () => 0, clearTimeout: () => {} };
vm.createContext(ctx);
const read = f => fs.readFileSync('app/static/js/' + f, 'utf8');
vm.runInContext(read('utils.js') + '\n' + read('hint.js') + '\n' + read('banking.js')
  + '\n' + read('iif.js') + '\nthis.BankingPage = BankingPage; this.IIFPage = IIFPage;', ctx);
const B = ctx.BankingPage;
const off = { connected: false };
const mercury = { connected: true, accounts: [{ id: 'm1', name: 'Checking', balance: '1.00', available_balance: '1.00' }],
  account_map: {}, since: '2026-01-01', last_sync: null, webhook: { path: '/api/mercury/webhook', event_types: [] } };
const simplefin = { connected: true, accounts: [], account_map: {}, reversed: [], last_sync: null };
(async () => {
  console.log(JSON.stringify({
    feeds_off: B._renderFeedSection(off, [], []) + B._renderMercurySection(off, [], []),
    feeds_on: B._renderFeedSection(simplefin, [], []) + B._renderMercurySection(mercury, [], []),
    interop: await ctx.IIFPage.render(),
  }));
})();
"""


@functools.lru_cache(maxsize=None)
def drawn():
    out = subprocess.run(
        ["node", "-e", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr[-4000:]
    return {k: Page(v) for k, v in json.loads(out.stdout).items()}


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@needs_node
def test_the_drawn_pages_hold_the_words_in_their_notes():
    pages = drawn()
    off, on, interop = pages["feeds_off"], pages["feeds_on"], pages["interop"]
    for words in (
        "Pull transactions straight from your bank",
        "See payments arriving in your Mercury account",
        "not affiliated with, endorsed by",
    ):
        assert words in off.note_text and words not in off.open_text, words
    assert "An administrator connects the bank feed." in off.open_text
    for words in (
        "Sync posted deposits and withdrawals",
        "Each sync checks this date range again",
        "Receive current, available",
        "Register the HTTPS URL below",
    ):
        assert words in on.note_text and words not in on.open_text, words
    assert "Connected. Choose which SlowBooks bank account" in on.open_text
    for words in (
        "Download SlowBooks data as .iif files",
        "Upload .iif files exported",
        "can't export transactions to IIF",
        "is not imported here",
    ):
        assert words in interop.note_text and words not in interop.open_text, words
    assert "Adjusting entries only" in interop.open_text


@needs_node
def test_the_drawn_pages_tortoise_counts_and_descriptions():
    pages = drawn()
    # SimpleFIN's one and Mercury's list, not connected; Mercury's list alone connected
    assert pages["feeds_off"].buttons == 2
    assert pages["feeds_on"].buttons == 1
    assert pages["interop"].buttons == 3
    for name, page in pages.items():
        missing = [i for i in page.described if i not in page.ids]
        assert missing == [], (name, missing)
