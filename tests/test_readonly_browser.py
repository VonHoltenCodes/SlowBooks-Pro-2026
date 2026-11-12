"""A read-only sign-in is offered nothing it cannot do, on any page or in any
document it opens: swept in playwright's Chromium, with the admin's page as
the control.

The server refuses every write from the readonly role ("Your role doesn't
allow this action"). W-L17 hid "+ New" and locked the forms a dialog opens,
but skytech's sweep of all 52 routes at 2.18.0 round 4, read-only against
admin, still found writes on offer: Deactivate and Delete on every account;
the whole Settings page; the Batch Payments form; Quick Entry's Save & Next;
CSV Import; Finish QBO connection; Banking's Connect. macbase1 added the
Attachments "Choose File" in an invoice's and a bill's view. Each let a
read-only user fill something in and then answered 403.

Here every route is opened twice on a bakery's books (the company of
tests/test_theme_contrast.py, served by the app itself): signed in as the
administrator, and as a read-only user made in Settings -> Users. So are the
main document views and some dialogs, and a job's tabs. Every visible,
enabled control is judged:

- a write: a file chooser; anything marked data-write where it is built; a
  "+ ..." create button; a field or button of a form that saves (one not
  marked data-readonly-ok); a handler that calls a page method which sends
  a POST, PUT, PATCH or DELETE (in its own code, or a method it calls), or
  one named in App.WRITE_ACTIONS; one that opens a blank form or an import;
  a field whose value a save reads (a budget cell, a deposit's date);
- otherwise a read: View, Print, Save PDF, reports, exports, filters, and
  opening a record's form, which shows it locked.

The read-only pages must offer no write. The admin's pages must show the
writes the gate agents found (or the sweep is not looking), and every read
the admin is offered must still be offered to the read-only user: View,
Print, Save PDF, the reports, the CSV and IIF exports, the statement picker.
Two kinds of page are the exceptions, each checked for what it says
instead: Payroll and HR, which are the administrator's; and the audit log,
which the server refuses to a read-only sign-in.

A bookkeeper is swept the same way against the administrator, with a second
judgment of each control: is it the administrator's (marked data-admin where
it is built, a backup download, a handler that sends a request the server
keeps for the administrator, or what a form only the administrator saves
sends)? None of those may be offered to a bookkeeper, and every other control
the administrator is offered, reads and writes alike, must be. Payroll, HR and
Migrate Data say they are the administrator's.

Skipped, as one module, where playwright or its Chromium is not installed
(tests/test_readonly_ui.py checks the same marks without a browser, and
tests/test_bookkeeper_ui.py a bookkeeper's pages).
"""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.preferences import UserPreference  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    REDIRECTS,
    _ok,
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
USER_PW = "long-enough-pw"

# The page objects the scripts declare (const InvoicesPage = { ... }). They
# are global bindings but not window properties, and the page's CSP allows
# no eval, so the sweep is handed them by name in its own source.
OBJECTS = sorted(
    {
        name
        for path in JS.glob("*.js")
        for name in re.findall(
            r"^const ([A-Z]\w*) = \{", path.read_text(encoding="utf-8"), re.M
        )
    }
)
OBJECTS_JS = (
    "{"
    + ", ".join(f"{n}: typeof {n} === 'undefined' ? undefined : {n}" for n in OBJECTS)
    + "}"
)

