"""#977, Reports and Construction: the teaching clauses go behind the tortoise.

A report's header line carried its facts (the dates, the basis, the count,
"As of") and, after them, a clause that teaches how to read or work the
report ("Click an amount for the transactions behind it"). The facts stay
in the open exactly as they were; only the clause moves, word for word, into
a hint() at the end of the line, named for the report. A line that was all
teaching (Past customers, Fixed Asset Reconciliation, the giving statements)
leaves its tortoise at the top of the dialog, under the title. The job pages'
grey lines go behind their field's (or tab's) own tortoise. No page shows
three or more at once, so none needs a section list (hintList).

The printed, PDF and CSV reports are drawn by the server from their own
headers, never from these strings: they gain no button and lose no fact.

tests/test_browser_hints_977_reports.py looks at the drawn pages.
"""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app/static/js"

# (inventory id, file, the text as the source spells it, the note's name)
MOVED = [
    (
        321,
        "reports.js",
        "Click an amount for the transactions behind it; the Total column is the"
        " ${escapeHtml(T('Profit & Loss'))} for the whole period.",
        "`${T('Profit & Loss')} by ${by === 'month' ? 'Month' : 'Quarter'}`",
    ),
    (
        322,
        "reports.js",
        "A line's type opens its document.",
        "`${T('Profit & Loss')} Detail`",
    ),
    (
        324,
        "reports.js",
        "Tax the company owes on materials bought untaxed for a job whose contractor"
        " pays the tax, each at the rate it was booked at (the setting today is"
        " ${SalesLines.taxPercent(u.rate)}%). Pay it as a check or bill to the tax"
        " agency on ${name}.",
        "'Use tax'",
    ),
    (
        325,
        "reports.js",
        "Sales with no tax, by why: the kind of certificate the document carries"
        " from its ${T('job').toLowerCase()} when the whole sale is exempt, exempt"
        " with no certificate on file, or lines left untaxed on a taxed sale.",
        "'Exempt sales'",
    ),
    (327, "reports.js", "Click an item for the lines behind it.", "'Sales by Item'"),
    (
        328,
        "reports.js",
        "Click an item for the lines behind it.",
        "'Purchases by Item'",
    ),
    (
        329,
        "reports.js",
        "Pick the account the lines are parked on: a holding account, or one chosen"
        " by mistake. Tick the lines to move, choose where they belong, preview the"
        " move, then post it as one change. Every moved line is listed in the"
        " result and in the audit log.",
        "'Lines on'",
    ),
    (
        334,
        "reports.js",
        "Click an amount for the transactions behind it, or a heading for that"
        " column's own report.",
        "T('P&L by Class')",
    ),
    (
        335,
        "reports.js",
        "Click an amount for the transactions behind it, or a heading for the"
        " ${escapeHtml(T('job'))}'s page on these dates.\n"
        "                ${Terms.text('\"No job\" holds untagged activity')} <em>and</em>"
        " ${Terms.text('the applied-cost credits behind Job Cost Entries, so its costs"
        " can be negative and the totals still match the P&L')}.",
        "`${T('P&L')} by ${T('Job')}`",
    ),
    (
        336,
        "reports.js",
        "Compare against the mapped fixed-asset and accumulated-depreciation\n"
        "            GL accounts — differences mean unposted acquisitions or manual GL edits.",
        "'Fixed Asset Reconciliation'",
    ),
    (339, "reports.js", "Click a job to drill down.", "T('Job Budget vs Actual')"),
    (
        345,
        "reports.js",
        "(percent complete = cost ÷ estimated cost). Estimated cost is actual plus"
        " the cost-to-complete forecast. A projected loss is booked in full."
        " Billings over earned are a liability; under earned are an asset. GP to"
        " date is earned less cost; backlog is the revised contract less earned."
        " Basis shows whether billing is from approved pay applications or posted"
        " revenue.",
        "'WIP Schedule'",
    ),
    (
        346,
        "reports.js",
        "Cost to complete is the remaining budget plus open commitments; projected"
        " final is actual + cost to complete.",
        "'Cost to Complete'",
    ),
    (
        348,
        "reports.js",
        "Leave an amount blank to use the default (budget less actual, never below"
        " zero). The WIP schedule uses actual plus this cost to complete as the"
        " job's estimated cost.",
        "'Cost-to-Complete Forecast'",
    ),
    (
        349,
        "reports.js",
        "Committed is the pre-tax value; received is what delivery tickets dated by"
        " then brought in, at the line rates; retainage held is held less released;"
        " remaining counts open commitments only; a closed commitment shows nothing"
        " remaining unless a bill is dated after this date.",
        "'Commitment Status'",
    ),
    (
        350,
        "reports.js",
        "A signed ticket on a change order that is not yet approved counts as not"
        " billed; the change order is named.",
        "'Field Ticket Log'",
    ),
    (
        351,
        "reports.js",
        "Held less released is what is outstanding; the totals are the Balance"
        " Sheet's Retainage Receivable and Retainage Payable.\n"
        "                A row named “Journal entries and other” is ledger balance no"
        " pay application or bill explains. Closed jobs are included.",
        "'Retainage'",
    ),
    (
        352,
        "reports.js",
        "Certificates that expire within ${data.window_days} days, subs with no W-9"
        " on file, and paid bills whose lien waiver is missing or still pending."
        " Nothing here blocks a payment.",
        "'Sub Compliance'",
    ),
    (
        353,
        "reports.js",
        "Every ${T('customer')} with a gift in the year gets a statement: cash"
        " contributions with the deductible portion, non-cash gifts described"
        " without a value. ${T('Customers')} who opted out in their record are"
        " skipped when emailing.",
        "'Year-End Giving Statements'",
    ),
    (
        354,
        "reports.js",
        "Entries with the same amount on the same side and the same or a similar"
        " payee, at most ${data.window_days} days apart. Nothing here is voided"
        " unless you void it.",
        "'Possible Duplicates'",
    ),
    (
        355,
        "reports.js",
        "Won is accepted or converted; the closing rate is won ÷ (won + lost), so"
        " open bids count neither way. Days run from the first time the estimate"
        " was emailed (else its date) to the decision.",
        "'Estimate Closing Rate'",
    ),
    (
        271,
        "jobs.js",
        "Click a type or code to open it; click the line count to see the posted"
        " lines; click a line to open its document.",
        "JobsPage.TAB_LABELS.costs",
    ),
    (
        272,
        "jobs.js",
        "Cost = qty × unit cost (or the line amount when no cost is entered);"
        " revenue = the line amount. Rows you edit here become manual and survive"
        " re-seeding.",
        "'Load estimate lines as budget'",
    ),
    (
        273,
        "jobs.js",
        "This change order returns an allowance\\'s unspent amount to the owner: it"
        " takes the amount off the contract with no markup. Change its amount,"
        " title or date; funding and markup are fixed.",
        "co.id ? `Change order ${co.number || ''}` : 'New change order'",
    ),
    (
        275,
        "jobs.js",
        "The date the owner signed. Reports, WIP and pay applications read the"
        " change order from this day. It cannot be before the change order's own"
        " date or the contract date.",
        "'Takes effect on'",
    ),
    (
        276,
        "jobs.js",
        "Approving it cannot take effect before this date, so use the day the"
        " allowance closed out (for a month-end return, a date in that month). It"
        " cannot be before the contract date or in a closed period.",
        "'Change order date'",
    ),
    (
        281,
        "jobs.js",
        "Labor posts at the employee's loaded cost rate with burden as its own line.",
        "JobsPage.TAB_LABELS.time",
    ),
    (
        283,
        "jobs.js",
        "Printed under the totals of this ${T('job').toLowerCase()}'s exempt"
        " estimates and ${T('invoices').toLowerCase()}. Each document keeps the"
        " certificate it was made with.",
        "'Exemption certificate'",
    ),
    (
        284,
        "job_costs.js",
        "Labor, equipment hours, mileage, burden, overhead — any job cost that"
        " isn't a bill. Offsets come from Settings → Cost Types.",
        "T('Job Cost Entries')",
    ),
    (
        285,
        "job_costs.js",
        "The accrual books itself out on this date; enter the bill the normal way"
        " when it comes. Clear it to keep the accrual open.",
        "'Reverses on'",
    ),
    (
        286,
        "job_costs.js",
        "Pick equipment to charge its hourly rate; pick an employee for internal"
        " labor (enter the hours and loaded rate). Leave the accounts blank to use"
        " the cost code's / cost type's defaults. The bill not in yet? Pick"
        " ${Terms.text('2010 Accrued Job Costs')} as the offset, never Accounts"
        " Payable.",
        "'Cost lines'",
    ),
]

