"""Terminology guards: the words a nonprofit sees come from one dictionary.

Source-level checks keep the JS and Python dictionaries identical, keep
protected words (Sales, Vendor ...) out of it, and make sure every shell
label that carries a business word is an exact key so the boot-time walk
(App.applyTerminology) actually swaps it. Behavioural checks pin the
company_type setting itself."""

import json
import re
from pathlib import Path

from app.services import terminology
from app.services.terminology import NONPROFIT, PROTECTED_WORDS, Terms

ROOT = Path(__file__).resolve().parent.parent
TERMS_JS = ROOT / "app/static/js/terms.js"
INDEX = ROOT / "index.html"

BUSINESS_WORD = re.compile(
    r"\b(Customers?|Invoices?|Sales Receipts?|Class(es)?|Jobs?)\b"
)


def _js_dictionary() -> dict:
    src = TERMS_JS.read_text(encoding="utf-8")
    m = re.search(r"const TERMS_NONPROFIT = (\{.*?\});", src, re.S)
    assert m, "TERMS_NONPROFIT literal not found in terms.js"
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# Dictionary shape
# ---------------------------------------------------------------------------


def test_js_and_python_dictionaries_agree():
    assert _js_dictionary() == NONPROFIT


def test_dictionary_never_maps_protected_words():
    for word in PROTECTED_WORDS:
        assert word not in NONPROFIT, word
    for key in NONPROFIT:
        assert not key.startswith("Sales ") or key.startswith("Sales Receipt"), key


def test_keys_are_business_words_and_values_differ():
    for key, value in NONPROFIT.items():
        assert key != value, key
        assert key[0].isupper(), key


# ---------------------------------------------------------------------------
# Lookup rules (Python; the JS mirror follows the same rules)
# ---------------------------------------------------------------------------


def test_business_mode_is_identity():
    t = Terms("business")
    assert t("Invoice") == "Invoice"
    assert t.text("Search customers, invoices...") == "Search customers, invoices..."
    assert not t.is_nonprofit
    assert Terms("banana").mode == "business"


def test_nonprofit_lookup_handles_plural_and_case():
    t = Terms("nonprofit")
    assert t("Invoice") == "Pledge"
    assert t("Invoices") == "Pledges"
    assert t("invoices") == "pledges"
    assert t("customer") == "donor"
    assert t("Classes") == "Funds"
    assert t("Vendor") == "Vendor"
    assert t("Sales Tax") == "Sales Tax"
    assert t("") == ""


def test_prose_swap_keeps_case_and_leaves_sales_tax_alone():
    t = Terms("nonprofit")
    assert t.text("Search customers, invoices...") == "Search donors, pledges..."
    assert t.text("No invoices yet") == "No pledges yet"
    assert t.text("Sales Tax Report") == "Sales Tax Report"
    assert t.text("CUSTOMER") == "DONOR"
    assert t.text("Profit & Loss by Class") == "Statement of Activities by Fund"


def test_filename_forms():
    t = Terms("nonprofit")
    assert t.slug("Profit & Loss") == "statement-of-activities"
    assert t.compact("Sales Receipt") == "Donation"
    assert Terms("business").slug("Profit & Loss") == "profit-loss"
    assert Terms("business").compact("Sales Receipt") == "SalesReceipt"


# ---------------------------------------------------------------------------
# The shell: every label the boot walk touches must be an exact key
# ---------------------------------------------------------------------------


def _shell_texts():
    html = INDEX.read_text(encoding="utf-8")
    texts = re.findall(r'<li class="nav-section">([^<]+)</li>', html)
    for m in re.finditer(
        r'<a href="#/[^"]*" class="nav-link[^"]*"[^>]*>.*?</a>', html, re.S
    ):
        inner = re.sub(r"<span[^>]*>.*?</span>", "", m.group(0), flags=re.S)
        texts.append(re.sub(r"<[^>]+>", "", inner).strip())
    texts += re.findall(
        r'<button class="tb-btn" data-action="[^"]*">([^<]+)</button>', html
    )
    return [t.replace("&amp;", "&") for t in texts]


def test_nav_and_toolbar_labels_with_business_words_are_keys():
    missing = [
        t for t in _shell_texts() if BUSINESS_WORD.search(t) and t not in NONPROFIT
    ]
    assert not missing, missing