SWEEP = r"""(root) => {
  const OBJ = __OBJECTS__;
  // A request the read-only role is refused
  const WRITE = /\bAPI\s*\.\s*(?:post|put|del|patch)\s*\(|\bAPI\s*\.\s*request\s*\(\s*['"`](?:POST|PUT|PATCH|DELETE)\b|\bmethod\s*:\s*['"`](?:POST|PUT|PATCH|DELETE)['"`]/;
  // A request the server keeps for the administrator (app.main's
  // _ADMIN_WRITE_PREFIXES and _ADMIN_ONLY_PREFIXES, and the routes that
  // call require_admin): refused to a bookkeeper too
  const ADMIN = new RegExp([
    /\bAPI\s*\.\s*(?:post|put|del|patch)\s*\(\s*['"`]\/(?:settings|backups|companies|users|tokens|employees|migration)\b/,
    /\bAPI\s*\.\s*(?:get|post|put|del|patch)\s*\(\s*['"`]\/(?:payroll|tax-forms|benefits|deductions|onboarding|users|tokens)\b/,
    /\bAPI\s*\.\s*(?:post|del)\s*\(\s*['"`]\/uploads\/logo\b/,
    // fetch: an import from another program, the logo
    /['"`]\/api\/(?:migration\/|uploads\/logo)/,
  ].map(r => r.source).join('|'));
  const CALL = /\b(this|[A-Z][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(/g;
  // A function's own code: not its comments, nor the handlers it writes
  // into the HTML it builds (onsubmit="InvoicesPage.save(event)" is the
  // form's call, not the call of the method that opens the form).
  const code = (src) => src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1')
    .replace(/\bon[a-z]+\s*=\s*(\\?["'])[\s\S]*?\1/g, '');
  // Does Obj.method send such a request itself, or through a method it calls?
  const sender = (re) => {
    const memo = new Map();
    const sends = (o, m, depth) => {
      const key = `${o}.${m}/${depth}`;
      if (memo.has(key)) return memo.get(key);
      memo.set(key, false);
      const fn = OBJ[o] && OBJ[o][m];
      let yes = false;
      if (typeof fn === 'function') {
        const c = code(fn.toString());
        yes = re.test(c);
        if (!yes && depth > 0) {
          for (const [, o2, m2] of c.matchAll(CALL)) {
            if (sends(o2 === 'this' ? o : o2, m2, depth - 1)) { yes = true; break; }
          }
        }
      }
      memo.set(key, yes);
      return yes;
    };
    return sends;
  };
  const writes = sender(WRITE);
  const keeps = sender(ADMIN);
  // The source of every method that sends a write: a field whose id or
  // class one of them reads is sent by a save.
  if (!window.__writerSources) {
    window.__writerSources = [];
    for (const obj of Object.values(OBJ)) {
      if (!obj || typeof obj !== 'object') continue;
      for (const m of Object.getOwnPropertyNames(obj)) {
        const fn = obj[m];
        if (typeof fn === 'function' && WRITE.test(code(fn.toString()))) {
          window.__writerSources.push(fn.toString());
        }
      }
    }
  }
  const readBySave = (el) => {
    const refs = [];
    if (el.id) refs.push('#' + el.id, `'${el.id}'`, `"${el.id}"`);
    el.classList.forEach(c => refs.push('.' + c));
    return refs.some(r => window.__writerSources.some(src => src.includes(r)));
  };
  const handlerWhy = (h) => {
    const first = /^\s*(\w+Page\.\w+)\(/.exec(h);
    if (first && App.WRITE_ACTIONS.has(first[1])) return `calls ${first[1]} (App.WRITE_ACTIONS)`;
    for (const [, o, m] of h.matchAll(CALL)) {
      if (o !== 'this' && writes(o, m, 1)) return `calls ${o}.${m}, which saves`;
    }
    if (/\.show(?:\w*Form|Create)\(\s*(?:null\b[^)]*)?\)|\.show\w*Import\(/.test(h)) return 'opens a blank form';
    const click = /getElementById\(\s*['"]([^'"]+)['"]\s*\)\s*\.click\(\)/.exec(h);
    if (click) {
      const target = document.getElementById(click[1]);
      if (target && target.type === 'file') return 'opens a file chooser';
    }
    return null;
  };
  const HANDLERS = ['onclick', 'onchange', 'oninput', 'onblur'];
  const why = (el) => {
    // a person's own preference (Settings → My Preferences) changes nothing
    // in the books, and the server takes it from every sign-in
    if (el.closest('[data-own-preference]')) return null;
    if (el.matches('input[type="file"]')) return 'chooses a file to upload';
    if (el.closest('[data-write]')) return 'marked data-write';
    // "+ New Invoice", "+ Add Garnishment": the app's own mark of a create
    if (el.matches('button, a.btn') && el.textContent.trim().startsWith('+')) return 'creates ("+ …")';
    for (const attr of HANDLERS) {
      const h = el.getAttribute(attr);
      const r = h && handlerWhy(h);
      if (r) return r;
    }
    // a form's fields and buttons (a link in it, Download, is a link)
    const form = el.matches('input, select, textarea, button') && el.closest('form');
    if (form && !/closeModal\(/.test(el.getAttribute('onclick') || '')) {
      const ok = el.closest('[data-readonly-ok]');
      if (!ok) return 'part of a form that saves';
      if (ok.contains(form) && handlerWhy(form.getAttribute('onsubmit') || '')) {
        return 'part of a form that saves, marked data-readonly-ok';
      }
    }
    if (el.matches('input, select, textarea') && readBySave(el)) return 'a field a save sends';
    return null;
  };
  // Is it the administrator's? The server refuses what it sends to a
  // bookkeeper as well.
  const adminWhy = (el) => {
    if (el.closest('[data-admin]')) return 'marked data-admin';
    if ((el.getAttribute('href') || '').includes('/api/backups/download/')) return 'downloads a backup';
    for (const attr of HANDLERS) {
      for (const [, o, m] of (el.getAttribute(attr) || '').matchAll(CALL)) {
        if (o !== 'this' && keeps(o, m, 1)) return `calls ${o}.${m}, which is the administrator's`;
      }
    }
    // what a form the administrator saves sends: its named fields, and a
    // button that submits it (one with no type does)
    const form = el.matches('input, select, textarea, button') && el.closest('form');
    const saves = form && [...(form.getAttribute('onsubmit') || '').matchAll(CALL)]
      .some(([, o, m]) => o !== 'this' && keeps(o, m, 1));
    if (saves && (el.name || (el.matches('button') && el.type === 'submit'))) {
      return 'sent by a form only an administrator saves';
    }
    return null;
  };
  const label = (el) => {
    let t;
    if (el.matches('select')) {
      t = el.getAttribute('aria-label') || el.id || el.name
        || (el.options[0] ? el.options[0].textContent : 'select');
    } else if (el.matches('input, textarea')) {
      t = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || el.id
        || el.type;
    } else {
      t = el.getAttribute('aria-label') || el.textContent || el.title || '';
    }
    return t.trim().replace(/\s+/g, ' ').slice(0, 60);
  };
  const box = document.querySelector(root);
  const out = [];
  if (!box) return out;
  const controls = box.querySelectorAll(
    'button, a[href], input, select, textarea, summary, [onclick], [onchange], [oninput], [onblur]');
  for (const el of controls) {
    if (el.matches('form, label')) continue;
    if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
    if (el.disabled || el.closest('fieldset:disabled')) continue;
    if (el.matches('input, textarea') && el.readOnly) continue;
    const handler = HANDLERS.map(a => el.getAttribute(a)).find(Boolean) || '';
    const tag = el.tagName.toLowerCase() + (el.matches('input') ? `[${el.type}]` : '');
    const text = label(el);
    const siblings = el.parentElement ? [...el.parentElement.querySelectorAll('button, a.btn')] : [];
    out.push({
      key: `${tag} "${text}" ${handler || el.getAttribute('href') || ''}`.trim(),
      text,
      why: why(el),
      admin_why: adminWhy(el),
      // the invoice row's Edit, hidden where View shows the record
      beside_view: text === 'Edit' && siblings.some(b => b.textContent.trim() === 'View'),
    });
  }
  return out;
}""".replace("__OBJECTS__", OBJECTS_JS)