# Two whose words come through Terms.text() (the company's own words for a
# job and a customer): hint(Terms.text('…'), …)
MOVED_TERMS = [
    (
        326,
        "reports.js",
        "A mailing list by service address and last job date. A customer marked Do"
        " not contact in their notes is flagged here and left out of the export.",
        "Terms.text('Past customers')",
    ),
    (269, "jobs.js", "Click a job to drill down.", "T('Jobs')"),
]

# The facts on the same lines: still in the open, word for word
FACTS = [
    (
        "reports.js",
        "a column per ${noun}${params.exclude_adjusting ? ', adjusting entries left"
        " out' : ''}${data.basis === 'cash' ? ', cash basis' : ''}${data.hide_zero ?"
        " ', zero balances hidden' : ''}.",
    ),
    (
        "reports.js",
        "${ReportsPage._basisWords(data)} &middot; ${data.line_count}"
        " ${data.line_count === 1 ? 'line' : 'lines'}.",
    ),
    (
        "reports.js",
        "${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}"
        "${ReportsPage._basisWords(data)}. ",
    ),
    (
        "reports.js",
        "${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}. Bills"
        " and vendor credits only. ",
    ),
    (
        "reports.js",
        "${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}${data.basis"
        " === 'cash' ? ', cash basis' : ''}.",
    ),
    (
        "reports.js",
        "Actuals for ${escapeHtml(range.start)} — ${escapeHtml(range.end)}; budgets"
        " and committed cost are job-to-date.",
    ),
    ("reports.js", "As of ${escapeHtml(data.as_of)} · cost-to-cost"),
    ("reports.js", "As of ${escapeHtml(data.as_of)}. "),
    ("reports.js", "As of ${escapeHtml(data.as_of)}. ${tie}"),
    (
        "reports.js",
        "T&amp;M tickets dated by ${escapeHtml(data.as_of)} that are not yet billed,"
        " at their bill rates. Status is today's.",
    ),
    (
        "reports.js",
        "${data.count} ${data.count === 1 ? 'group' : 'groups'}${hidden}",
    ),
    (
        "reports.js",
        "Estimates dated ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)},"
        " by status today. ${data.before_tax ? 'Dollars are the estimates before"
        " sales tax (subtotal and markups).' : 'Dollars are the estimate totals,"
        " sales tax included.'}${undated}",
    ),
    # all facts (its dates, and what is job-to-date): stays whole (#347)
    (
        "reports.js",
        "Cost is for ${escapeHtml(range.start)} — ${escapeHtml(range.end)}; bills,"
        " paid, retainage and open A/P are job-to-date.",
    ),
    ("jobs.js", "${Terms.text('Job-to-date figures from posted lines.')}"),
    # the back-dated change order's warning stays under its field
    ("jobs.js", '</label>\n                <input id="co-approved-on"'),
    ("jobs.js", "${oldNote}</div>"),
]