def test_shell_loads_terms_before_pages():
    html = INDEX.read_text(encoding="utf-8")
    assert html.index("terms.js") < html.index("customers.js")
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    # the first page waits for the settings (and the sign-in's state) since 2.24.0
    assert (
        "Promise.all([App.loadCompanySettings(), App.initSystemInfo()]).then(" in app_js
    )
    assert "App.applyTerminology();" in app_js


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_company_type_defaults_to_business(client):
    assert client.get("/api/settings").json()["company_type"] == "business"


def test_company_type_round_trips_and_rejects_unknown_values(client):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    assert r.json()["company_type"] == "nonprofit"
    r = client.put("/api/settings", json={"company_type": "banana"})
    assert r.status_code == 422
    assert client.get("/api/settings").json()["company_type"] == "nonprofit"
    r = client.put("/api/settings", json={"company_type": "business"})
    assert r.json()["company_type"] == "business"


def test_entity_type_round_trips_and_rejects_unknown_values(client):
    # unset by default so existing companies see no change (#488)
    assert client.get("/api/settings").json()["entity_type"] == ""
    r = client.put("/api/settings", json={"entity_type": "s_corp"})
    assert r.status_code == 200, r.text
    assert r.json()["entity_type"] == "s_corp"
    # the empty string is a valid value: it clears the setting
    assert client.put("/api/settings", json={"entity_type": ""}).status_code == 200
    r = client.put("/api/settings", json={"entity_type": "sole_prop"})
    assert r.status_code == 422


def test_llc_tax_election_round_trips_and_rejects_unknown_values(client):
    assert client.get("/api/settings").json()["llc_tax_election"] == ""
    r = client.put("/api/settings", json={"llc_tax_election": "disregarded"})
    assert r.status_code == 200, r.text
    assert r.json()["llc_tax_election"] == "disregarded"
    assert client.put("/api/settings", json={"llc_tax_election": ""}).status_code == 200
    r = client.put("/api/settings", json={"llc_tax_election": "banana"})
    assert r.status_code == 422


def test_terms_from_db_follow_the_setting(client, db_session):
    assert not terminology.terms_from_db(db_session).is_nonprofit
    client.put("/api/settings", json={"company_type": "nonprofit"})
    assert terminology.terms_from_db(db_session).is_nonprofit
    assert terminology.terms_for({"company_type": "nonprofit"})("Class") == "Fund"
    assert terminology.terms_for(None)("Class") == "Class"


# ---------------------------------------------------------------------------
# Setup accounts (what the Settings page calls when switching to nonprofit)
# ---------------------------------------------------------------------------


def test_setup_accounts_is_idempotent_and_yields_taken_numbers(client, seed_accounts):
    # 3300 is free in the seed chart; take 3400 first to prove the yield.
    r = client.post(
        "/api/accounts",
        json={
            "name": "Old Reserve",
            "account_number": "3400",
            "account_type": "equity",
        },
    )
    assert r.status_code in (200, 201), r.text

    first = client.post("/api/nonprofit/setup-accounts")
    assert first.status_code == 200, first.text
    by_name = {a["name"]: a for a in first.json()}
    assert by_name["Net Assets Without Donor Restrictions"]["account_number"] == "3300"
    assert by_name["Net Assets With Donor Restrictions"]["account_type"] == "equity"
    assert by_name["Net Assets With Donor Restrictions"]["account_number"] is None
    assert by_name["In-Kind Contributions"]["account_number"] == "4400"
    assert by_name["Bad Debt Expense"]["account_number"] == "6960"  # seeded
    assert all(a["is_system"] for a in first.json())

    second = client.post("/api/nonprofit/setup-accounts")
    assert [a["id"] for a in second.json()] == [a["id"] for a in first.json()]
    assert client.get("/api/accounts").status_code == 200
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert names.count("Bad Debt Expense") == 1


# ---------------------------------------------------------------------------
# Chokepoints: labels in these positions must go through T()
# ---------------------------------------------------------------------------