OPEN = "() => !document.getElementById('modal-overlay').classList.contains('hidden')"

# What the two gate agents found still offered to a read-only sign-in on
# 079732a (skytech's W-L17 leftovers, and macbase1's Choose File). The
# admin's pages must show each as a write, or the sweep is not looking.
FOUND_ON_PAGES = {
    "#/accounts": {"Deactivate", "Delete", "Import…"},
    "#/settings": {
        "Save Settings",
        "Create Backup",
        "Seed Default Templates",
        "Create default offset accounts",
        "Load standard divisions and common sections",
        "Send Test Email",
        "Add Class",
        "Add Cost Type",
        "Add Cost Code",
        "Add Equipment",
        "Rename",
        "Deactivate",
        "Import CSV",
        "Save AI settings",
        "Test",
    },
    "#/batch-payments": {"Select All", "Apply Batch Payment"},
    # the other deposits' rows on the slip (#792)
    "#/deposits": {"Add line", "Make Deposit"},
    "#/quick-entry": {"Save & Next (Ctrl+Enter)"},
    "#/bill-entry": {"Save & Next (Ctrl+Enter)"},
    "#/csv": {"Import"},
    "#/banking": {"Connect"},
}
FOUND_IN_DIALOGS = {
    "InvoicesPage.view({sent})": {"file"},
    "BillsPage.view({bill})": {"file"},
    "ExpensesPage.showDetail({expense})": {"file"},
    "ReportsPage.arAging()": {
        "Apply Late Fees",
        "Email All Overdue",
        "Send Collection Letters",
    },
}