# tortoises each file ends up with, all from the lines above
TORTOISES = {"reports.js": 22, "jobs.js": 8, "job_costs.js": 3}


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def _end_of(src, start):
    """The index just past the call whose "(" is at `start`: strings,
    template literals and the ${...} inside them read as JavaScript does."""
    stack = [["code", 0]]  # a code context counts its brackets
    i = start
    while i < len(src):
        c = src[i]
        top = stack[-1]
        if top[0] == "code":
            if c in "'\"`":
                stack.append(["str", c])
            elif c in "({[":
                top[1] += 1
            elif c in ")]}":
                if c == "}" and top[1] == 0 and len(stack) > 1:
                    stack.pop()  # back into the template literal
                else:
                    top[1] -= 1
                    if top[1] == 0 and len(stack) == 1:
                        return i + 1
        else:
            if c == "\\":
                i += 2
                continue
            if top[1] == "`" and src.startswith("${", i):
                stack.append(["code", 0])
                i += 2
                continue
            if c == top[1]:
                stack.pop()
        i += 1
    raise AssertionError("an unclosed hint(")


def _spans(src):
    """Where each hint(...) call sits in the source: (start, end)."""
    return [
        (m.start(), _end_of(src, m.start() + len("hint")))
        for m in re.finditer(r"\bhint\(", src)
    ]


def _calls(src):
    return [src[a:b] for a, b in _spans(src)]


def _inside(spans, i):
    return any(a <= i < b for a, b in spans)


@pytest.mark.parametrize(
    "ident,name,text,about",
    MOVED + [(i, n, t, a) for i, n, t, a in MOVED_TERMS],
    ids=[str(m[0]) for m in MOVED + MOVED_TERMS],
)
def test_the_clause_is_behind_a_tortoise_and_no_longer_in_the_open(
    ident, name, text, about
):
    src = _src(name)
    spans = _spans(src)
    hits = [i for i in range(len(src)) if src.startswith(text, i)]
    assert hits, f"#{ident}: {text!r} is gone from {name}"
    for i in hits:
        assert _inside(spans, i), f"#{ident}: {text!r} is still in the open in {name}"
    # and the note is named for its report, tab or field
    assert any(
        text in call and call[:-1].rstrip().endswith(about) for call in _calls(src)
    ), f"#{ident}: no hint of {text!r} named {about}"


@pytest.mark.parametrize("name,fact", FACTS, ids=[f[1][:40] for f in FACTS])
def test_the_facts_on_the_same_line_stay_in_the_open(name, fact):
    src = _src(name)
    spans = _spans(src)
    hits = [i for i in range(len(src)) if src.startswith(fact, i)]
    assert hits, f"{fact!r} is gone from {name}"
    assert all(not _inside(spans, i) for i in hits), f"{fact!r} went into a note"