# Interop and form pages keep their own vocabulary on purpose: QuickBooks
# words on the import screens, IRS words on the tax screens, workers-comp
# "class code" on employees.
LEAVE_ALONE = {
    "iif.js",
    "ocr.js",
    "ocr_canvas.js",
    "migration.js",
    "tax.js",
    "employees.js",
    "companies.js",
    "terms.js",
}
CHOKEPOINT_PATTERNS = [
    # page headers, table headers, form labels, modal titles
    r"<h2>[^<$]*\b(Customers?|Invoices?|Sales Receipts?|Class(es)?|Jobs?)\b[^<]*</h2>",
    r'<th scope="col"(?: class="amount")?>(Customers?|Invoices?|Sales Receipts?|Class(es)?|Jobs?)</th>',
    r"<label>(Customer|Invoice|Sales Receipt|Class|Job)( \*)?</label>",
    r"openModal\(\s*[\"'`](Customer|Invoice|Sales Receipt|Job|Class)\b",
    # renderListPage({ title: 'Invoices' ... })
    r"\btitle:\s*['\"][^'\"]*\b(Customers?|Invoices?|Sales Receipts?|Class(es)?|Jobs?)\b",
    # Report Center cards and modal titles
    r'<div class="card-header">[^<$]*\b(Customer|Profit & Loss|Balance Sheet|Class|Income|Jobs?)\b',
    # a card's title, given to ReportsPage._card
    r"\bcard\('[a-z0-9-]+', '[^']*\b(Customer|Profit & Loss|Balance Sheet|Class|Income|Jobs?)\b",
    r"openPeriodModal\(\s*[\"'][^\"']*\b(Customer|Profit & Loss|Balance Sheet|Class|Job)\b",
]


def test_chokepoint_literals_go_through_T():
    offenders = []
    for f in sorted((ROOT / "app/static/js").glob("*.js")):
        if f.name in LEAVE_ALONE:
            continue
        src = f.read_text(encoding="utf-8")
        for pat in CHOKEPOINT_PATTERNS:
            for m in re.finditer(pat, src):
                offenders.append(f"{f.name}: {m.group(0)[:70]}")
    assert not offenders, offenders


def test_route_labels_are_rewritten_at_boot():
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    labels = re.findall(r"label:\s*'([^']+)'", app_js)
    business = [lb for lb in labels if BUSINESS_WORD.search(lb)]
    assert business, "expected business-worded route labels to exist"
    missing = [lb for lb in business if lb not in NONPROFIT]
    assert not missing, missing


def test_pdf_templates_use_terms_for_document_names():
    inv = (ROOT / "app/templates/invoice_pdf.html").read_text(encoding="utf-8")
    # the printed face is literal by design: the doc kind decides it
    assert "doc_kind" in inv and "terms('Invoice')" not in inv
    stmt = (ROOT / "app/templates/statement_pdf.html").read_text(encoding="utf-8")
    assert "terms('Total Invoiced')" in stmt


# ---------------------------------------------------------------------------
# Behavioural: the server speaks the company's words
# ---------------------------------------------------------------------------


def test_report_pdf_filenames_and_dashboard_follow_company_type(client, seed_accounts):
    r = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert 'filename="profit-loss_' in r.headers["content-disposition"]
    widgets = client.get("/api/dashboard/widgets").json()
    assert {w["id"]: w["title"] for w in widgets["widgets"]}[
        "receivables"
    ] == "Total Receivables"

    client.put("/api/settings", json={"company_type": "nonprofit"})
    r = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    assert 'filename="statement-of-activities_' in r.headers["content-disposition"]
    r = client.get("/api/reports/balance-sheet/pdf?as_of_date=2026-12-31")
    assert (
        'filename="statement-of-financial-position_' in r.headers["content-disposition"]
    )
    widgets = client.get("/api/dashboard/widgets").json()
    titles = {w["id"]: w["title"] for w in widgets["widgets"]}
    assert titles["receivables"] == "Pledges Receivable"
    assert titles["active_customers"] == "Active Donors"
    assert titles["pnl_month"].startswith("Activities:")
    assert "open_pos" not in widgets["default_order"]
    assert "job_budget_vs_actual" in widgets["default_order"]