# The main document views, and dialogs a read-only user opens.
DIALOGS = [
    ("#/invoices", "InvoicesPage.view({sent})"),
    ("#/invoices", "InvoicesPage.view({partial})"),
    ("#/invoices", "InvoicesPage.showForm({draft})"),
    ("#/sales-receipts", "SalesReceiptsPage.view({receipt})"),
    ("#/estimates", "EstimatesPage.view({estimate})"),
    ("#/credit-memos", "CreditMemosPage.view({memo})"),
    ("#/payments", "PaymentsPage.view({payment})"),
    ("#/deposits", "DepositsPage.view({deposit})"),
    ("#/bills", "BillsPage.view({bill})"),
    ("#/bills", "BillsPage.view({paid_bill})"),
    ("#/bills", "BillsPage.viewPayment({bill_payment})"),
    ("#/purchase-orders", "PurchaseOrdersPage.view({po})"),
    ("#/vendor-credits", "VendorCreditsPage.view({vendor_credit})"),
    ("#/expenses", "ExpensesPage.showDetail({expense})"),
    ("#/journal", "JournalPage.view({journal})"),
    ("#/job-costs", "JobCostsPage.view({job_cost})"),
    ("#/customers", "CustomersPage.showDetails({customer})"),
    ("#/vendors", "VendorsPage.showForm({vendor})"),
    ("#/vendors", "VendorsPage.showDetails({vendor})"),
    ("#/items", "ItemsPage.showMovements({loaf})"),
    ("#/accounts", "App.showAccountForm({checking})"),
    ("#/banking/{checking}", "BankingPage.showReconciliations({checking})"),
    ("#/reports", "ReportsPage.arAging()"),
    ("#/reports", "ReportsPage.profitLoss()"),
    ("#/reports", "ReportsPage.customerStatementPicker()"),
    ("#/reports", "ReportsPage.report1099()"),
    ("#/settings", "SettingsPage.editTemplate({template})"),
    # a record's form, where it is the only view: shown locked, including
    # what it fills in after it opens (the lines, the pickers)
    ("#/customers", "CustomersPage.showForm({customer})"),
    # an estimate's form, without its cost side for a read-only sign-in (#824)
    ("#/estimates", "EstimatesPage.showForm({estimate})"),
    ("#/items", "ItemsPage.showForm({loaf})"),
    ("#/jobs", "JobsPage.showForm({job})"),
    ("#/recurring", "RecurringPage.showForm({recurring})"),
    ("#/reseller-permits", "ResellerPermitsPage.showForm({permit_soon})"),
    ("#/bank-rules", "BankRulesPage.showForm({bank_rule})"),
    ("#/fixed-assets", "FixedAssetsPage.showAssetForm({asset})"),
    ("#/hr/pto", "PTOPage.showPolicyForm({pto_policy})"),
]
JOB_TABS = ("costs", "budget", "transactions", "time")
# A read the server refuses to a read-only sign-in: the audit log snapshots
# whole records (app.main._role_allows), and the page says whose it is
# (it showed the server's refusal until skytech's round-6 note).
REFUSED_READS = {"#/audit": "Audit Log isn't open to a read-only sign-in"}


def _user(company, db_session, username, role):
    """A user made in Settings -> Users, signed in on a client of its own
    (the admin's client stays signed in). The dashboard's cards are
    remembered per login and a read-only user cannot save a layout, so each
    is given the admin's: every card, as seed_books left it."""
    user = _ok(
        company.post(
            "/api/users",
            json={"username": username, "password": USER_PW, "role": role},
        )
    )
    layout = _ok(company.get("/api/preferences/dashboard"))["value"]
    assert layout and layout["order"]
    db_session.add(
        UserPreference(user_id=user["id"], key="dashboard", value=json.dumps(layout))
    )
    db_session.commit()
    signed_in = TestClient(app)
    r = signed_in.post(
        "/api/auth/login", json={"username": username, "password": USER_PW}
    )
    assert r.status_code == 200, r.text
    return signed_in


def _reader(company, db_session):
    return _user(company, db_session, "rita", "readonly")


def _keeper(company, db_session):
    return _user(company, db_session, "kim", "bookkeeper")


def _signed_in(browser, client, role):
    page, handled = _open(browser, client)
    # the role arrives with /api/auth/status, after the first page
    page.wait_for_function(f"() => App.role === '{role}'")
    settle(page, handled)
    return page, handled


def _settings_groups(page):
    """The groups Settings lists for this sign-in, in order (#720)."""
    return page.evaluate(
        "() => [...document.querySelectorAll('#settings-groups [role=\"tab\"]')]"
        ".filter(t => !t.hidden).map(t => t.dataset.groupTab)"
    )