def test_each_file_has_only_these_tortoises_and_no_section_list():
    for name, n in TORTOISES.items():
        src = _src(name)
        assert len(_spans(src)) == n, name
        # no page here shows three or more at once: no hintList
        assert "hintList(" not in src, name


def test_the_old_grey_lines_are_gone():
    reports = _src("reports.js")
    # the clauses' old homes: a grey <p>/<div> holding only the teaching
    for gone in (
        '<p style="font-size:12px; color:var(--gray-500); margin-bottom:6px;">Tax the company owes',
        '<p style="font-size:12px; color:var(--gray-500); margin-bottom:6px;">Sales with no tax',
        '<p style="margin-bottom:10px; color:var(--gray-500); font-size:12px;">${Terms.text(\'A mailing list',
        '<p style="color:var(--gray-500);">Pick the account',
        '<p style="font-size:12px;color:var(--gray-500);margin:8px 0;">Every',
        '<p style="margin-bottom:12px; font-size:12px; color:var(--gray-500);">Entries with',
    ):
        assert gone not in reports, gone
    jobs = _src("jobs.js")
    assert '<div class="hint">Printed under the totals' not in jobs
    assert "<span>Click a type or code" not in jobs
    costs = _src("job_costs.js")
    assert (
        '<div class="toolbar" style="font-size:11px; color:var(--gray-500);">'
        not in costs
    )


def test_the_reclassify_note_shows_only_while_no_account_is_picked():
    src = _src("reports.js")
    assert (
        '<label for="rc-account" style="font-size:11px;">Lines on</label>${accountId ? \'\' : hint('
        in src
    )
    assert "return picker;" in src


# --- the printed, PDF and CSV reports --------------------------------------
# Drawn by the server from their own headers: no button, every fact.

PRINTS = [
    ("wip-schedule/print?as_of=2026-09-30", ["As of 2026-09-30", "cost-to-cost"]),
    ("cost-to-complete/print?as_of=2026-09-30", ["2026-09-30"]),
    ("retainage/print?as_of=2026-09-30", ["2026-09-30"]),
    ("compliance/print?as_of=2026-09-30", ["2026-09-30"]),
    ("commitment-status/print?as_of=2026-09-30", ["2026-09-30"]),
    ("field-ticket-log/print?as_of=2026-09-30", ["2026-09-30"]),
    ("cost-to-complete-forecast/print?as_of=2026-09-30", ["2026-09-30"]),
    (
        "job-budget-vs-actual/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    (
        "job-cost-by-vendor/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    ("sales-by-item/print?start_date=2026-09-01&end_date=2026-09-30", ["2026-09-01"]),
    (
        "purchases-by-item/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    (
        "profit-loss-by-class/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    (
        "profit-loss-by-job/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    (
        "profit-loss-detail/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    (
        "estimate-closing-rate/print?start_date=2026-09-01&end_date=2026-09-30",
        ["2026-09-01"],
    ),
    ("sales-tax/print?start_date=2026-09-01&end_date=2026-09-30", ["2026-09-01"]),
]

NO_BUTTON = ("hint-icon", "hint-pop", "tortoise-hint", "data-hint")


@pytest.fixture(name="construction")
def construction_fixture(client, seed_accounts):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    return client


def _dates_any(body, fact):
    """A date as the print spells it: ISO, or as the company formats it."""
    if fact in body:
        return True
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", fact)
    if not m:
        return False
    y, mo, d = m.groups()
    return any(f in body for f in (f"{mo}/{d}/{y}", f"{int(mo)}/{int(d)}/{y}"))


@pytest.mark.parametrize("path,facts", PRINTS, ids=[p[0].split("?")[0] for p in PRINTS])
def test_the_printed_report_has_no_button_and_keeps_its_facts(
    construction, path, facts
):
    r = construction.get(f"/api/reports/{path}")
    assert r.status_code == 200, (path, r.text[:300])
    body = r.text
    for word in NO_BUTTON:
        assert word not in body, (path, word)
    for fact in facts:
        assert _dates_any(body, fact), (path, fact)


@pytest.mark.parametrize("path,facts", PRINTS, ids=[p[0].split("?")[0] for p in PRINTS])
def test_the_csv_export_has_no_button(construction, path, facts):
    csv_path = path.replace("/print?", "/csv?")
    r = construction.get(f"/api/reports/{csv_path}")
    if r.status_code == 404:
        pytest.skip(f"{csv_path}: no CSV route")
    assert r.status_code == 200, (csv_path, r.text[:300])
    for word in NO_BUTTON:
        assert word not in r.text, (csv_path, word)
    # and no teaching clause was added to it
    for _ident, _name, text, _about in MOVED[:3]:
        assert text.split("${")[0][:30] not in r.text
