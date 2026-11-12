"""Text in the dialogs meets WCAG AA contrast, in both themes.

The pages are swept in tests/test_theme_contrast.py, as the macOS gate
sweeps them; nothing swept a dialog. Every dialog the app opens is swept
here, whole, in the light and the dark theme, opened on books with
something in them (the same company as the page sweep): the forms (New and
Edit Invoice, Receive Payment, Enter Bill, Pay Bills, New Customer...), the
documents' own views, the reports, the settings dialogs; a nonprofit's own
pages and dialogs, on the same books switched to nonprofit; and the
closing-date password prompt, the sign-in and first-run setup screens, and
the desktop app's PDF window. Same sweep, same thresholds.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.bakery_books import _ok, seed_books  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    PAGE_SWEEP,
    SERVED,
    _open,
    _theme,
    _visit,
    below_threshold,
    browser_fixture,
    new_page,
    company_fixture,
    settle,
    sweep_of,
)

MODAL_SWEEP = sweep_of("document.getElementById('modal')")
OPEN = "() => !document.getElementById('modal-overlay').classList.contains('hidden')"

# Each dialog as the app opens it, by the call its button makes; the names
# in braces are what the company was seeded with (seed_books).
SALES = [
    "CustomersPage.showForm()",
    "CustomersPage.showForm({customer})",
    "CustomersPage.showDetails({customer})",
    # saving a customer whose name is close to one on file
    "CustomersPage._confirmDuplicate(document.createElement('form'), null,"
    " {{name: 'Salt and Pine Catering'}},"
    " [{{id: {customer}, name: 'Salt & Pine Catering Co.', similarity: 0.91}}])",
    "InvoicesPage.showForm()",
    "InvoicesPage.showForm({draft})",
    "InvoicesPage.view({sent})",
    "InvoicesPage.view({void})",
    "InvoicesPage.showApplyCredit({invoice2})",
    "InvoicesPage.emailInvoice({sent})",
    "InvoicesPage.showWriteOff({partial}, 300)",
    "PaymentsPage.showForm()",
    "PaymentsPage.showForm(null, {customer})",
    "PaymentsPage.view({payment})",
    "PaymentsPage.showApplyCredit('credit_memo', {memo}, {customer2})",
    "EstimatesPage.showForm()",
    "EstimatesPage.showForm({estimate})",
    "EstimatesPage.view({estimate})",
    "SalesReceiptsPage.showForm()",
    "SalesReceiptsPage.view({receipt})",
    "CreditMemosPage.showForm()",
    "CreditMemosPage.view({memo})",
    "CreditMemosPage.showApply({memo})",
    "RecurringPage.showForm()",
    "RecurringPage.showForm({recurring})",
    "ItemsPage.showForm()",
    "ItemsPage.showForm({loaf})",
    "ItemsPage.showMovements({loaf})",
    "ItemsPage.showAdjust({loaf})",
    "DepositsPage.view({deposit})",
    "TaxPage.showPaySalesTax()",
    "ResellerPermitsPage.showForm()",
    "ResellerPermitsPage.showForm({permit_soon})",
    "ResellerPermitsPage.verifyWorkflow({permit_expired})",
    "ResellerPermitsPage.verifyWorkflow({permit_soon})",
    "JobsPage.showForm()",
    "JobsPage.showForm({job})",
    "JobCostsPage.showForm()",
    "JobCostsPage.showAllocate()",
    "JobCostsPage.view({job_cost})",
]
PURCHASES = [
    "VendorsPage.showForm()",
    "VendorsPage.showForm({vendor})",
    "VendorsPage.showDetails({vendor})",
    "VendorsPage.showDetails({vendor2})",
    "VendorsPage._confirmDuplicate(document.createElement('form'), null,"
    " {{name: 'Cascade Flour Mills'}},"
    " [{{id: {vendor}, name: 'Cascade Flour Mill', similarity: 0.96}}])",
    "BillsPage.showForm()",
    "BillsPage.showPayForm()",
    # Enter Bill with its receipt-scan review panel open, as a scan leaves it
    "(async () => {{ await BillsPage.showForm();"
    " document.getElementById('ocr-canvas-panel').style.display = 'block';"
    " OcrCanvas._refreshToolbar();"
    " OcrCanvas._msg('Colored boxes are what the scan found. Drag a new box to fix"
    " anything it missed or got wrong.'); }})()",
    "BillsPage.view({bill})",
    "BillsPage.view({paid_bill})",
    "BillsPage.viewPayment({bill_payment})",
    "PurchaseOrdersPage.showForm()",
    "PurchaseOrdersPage.showForm({po})",
    "PurchaseOrdersPage.view({po})",
    "PurchaseOrdersPage.convertToBill({po})",
    "VendorCreditsPage.showForm()",
    "VendorCreditsPage.view({vendor_credit})",
    "VendorCreditsPage.showApply({vendor_credit})",
    "ExpensesPage.showForm()",
    "ExpensesPage.showDetail({expense})",
    "CCChargesPage.showForm()",
    "JournalPage.showForm()",
    "JournalPage.view({journal})",
    "App.showAccountForm()",
    "App.showAccountForm({checking})",
    "App.showChartImport()",
    "BankRulesPage.showForm()",
    "BankRulesPage.showForm({bank_rule})",
    "FixedAssetsPage.showTypeForm()",
    "FixedAssetsPage.showAssetForm()",
    "FixedAssetsPage.showPostPurchaseForm({asset})",
    "FixedAssetsPage.showDepreciationForm()",
    "FixedAssetsPage.showDisposeForm({asset})",
    "FixedAssetsPage.showImportForm()",
]
# opened over the bank register, as its buttons open them
BANKING = [
    "BankingPage.showAccountForm()",
    "BankingPage.showEntryForm({checking})",
    "BankingPage.showTransferForm({checking})",
    "BankingPage.showTransfers()",
    "BankingPage.startReconcile({checking})",
    "BankingPage.showReconciliations({checking})",
    "BankingPage.showOFXImport({checking})",
    "BankingPage.showSimpleFINHistory()",
    "BankingPage.showMatch({feed_line}, {checking})",
    "BankingPage.showReconReport({reconciliation})",
]
REPORTS = [
    "ReportsPage.profitLoss()",
    "ReportsPage.profitLossDetail()",
    "ReportsPage.profitLoss({{columns: 'month'}})",  # by month (#478)
    "ReportsPage.profitLossByClass()",
    "ReportsPage.profitLossUnclassified()",
    "ReportsPage.profitLossByJob()",
    "ReportsPage.balanceSheet()",
    "ReportsPage.trialBalance()",
    "ReportsPage.arAging()",
    "ReportsPage.apAging()",
    "ReportsPage.salesTax()",
    "ReportsPage.generalLedger()",
    "ReportsPage.incomeByCustomer()",
    "ReportsPage.cashFlow()",
    "ReportsPage.jobProfitability()",
    "ReportsPage.jobBudgetVsActual()",
    "ReportsPage.report1099()",
    "ReportsPage.customerStatementPicker()",
    "ReportsPage.fixedAssetReconciliation()",
    "ReportsPage.openDrillDown({checking}, 'Checking', '2026-01-01', '2026-09-30')",
    # a P&L total's drill-down (#479)
    "ReportsPage.openSetDrillDown({{section: 'net_income'}}, 'Net Income', '2026-01-01', '2026-09-30')",
    # what emailing statements answers when some could not be sent
    "ReportsPage._sendResult('Statements', 'Emailed 1 statement.',"
    " ['Harbor District Events: no email address on file'])",
]
PEOPLE = [
    "EmployeesPage.showForm()",
    "EmployeesPage.showForm({employee})",
    "EmployeesPage.viewDetails({employee})",
    "EmployeesPage._showEverifyForm({employee})",
    "PayrollPage.showRunForm()",
    "PayrollPage.view({pay_run})",
    "PTOPage.showPolicyForm()",
    "PTOPage.showPolicyForm({pto_policy})",
    "PTOPage.showRequestForm()",
    "PTOPage.showAccrualForm()",
    "BenefitsPage.showCodeForm()",
    "BenefitsPage.showCodeForm({benefit_code})",
    "BenefitsPage.showRates({benefit_code})",
    "BenefitsPage.showGroupForm()",
    "BenefitsPage.showGroupForm({benefit_group})",
    "BenefitsPage.showMembers({benefit_group})",
    "BenefitsPage.showEnrollForm({employee})",
    "DeductionsPage.showGarnishmentForm({employee})",
    "OnboardingPage.viewChecklist({employee})",
    "TimeEntriesPage.showForm()",
]
SETTINGS = [
    "SettingsPage.confirmRestore('harbor-light-bakery_2026-09-25_2210.db')",
    "SettingsPage.editTemplate({template})",
    "SettingsPage.showCostCodeImport()",
    "CompaniesPage.showCreate()",
    # Add a card, on a dashboard trimmed to two cards: the rest are offered
    "(DashboardPage._order = DashboardPage._order.slice(0, 2), DashboardPage.showAdd())",
    # last, as it stays: a form as a read-only sign-in sees it, locked
    "(async () => {{ App.role = 'readonly'; await InvoicesPage.showForm(); }})()",
]


def _dialogs(page, handled, books, openers):
    """Open each dialog, sweep it in both themes, close it. Returns the
    sweeps, keyed (theme, "<title> — <call>"), and the calls that opened
    nothing."""
    swept, unopened = {}, []
    for opener in openers:
        call = opener.format(**books)
        page.evaluate("() => closeModal()")
        try:
            page.evaluate(f"async () => {{ await {call}; }}")
            page.wait_for_function(OPEN, timeout=5000)
        except Exception as exc:  # the call failed, or opened nothing
            unopened.append(f"{call}: {str(exc).splitlines()[0]}")
            continue
        settle(page, handled)
        title = page.evaluate(
            "() => document.getElementById('modal-title').textContent"
        )
        for theme in ("light", "dark"):
            _theme(page, theme)
            swept[(theme, f"{title} — {call}")] = page.evaluate(MODAL_SWEEP)
    page.evaluate("() => closeModal()")
    return swept, unopened


def _sweep_dialogs(browser, company, books, groups):
    page, handled = _open(browser, company)
    swept, unopened = {}, []
    try:
        for route, openers in groups:
            _visit(page, handled, route.format(**books))
            got, missed = _dialogs(page, handled, books, openers)
            swept.update(got)
            unopened += missed
    finally:
        page.close()
    assert unopened == []
    assert len(swept) == 2 * sum(len(openers) for _, openers in groups)
    return swept


def test_the_sales_dialogs_meet_aa_in_both_themes(browser, company, books):
    swept = _sweep_dialogs(browser, company, books, [("#/invoices", SALES)])
    assert below_threshold(swept) == []


def test_the_purchase_banking_and_report_dialogs_meet_aa_in_both_themes(
    browser, company, books
):
    swept = _sweep_dialogs(
        browser,
        company,
        books,
        [
            ("#/bills", PURCHASES),
            ("#/banking/{checking}", BANKING),
            ("#/reports", REPORTS),
        ],
    )
    assert below_threshold(swept) == []


def test_the_people_and_settings_dialogs_meet_aa_in_both_themes(
    browser, company, books
):
    swept = _sweep_dialogs(
        browser, company, books, [("#/employees", PEOPLE), ("#/settings", SETTINGS)]
    )
    assert below_threshold(swept) == []


# The same books kept by a nonprofit: donors, pledges, funds, a release from
# restriction, an overhead allocation, an in-kind gift.
NONPROFIT_PAGES = [
    "#/",
    "#/customers",
    "#/invoices",
    "#/sales-receipts",
    "#/releases",
    "#/functional-allocations",
    "#/in-kind-gifts",
    "#/reports",
    "#/settings",
]
NONPROFIT_DIALOGS = [
    "InvoicesPage.showForm()",
    "InvoicesPage.view({pledge})",
    "ReleasesPage.showForm()",
    "ReleasesPage.view({release})",
    "AllocationsPage.showRule()",
    "AllocationsPage.showRule({rule})",
    "AllocationsPage.showRun({rule})",
    "AllocationsPage.view({allocation})",
    "InKindPage.showForm()",
    "InKindPage.view({gift})",
    "SettingsPage.editFund({fund})",
    "ReportsPage.statementOfFinancialPosition()",
    "ReportsPage.statementOfActivities()",
    "ReportsPage.fundBalances()",
    "ReportsPage.functionalExpenses()",
    "ReportsPage.pledges()",
    "ReportsPage.givingStatements()",
]


@pytest.fixture
def nonprofit(client, seed_accounts):
    S = seed_books(client, seed_accounts)
    a = {number: account.id for number, account in seed_accounts.items()}

    def post(path, body=None):
        return _ok(client.post(path, json=body or {}))

    _ok(client.put("/api/settings", json={"company_type": "nonprofit"}))
    post("/api/nonprofit/setup-accounts")
    S["fund"] = post(
        "/api/classes",
        {
            "name": "Youth Music Program",
            "restriction": "temporarily_restricted",
            "default_function": "program",
        },
    )["id"]
    S["pledge"] = post(
        "/api/invoices",
        {
            "customer_id": S["customer2"],
            "date": "2026-09-01",
            "is_pledge": True,
            "class_id": S["fund"],
            "lines": [{"description": "Annual pledge", "quantity": 1, "rate": 1200}],
        },
    )["id"]
    S["release"] = post(
        "/api/nonprofit/releases",
        {"date": "2026-09-30", "class_id": S["fund"], "amount": "250.00"},
    )["id"]
    S["rule"] = post(
        "/api/nonprofit/allocation-rules",
        {
            "name": "Shared costs by headcount",
            "basis": "percent",
            "source_account_id": a["6000"],
            "targets": [
                {"function": "program", "weight": 70},
                {"function": "management", "weight": 20},
                {"function": "fundraising", "weight": 10},
            ],
        },
    )["id"]
    S["allocation"] = post(
        "/api/nonprofit/allocations",
        {
            "date": "2026-09-30",
            "rule_id": S["rule"],
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
    )["id"]
    S["gift"] = post(
        "/api/in-kind-gifts",
        {
            "customer_id": S["customer"],
            "date": "2026-09-14",
            "memo": "For the youth program",
            "lines": [
                {
                    "description": "Yamaha U1 upright piano",
                    "quantity": 1,
                    "fair_value": "6500",
                    "debit_account_id": a["1500"],
                }
            ],
        },
    )["id"]
    return S


def _toggle_to(page, handled, theme):
    """The theme switched on a page already drawn, as the toolbar's toggle
    switches it: App.toggleTheme sets the attribute and sends
    slowbooks:themechange, and a page that draws for its theme repaints on
    it (the dashboard's aging bar picks ink or white for each slice's
    figure in the theme it is drawn in, NEW-60). Setting the attribute alone
    left a figure drawn for one theme to be measured in the other (#814)."""
    page.evaluate(
        """(t) => { const now = document.documentElement.getAttribute('data-theme')
                                || 'light';
                    if (now !== t) App.toggleTheme(); }""",
        theme,
    )
    _theme(page, theme)
    settle(page, handled)


def test_a_nonprofits_pages_and_dialogs_meet_aa_in_both_themes(
    browser, client, nonprofit
):
    page, handled = _open(browser, client)
    swept = {}
    try:
        for route in NONPROFIT_PAGES:
            _visit(page, handled, route)
            for theme in ("dark", "light"):
                _toggle_to(page, handled, theme)
                swept[(theme, route)] = page.evaluate(PAGE_SWEEP)
        dialogs, unopened = _dialogs(page, handled, nonprofit, NONPROFIT_DIALOGS)
    finally:
        page.close()
    assert unopened == []
    assert len(dialogs) == 2 * len(NONPROFIT_DIALOGS)
    texts = {it["text"] for items in swept.values() for it in items}
    assert {"+ New Donor", "+ In-Kind Gift", "IK-0001"} <= texts
    # the pledge's slice of the aging bar carries its figure in both themes,
    # drawn for the theme it is measured in (#814)
    for theme in ("dark", "light"):
        assert "$1.2k" in {it["text"] for it in swept[(theme, "#/")]}
    assert below_threshold({**swept, **dialogs}) == []


# /api/auth/status as the sign-in screen reads it, for each screen it draws
SIGN_IN_SCREENS = {
    "sign-in, the desktop app": {
        "authenticated": False,
        "setup_needed": False,
        "multi_user": False,
        "desktop": True,
        "company_name": "Harbor Light Bakery",
    },
    "sign-in, several users": {
        "authenticated": False,
        "setup_needed": False,
        "multi_user": True,
        "usernames": ["lena", "jonah"],
        "company_name": "Harbor Light Bakery",
    },
    "setup, a new company": {
        "authenticated": False,
        "setup_needed": True,
        "company_name": "",
    },
    "setup, a file that has books": {
        "authenticated": False,
        "setup_needed": True,
        "has_data": True,
        "company_name": "Harbor Light Bakery",
    },
}
CLOSING_PROMPT = "document.querySelector('[aria-labelledby=\"closing-pw-title\"]')"


def test_the_sign_in_screens_and_the_closing_date_prompt_meet_aa(browser, client):
    served = dict(SERVED)
    page, handled = _open(browser, client, served)
    swept = {}
    try:
        # the closing-date password prompt, after a wrong password
        page.evaluate(
            """() => { API.askClosingDatePassword('This invoice is dated Jun 30, 2026, '
                + 'in a closed period (the books are closed through Jul 31, 2026).', true); }"""
        )
        page.wait_for_function(f"() => !!{CLOSING_PROMPT}")
        for theme in ("light", "dark"):
            _theme(page, theme)
            swept[(theme, "closing-date prompt")] = page.evaluate(
                sweep_of(CLOSING_PROMPT)
            )
        page.keyboard.press("Escape")
        # the sign-in and setup screens, each with an error showing
        for name, status in SIGN_IN_SCREENS.items():
            served["/api/auth/status"] = status
            page.evaluate(
                """async () => { const o = document.getElementById('auth-overlay');
                                 if (o) o.remove();
                                 await SlowbooksAuth.promptAuth(); }"""
            )
            page.wait_for_selector("#auth-overlay #auth-error", state="attached")
            page.evaluate("""() => { document.getElementById('auth-error').textContent =
                           'That password is not correct. Try again.'; }""")
            for theme in ("light", "dark"):
                _theme(page, theme)
                swept[(theme, name)] = page.evaluate(
                    sweep_of("document.getElementById('auth-overlay')")
                )
    finally:
        page.close()
    assert all(swept.values()) and len(swept) == 2 * (1 + len(SIGN_IN_SCREENS))
    texts = {it["text"] for items in swept.values() for it in items}
    assert {"Choose a different company →", "Who is signing in?"} <= texts
    assert below_threshold(swept) == []


def test_the_pdf_window_meets_aa_in_both_colour_schemes(browser):
    """The desktop app's PDF window (desktop_launcher._VIEWER_PAGE) follows
    the system's light or dark setting, not the app's theme."""
    import desktop_launcher

    html = desktop_launcher._VIEWER_PAGE.substitute(
        title="Invoice 1002",
        name="Invoice_1002.pdf",
        path="/Users/lena/Documents/SlowBooks Pro/Documents/Invoice_1002.pdf",
        where="/Users/lena/Documents/SlowBooks Pro/Documents",
        src="about:blank",
        open_label="Open in Preview",
    )
    page = new_page(browser, viewport={"width": 1100, "height": 700})
    swept = {}
    try:
        page.set_content(html)
        # as the window is once its bridge is ready: the buttons enabled
        page.evaluate("""() => { document.getElementById('note').textContent =
                       'Not available in this window.';
                       document.querySelectorAll('button')
                           .forEach(b => { b.disabled = false; }); }""")
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            swept[(scheme, "PDF window")] = page.evaluate(PAGE_SWEEP)
    finally:
        page.close()
    texts = {it["text"] for items in swept.values() for it in items}
    assert {"Invoice_1002.pdf", "Show in folder"} <= texts
    assert below_threshold(swept) == []