def _sweep_pages(browser, client, role, books):
    page, handled = _signed_in(browser, client, role)
    swept, headings, texts, admin_notes = {}, {}, {}, {}
    try:
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        routes = [f"#{p}" for p in paths if p not in REDIRECTS]
        routes += [f"#/jobs/{books['job']}", f"#/banking/{books['checking']}"]
        # payroll and HR: the administrator's pages
        # (a document route, #/payroll/:id, is swept through no address)
        admin_only = {
            f"#{p}"
            for p in page.evaluate(
                """() => Object.keys(App.routes).filter(k =>
                    !k.includes('/:') && App.ADMIN_ONLY_PAGES.includes(App.routes[k].page))"""
            )
        }
        for route in routes:
            _visit(page, handled, route)
            if route == "#/analytics":
                # its list of AI analyses arrives after the charts are drawn,
                # which can outlast the quiet settle waits for
                page.wait_for_function("() => AnalyticsPage.state.aiActions !== null")
                settle(page, handled)
            headings[route] = page.evaluate(
                "() => (document.querySelector('#page-content h2, #page-content h3')"
                " || {}).textContent || ''"
            ).strip()
            texts[route] = page.inner_text("#page-content")
            swept[route], admin_notes[route] = [], []
            labels = page.evaluate(
                "() => typeof SettingsPage === 'undefined' ? []"
                " : SettingsPage.GROUPS.map(g => g.label)"
            )
            # Settings shows one group of its sections at a time (#720):
            # the page is each group in turn
            for group in _settings_groups(page) if route == "#/settings" else [None]:
                if group:
                    page.evaluate("(g) => SettingsPage.openGroup(g)", group)
                seen = {c["key"] for c in swept[route]}
                swept[route] += [
                    c
                    for c in page.evaluate(SWEEP, "#page-content")
                    if c["key"] not in seen
                    # the list's own tabs: which groups a sign-in is shown
                    # is tests/test_browser_settings_groups.py's
                    and not (c["key"].startswith('button "') and c["text"] in labels)
                ]
                # the sentences that say a control is the administrator's
                admin_notes[route] += [
                    n
                    for n in page.evaluate(
                        "() => [...document.querySelectorAll('#page-content [data-admin-note]')]"
                        ".filter(n => n.getClientRects().length).map(n => n.textContent.trim())"
                    )
                    if n not in admin_notes[route]
                ]
        # a job's other tabs re-render in place
        for tab in JOB_TABS:
            page.evaluate("async (t) => { await JobsPage.setTab(t); }", tab)
            settle(page, handled)
            swept[f"#/jobs/{books['job']} ({tab})"] = page.evaluate(
                SWEEP, "#page-content"
            )
        sidebar = page.evaluate(
            """() => [...document.querySelectorAll('#sidebar .nav-link')]
            .filter(a => a.getClientRects().length).map(a => a.dataset.page)"""
        )
        notes = {
            route: page.evaluate(
                "async (h) => { await App.navigate(h);"
                " return document.querySelectorAll('#page-content .readonly-note').length; }",
                route,
            )
            for route in (
                "#/settings",
                "#/quick-entry",
                "#/bill-entry",
                "#/batch-payments",
            )
        }
    finally:
        page.close()
    return {
        "swept": swept,
        "headings": headings,
        "texts": texts,
        "admin_notes": admin_notes,
        "admin_only": admin_only,
        "sidebar": sidebar,
        "notes": notes,
    }


def _sweep_dialogs(browser, client, role, books, columns=None):
    """Every dialog's controls; with `columns`, each dialog's column headings
    as well (the estimate form's Cost column is the administrator's, #824)."""
    page, handled = _signed_in(browser, client, role)
    swept, unopened = {}, []
    try:
        for route, opener in DIALOGS:
            _visit(page, handled, route.format(**books))
            page.evaluate("() => closeModal()")
            try:
                page.evaluate(f"async () => {{ await {opener.format(**books)}; }}")
                page.wait_for_function(OPEN, timeout=5000)
            except Exception as exc:  # the call failed, or opened nothing
                unopened.append(f"{opener}: {str(exc).splitlines()[0]}")
                continue
            settle(page, handled)
            swept[opener] = page.evaluate(SWEEP, "#modal")
            if columns is not None:
                columns[opener] = page.evaluate(
                    "() => [...document.querySelectorAll('#modal th')]"
                    ".map(th => th.textContent.trim())"
                )
        page.evaluate("() => closeModal()")
    finally:
        page.close()
    assert unopened == [], (role, unopened)
    return swept


def _offered_writes(swept, judge="why"):
    """{where: ["<control> — why", ...]} for every write on offer (judge
    "admin_why": every control of the administrator's)."""
    out = {}
    for where, controls in swept.items():
        seen = sorted({f"{c['key']} — {c[judge]}" for c in controls if c[judge]})
        if seen:
            out[where] = seen
    return out


def _reads(controls):
    return {c["key"] for c in controls if not c["why"] and not c["beside_view"]}


# Reads that are the administrator's alone: a backup is the whole company,
# password hashes and payroll records included (the server refuses the rest).
ADMIN_ONLY_READS = ("/api/backups/download/",)


def _lost_reads(admin, reader):
    """The reads the admin is offered that the read-only user is not."""
    out = {}
    for where, controls in admin.items():
        shown = {c["key"] for c in reader.get(where, [])}
        lost = sorted(
            k
            for k in _reads(controls) - shown
            if not any(ref in str(k) for ref in ADMIN_ONLY_READS)
        )
        if lost:
            out[where] = lost
    return out


def _unseen(admin, found, judge="why"):
    """The gate's findings the sweep did not see as writes on the admin's
    page (judge "admin_why": as the administrator's)."""
    out = {}
    for where, labels in found.items():
        writes = {c["text"] for c in admin.get(where, []) if c[judge]}
        writes |= {
            "file" for c in admin.get(where, []) if c["key"].startswith("input[file]")
        }
        missing = sorted(labels - writes)
        if missing:
            out[where] = missing
    return out