def test_invoice_and_receipt_pdfs_are_named_in_the_company_words(
    client, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    sr = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "deposit_to_account_id": seed_accounts["1010"].id,
            "lines": [{"description": "Gift", "quantity": 1, "rate": 100}],
        },
    )
    assert sr.status_code in (200, 201), sr.text
    inv_id = sr.json()["invoice"]["id"]
    r = client.get(f"/api/invoices/{inv_id}/pdf")
    assert r.content[:5] == b"%PDF-"
    assert (
        f"DonationReceipt_{sr.json()['invoice']['invoice_number']}.pdf"
        in r.headers["content-disposition"]
    )
    preview = client.get(f"/api/invoices/{inv_id}/print-preview").text
    assert "DONATION RECEIPT" in preview and "SALES RECEIPT" not in preview
    stmt = client.get(f"/api/reports/customer-statement/{seed_customer.id}/pdf")
    assert stmt.status_code == 200 and stmt.content[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Static sweep: no user-facing literal in the SPA may carry a business word
# without going through T() / Terms.text(). Found the hard way — two leaks
# shipped in 2.9.0 screenshots ("Sales totals per donor", an aging header
# that said Customer) that the narrower regexes above never looked at.
# ---------------------------------------------------------------------------

_SWEEP_EXEMPT = {
    # interop, tax, HR and shell files keep their own vocabulary by design
    "iif.js",
    "ocr.js",
    "ocr_receipts.js",
    "migration.js",
    "tax.js",
    "employees.js",
    "companies.js",
    "terms.js",
    "desktop_shim.js",
    "bootstrap.js",
    "api.js",
    "auth.js",
    "payroll.js",
    "benefits.js",
    "pto.js",
    "onboarding.js",
    "garnishments.js",
    "time_entries.js",
    "tax_forms.js",
    "portal.js",
    "reseller_permits.js",
    "ocr_canvas.js",
}
_SWEEP_WORDS = re.compile(
    r"\b(Customers?|Invoices?|Sales Receipts?|Class(?:es)?|Jobs?|Net Income|"
    r"Profit & Loss|P&L|Balance Sheet|Equity|Income|A/R|Receivables?)\b"
)


def _sweep_hits():
    hits = []
    for path in sorted((ROOT / "app/static/js").glob("*.js")):
        if path.name in _SWEEP_EXEMPT:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r"\bT\(|Terms\.(text|isNonprofit)\(", line):
                continue
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            if (
                "// literal face" in line
            ):  # printed-document names are literal by design
                continue
            found = []
            found += [m.group(1) for m in re.finditer(r">([^<>{}]*?)<", line)]
            # text that trails a ${...} expression: `${id ? 'Update' : 'Create'} Customer</button>`
            found += [m.group(1) for m in re.finditer(r"\}([^<>{}$]*?)<", line)]
            # quoted UI strings on the line (modal titles, toasts inside ternaries)
            found += [
                m.group(2)
                for m in re.finditer(r"(['\"])((?:(?!\1).){3,120})\1", line)
                if not re.search(r"^[#/]|^[a-z_./-]+$|\.png|\.pdf|://", m.group(2))
            ]
            found += [
                m.group(1)
                for m in re.finditer(
                    r'(?:placeholder|title|aria-label|label)="([^"]*)"', line
                )
            ]
            found += [
                m.group(2)
                for m in re.finditer(
                    r"(?:toast|confirm|alert)\(\s*(['\"`])(.*?)\1", line
                )
            ]
            found += [
                m.group(2)
                for m in re.finditer(
                    r"(?:title|label|heading)\s*:\s*(['\"])(.*?)\1", line
                )
            ]
            for text in found:
                if _SWEEP_WORDS.search(text):
                    hits.append((path.name, lineno, text.strip()))
    return hits


def test_no_unwrapped_business_words_in_spa_text():
    """Every hit must be an App.routes label (rewritten at boot by
    applyTerminology — see test_route_labels_are_rewritten_at_boot); anything
    else is a leak the nonprofit switch will show."""
    keys = set(NONPROFIT)
    # the App.routes table: from "routes: {" to the first line that closes
    # it at the object's indent (the table grows as pages get addresses)
    lines = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines, 1) if ln.strip() == "routes: {")
    end = next(i for i, ln in enumerate(lines, 1) if i > start and ln == "    },")
    leaks = [
        h
        for h in _sweep_hits()
        if not (h[0] == "app.js" and start < h[1] < end and h[2] in keys)
    ]
    assert leaks == [], "unwrapped business words in SPA text:\n" + "\n".join(
        f"  {f}:{n}: {t}" for f, n, t in leaks
    )


def test_every_T_call_resolves_to_a_dictionary_key():
    """T('Job name') is not a key, so it rendered "Job name" for a nonprofit —
    the wrapper looked right and did nothing (2.9.1 audit). Every argument
    must be a key, or a singular/plural of one, case-insensitively."""
    keys = {k.lower() for k in NONPROFIT}

    def resolves(arg):
        a = arg.lower()
        return a in keys or (a.endswith("s") and a[:-1] in keys) or (a + "s") in keys

    bad = []
    for path in sorted((ROOT / "app/static/js").glob("*.js")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            for m in re.finditer(r"\bT\(\s*(['\"])(.*?)\1\s*\)", line):
                if not resolves(m.group(2)):
                    bad.append(f"{path.name}:{lineno}: T({m.group(2)!r})")
    assert bad == [], "T() keys that do not resolve:\n" + "\n".join(bad)