def _lost_to_keeper(admin, keeper):
    """What the admin is offered, and is not the administrator's, that a
    bookkeeper is not: reads and writes alike."""
    out = {}
    for where, controls in admin.items():
        shown = {c["key"] for c in keeper.get(where, [])}
        lost = sorted({c["key"] for c in controls if not c["admin_why"]} - shown)
        if lost:
            out[where] = lost
    return out


def test_no_page_offers_a_read_only_sign_in_a_write(
    browser, company, books, db_session
):
    reader = _reader(company, db_session)
    admin_run = _sweep_pages(browser, company, "admin", books)
    ro_run = _sweep_pages(browser, reader, "readonly", books)
    admin, ro = admin_run["swept"], ro_run["swept"]

    assert len(admin) >= 50 and set(admin) == set(ro), sorted(set(admin) ^ set(ro))
    # the sweep sees the writes on the admin's pages: the gate's findings
    assert _unseen(admin, FOUND_ON_PAGES) == {}
    # none is offered to the read-only sign-in, on any page
    assert _offered_writes(ro) == {}
    # Payroll and HR say whose they are, rather than half loading
    hr = ro_run["admin_only"]
    assert {"#/payroll", "#/employees", "#/hr/onboarding"} <= hr
    assert {
        route: ro_run["headings"][route]
        for route in hr
        if not ro_run["headings"][route].endswith("is for administrators")
    } == {}
    assert all(admin_run["headings"][r] for r in hr)
    # the audit log is a read the server refuses it, and the page says so
    for route, sentence in REFUSED_READS.items():
        assert sentence in ro_run["texts"][route], ro_run["texts"][route]
    # and it keeps every other read: View, Print, the reports, the exports
    kept = {
        route: c
        for route, c in admin.items()
        if route not in hr and route not in REFUSED_READS
    }
    assert _lost_reads(kept, ro) == {}
    # the forms on a page are shown locked, with the sentence that says why
    assert ro_run["notes"] == {
        "#/settings": 1,
        "#/quick-entry": 1,
        "#/bill-entry": 1,
        "#/batch-payments": 1,
    }
    assert set(admin_run["notes"].values()) == {0}
    # the sidebar does not offer the pages that only enter things
    only_enter = {"batch-payments", "bill-entry", "opening-balances", "migrate"}
    assert only_enter <= set(admin_run["sidebar"])
    assert only_enter & set(ro_run["sidebar"]) == set()
    assert {"invoices", "reports", "accounts", "settings"} <= set(ro_run["sidebar"])


def test_no_document_or_dialog_offers_a_read_only_sign_in_a_write(
    browser, company, books, db_session
):
    reader = _reader(company, db_session)
    admin_columns, ro_columns = {}, {}
    admin = _sweep_dialogs(browser, company, "admin", books, admin_columns)
    ro = _sweep_dialogs(browser, reader, "readonly", books, ro_columns)

    assert _unseen(admin, FOUND_IN_DIALOGS) == {}
    assert _offered_writes(ro) == {}
    assert _lost_reads(admin, ro) == {}
    # Copy Payment Link is the administrator's and a bookkeeper's (#822):
    # the server refuses the link to a read-only sign-in, read or made
    for opener in ("InvoicesPage.view({sent})", "InvoicesPage.view({partial})"):
        assert "Copy Payment Link" in {c["text"] for c in admin[opener]}, opener
        assert "Copy Payment Link" not in {c["text"] for c in ro[opener]}, opener
    # the statement picker only opens a document: it stays usable
    picker = {c["text"] for c in ro["ReportsPage.customerStatementPicker()"]}
    assert "Generate PDF" in picker
    # an estimate's cost side is the contractor's own (#824): the read-only
    # form has no Cost column, the administrator's does, and every other
    # column is the same
    form = "EstimatesPage.showForm({estimate})"
    assert "Cost" in admin_columns[form]
    assert "Cost" not in ro_columns[form]
    assert [c for c in admin_columns[form] if c != "Cost"] == ro_columns[form]


def test_a_read_only_sign_in_leaves_settings_unasked(
    browser, company, books, db_session
):
    """Settings is locked for a read-only sign-in, and a locked field is not
    in FormData. When the role arrived after the page (a bookmarked
    #/settings), the page compared as changed with the copy taken before it
    was locked, and leaving asked "You have unsaved changes in Settings" of
    someone who could not change anything."""
    reader = _reader(company, db_session)
    page, handled = _signed_in(browser, reader, "readonly")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    try:
        # the order of a first page: Settings is in before the role is known
        page.evaluate(
            """() => { App._roleObserver.disconnect(); App._roleObserver = null;
            App.role = 'admin'; document.body.classList.remove('role-readonly'); }"""
        )
        _visit(page, handled, "#/settings")
        page.evaluate("() => App.setRole('readonly')")
        settle(page, handled)
        assert page.evaluate(
            "() => document.querySelector('#settings-form [name=company_name]').disabled"
        )
        _visit(page, handled, "#/reports")
        assert (page.evaluate("location.hash"), asked) == ("#/reports", [])
    finally:
        page.close()


# ---- a bookkeeper ------------------------------------------------------------
# A bookkeeper keeps the daily books, but the administrator's writes are
# refused to it: company settings, backups, new company files, the logo,
# Migrate Data. The pages
# offered them all the same (2.18.0). The controls the owner and the gate
# named, as the sweep labels them: the admin's pages must show each as the
# administrator's, or the sweep is not looking.
ADMIN_ON_PAGES = {
    "#/settings": {
        "Save Settings",
        "Send Test Email",
        "Create Backup",
        "Restore…",
        "Download",
        "company_name",
        # the logo's file picker, by its name (#198: it was known by its id)
        "Logo image",
        "ocr-engine-pref",
        "Save AI settings",
    },
    "#/companies": {"+ New Company"},
}
KEEPER_NOTES = {
    "#/settings": [
        "Company settings are changed by an administrator.",
        "AI settings are changed by an administrator.",
        "Backups are made, downloaded and restored by an administrator.",
    ],
    "#/companies": ["New company files are created by an administrator."],
}


def test_no_page_offers_a_bookkeeper_an_administrators_control(
    browser, company, books, db_session
):
    keeper = _keeper(company, db_session)
    admin_run = _sweep_pages(browser, company, "admin", books)
    keeper_run = _sweep_pages(browser, keeper, "bookkeeper", books)
    admin, kept = admin_run["swept"], keeper_run["swept"]

    assert len(admin) >= 50 and set(admin) == set(kept), sorted(set(admin) ^ set(kept))
    # the sweep sees the administrator's controls on the admin's pages
    assert _unseen(admin, ADMIN_ON_PAGES, "admin_why") == {}
    # none is offered to the bookkeeper, on any page
    assert _offered_writes(kept, "admin_why") == {}
    # Payroll, HR and Migrate Data say whose they are, rather than half loading
    only = keeper_run["admin_only"]
    assert {"#/payroll", "#/employees", "#/migrate"} <= only
    assert {
        route: keeper_run["headings"][route]
        for route in only
        if not keeper_run["headings"][route].endswith("is for administrators")
    } == {}
    # everything else stays: Settings' lists, every other page's reads and
    # writes
    assert (
        _lost_to_keeper({r: c for r, c in admin.items() if r not in only}, kept) == {}
    )
    # a sentence says why, where the controls were
    assert {r: keeper_run["admin_notes"][r] for r in KEEPER_NOTES} == KEEPER_NOTES
    assert {r: n for r, n in admin_run["admin_notes"].items() if n} == {}
    # the sidebar leaves out Migrate Data, and keeps the pages a bookkeeper uses
    assert "migrate" in admin_run["sidebar"]
    assert "migrate" not in keeper_run["sidebar"]
    assert {
        "batch-payments",
        "opening-balances",
        "settings",
        "companies",
    } <= set(keeper_run["sidebar"])


def test_a_bookkeeper_on_a_first_page_of_settings(browser, company, books, db_session):
    """Settings drawn before the role is known (a bookmarked #/settings): once
    it arrives, the company settings are locked with the sentence and the
    lists stay the bookkeeper's; opening an email template sends only its own
    requests (its Edit button used to submit the whole Settings form); and
    leaving does not ask about unsaved changes."""
    keeper = _keeper(company, db_session)
    page, handled = _signed_in(browser, keeper, "bookkeeper")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    try:
        # the order of a first page: Settings is in before the role is known
        page.evaluate("""() => { if (App._roleObserver) App._roleObserver.disconnect();
            App._roleObserver = null; App.role = 'admin'; }""")
        _visit(page, handled, "#/settings")
        assert page.evaluate("() => !!document.getElementById('logo-upload')")
        page.evaluate("() => App.setRole('bookkeeper')")
        settle(page, handled)
        # Settings shows one group at a time (#720): what each group shows
        got = page.evaluate("""() => {
            const shown = el => !!el && el.getClientRects().length > 0;
            const form = document.getElementById('settings-form');
            const named = [...form.querySelectorAll('[name]')];
            const add = document.getElementById('new-class-name');
            const got = {
                fields: named.length,
                open: named.filter(f => !f.disabled).map(f => f.name),
                save: false, picker: false, notes: [], add_class: false,
            };
            const groups = [...document.querySelectorAll('#settings-groups [role="tab"]')]
                .filter(t => !t.hidden).map(t => t.dataset.groupTab);
            for (const g of groups) {
                SettingsPage.openGroup(g);
                got.save = got.save || shown(document.getElementById('settings-save-btn'));
                got.picker = got.picker || shown(document.getElementById('logo-upload'));
                for (const n of [...form.querySelectorAll('[data-admin-note]')].filter(shown)) {
                    const t = n.textContent.trim();
                    if (!got.notes.includes(t)) got.notes.push(t);
                }
                got.add_class = got.add_class || (shown(add) && !add.disabled);
            }
            SettingsPage.openGroup('company');
            return got;
        }""")
        # 49 named fields since the QuickBooks Online section left (2.23.2)
        assert got["fields"] >= 45 and got["open"] == []
        assert (got["save"], got["picker"], got["add_class"]) == (False, False, True)
        assert got["notes"] == KEEPER_NOTES["#/settings"]
        # opening a template asks for the template, and sends no settings
        before = len(handled)
        page.evaluate(
            "() => document.querySelector('#email-template-list button').click()"
        )
        page.wait_for_function(OPEN)
        settle(page, handled)
        sent = handled[before:]
        assert sent and all(
            p.startswith(("/api/email-templates/", "/api/invoices?")) for p in sent
        ), sent
        assert page.evaluate("() => document.querySelectorAll('.toast').length") == 0
        page.evaluate("() => closeModal()")
        _visit(page, handled, "#/reports")
        assert (page.evaluate("location.hash"), asked) == ("#/reports", [])
    finally:
        page.close()


# --- a construction job's pay applications ------------------------------


def _construction_job_with_a_draft(client, customer_id):
    """A construction company's job with a schedule of values and an open
    draft pay application on it (the bakery's jobs have no Pay apps tab)."""
    assert _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))
    job = _ok(
        client.post("/api/jobs", json={"customer_id": customer_id, "name": "Tower"})
    )
    _ok(
        client.put(
            f"/api/jobs/{job['id']}/contract",
            json={
                "contract_type": "lump_sum",
                "contract_amount": 100000,
                "retainage_pct": 10,
                "contract_date": "2026-01-02",
            },
        )
    )
    _ok(
        client.put(
            f"/api/jobs/{job['id']}/sov",
            json={"lines": [{"description": "Base", "scheduled_value": 100000}]},
        )
    )
    draft = _ok(
        client.post(
            "/api/pay-applications", json={"job_id": job["id"], "date": "2026-04-30"}
        )
    )
    assert draft["status"] == "draft" and draft["lines"]
    return job["id"], draft["id"]


def _signed_in_as(client, username, role):
    """A user of the role, made by the admin, signed in on a client of its
    own."""
    _ok(
        client.post(
            "/api/users",
            json={"username": username, "password": USER_PW, "role": role},
        )
    )
    signed_in = TestClient(app)
    r = signed_in.post(
        "/api/auth/login", json={"username": username, "password": USER_PW}
    )
    assert r.status_code == 200, r.text
    return signed_in


def _pay_app_buttons(browser, client, role, job_id, draft_id):
    """The buttons shown on the job's Pay apps tab, and, for a sign-in that
    may open it (Edit is hidden from the others: opening a draft refreshes
    its lines), in the draft's form."""
    page, handled = _signed_in(browser, client, role)
    try:
        _visit(page, handled, f"#/jobs/{job_id}")
        page.evaluate("async () => { await JobsPage.setTab('payapps'); }")
        settle(page, handled)
        # the pay applications' own buttons: their handlers are JobsPage.pa*
        shown = """() => [...document.querySelectorAll('#page-content button')]
            .filter(b => b.getClientRects().length && !b.disabled)
            .filter(b => /JobsPage\\.pa|pay-applications/.test(b.getAttribute('onclick') || '')
                || b.getAttribute('type') === 'submit')
            .map(b => b.textContent.trim())"""
        tab = page.evaluate(shown)
        form = []
        if "Edit" in tab:
            page.evaluate("async (id) => { await JobsPage.paEdit(id); }", draft_id)
            settle(page, handled)
            form = page.evaluate(shown)
    finally:
        page.close()
    return tab, form


def test_a_pay_application_offers_delete_only_to_a_sign_in_that_can_delete_it(
    browser, client, seed_accounts, seed_customer
):
    """An open pay application's Delete is offered to the administrator, on
    its row and in its form; a read-only or accountant sign-in, which the
    server refuses, is not offered it."""
    job_id, draft_id = _construction_job_with_a_draft(client, seed_customer.id)
    tab, form = _pay_app_buttons(browser, client, "admin", job_id, draft_id)
    assert "Delete" in tab and "Delete" in form
    for role in ("readonly", "accountant"):
        signed_in = _signed_in_as(client, f"pa-{role}", role)
        tab, form = _pay_app_buttons(browser, signed_in, role, job_id, draft_id)
        assert "Edit" not in tab and "PDF" in tab, (role, tab)
        assert "Delete" not in tab, (role, tab)
        # and the server refuses it
        r = signed_in.delete(f"/api/pay-applications/{draft_id}")
        assert r.status_code == 403, (role, r.text)
