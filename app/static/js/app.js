/**
 * App shell — the left-panel Navigator (the icon sidebar everyone
 * remembers from QuickBooks) plus hash-based routing to each page.
 */
const App = {
    routes: {
        '/':              { page: 'dashboard',       label: 'Dashboard',          render: () => DashboardPage.render() },
        '/customers':     { page: 'customers',       label: 'Customer Center',    render: () => CustomersPage.render() },
        // A customer's or a vendor's page (a dialog over its list) has an
        // address of its own (#238, R8 review): a report opened from the
        // page comes Back to the page, not the list, and a reload keeps it.
        // The list row and the report rows go there (App.documentAddress).
        '/customers/:id': { page: 'customers',       label: 'Customer',           render: (id) => App.withDocument(() => CustomersPage.render(), () => CustomersPage.showDetails(id)) },
        '/jobs':          { page: 'jobs',            label: 'Jobs',               render: () => JobsPage.render() },
        '/jobs/:id':      { page: 'jobs',            label: 'Job',                render: (id, query) => JobsPage.renderDetail(id, query) },
        '/job-costs':     { page: 'job-costs',       label: 'Job Cost Entries',   render: () => JobCostsPage.render() },
        // A posting's own page where it has one (#241): the drill-down and
        // the register link a job cost, a pay run, a credit memo, an in-kind
        // gift, a release or an allocation to the document over its list.
        '/job-costs/:id': { page: 'job-costs',       label: 'Job Cost Entry',     render: (id) => App.withDocument(() => JobCostsPage.render(), () => JobCostsPage.view(id)) },
        '/releases':      { page: 'releases',        label: 'Releases from Restriction', nonprofit: true, render: () => ReleasesPage.render() },
        '/releases/:id':  { page: 'releases',        label: 'Release from Restriction', nonprofit: true, render: (id) => App.withDocument(() => ReleasesPage.render(), () => ReleasesPage.view(id)) },
        '/functional-allocations': { page: 'functional-allocations', label: 'Functional Allocations', nonprofit: true, render: () => AllocationsPage.render() },
        '/functional-allocations/:id': { page: 'functional-allocations', label: 'Functional Allocation', nonprofit: true, render: (id) => App.withDocument(() => AllocationsPage.render(), () => AllocationsPage.view(id)) },
        '/vendors':       { page: 'vendors',         label: 'Vendor Center',      render: () => VendorsPage.render() },
        '/vendors/:id':   { page: 'vendors',         label: 'Vendor',             render: (id) => App.withDocument(() => VendorsPage.render(), () => VendorsPage.showDetails(id)) },
        '/items':         { page: 'items',           label: 'Item List',          render: () => ItemsPage.render() },
        '/invoices':      { page: 'invoices',        label: 'Create Invoices',    render: () => InvoicesPage.render() },
        // A posting's own address (#/invoices/12, #/deposits/31): the bank
        // register and the report drill-downs link each line to the document
        // behind it (app/services/bank_register.py source_link), and every
        // link but a vendor credit's said "Page not found" (explore 2.17.3).
        // The document opens over its list (App.withDocument); a card charge
        // or a transfer opens as its journal entry.
        '/invoices/:id':      { page: 'invoices',   label: 'Invoice',       render: (id) => App.withDocument(() => InvoicesPage.render(), () => InvoicesPage.view(id)) },
        '/sales-receipts': { page: 'sales-receipts', label: 'Enter Sales Receipts', render: () => SalesReceiptsPage.render() },
        '/in-kind-gifts': { page: 'in-kind-gifts',   label: 'In-Kind Gifts',      nonprofit: true, render: () => InKindPage.render() },
        '/in-kind-gifts/:id': { page: 'in-kind-gifts', label: 'In-Kind Gift',     nonprofit: true, render: (id) => App.withDocument(() => InKindPage.render(), () => InKindPage.view(id)) },
        '/estimates':     { page: 'estimates',       label: 'Create Estimates',   render: () => EstimatesPage.render() },
        '/payments':      { page: 'payments',        label: 'Receive Payments',   render: () => PaymentsPage.render() },
        '/payments/:id':      { page: 'payments',   label: 'Payment',       render: (id) => App.withDocument(() => PaymentsPage.render(), () => PaymentsPage.view(id)) },
        '/banking':       { page: 'banking',         label: 'Banking',            render: () => BankingPage.render() },
        '/banking/:id':   { page: 'banking',         label: 'Register',           render: (id, query) => BankingPage.renderRegister(id, query) },
        '/banking/transfers/:id': { page: 'banking', label: 'Transfer',     render: (id) => App.withDocument(() => BankingPage.render(), () => JournalPage.view(id)) },
        '/accounts':      { page: 'accounts',        label: 'Chart of Accounts',  render: () => App.renderAccounts() },
        // A class's own page and the Classes list (#234): the period, the
        // tab and the Show choice ride in the query, so a reload, a bookmark
        // or Back reproduces the page (docs/dev/report-views.md).
        '/classes':       { page: 'classes',         label: 'Classes',            render: (_id, query) => ClassesPage.render(query) },
        '/classes/:id':   { page: 'classes',         label: 'Class',              render: (id, query) => ClassesPage.renderDetail(id, query) },
        '/reports':       { page: 'reports',         label: 'Report Center',      render: () => ReportsPage.render() },
        // A report view's own address (#/reports/profit-loss?start_date=…,
        // #/reports/account-transactions?account_id=…): the Report Center is
        // the page, and the view opens over it from the query, as a document
        // opens over its list. Which views exist, and what each takes, is
        // ReportsPage._VIEWS (docs/dev/report-views.md).
        '/reports/:view': { page: 'reports',         label: 'Report',             render: (view, query) => App.withDocument(() => ReportsPage.render(), () => ReportsPage.openView(view, query)) },
        '/settings':      { page: 'settings',        label: 'Company Settings',   render: () => SettingsPage.render() },
        '/iif':           { page: 'iif',             label: 'Interoperability',   render: () => IIFPage.render() },
        '/quick-entry':   { page: 'quick-entry',     label: 'Quick Entry',        render: () => App.renderQuickEntry() },
        // Phase 1: Foundation
        '/audit':         { page: 'audit',           label: 'Audit Log',          render: () => AuditPage.render() },
        // Phase 2: Accounts Payable
        '/purchase-orders': { page: 'purchase-orders', label: 'Purchase Orders',  render: () => PurchaseOrdersPage.render() },
        '/bills':         { page: 'bills',           label: 'Bills',              render: () => BillsPage.render() },
        '/bills/:id':         { page: 'bills',      label: 'Bill',          render: (id) => App.withDocument(() => BillsPage.render(), () => BillsPage.view(id)) },
        '/bill-payments/:id': { page: 'bills',      label: 'Bill Payment',  render: (id) => App.withDocument(() => BillsPage.render(), () => BillsPage.viewPayment(id)) },
        '/credit-memos':  { page: 'credit-memos',    label: 'Credit Memos',       render: () => CreditMemosPage.render() },
        '/credit-memos/:id': { page: 'credit-memos', label: 'Credit Memo',        render: (id) => App.withDocument(() => CreditMemosPage.render(), () => CreditMemosPage.view(id)) },
        '/vendor-credits':{ page: 'vendor-credits',  label: 'Vendor Credits',     render: () => VendorCreditsPage.render() },
        '/vendor-credits/:id': { page: 'vendor-credits', label: 'Vendor Credit',  render: (id) => App.withDocument(() => VendorCreditsPage.render(), () => VendorCreditsPage.view(id)) },
        // Phase 3: Productivity
        '/recurring':     { page: 'recurring',       label: 'Recurring Invoices', render: () => RecurringPage.render() },
        '/batch-payments': { page: 'batch-payments', label: 'Batch Payments',     render: () => BatchPaymentsPage.render() },
        // Phase 4: CSV Import/Export
        '/csv':           { page: 'csv',             label: 'CSV Import/Export',  render: () => App.renderCSV() },
        // Phase 5: Advanced Integration
        '/tax':           { page: 'tax',             label: 'Tax Reports',        render: () => TaxPage.render() },
        // Phase 6: Ambitious
        '/companies':     { page: 'companies',       label: 'Companies',          render: () => CompaniesPage.render() },
        '/employees':     { page: 'employees',       label: 'Employees',          render: () => EmployeesPage.render() },
        '/payroll':       { page: 'payroll',         label: 'Payroll',            render: () => PayrollPage.render() },
        '/payroll/:id':   { page: 'payroll',         label: 'Pay Run',            render: (id) => App.withDocument(() => PayrollPage.render(), () => PayrollPage.view(id)) },
        // Tier 1/2/3: Payroll & HR
        '/hr/onboarding':   { page: 'hr-onboarding',   label: 'Onboarding',       render: () => OnboardingPage.render() },
        '/hr/time-entries': { page: 'hr-time-entries', label: 'Time Entries',      render: () => TimeEntriesPage.render() },
        '/hr/pto':          { page: 'hr-pto',           label: 'Time Off',         render: () => PTOPage.render() },
        '/hr/benefits':     { page: 'hr-benefits',     label: 'Benefits',          render: () => BenefitsPage.render() },
        '/hr/deductions':   { page: 'hr-deductions',   label: 'Garnishments',      render: () => DeductionsPage.render() },
        '/hr/tax-forms':    { page: 'hr-tax-forms',    label: 'Tax Forms',         render: () => TaxFormsPage.render() },
        '/reseller-permits':{ page: 'reseller-permits',label: 'Reseller Permits', render: () => ResellerPermitsPage.render() },
        // Phase 9: Analytics (real-time business intelligence)
        '/analytics':     { page: 'analytics',       label: 'Analytics & AI',     render: () => AnalyticsPage.render() },
        // Phase 9: Forum Bug Fixes & Missing Features
        '/journal':       { page: 'journal',         label: 'Journal Entries',    render: () => JournalPage.render() },
        '/journal/:id':       { page: 'journal',    label: 'Journal Entry', render: (id) => App.withDocument(() => JournalPage.render(), () => JournalPage.view(id)) },
        '/deposits':      { page: 'deposits',        label: 'Make Deposits',      render: () => DepositsPage.render() },
        '/deposits/:id':      { page: 'deposits',   label: 'Deposit',       render: (id) => App.withDocument(() => DepositsPage.render(), () => DepositsPage.view(id)) },
        // The Check Register page is the Banking register now (2.10); old bookmarks land there.
        // (replaceState: Back from Banking must not land on the alias, which would send it forward again;
        // the entry keeps its state, so Back still knows what is behind it)
        '/check-register': { page: 'banking',         label: 'Banking',            render: () => { history.replaceState(history.state, '', '#/banking'); App.navigate('#/banking'); return ''; } },
        '/cc-charges':    { page: 'cc-charges',      label: 'CC Charges',         render: () => CCChargesPage.render() },
        '/cc-charges/:id':    { page: 'cc-charges', label: 'CC Charge',     render: (id) => App.withDocument(() => CCChargesPage.render(), () => JournalPage.view(id)) },
        '/expenses':      { page: 'expenses',        label: 'Enter Expenses',     render: () => ExpensesPage.render() },
        '/expenses/:id':      { page: 'expenses',   label: 'Expense',       render: (id) => App.withDocument(() => ExpensesPage.render(), () => ExpensesPage.showDetail(id)) },
        // Phase 10: Quick Wins + Medium Effort Features
        '/budgets':       { page: 'budgets',         label: 'Budget vs Actual',   render: () => BudgetsPage.render() },
        '/bank-rules':    { page: 'bank-rules',      label: 'Bank Rules',         render: () => BankRulesPage.render() },
        '/fixed-assets':  { page: 'fixed-assets',    label: 'Fixed Assets',       render: () => FixedAssetsPage.render() },
        '/migrate':       { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render() },
        '/xero-import':   { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render('xero') },
        '/myob-import':   { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render('myob') },
        '/opening-balances': { page: 'opening-balances', label: 'Opening Balances', render: () => OpeningBalancesPage.render() },
    },

    // A document over its list: the list is the page, and the document opens
    // in the dialog once the page is in place. One that cannot be opened
    // (gone since the link was made) says so over the list.
    async withDocument(list, open) {
        const html = await list();
        setTimeout(() => {
            // the dialog about to open is the address's own, over the list
            // (App.dialogClosed)
            App._dialogUnder = App.pageUnder(location.hash);
            Promise.resolve().then(open)
                .then(() => {
                    // the dialog's address is the bar's: Back stays live over
                    // it (App.editingDialog); and Back to a page left by one
                    // of its row links (a customer's page, an invoice) puts
                    // focus back on the link, as a report's row has it, once
                    // the dialog's own first focus has gone by
                    App.dialogAddressed();
                    if (history.state && history.state.focus) setTimeout(() => ReportsPage._refocusRow($('#modal-body')), 0);
                })
                .catch(err => toast(err.message || 'Could not open this document', 'error'));
        }, 0);
        return html;
    },

    // A dialog that is a document (a customer's page opened from its list
    // row): its address goes on the bar as a history entry of its own,
    // recording where it was pushed from as App.navigate does, so Back
    // from a report opened on the page returns to the page, and Back from
    // the page to the list. Nothing happens when the address is already
    // there (the page opened through its route).
    documentAddress(hash) {
        const here = location.hash || '#/';
        if (here !== hash) {
            history.pushState(App.entryState(here), '', hash);
            App._dialogUnder = App._dialogUnder || here;
        }
        App.dialogAddressed();  // the open dialog's own: Back stays live over it
    },

    // The app moving on after one of its own actions (a save, a void, a
    // mark-as-sent): the dialog closes with the address kept and the page
    // is redrawn from it, so a document opened by its address comes back
    // updated and a customer's page comes back after an edit from it. Not
    // a dismissal: see closeModal (NEW-23 review).
    refresh() {
        closeModal({ keepAddress: true });
        App.navigate(location.hash);
    },

    // ---- A dialog with an address of its own (NEW-30 review) --------------
    // The router puts a dialog's address on the bar three ways: a document
    // route (App.withDocument: #/invoices/12 over the list), a customer's or
    // vendor's page (App.documentAddress) and a report view
    // (ReportsPage.setAddress). Each marks the open dialog with that address;
    // openModal clears the mark, so a plain form (New Invoice, Edit Customer,
    // a Settings form) opened over the page, or over an addressed dialog,
    // carries none. Back is inert over such a form: the button sits under
    // the overlay and the chord does nothing, since a Back that discarded a
    // half-typed form would be worse than none; the user closes the form
    // first. Over an addressed dialog Back stays live: it is how a report, a
    // document or a page is left.
    dialogAddressed() {
        const m = $('#modal');
        if (m) m.dataset.address = location.hash || '#/';
        // the sidebar stays usable over a report or a document (a view,
        // not a form): the Open group's links and × reach it (W-22 /
        // NEW-159); syncBack sets and clears the class
        // An opener that puts its address on the bar first and opens next
        // (the statement picker, the 1099 summary: ReportsPage.setAddress,
        // then openModal): its mark survives the open for this task
        // (utils.js openModal reads _marking).
        App._marking = true;
        setTimeout(() => { App._marking = false; }, 0);
        App.addressShown();
        App.screenShown(null);  // the dialog's own screen, with its title (#336)
    },
    editingDialog() {
        const overlay = $('#modal-overlay'), m = $('#modal');
        if (!overlay || !m || overlay.classList.contains('hidden')) return false;
        return (m.dataset.address || '') !== (location.hash || '#/');
    },
    backAllowed() { return App.canGoBack() && !App.editingDialog(); },

    // ---- Back, within the app (macOS gate NEW-30) --------------------------
    // The Mac app's window has no Back of its own (no button, gesture or
    // menu item), so a hop with no "Back to …" of its own was one way. The
    // toolbar's Back and its shortcut (⌘[ on a Mac, Alt+← elsewhere) go
    // back through history, and only while an app page is behind:
    // history.length is no guide, it counts the entries ahead and the
    // desktop shell's own. Each entry the app pushes records the address it
    // was pushed from (history.state.from: App.navigate, App.documentAddress,
    // ReportsPage.setAddress); an entry a link made (a sidebar link's hash
    // change) is stamped the same way when the router first sees it, and
    // the session's first entry is stamped with nothing behind it. So `from`
    // says whether there is somewhere to go. (A replaceState elsewhere
    // should carry history.state along, or Back goes dark on that entry.)
    _here: null,   // the address shown, for the next stamp
    _nShown: 0,    // the entry shown: its place in the chain
    // What an entry of the app's records: the address it was pushed from
    // (from: Back and "Back to …" read it) and its place in the chain (n,
    // one more than the entry it follows), so a move away declined can
    // tell which way the entry it left lies (App.stayPut).
    entryState(from) { return { from, n: App._nShown + 1 }; },
    // A move away declined (the Settings leave guard, with edits unsaved):
    // the page still shows the entry it was on, so the history goes back to
    // that entry — behind, for a link's new entry (the browser had already
    // made it) or a Forward; ahead, for a Back — with nothing re-rendered or
    // closed on the way (_stay: the popstate and navigate handlers let it
    // pass). The entry's own state says what is behind it, so the toolbar's
    // Back stays as it was. (NEW-30 review, round 3.)
    _stay: false,
    stayPut() {
        const n = history.state && history.state.n;
        App._stay = true;
        setTimeout(() => { App._stay = false; }, 2000);  // never stuck
        if (typeof n === 'number' && n < App._nShown) history.forward(); else history.back();
    },
    isMac() { return typeof navigator !== 'undefined' && /Mac|iP(hone|ad|od)/.test(navigator.platform || ''); },
    // What a toolbar button says about its shortcut: the hover reads
    // "Enter Bill (Alt+B)" — "(⌥B)" on a Mac, where Alt is Option — and a
    // screen reader hears the ARIA token, which stays Alt+B everywhere
    // (the way Back announces ⌘[ / Alt+←).
    shortcutAttrs(letter, name) {
        const shown = (App.isMac() ? '⌥' : 'Alt+') + letter;
        return `title="${escapeHtml(name)} (${shown})" aria-keyshortcuts="Alt+${letter}"`;
    },
    // What moved focus last: the keyboard (any key but a modifier held on
    // its own: a click then the Mac's ⌘[ Back is the keyboard's) or a
    // pointer (App.init keeps it). A text box matches :focus-visible on a
    // click as well as on Tab, so what keeps the keyboard's place in sight
    // — scrolls a control out from under Settings' save bar, a grid's
    // frozen parts, a table's edge — asks this and leaves a pointer's focus
    // where the pointer put it.
    _input: null,
    _MODIFIER_KEYS: ['Shift', 'Control', 'Alt', 'AltGraph', 'Meta', 'OS', 'Super', 'Hyper', 'Fn', 'FnLock', 'CapsLock'],
    keyboardFocus() { return App._input !== 'pointer'; },
    // A table wider than its container scrolls sideways in it, and
    // Chromium's focus leaves a stop partly in view where it is (32px of
    // it counts as in view): the Dispose button half cut at the edge. The
    // keyboard's stop is brought in whole, its ring and halo with it (its
    // scroll-margin); a pointer's focus stays where the pointer put it.
    // The P&L grid has its own (ReportsPage._gridReveal).
    revealInTable(e) {
        const a = e.target, c = a && a.closest && a.closest('.table-container');
        if (!c || a === c || c.id === 'grid-scroll' || !App.keyboardFocus()) return;
        if (c.scrollWidth > c.clientWidth) a.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    },

    // A table wider than its container with no control in it (Benefits'
    // rates) scrolled for a mouse but not for the keyboard: Tab walks
    // controls only (WCAG 2.1.1). Such a container takes Tab itself, a
    // region named by the table's caption or heading (else its dialog's or
    // page's), which the arrow keys scroll and the keyboard's ring marks;
    // one that fits, or holds a control, is left as it is. Run whenever
    // the page or a dialog changes and when the window does (App.init).
    markScrollRegions() {
        for (const c of document.querySelectorAll('#page-content .table-container, #modal .table-container')) {
            if (c.id === 'grid-scroll') continue;  // the P&L grid is a region of its own
            const wide = c.scrollWidth > c.clientWidth;
            const empty = !c.querySelector('a[href], button, input, select, textarea, summary, [tabindex]');
            if (wide && empty) {
                if (!c.hasAttribute('data-scroll-region')) {
                    c.setAttribute('data-scroll-region', '');
                    c.setAttribute('tabindex', '0');
                    c.setAttribute('role', 'region');
                }
                c.setAttribute('aria-label', App._regionName(c));
            } else if (c.hasAttribute('data-scroll-region')) {
                ['data-scroll-region', 'tabindex', 'role', 'aria-label'].forEach(a => c.removeAttribute(a));
            }
        }
    },
    _regionName(c) {
        const said = (el) => (el && el.textContent || '').replace(/\s+/g, ' ').trim();
        const caption = said(c.querySelector('caption'));
        if (caption) return caption;
        for (let el = c; el && !['page-content', 'modal-body'].includes(el.id); el = el.parentElement) {
            for (let p = el.previousElementSibling; p; p = p.previousElementSibling) {
                const h = p.matches('h1, h2, h3, h4') ? p : [...p.querySelectorAll('h1, h2, h3, h4')].pop();
                if (said(h)) return said(h);
            }
        }
        const title = c.closest('#modal') && said(document.getElementById('modal-title'));
        return title || said(document.querySelector('#page-content .page-header h2')) || 'Table';
    },
    canGoBack() { return !!(history.state && history.state.from); },
    goBack() { if (App.backAllowed()) history.back(); },
    // ⌘[ on a Mac (the key, or where the layout puts "["; ⌘⌥[ is not it),
    // Alt+← elsewhere
    isBackKey(e) {
        if (App.isMac()) return e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey && (e.code === 'BracketLeft' || e.key === '[');
        return e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.code === 'ArrowLeft';
    },
    // The toolbar's Back follows the entry shown: after every push, stamp
    // and history move.
    syncBack() {
        const overlay = $('#modal-overlay'), m = $('#modal');
        const addressed = !!(overlay && m && !overlay.classList.contains('hidden')
            && m.dataset.address === (location.hash || '#/'));
        // the sidebar is raised over a view's overlay (W-22) only while the
        // dialog sits clear of it: a wide grid reaching over the sidebar
        // keeps the overlay whole, or the sidebar would cover its edge
        const sidebar = $('#sidebar');
        const clear = addressed && sidebar && m
            && m.getBoundingClientRect().left >= sidebar.getBoundingClientRect().right - 1;
        if (document.body && document.body.classList) document.body.classList.toggle('dialog-addressed', !!clear);
        if (addressed && !App._resizeSync) {
            App._resizeSync = true;
            window.addEventListener('resize', () => App.syncBack());
        }
        const btn = $('#back-btn');
        if (!btn) return;
        btn.disabled = !App.canGoBack();
        // under a form's overlay, not above it (App.editingDialog)
        btn.classList.toggle('tb-back--under', App.editingDialog());
    },
    // An address is on the bar (pushed, stamped or replaced): noted for the
    // next stamp, and Back follows it.
    addressShown() {
        App._here = location.hash || '#/';
        if (history.state && typeof history.state.n === 'number') App._nShown = history.state.n;
        App.syncBack();
    },
    // The button says its shortcut, for the platform in use
    labelBack() {
        const btn = $('#back-btn');
        if (!btn) return;
        const mac = App.isMac();
        btn.title = mac ? 'Back (⌘[)' : 'Back (Alt+←)';
        btn.setAttribute('aria-keyshortcuts', mac ? 'Meta+[' : 'Alt+ArrowLeft');
    },

    // The page under the open dialog, when the dialog has an address of
    // its own: a report view (#/reports/profit-loss?…) or a document over
    // its list (#/customers/12). Set by what opens one — withDocument (the
    // list route under the document), documentAddress and
    // ReportsPage.setAddress (the page the dialog was pushed from, kept
    // through a report's own hops: a drill-down over the P&L is still
    // over the Report Center) — and cleared by navigate; null while the
    // open dialog is a plain form, or none is open.
    _dialogUnder: null,

    // The dialog dismissed (Close, ×, Escape: not the app moving on) had an
    // address of its own, so the page under it goes on the bar —
    // #/reports, #/customers, #/budgets — replaced, not pushed: the entry
    // the view was pushed onto becomes the page it opened over, and a
    // refresh (App.navigate(location.hash) after a save), a reload or
    // Back cannot bring the closed view back (NEW-23).
    // ---- Recent screens (#336, #462) -----------------------------------------
    // Working a close means moving between a Profit & Loss, a bank register
    // and a payroll register over and over, each move a scroll through the
    // whole sidebar. The screens opened in this browser session (pages,
    // registers, documents, and the dialog reports with their dates) are
    // kept by address in sessionStorage, so a reload keeps them and a new
    // window starts clean. Since #462 they are one line at the top of the
    // sidebar, "Recent: <the screen showing> ▾", a disclosure button that
    // opens the last seven, most recent first; it used to be a growing list
    // of lines, each with an ×, that pushed the menu down.
    OPEN_TABS_KEY: 'slowbooks-open-screens',
    OPEN_TABS_MAX: 7,
    _openTabs: null,
    _recentOpen: false,
    // this page load: a history entry's number (entryState) means
    // something only within one load, so a tab remembers which load
    // numbered it (NEW-164: a stored number matched a fresh entry's)
    _loadId: String(Date.now()) + Math.random().toString(36).slice(2, 8),
    // whose list it is: the sign-in and the company (NEW-164: it carried
    // over to another user, and to another company on the same window)
    _tabsOwner: '',

    _ownerOf(status) {
        const user = status && status.user ? status.user.username : '';
        const company = status ? (status.company_name || '') : '';
        return `${user}|${company}`;
    },
    // called once the sign-in and the company are known: another owner's
    // list is dropped
    _tabsOwnerChanged(status) {
        const owner = App._ownerOf(status);
        if (owner === App._tabsOwner) return;
        App._tabsOwner = owner;
        let stored = null;
        try { stored = JSON.parse(sessionStorage.getItem(App.OPEN_TABS_KEY) || 'null'); } catch (e) { stored = null; }
        if (stored && !Array.isArray(stored) && stored.owner !== owner) {
            App._openTabs = [];
            App._saveOpenTabs();
            App.renderOpenTabs();
        }
    },

    openTabs() {
        if (App._openTabs) return App._openTabs;
        let stored = null;
        try { stored = JSON.parse(sessionStorage.getItem(App.OPEN_TABS_KEY) || 'null'); } catch (e) { stored = null; }
        const list = Array.isArray(stored) ? [] : ((stored && Array.isArray(stored.tabs)) ? stored.tabs : []);
        if (stored && !Array.isArray(stored) && typeof stored.owner === 'string' && !App._tabsOwner) App._tabsOwner = stored.owner;
        // kept oldest first; a list stored before #462 held up to twelve,
        // so only the newest seven are kept
        App._openTabs = list.filter(t => t && typeof t.key === 'string' && typeof t.label === 'string')
            .slice(-App.OPEN_TABS_MAX);
        return App._openTabs;
    },

    _saveOpenTabs() {
        try {
            sessionStorage.setItem(App.OPEN_TABS_KEY, JSON.stringify({ owner: App._tabsOwner, tabs: App.openTabs() }));
        } catch (e) { /* no storage: the list lives for the page */ }
    },

    // The address a screen is listed by: the hash without its '#', the
    // query included (a report with its dates is its own screen).
    _screenKey() { return (location.hash || '#/').replace(/^#/, '') || '/'; },

    // Words for a report's period, from its address: the period's name, or
    // the dates of a custom range, or an as-of date.
    _periodWords(query) {
        const names = {
            this_month: 'this month', this_quarter: 'this quarter', this_year: 'this year',
            this_year_to_date: 'this year to date', last_month: 'last month',
            last_quarter: 'last quarter', last_year: 'last year', last_year_to_date: 'last year to date',
        };
        if (query.period && names[query.period]) return names[query.period];
        if (query.as_of_date) return `as of ${formatDate(query.as_of_date)}`;
        if (query.start_date && query.end_date) return `${formatDate(query.start_date)} – ${formatDate(query.end_date)}`;
        if (query.year) return String(query.year);
        return '';
    },

    // The screen's own name: an open dialog's title (a report, a document)
    // with its period, else the page's heading, else the route's label.
    _screenLabel(route) {
        const { path, query } = App.parseHash(location.hash || '#/');
        const overlay = $('#modal-overlay'), modal = $('#modal');
        const dialog = overlay && modal && !overlay.classList.contains('hidden')
            && modal.dataset.address === (location.hash || '#/');
        let label = '';
        // a report view's address names the view: its own name, with its
        // period, whether or not its dialog is up yet (a "Reports" entry
        // for the Report Center under it: W-22's note)
        const view = /^\/reports\/([a-z0-9-]+)$/.exec(path);
        const known = view && typeof ReportsPage !== 'undefined' && ReportsPage._VIEWS && ReportsPage._VIEWS[view[1]];
        if (known) {
            label = typeof known.label === 'function' ? known.label() : known.label;
            if (dialog) label = ($('#modal-title') || {}).textContent || label;
            const when = App._periodWords(query);
            if (when) label = `${label} · ${when}`;
        } else if (dialog) {
            label = ($('#modal-title') || {}).textContent || '';
            const when = App._periodWords(query);
            if (when) label = `${label} · ${when}`;
        } else {
            const h2 = $('#page-content h2');
            const own = h2 && [...h2.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
            if (own) label = own.textContent;
        }
        label = (label || (route && route.label) || '').replace(/\s+/g, ' ').trim();
        return label.length > 48 ? `${label.slice(0, 47)}…` : label;
    },

    // A screen shown: listed (or its label refreshed) and the list redrawn.
    // The Dashboard and the Report Center are one click away already.
    screenShown(route) {
        const key = App._screenKey();
        if (key === '/' || key === '/reports') { App.renderOpenTabs(); return; }
        const label = App._screenLabel(route);
        if (!label) { App.renderOpenTabs(); return; }
        const tabs = App.openTabs();
        // the history entry's own number (App.entryState): a screen whose
        // address was replaced on the same entry (a report view over the
        // Report Center it opened on, a period change in the open report)
        // is the same screen under its new address, not a second one
        const n = history.state && typeof history.state.n === 'number' ? history.state.n : null;
        const found = tabs.find(t => t.key === key)
            || (n !== null ? tabs.find(t => t.n === n && t.load === App._loadId) : null);
        // kept oldest first; the screen shown moves to the end, so the
        // dropdown (newest first) leads with it and the oldest drops off
        if (found) tabs.splice(tabs.indexOf(found), 1);
        tabs.push({ key, label, n, load: App._loadId });
        while (tabs.length > App.OPEN_TABS_MAX) tabs.shift();
        App._saveOpenTabs();
        App.renderOpenTabs();
    },

    // Clear, at the foot of the dropdown: the list empties and the control
    // goes; the keyboard lands on the menu's first link.
    closeAllTabs() {
        const had = document.activeElement && document.activeElement.closest
            && document.activeElement.closest('#recent-screens');
        App._openTabs = [];
        App._recentOpen = false;
        App._saveOpenTabs();
        App.renderOpenTabs();
        if (had) {
            const first = document.querySelector('#sidebar .nav-links .nav-link');
            if (first) first.focus();
        }
    },

    renderOpenTabs() {
        const list = $('#sidebar .nav-links');
        if (!list) return;
        let group = $('#recent-screens');
        const tabs = App.openTabs();
        if (!tabs.length) { if (group) group.remove(); App._recentOpen = false; return; }
        if (!group) {
            group = document.createElement('li');
            group.id = 'recent-screens';
            group.className = 'recent-screens';
            list.insertBefore(group, list.firstElementChild);
            App._wireRecent(group);
        }
        const here = App._screenKey();
        const current = tabs.find(t => t.key === here);
        const focused = document.activeElement && group.contains(document.activeElement) ? document.activeElement : null;
        const keep = focused ? (focused.dataset.key || focused.dataset.recent || '') : null;
        const open = App._recentOpen;
        const newest = tabs.slice().reverse();
        group.innerHTML = `
            <button type="button" id="recent-toggle" class="recent-screens__toggle" data-recent="toggle"
                aria-expanded="${open ? 'true' : 'false'}" aria-controls="recent-menu"${current ? ` title="${escapeHtml(current.label)}"` : ''}>
                <span class="recent-screens__word">Recent${current ? ':' : ''}</span>
                ${current ? `<span class="recent-screens__here">${escapeHtml(current.label)}</span>` : ''}
                <span class="recent-screens__caret" aria-hidden="true">&#9662;</span>
            </button>
            <ul id="recent-menu" class="recent-screens__list" aria-label="Recent screens"${open ? '' : ' hidden'}>${newest.map(t => `
                <li class="recent-screens__item${t.key === here ? ' recent-screens__item--here' : ''}">
                    <a href="#${escapeHtml(t.key)}" class="recent-screens__link" data-key="${escapeHtml(t.key)}"${t.key === here ? ' aria-current="page"' : ''}
                        title="${escapeHtml(t.label)}">${escapeHtml(t.label)}</a></li>`).join('')}
                <li class="recent-screens__foot"><button type="button" class="recent-screens__clear" data-recent="clear">Clear</button></li>
            </ul>`;
        if (keep !== null) {
            // a screen picked from the list closes it: the link that had the
            // keyboard is now inside the hidden list, so the button takes it
            // (focus on a hidden link drops to <body>)
            let again = keep ? group.querySelector(`[data-key="${CSS.escape(keep)}"], [data-recent="${CSS.escape(keep)}"]`) : null;
            if (again && !open && again.closest('#recent-menu')) again = null;
            again = again || group.querySelector('#recent-toggle');
            if (again) again.focus();
        }
    },

    // Open or close the dropdown; `focus` is 'first', 'last' or 'toggle'
    // (where the keyboard goes once it has opened or closed).
    setRecentOpen(open, focus) {
        App._recentOpen = !!open;
        const group = $('#recent-screens');
        if (!group) return;
        const btn = group.querySelector('#recent-toggle');
        const menu = group.querySelector('#recent-menu');
        if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (menu) menu.hidden = !open;
        const items = App._recentItems();
        if (focus === 'first' && items[0]) items[0].focus();
        else if (focus === 'last' && items.length) items[items.length - 1].focus();
        else if (focus === 'toggle' && btn) btn.focus();
    },

    _recentItems() {
        return [...$$('#recent-menu .recent-screens__link, #recent-menu .recent-screens__clear')];
    },

    // The control's handlers, once, on its <li>: the button opens and
    // closes; arrows, Home and End move through the screens and Clear; Esc
    // closes and gives the button the focus; Tab moves on and the dropdown
    // closes behind it; a click outside closes it too.
    _wireRecent(group) {
        group.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-recent]');
            const link = e.target.closest('.recent-screens__link');
            if (btn && btn.dataset.recent === 'toggle') App.setRecentOpen(!App._recentOpen);
            else if (btn && btn.dataset.recent === 'clear') App.closeAllTabs();
            else if (link) App._recentOpen = false;
        });
        group.addEventListener('keydown', (e) => {
            if (!App._recentOpen && e.target.id === 'recent-toggle' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault();
                App.setRecentOpen(true, e.key === 'ArrowDown' ? 'first' : 'last');
                return;
            }
            if (!App._recentOpen) return;
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                App.setRecentOpen(false, 'toggle');
                return;
            }
            const items = App._recentItems();
            const i = items.indexOf(document.activeElement);
            let to = null;
            if (e.key === 'ArrowDown') to = i < 0 ? 0 : (i + 1) % items.length;
            else if (e.key === 'ArrowUp') to = i < 0 ? items.length - 1 : (i - 1 + items.length) % items.length;
            else if (e.key === 'Home') to = 0;
            else if (e.key === 'End') to = items.length - 1;
            if (to !== null && items[to]) { e.preventDefault(); items[to].focus(); }
        });
        group.addEventListener('focusout', (e) => {
            if (App._recentOpen && e.relatedTarget && !group.contains(e.relatedTarget)) App.setRecentOpen(false);
        });
        document.addEventListener('click', (e) => {
            const g = $('#recent-screens');
            if (App._recentOpen && g && !g.contains(e.target)) App.setRecentOpen(false);
        });
    },

    dialogClosed() {
        const under = App._dialogUnder;
        App._dialogUnder = null;
        if (!under || (location.hash || '#/') === under) return;
        // replaced, and the entry keeps its state: what it was pushed from
        // is still behind it, so the toolbar's Back follows (NEW-30)
        history.replaceState(history.state, '', under);
        App.addressShown();
        App.renderOpenTabs();
    },

    // The page a document's or a view's address opens over: the list route
    // of the same page ('#/customers' for '#/customers/12', '#/reports'
    // for '#/reports/profit-loss?…'); a page's own address as it is.
    pageUnder(hash) {
        const here = hash || '#/';
        const { route, param } = App.matchRoute(App.parseHash(here).path);
        if (!route || param === null) return here;
        const base = Object.keys(App.routes).find(k => !k.includes('/:') && App.routes[k].page === route.page);
        return base ? `#${base}` : here;
    },

    // The route for a path and its one-segment parameter: '/jobs/:id'
    // matches '/jobs/12' (param '12'); a path with a second segment after
    // the parameter matches nothing.
    matchRoute(path) {
        let route = App.routes[path] || null;
        let param = null;
        if (!route) {
            for (const [key, r] of Object.entries(App.routes)) {
                const i = key.indexOf('/:');
                if (i > 0 && path.startsWith(key.slice(0, i + 1)) && !path.slice(i + 1).includes('/')) {
                    route = r; param = decodeURIComponent(path.slice(i + 1)); break;
                }
            }
        }
        return { route, param };
    },

    // An address, taken apart: '#/reports/profit-loss?start_date=2026-07-01'
    // is the path '/reports/profit-loss' and the query
    // {start_date: '2026-07-01'}. The path picks the route; the query is
    // handed to the route's render as its second argument, so a report's
    // dates and filters, or a page's tab, ride in the address (R7). Before
    // this the whole string was the path, and a query was swallowed into
    // the :id parameter of whichever route it reached.
    parseHash(hash) {
        const full = String(hash || '').replace(/^#/, '') || '/';
        const i = full.indexOf('?');
        const path = (i < 0 ? full : full.slice(0, i)) || '/';
        const query = {};
        if (i >= 0) {
            for (const [k, v] of new URLSearchParams(full.slice(i + 1))) query[k] = v;
        }
        return { path, query };
    },

    async navigate(hash) {
        // the way back to the entry a declined move left (App.stayPut):
        // the page is still that entry's, nothing to render
        if (App._stay) { App._stay = false; App.addressShown(); return; }
        // an invited sign-in with its one-time password sets its own
        // before any page (#345); the server refuses everything else
        if (App.mustChangePassword) {
            $('#page-content').innerHTML = App._passwordChangeHtml();
            App.setStatus('Set your password');
            return;
        }
        if (App._pageCleanup) { App._pageCleanup(); App._pageCleanup = null; }
        // A dialog open over the page left (a report, a document) does not
        // stay over the page gone to: browser Back from an open report
        // re-rendered the Report Center under the report still showing
        // (R7 review). Every route that opens one ('/reports/:view', the
        // document routes) opens it afresh after rendering.
        const overlay = $('#modal-overlay');
        if (overlay && !overlay.classList.contains('hidden')) closeModal({ keepAddress: true });
        App._dialogUnder = null;
        const { path, query } = App.parseHash(hash);
        const full = String(hash || '').replace(/^#/, '') || '/';
        // Keep the address in step with the page shown. The toolbar's Home,
        // Quick Entry and Reports (and the shortcuts, search results and the
        // pages that move on by themselves) came here without changing it,
        // so the sidebar link of the page left behind then did nothing:
        // clicking it changed no hash (2.18.0 gate, macbase1 NEW-9).
        // pushState gives Back an entry, as a link does, and fires no
        // hashchange to navigate a second time. The query rides along: the
        // address is the whole of it. The entry remembers the address it
        // was pushed from (history.state.from, as ReportsPage.setAddress
        // records it): a view's "Back to …" reads it to go back through
        // history, and Back/Forward restore it with the entry.
        const here = location.hash || '#/';
        if (here !== `#${full}`) history.pushState(App.entryState(here), '', `#${full}`);
        // An entry a link made (a hash change: the sidebar, a row's link)
        // has no state yet: stamped with the address left, as a push is,
        // so Back knows there is an app page behind it (App.canGoBack).
        else if (!history.state) history.replaceState(App.entryState(App._here), '', here);
        App.addressShown();
        const { route, param } = App.matchRoute(path);
        if (!route) { $('#page-content').innerHTML = '<p>Page not found</p>'; return; }

        // Update active nav
        $$('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.page === route.page);
        });

        // Status bar
        App.setStatus(`Loading ${route.label}...`);

        // The nonprofit pages are in the sidebar only in nonprofit mode, but
        // a bookmark or a typed URL reached them in a business company too
        // (W-L13) — and posted to net-asset accounts a business never has.
        if (route.nonprofit && !Terms.isNonprofit()) {
            $('#page-content').innerHTML = App._nonprofitOnlyHtml(route.label);
            App.setStatus(`${route.label} — nonprofit companies only`);
            return;
        }
        // Payroll and HR are the administrator's: the server refuses them to
        // every other role, reads included, and the sidebar leaves them out.
        // A bookmark or a typed address still opened them half loaded, on
        // buttons that answered 403; a read-only user's View Checklist even
        // tried to set up a checklist (2.18.0 gate, W-L17 leftovers). So is
        // Migrate Data, whose dry run and import are refused to every other
        // role. The role can arrive while the first page loads: asked again
        // after.
        // The audit log is closed to a read-only sign-in (it keeps every
        // earlier value of every record); typed in, it said only "Couldn't
        // load this page" (skytech, 2.18.0 round 6).
        const notForReadOnly = () => App.NOT_FOR_READONLY_PAGES.includes(route.page) && App.isReadOnly();
        const adminOnly = () => (App.ADMIN_ONLY_PAGES.includes(route.page) && App.role !== 'admin')
            || notForReadOnly();
        const showAdminOnly = () => {
            if (notForReadOnly()) {
                $('#page-content').innerHTML = App._notForReadOnlyHtml(route.label);
                App.setStatus(`${route.label} — not open to a read-only sign-in`);
                return;
            }
            $('#page-content').innerHTML = App._adminOnlyHtml(route.label, route.page);
            App.setStatus(`${route.label} — administrators only`);
        };
        if (adminOnly()) return showAdminOnly();

        try {
            const html = await route.render(param, query);
            if (adminOnly()) return showAdminOnly();
            $('#page-content').innerHTML = html;
            App.setStatus(`${route.label} — Ready`);
            App.screenShown(route);  // listed under Open (#336)
            if (route.mount) App._pageCleanup = route.mount();
            // Back to a page left by one of its own rows (the chart's
            // account links to a register or a drill-down): the keyboard
            // goes back on the row, as a report's rows have it (W-7)
            if (history.state && history.state.focus) ReportsPage._refocusRow($('#page-content'));
        } catch (err) {
            if (adminOnly()) return showAdminOnly();
            // a sign-in that must set its password first: the form, not
            // the refusal its page met (NEW-149)
            if (App.mustChangePassword) {
                $('#page-content').innerHTML = App._passwordChangeHtml();
                App.setStatus('Set your password');
                return;
            }
            // Server-side detail (err.message and stack) goes to console
            // for devs; the DOM gets a clean user-facing error with a
            // recovery action. Avoid leaking framework internals into
            // the rendered page (S1 audit finding).
            console.error(err);
            $('#page-content').innerHTML = `<div class="empty-state">
                <h3>Couldn't load this page</h3>
                <p>${escapeHtml(err.message || 'An unexpected error occurred.')}</p>
                <p style="margin-top:12px;">
                    <a href="#/" class="btn btn-secondary" onclick="App.navigate('#/')">Return to Dashboard</a>
                </p>
            </div>`;
            App.setStatus('Error loading page');
        }
    },

    _nonprofitOnlyHtml(label) {
        return `<div class="empty-state">
            <h3>${escapeHtml(label)} is for nonprofit companies</h3>
            <p>This company is set up as a business, so there is nothing to record here.
               If it is a nonprofit, change its Company Type in Settings first.</p>
            <p style="margin-top:12px;">
                <a href="#/" class="btn btn-secondary">Return to Dashboard</a>
                <a href="#/settings" class="btn btn-secondary">Open Settings</a>
            </p>
        </div>`;
    },

    // Why a page is the administrator's; payroll and HR unless named here.
    _ADMIN_ONLY_WHY: {
        migrate: "Bringing books in from another program is open to an administrator's sign-in only.",
    },

    _notForReadOnlyHtml(label) {
        return `<div class="empty-state">
            <h3>${escapeHtml(label)} isn't open to a read-only sign-in</h3>
            <p>It keeps every earlier value of every record, so it is for administrators and bookkeepers.
               An administrator can change your role under Settings → Users.</p>
            <p style="margin-top:12px;">
                <a href="#/" class="btn btn-secondary">Return to Dashboard</a>
            </p>
        </div>`;
    },

    _adminOnlyHtml(label, page) {
        const why = App._ADMIN_ONLY_WHY[page]
            || "Payroll and staff records open to an administrator's sign-in only.";
        return `<div class="empty-state">
            <h3>${escapeHtml(label)} is for administrators</h3>
            <p>${escapeHtml(why)}
               An administrator can change your role under Settings → Users.</p>
            <p style="margin-top:12px;">
                <a href="#/" class="btn btn-secondary">Return to Dashboard</a>
            </p>
        </div>`;
    },

    // ---- An invited sign-in sets its own password (#345) ------------------
    mustChangePassword: false,

    _passwordChangeHtml() {
        return `<div class="page-header"><h2>Set your password</h2></div>
            <p style="max-width:520px; margin-bottom:12px;">The password you signed in with was for one use. Choose your own to continue; nothing else opens until you do.</p>
            <form onsubmit="App.savePassword(event)" style="max-width:420px;" data-readonly-ok>
                <div class="form-group"><label>New password <span style="font-weight:normal; color:var(--gray-500);">(at least 8 characters)</span></label>
                    <input name="new_password" type="password" required minlength="8" autocomplete="new-password" autofocus></div>
                <div class="form-group"><label>Again</label>
                    <input name="again" type="password" required minlength="8" autocomplete="new-password"></div>
                <div class="form-actions"><button type="submit" class="btn btn-primary">Save password</button></div>
            </form>`;
    },

    async savePassword(e) {
        e.preventDefault();
        const form = e.target;
        if (form.new_password.value !== form.again.value) { toast('The two passwords differ', 'error'); return; }
        try {
            await API.post('/auth/password', { new_password: form.new_password.value });
            App.mustChangePassword = false;
            toast('Password saved');
            App.navigate('#/');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Read-only sign-ins (Server Edition) -------------------------------
    // The server refuses every write from the readonly role with a 403 —
    // that stays the enforcement. But every page offered "+ New", and a
    // whole form could be filled in before the refusal arrived (2.17.3
    // exploratory test, W-L17). Once /api/auth/status names the role, the
    // create buttons are hidden and every form, a dialog's or a page's, is
    // shown locked, with a sentence saying why.
    role: 'admin',
    username: '',
    READ_ONLY_MESSAGE: 'Your sign-in is read-only: you can look, but not save changes. '
        + 'An administrator can change your role under Settings → Users.',
    // An outside accountant's sign-in (#340): the server lets it read
    // everything (the audit log included) and post journal entries, and
    // refuses every other write with its own sentence. Here it is treated
    // as read-only everywhere but the Journal Entries page: the create
    // buttons go, every form opens locked with this sentence, and the
    // entry form, Void (on its own entries) and Attach stay.
    ACCOUNTANT_MESSAGE: 'Your sign-in is an accountant\'s: you can read everything and post '
        + 'journal entries, but not change documents, bank entries or settings. '
        + 'An administrator can change your role under Settings → Users.',
    ACCOUNTANT_ACTIONS: new Set(['JournalPage.showForm', 'JournalPage.save', 'JournalPage.void',
        'JournalPage.uploadAttachment', 'JournalPage.addLine', 'JournalPage.removeLine']),

    isReadOnly() { return App.role === 'readonly'; },

    // the role, or the books: an accountant's copy (#346) takes journal
    // entries and nothing else, whoever is signed in to it (W-20)
    inCopy: false,
    copyDividingDate: '',
    COPY_MESSAGE() {
        return `This is an accountant's copy: it takes journal entries dated on or before ${App.copyDividingDate || 'the dividing date'} and nothing else. Send changes back from Companies when they are in.`;
    },

    isAccountant() { return App.role === 'accountant' || App.inCopy; },

    // read-only, or an accountant away from the journal: what is locked
    isLocked() { return App.isReadOnly() || App.isAccountant(); },

    lockedMessage() {
        if (App.inCopy) return App.COPY_MESSAGE();
        return App.isAccountant() ? App.ACCOUNTANT_MESSAGE : App.READ_ONLY_MESSAGE;
    },

    isAdmin() { return App.role === 'admin'; },

    setRole(role, username) {
        App.role = role || 'admin';
        if (username !== undefined) App.username = username || '';
        document.body.classList.toggle('role-readonly', App.isReadOnly());
        document.body.classList.toggle('role-accountant', App.isAccountant());
        // the first page can open before the role is known
        const open = document.querySelector('#sidebar .nav-link.active');
        if (App.role !== 'admin' && open && App.ADMIN_ONLY_PAGES.includes(open.dataset.page)) {
            App.navigate(location.hash);
        }
        const page = document.getElementById('page-content');
        if ((App.isAdmin() && !App.inCopy) || !page) return;
        if (App.isLocked()) {
            // the toolbar's shortcuts to new documents, and batch entry
            document.querySelectorAll('#topbar .tb-btn[data-action], #topbar .tb-btn[data-nav="#/quick-entry"]')
                .forEach(b => b.classList.add('hidden'));
            // the sidebar's pages that only enter things (Batch Payments...)
            App.hideWriteControls(document.getElementById('sidebar'));
        }
        if (App.isReadOnly()) {
            // the Audit Log, which the server refuses this role (an
            // accountant's sign-in reads it: seeing what moved is the job)
            document.querySelectorAll('#sidebar a[href="#/audit"]').forEach(l => {
                (l.closest('li') || l).classList.add('hidden');
            });
        }
        const roots = [page, document.getElementById('modal-body')].filter(Boolean);
        roots.forEach(App.rolePass);
        if (!App._roleObserver) {
            // Pages re-render in place (tabs, filters), and pages and dialogs
            // fill in after they open (Settings' lists, a report's figures):
            // keep them clean. The skytech sweep at 2.18.0 still found AR
            // Aging's Apply Late Fees on offer, drawn after the dialog opened.
            App._roleObserver = new MutationObserver(() => roots.forEach(App.rolePass));
            roots.forEach(r => App._roleObserver.observe(r, { childList: true, subtree: true }));
        }
    },

    // What a sign-in other than the administrator's gets of a page or a
    // dialog: the administrator's controls taken away, and for a read-only
    // sign-in every write as well.
    rolePass(root) {
        App.adminPass(root);
        App.readOnlyPass(root);
    },

    // ---- The administrator's controls (Server Edition) ---------------------
    // Some writes are the administrator's: company settings, backups, new
    // company files, the logo, connecting and importing from QuickBooks
    // Online (app.main's _ADMIN_WRITE_PREFIXES, and the routes that call
    // require_admin). The server refuses them to every other role, but a
    // bookkeeper was offered them: the whole Settings page could be filled
    // in before Save Settings answered "Admin role required". They are
    // marked where they are built, and for any role but admin:
    //   data-admin         a control only an administrator can use is hidden;
    //                      a field so marked shows its value, locked
    //   data-admin-fields  a form whose fields only an administrator saves
    //                      (Settings) shows its named fields locked: they are
    //                      what it sends
    //   data-admin-note    the sentence that says why, drawn hidden where the
    //                      controls are, is shown
    // A read-only sign-in's forms carry its own sentence, so the notes stay
    // hidden for it: one sentence, not two.
    adminPass(root) {
        if (!root || App.isAdmin()) return;
        root.querySelectorAll('[data-admin]').forEach(el => {
            const field = /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
            if (field) el.disabled = true;
            // a field shows its value; a file chooser has none to show
            if (!field || el.type === 'file') el.classList.add('hidden');
        });
        root.querySelectorAll('form[data-admin-fields]').forEach(form => {
            form.querySelectorAll('input[name], select[name], textarea[name]')
                .forEach(el => { el.disabled = true; });
        });
        if (App.isReadOnly()) return;
        root.querySelectorAll('[data-admin-note]').forEach(el => el.classList.remove('hidden'));
    },

    // What a read-only sign-in can't do, named by the page method a button
    // calls: the server refuses every one ("Your role doesn't allow this
    // action"), so the button isn't shown (skytech W-L17, 2.18.0 gate: a
    // read-only sign-in still saw Edit, Mark Sent, Void, Duplicate and
    // Upload on an invoice). Opening a record's form stays: it opens locked,
    // with the read-only note, and for some records it is the only view.
    // Reads stay too: View, Print, Save PDF, reports and IIF/CSV exports.
    // A request key for a write a client may retry (#292): random, so two
    // forms never share one; the browser's own generator where it has it
    requestKey() {
        try { return crypto.randomUUID(); } catch (e) { /* no crypto: fall through */ }
        return 'k-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
    },

    WRITE_ACTIONS: new Set([
        'InvoicesPage.void', 'InvoicesPage.markSent', 'InvoicesPage.duplicate', 'InvoicesPage.uploadAttachment',
        'InvoicesPage.deleteAttachment', 'InvoicesPage.showApplyCredit', 'InvoicesPage.showWriteOff',
        'InvoicesPage.emailInvoice', 'InvoicesPage.copyPaymentLink', 'InvoicesPage.checkPaymentStatus',
        'EstimatesPage.convert', 'SalesReceiptsPage.void', 'CreditMemosPage.void', 'CreditMemosPage.showApply',
        'CreditMemosPage.doApply', 'PaymentsPage.void', 'PaymentsPage.showApplyCredit', 'DepositsPage.voidDeposit',
        'DepositsPage.makeDeposit', 'BillsPage.void', 'BillsPage.voidBillPayment', 'BillsPage.uploadAttachment',
        'BillsPage.deleteAttachment', 'BillsPage.showPayForm', 'VendorCreditsPage.void', 'VendorCreditsPage.showApply',
        'VendorCreditsPage.doApply', 'PurchaseOrdersPage.convertToBill', 'PurchaseOrdersPage.doConvert',
        'ExpensesPage.void', 'ExpensesPage.uploadAttachment', 'ExpensesPage.deleteAttachment', 'CCChargesPage.voidCharge',
        'JournalPage.void', 'JournalPage.uploadAttachment', 'JournalPage.deleteAttachment',
        'JobCostsPage.voidEntry', 'JobCostsPage.showAllocate', 'JobsPage.postTime', 'JobsPage.remove',
        'JobsPage.saveBudget', 'JobsPage.seedBudget', 'InKindPage.voidEntry', 'ReleasesPage.voidEntry',
        'AllocationsPage.voidEntry', 'AllocationsPage.deleteRule', 'AllocationsPage.showRun', 'RecurringPage.generateNow',
        'RecurringPage.del', 'ResellerPermitsPage.del', 'ResellerPermitsPage.verifyWorkflow', 'TimeEntriesPage.approve',
        'TimeEntriesPage.reject', 'PayrollPage.process', 'PTOPage.approveRequest', 'PTOPage.rejectRequest',
        'PTOPage.runAccrual', 'PTOPage.revalue', 'DeductionsPage.endGarnishment', 'BenefitsPage.endEnrollment',
        'BenefitsPage.retireCode', 'BenefitsPage.seedStandard', 'BenefitsPage.setupAccounts', 'BenefitsPage.rebuildYTD',
        'BenefitsPage.createRemittanceBill', 'BenefitsPage.deleteRate', 'BenefitsPage.deleteGroup',
        'OnboardingPage.completeTask', 'FixedAssetsPage.showPostPurchaseForm', 'FixedAssetsPage.showDisposeForm',
        'FixedAssetsPage.showDepreciationForm', 'FixedAssetsPage.showImportForm', 'ItemsPage.showAdjust',
        'BankingPage.voidEntry', 'BankingPage.editEntry', 'BankingPage.saveEntryAnyway', 'BankingPage.voidTransfer', 'BankingPage.showEntryForm', 'BankingPage.showTransferForm',
        'BankingPage.showAccountForm', 'BankingPage.showOFXImport', 'BankingPage.confirmOFXImport', 'BankingPage.startReconcile',
        'BankingPage.finishReconcile', 'BankingPage.abandonReconcile', 'BankingPage.matchLine', 'BankingPage.showMatch',
        'BankingPage.excludeLine', 'BankingPage.findMatches', 'BankingPage.addAll', 'BankingPage.postLegacy',
        'BankingPage.dismissLegacy', 'BankingPage.syncSimpleFIN', 'BankingPage.disconnectSimpleFIN',
        'BankingPage.showSimpleFINHistory', 'BankRulesPage.deleteRule', 'BankRulesPage.applyAll', 'TaxPage.showPaySalesTax',
        'ReportsPage.sendCollectionLetters', 'ReportsPage.batchEmailStatements', 'ReportsPage.emailGivingStatements',
        'ReportsPage.applyLateFees', 'ReportsPage.deleteSaved', 'ReportsPage.voidDuplicate',
        'ReportsPage.dismissDuplicate', 'ReportsPage.restoreDuplicate', 'IIFPage.importFile',
        'IIFPage.importQbReportCsv', 'MigrationPage.doImport', 'OpeningBalancesPage.save', 'BudgetsPage.saveAll',
        'PayrollPage.showOpeningImport', 'PayrollPage.applyOpeningImport', 'EmployeesPage.deleteOpening',
    ]),

    // "+ New Invoice", "+ Record Payment", "New Account": a create button is
    // labelled "+ …", or is the page header's primary action; and every
    // button that calls one of WRITE_ACTIONS.
    //
    // The rest is marked where it is built, with data-write: a control only
    // an edit can use (Deactivate on an account, Create Backup, a bank
    // line's Add, the panel that imports a file). It is hidden; a field
    // marked so shows a value (a budget, a stored category), so it stays in
    // sight, locked. A file chooser is only ever an upload: hidden (macbase1,
    // 2.18.0 round 4: the Attachments "Choose File" in an invoice's view).
    hideWriteControls(root) {
        if (!root || !App.isLocked()) return;
        root.querySelectorAll('button, a.btn, [data-write], input[type="file"]').forEach(el => {
            // the accountant keeps the journal page's own controls (#340)
            if (App.isAccountant() && (el.hasAttribute('data-accountant-ok') || el.closest('[data-accountant-ok]'))) return;
            if (el.getAttribute('data-write') !== null) {
                if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) el.disabled = true;
                else el.classList.add('hidden');
                return;
            }
            if (el.tagName === 'INPUT') {  // the file choosers
                el.disabled = true;
                el.classList.add('hidden');
                return;
            }
            const label = (el.textContent || '').trim();
            const headerAction = el.classList.contains('btn-primary') && el.closest('.page-header');
            const call = /^\s*(\w+Page\.\w+)\(/.exec(el.getAttribute('onclick') || '');
            // Edit beside View (an invoice's row): View shows the record, and
            // Edit would only open it locked
            const editBesideView = label === 'Edit' && !!el.parentElement
                && [...el.parentElement.querySelectorAll('button, a.btn')]
                    .some(b => (b.textContent || '').trim() === 'View');
            if (App.isAccountant() && call && App.ACCOUNTANT_ACTIONS.has(call[1])) return;
            if (label.startsWith('+') || headerAction || editBesideView
                || (call && App.WRITE_ACTIONS.has(call[1]))) {
                el.classList.add('hidden');
            }
        });
    },

    // Called by openModal(), and for the page by readOnlyPass: Settings,
    // Quick Entry and Batch Payments are forms on the page itself, and were
    // left open to a read-only sign-in until Save (skytech, 2.18.0 round 4).
    // A form that only opens a document (the customer statement) carries
    // data-readonly-ok and stays usable; so does a button that only opens a
    // record inside a locked form (an email template, shown locked in turn).
    lockForms(root) {
        if (!root || !App.isLocked()) return;
        // the accountant's own form: the journal entry's (data-accountant-ok)
        const skip = App.isAccountant() ? ':not([data-accountant-ok])' : '';
        root.querySelectorAll(`form:not([data-readonly-ok])${skip}`).forEach(form => {
            form.querySelectorAll('input, select, textarea').forEach(el => { el.disabled = true; });
            form.querySelectorAll('button').forEach(b => {
                if (/closeModal\(/.test(b.getAttribute('onclick') || '')) return;
                if (b.hasAttribute('data-readonly-ok')) return;
                b.disabled = true;
                b.style.opacity = '0.5';
                b.style.cursor = 'not-allowed';
            });
            form.onsubmit = (e) => { e.preventDefault(); toast(App.lockedMessage(), 'error'); return false; };
            if (!form.querySelector('.readonly-note')) {
                form.insertAdjacentHTML('afterbegin',
                    `<div class="hint hint--locked readonly-note" style="margin-bottom:10px;">${escapeHtml(App.lockedMessage())}</div>`);
            }
        });
    },

    // What a read-only sign-in gets of a page or a dialog: its forms locked,
    // and nothing offered that only an edit could use.
    readOnlyPass(root) {
        App.lockForms(root);
        App.hideWriteControls(root);
    },

    setStatus(text) {
        const el = $('#status-text');
        if (el) el.textContent = text;
    },

    updateClock() {
        const now = new Date();
        const clock = $('#topbar-clock');
        if (clock) clock.textContent = now.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
        const statusDate = $('#status-date');
        if (statusDate) statusDate.textContent = now.toLocaleDateString('en-US', {weekday:'long', year:'numeric', month:'long', day:'numeric'});
    },

    showAbout() {
        const splash = $('#splash');
        if (splash) splash.classList.remove('hidden');
    },

    // Theme toggle — Feature 12: Dark Mode
    toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('slowbooks-theme', next);
        const btn = $('#theme-toggle');
        if (btn) btn.innerHTML = next === 'dark' ? '&#9788;' : '&#9790;';
        // Canvas ink is painted, not styled: a chart on screen keeps the old
        // theme's axis and grid colours until it is redrawn (1.06:1 on the
        // dashboard trend — skytech, 2.16.0 gate). Pages that draw charts
        // listen for this and redraw.
        document.dispatchEvent(new CustomEvent('slowbooks:themechange', { detail: { theme: next } }));
    },

    loadTheme() {
        const saved = localStorage.getItem('slowbooks-theme');
        if (saved === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
            const btn = $('#theme-toggle');
            if (btn) btn.innerHTML = '&#9788;';
        }
    },

    // Inactive accounts are hidden by default — that is the point of
    // deactivating one. But they must be reachable, or deactivating is a
    // one-way trip with no way back to the row (issue #139).
    _showInactiveAccounts: false,

    toggleInactiveAccounts() {
        App._showInactiveAccounts = !App._showInactiveAccounts;
        App.navigate('#/accounts');
    },

    // Where an account's register is (#240): the bank register for a bank or
    // card account (its page knows feeds, reconciliations, Entry and
    // Transfer), the drill-down for this year for every other account — the
    // period shell on it widens the dates. An inactive account opens too:
    // its history is still there.
    accountRegisterHref(account) {
        if (account.bank_kind) return `#/banking/${Number(account.id)}`;
        const year = new Date().getFullYear();
        return ReportsPage.viewUrl('account-transactions', {
            account_id: Number(account.id), period: 'this_year', start_date: `${year}-01-01`, end_date: `${year}-12-31`,
        });
    },

    openAccountRegister(id, isBank) {
        App.navigate(App.accountRegisterHref({ id, bank_kind: isBank ? 'bank' : null }));
    },

    // The chart's filter box: typed text keeps the rows whose number or
    // name contains it, and the type headings of the rows left; the rest
    // are hidden, not removed, so clearing the box brings everything back.
    _accountsFilter: '',

    filterAccounts(text) {
        App._accountsFilter = text || '';
        const needle = App._accountsFilter.trim().toLowerCase();
        const groups = {};
        const details = {};
        $$('tr[data-account-row]').forEach(tr => {
            const shown = !needle || (tr.dataset.accountRow || '').includes(needle);
            tr.hidden = !shown;
            const type = tr.dataset.accountType;
            groups[type] = (groups[type] || 0) + (shown ? 1 : 0);
            const detail = tr.dataset.accountDetail;
            details[detail] = (details[detail] || 0) + (shown ? 1 : 0);
        });
        // a sub-account left showing while its parent is filtered out says
        // its full path, since the indent no longer can (#416)
        $$('tr[data-parent-id]').forEach(tr => {
            const span = tr.querySelector('.account-path');
            const up = document.querySelector(`tr[data-account-id="${tr.dataset.parentId}"]`);
            if (span) span.hidden = !(span.dataset.always || (!tr.hidden && up && up.hidden));
        });
        $$('tr[data-account-group]').forEach(tr => { tr.hidden = !(groups[tr.dataset.accountGroup] || 0); });
        $$('tr[data-account-detail-group]').forEach(tr => { tr.hidden = !(details[tr.dataset.accountDetailGroup] || 0); });
        const note = $('#accounts-filter-note');
        if (note) {
            const n = Object.values(groups).reduce((a, b) => a + b, 0);
            note.textContent = needle ? (n === 1 ? '1 account matches' : `${n} accounts match`) : '';
        }
    },

    // The seeded chart stays beside an imported one; the seeded accounts
    // nobody uses (no lines, no sub-accounts, not a control account, not
    // named by a setting or a register) can go in one step (#332). A dry
    // run lists them first; the result names what went and what stayed.
    async showSeededCleanup() {
        let preview;
        try { preview = await API.get('/accounts/unused-seeded'); } catch (err) { toast(err.message, 'error'); return; }
        const rows = preview.removable.map(a => `<li>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</li>`).join('');
        const kept = preview.kept.map(a => `<li>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)} <span style="color:var(--gray-500);">— ${escapeHtml(a.reason)}</span></li>`).join('');
        openModal('Remove unused seeded accounts', `
            <p style="font-size:12px; margin-bottom:8px;">Every new company starts with a seeded chart of accounts. When a real chart is imported the seeded accounts stay beside it. These seeded accounts have never been used and can go now; any with postings, sub-accounts, a register or a job in the settings stay.</p>
            ${preview.removable.length ? `<p style="font-size:12px; margin-bottom:4px;"><strong>${preview.removable.length} to remove:</strong></p><ul style="font-size:12px; columns:2; margin:0 0 8px 16px;">${rows}</ul>` : '<p style="font-size:12px;">Nothing to remove: every seeded account is in use or already gone.</p>'}
            ${preview.kept.length ? `<details style="font-size:11px; margin-bottom:8px;"><summary>${preview.kept.length} seeded account(s) stay</summary><ul style="margin:4px 0 0 16px;">${kept}</ul></details>` : ''}
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                ${preview.removable.length ? `<button type="button" data-destructive class="btn btn-primary" onclick="App.runSeededCleanup()">Remove ${preview.removable.length} account${preview.removable.length === 1 ? '' : 's'}</button>` : ''}
            </div>`);
    },
    async runSeededCleanup() {
        try {
            const res = await API.post('/accounts/unused-seeded/remove', {});
            toast(`${res.removed.length} seeded account${res.removed.length === 1 ? '' : 's'} removed${res.kept.length ? `; ${res.kept.length} stayed` : ''}`);
            closeModal();
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },

    // QuickBooks' fifteen account types (#390), in picker order, from the
    // server so the page never keeps its own copy of the list.
    async _detailTypes() {
        if (!App._detailTypeList) App._detailTypeList = await API.get('/accounts/detail-types');
        return App._detailTypeList;
    },

    // The chart's parent links (#416), for the nested view and the parent
    // picker. Every walk stops at a repeat: a loop saved before 2.27.0's
    // guard must not hang the page.
    _accountTree(accounts) {
        const byId = new Map(accounts.map(a => [a.id, a]));
        const children = new Map();
        for (const a of accounts) {
            if (a.parent_id == null || a.parent_id === a.id) continue;
            if (!children.has(a.parent_id)) children.set(a.parent_id, []);
            children.get(a.parent_id).push(a);
        }
        const names = {};
        for (const a of accounts) {
            const k = a.name.trim().toLowerCase();
            names[k] = (names[k] || 0) + 1;
        }
        const path = a => {
            const parts = [], seen = new Set();
            for (let c = a; c && !seen.has(c.id); c = byId.get(c.parent_id)) {
                seen.add(c.id);
                parts.unshift(c.name);
            }
            return parts.join(': ');
        };
        // the account and every account under it, at any depth
        const family = a => {
            const out = new Set(), todo = [a.id];
            while (todo.length) {
                const id = todo.pop();
                if (out.has(id)) continue;
                out.add(id);
                for (const c of children.get(id) || []) todo.push(c.id);
            }
            return out;
        };
        // every account under this one, at any depth, in tree order (self
        // excluded) — what a move retypes, and what the status line counts
        const descendants = a => {
            const out = [], seen = new Set([a.id]);
            const walk = id => {
                for (const c of children.get(id) || []) {
                    if (seen.has(c.id)) continue;
                    seen.add(c.id);
                    out.push(c);
                    walk(c.id);
                }
            };
            walk(a.id);
            return out;
        };
        // the sub-accounts a detail-type change cascades to: descendants
        // reached through sub-accounts that all share `detail`, in tree order
        // (self excluded). A sub-account of another type stops the walk, as
        // _cascade_detail_type stops — so a pinned child, or one of another
        // type, and anything under it are left out (#416).
        const sharing = (a, detail) => {
            const out = [], seen = new Set([a.id]);
            const walk = id => {
                for (const c of children.get(id) || []) {
                    if (seen.has(c.id) || c.detail_type !== detail) continue;
                    seen.add(c.id);
                    out.push(c);
                    walk(c.id);
                }
            };
            walk(a.id);
            return out;
        };
        const collides = a => names[a.name.trim().toLowerCase()] > 1;
        return { byId, children, path, family, descendants, sharing, collides };
    },

    // Each detail type's accounts in tree order (the list sorted by detail
    // type; all as one when byDetail is false): each parent, then its
    // sub-accounts indented under it, each level in the list's own order
    // (number, then name). A sub-account whose parent is listed elsewhere
    // (another detail type, or hidden) starts its own branch.
    _accountTreeOrder(accts, byDetail = true) {
        const out = [];
        const groups = [];
        for (const a of accts) {
            const last = groups[groups.length - 1];
            if (last && (!byDetail || last[0].detail_type === a.detail_type)) last.push(a);
            else groups.push([a]);
        }
        for (const list of groups) {
            const here = new Set(list.map(a => a.id));
            const kids = new Map();
            const roots = [];
            for (const a of list) {
                if (a.parent_id != null && a.parent_id !== a.id && here.has(a.parent_id)) {
                    if (!kids.has(a.parent_id)) kids.set(a.parent_id, []);
                    kids.get(a.parent_id).push(a);
                } else roots.push(a);
            }
            const done = new Set();
            const walk = (a, depth) => {
                if (done.has(a.id)) return;
                done.add(a.id);
                out.push({ a, depth });
                for (const c of kids.get(a.id) || []) walk(c, depth + 1);
            };
            roots.forEach(a => walk(a, 0));
            list.forEach(a => walk(a, 0));  // a loop saved before the guard
        }
        return out;
    },

    async renderAccounts() {
        const [accounts, detailTypes] = await Promise.all([API.get('/accounts'), App._detailTypes()]);
        const detailOrder = detailTypes.map(d => d.value);
        const tree = App._accountTree(accounts);
        const grouped = {};
        let inactiveCount = 0;
        for (const a of accounts) {
            if (!grouped[a.account_type]) grouped[a.account_type] = [];
            const inactive = a.is_active === false;
            if (inactive) inactiveCount++;
            if (!inactive || App._showInactiveAccounts) grouped[a.account_type].push(a);
        }

        const typeOrder = ['asset', 'liability', 'equity', 'income', 'cogs', 'expense'];
        const typeNames = { asset: 'Assets', liability: 'Liabilities', equity: T('Equity'),
            income: T('Income'), cogs: 'Cost of Goods Sold', expense: 'Expenses' };

        let html = `
            ${skipLinkHtml('#accounts-filter', 'Skip to the account filter')}
            <div class="page-header">
                <h2 id="accounts-heading">Chart of Accounts</h2>
                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                    <input type="search" id="accounts-filter" aria-label="Filter accounts" placeholder="Filter by number or name" value="${escapeHtml(App._accountsFilter)}" style="width:200px;" oninput="App.filterAccounts(this.value)">
                    <span id="accounts-filter-note" role="status" style="font-size:11px; color:var(--gray-500);"></span>
                    ${inactiveCount ? `<button class="btn btn-sm btn-secondary" onclick="App.toggleInactiveAccounts()">${App._showInactiveAccounts ? 'Hide' : 'Show'} ${inactiveCount} inactive</button> ` : ''}
                    <button class="btn btn-secondary" data-write onclick="App.showChartImport()">Import…</button>
                    <button class="btn btn-secondary" data-write onclick="App.showSeededCleanup()">Remove unused seeded accounts…</button>
                    <button class="btn btn-primary" onclick="App.showAccountForm()">New Account</button>
                </div>
            </div>
            <p style="font-size:11px; color:var(--gray-500); margin:-4px 0 8px;">An account's name opens its register: the bank register for a bank or card account, this year's transactions for the rest.</p>
            <nav aria-labelledby="accounts-heading"><div class="table-container"><table>
                <thead><tr><th scope="col" style="width:80px;">Number</th><th scope="col">Name</th><th scope="col" style="width:160px;">Type</th><th scope="col" class="amount" style="width:100px;">Balance</th><th scope="col" style="width:190px;">Actions</th></tr></thead>
                <tbody>`;

        for (const type of typeOrder) {
            const accts = grouped[type] || [];
            if (accts.length === 0) continue;
            html += `<tr data-account-group="${type}" style="background:linear-gradient(180deg, #e8ecf2 0%, #dde2ea 100%);"><td colspan="5" style="font-weight:700; color:var(--qb-navy); font-size:11px; padding:4px 10px;">${typeNames[type]}</td></tr>`;
            // A second heading per detail type under the type (#390), so an
            // account under the wrong one (a loan among the current
            // liabilities) shows in the first scroll. Number order within.
            const rank = d => { const i = detailOrder.indexOf(d); return i < 0 ? detailOrder.length : i; };
            accts.sort((x, y) => rank(x.detail_type) - rank(y.detail_type));
            let detailShown = null;
            for (const { a, depth } of App._accountTreeOrder(accts)) {
                if (a.detail_type !== detailShown) {
                    detailShown = a.detail_type;
                    html += `<tr data-account-detail-group="${escapeHtml(a.detail_type || '')}"><td colspan="5" style="font-weight:600; color:var(--gray-600); font-size:11px; padding:3px 10px 3px 22px; background:var(--gray-50);">${escapeHtml(T(a.detail_type_label || ''))}</td></tr>`;
                }
                const inactive = a.is_active === false;
                const path = tree.path(a);
                const parent = tree.byId.get(a.parent_id);
                // the full path is written out where the indent cannot say
                // it: a sub-account listed away from its parent (under 1100,
                // a bank, or a parent left hidden as inactive), or a name
                // another account has too
                const showPath = parent && (depth === 0 || tree.collides(a));
                const haystack = escapeHtml(`${a.account_number || ''} ${a.name} ${path}`.toLowerCase());
                html += `<tr${inactive ? ' class="row--dim"' : ''} data-account-id="${Number(a.id)}"${parent ? ` data-parent-id="${Number(parent.id)}"` : ''} data-account-row="${haystack}" data-account-type="${type}" data-account-detail="${escapeHtml(a.detail_type || '')}">
                    <td style="font-family:var(--font-mono);">${escapeHtml(a.account_number || '')}</td>
                    <td style="padding-left:${10 + depth * 20}px;">${depth ? '<span aria-hidden="true" style="color:var(--gray-500);">└ </span>' : ''}<a href="${escapeHtml(App.accountRegisterHref(a))}" data-row-key="account:${Number(a.id)}" onclick="ReportsPage._leaveFrom(this)" style="font-weight:700; color:var(--text-link); text-decoration:none;" title="${escapeHtml(parent ? `Open the register: ${path}` : 'Open the register')}">${escapeHtml(a.name)}</a>${parent ? `<span class="visually-hidden">, sub-account of ${escapeHtml(tree.path(parent))}</span>` : ''}${parent ? ` <span class="account-path" aria-hidden="true"${showPath ? ' data-always="1"' : ' hidden'} style="font-size:11px; color:var(--gray-500);">${escapeHtml(path)}</span>` : ''}${a.is_control ? ` <span class="badge-control" title="${escapeHtml(a.control_purpose || 'the software finds this account by its number')}">control</span>` : ''}${inactive ? ' <span class="badge badge-draft">inactive</span>' : ''}</td>
                    <td>${escapeHtml(T(a.detail_type_label || a.account_type))}</td>
                    <td class="amount">${formatCurrency(a.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="App.showAccountForm(${a.id})">Edit</button>
                        ${inactive
                            ? `<button class="btn btn-sm btn-secondary" data-write onclick="App.setAccountActive(${a.id}, true)">Reactivate</button>`
                            : `<button class="btn btn-sm btn-secondary" data-write onclick="App.setAccountActive(${a.id}, false)">Deactivate</button>`}
                        ${a.is_control ? '' : `<button data-destructive class="btn btn-sm btn-secondary" data-write onclick="App.deleteAccount(${a.id})">Delete</button>`}
                    </td>
                </tr>`;
            }
        }
        html += `</tbody></table></div></nav>`;
        // a filter typed before a re-render (Show inactive, a save) still applies
        if (App._accountsFilter) setTimeout(() => App.filterAccounts(App._accountsFilter), 0);
        return html;
    },

    async showAccountForm(id = null) {
        let acct = { name: '', account_number: '', account_type: 'expense', detail_type: 'expense', description: '', parent_id: null };
        const [loaded, accounts] = await Promise.all([id ? API.get(`/accounts/${id}`) : null, API.get('/accounts')]);
        if (loaded) acct = loaded;
        App._accountFormState = { acct, accounts, tree: App._accountTree(accounts) };

        // One picker (#390): a group per base type, QuickBooks' types inside;
        // the base type follows from the choice. Accounts Receivable and
        // Payable belong to 1100 and 2000 alone, so they are offered only
        // where the account already is one.
        const detailTypes = await App._detailTypes();
        const typeHeadings = { asset: 'Assets', liability: 'Liabilities', equity: T('Equity'),
            income: T('Income'), cogs: 'Cost of Goods Sold', expense: 'Expenses' };
        const picker = Object.keys(typeHeadings).map(t => {
            const opts = detailTypes
                .filter(d => d.account_type === t && (!d.control_only || d.value === acct.detail_type))
                .map(d => `<option value="${d.value}" data-account-type="${d.account_type}" ${acct.detail_type === d.value ? 'selected' : ''}>${escapeHtml(T(d.label))}</option>`)
                .join('');
            return opts ? `<optgroup label="${escapeHtml(typeHeadings[t])}">${opts}</optgroup>` : '';
        }).join('');
        // A control account is found by its number when a document posts, so the
        // number and the type are fixed and the API refuses to change them (400).
        // Renaming is allowed and is the point — say so instead of hiding the form.
        const locked = !!acct.is_control;
        // A number is optional (digits; 6150.1 or 6150-01 for a sub-account):
        // many companies don't number their accounts, and the form read as
        // if one were required (#332)
        const numberRequired = false;
        const lockNote = locked
            ? `<div class="form-group full-width"><div class="hint hint--locked">
                   <strong>${escapeHtml(acct.account_number || '')} ${escapeHtml(acct.name)} is a control account.</strong>
                   The software finds it by its number to post ${escapeHtml(acct.control_purpose || 'part of the books')},
                   so the number and type (and its detail type) cannot change — a document that could not find it would have nowhere to post.
                   <em>You can rename it.</em>
               </div></div>`
            : '';
        openModal(id ? 'Edit Account' : 'New Account', `
            <form onsubmit="App.saveAccount(event, ${id})">
                <div class="form-grid">
                    ${lockNote}
                    <div class="form-group"><label>Account Number <span style="font-weight:normal; color:var(--gray-500);">(optional)</span></label>
                        <input name="account_number" value="${escapeHtml(acct.account_number || '')}"${locked ? ' readonly disabled' : ''}
                            ${numberRequired ? 'required' : ''} pattern="\\d+([.\\-]\\d+)*" maxlength="20" placeholder="e.g. 6150, or leave blank"
                            title="Optional. Digits, like 6150; a sub-account can use 6150.1 or 6150-01."></div>
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(acct.name)}"></div>
                    <div class="form-group"><label>Type *</label>
                        <select name="detail_type" data-was="${escapeHtml(acct.detail_type || '')}" aria-describedby="account-type-hint" onchange="App.accountTypeChanged()"${locked ? ' disabled' : ''}>
                            ${picker}
                        </select>
                        <span id="account-type-hint" class="hint">A bank or card account gets a register. A sub-account shares its parent's detail type, except under a bank, card, or a 1100 or 2000 parent, where it is set on its own.</span></div>
                    <div class="form-group"><label for="account-parent">Sub-account of</label>
                        <select name="parent_id" id="account-parent" data-was="${acct.parent_id == null ? '' : Number(acct.parent_id)}" aria-describedby="account-parent-hint" onchange="App.accountParentChanged()">
                            ${App._accountParentOptions(acct.parent_id)}
                        </select>
                        <span id="account-parent-hint" class="hint">${App._accountStandsAlone(acct) ? 'Found by its number when the books post, so it stays a top-level account.' : 'Accounts of the same type. A sub-account is listed under its parent.'}</span>
                        <span id="account-parent-status" class="hint" role="status"></span></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description">${escapeHtml(acct.description || '')}</textarea></div>
                </div>
                <div id="account-form-error" role="alert" style="color:var(--danger); font-size:12px; margin-top:8px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Account</button>
                </div>
            </form>`);
    },

    // The parents the server would take for the account on the form (#416):
    // the same base type; never the account itself or one under it (the
    // loop guard); under 1100, 2000, a bank or a card any detail type, under
    // any other parent the sub-account takes the parent's detail type, so a
    // bank or card account goes only under those, and a control account,
    // whose detail type is fixed, only under a parent of its own. Inactive
    // accounts are left out, except the parent it has.
    _accountParentChoices(detail) {
        const { acct, accounts, tree } = App._accountFormState;
        if (App._accountStandsAlone(acct)) return [];
        const d = (App._detailTypeList || []).find(x => x.value === detail);
        const base = d ? d.account_type : acct.account_type;
        const mine = acct.id ? tree.family(acct) : new Set();
        return accounts.filter(p => {
            if (mine.has(p.id) || p.account_type !== base) return false;
            if (p.is_active === false && p.id !== acct.parent_id) return false;
            if (App._PINNED_DETAILS.includes(p.detail_type)) return true;
            if (['bank', 'credit_card'].includes(detail)) return false;
            return !acct.is_control || p.detail_type === acct.detail_type;
        });
    },

    _PINNED_DETAILS: ['bank', 'credit_card', 'accounts_receivable', 'accounts_payable'],

    // A control account (1100, 2000, Checking…) is found by its number and
    // takes no new parent; the server refuses one (#416).
    _accountStandsAlone(acct) {
        return !!acct.is_control || ['accounts_receivable', 'accounts_payable'].includes(acct.detail_type);
    },

    _accountParentOptions(selected, detail = App._accountFormState.acct.detail_type, keep = true) {
        const { tree } = App._accountFormState;
        const choices = App._accountParentChoices(detail);
        // the parent it has is always offered on opening, whatever the rule
        // (a chart imported before it): saving a rename must not move it
        const current = tree.byId.get(selected);
        if (keep && current && !choices.includes(current)) choices.push(current);
        // tree order, each named by its full path so two Hotels differ
        const order = App._accountTreeOrder(choices, false);
        return `<option value="">None (a top-level account)</option>` + order.map(({ a: p }) =>
            `<option value="${Number(p.id)}"${p.id === selected ? ' selected' : ''}>${escapeHtml([p.account_number, tree.path(p)].filter(Boolean).join(' '))}</option>`
        ).join('');
    },

    // "Lodging and Hotel (2 sub-accounts)": up to two named, then "and N
    // more", with the count. For the status line's cascade warning (#416).
    _subAccountList(subs) {
        const names = subs.map(s => s.name);
        const list = subs.length <= 2
            ? names.join(' and ')
            : `${names.slice(0, 2).join(', ')} and ${subs.length - 2} more`;
        return `${list} (${App._subAccountCount(subs)})`;
    },

    _subAccountCount(subs) {
        return `${subs.length} sub-account${subs.length === 1 ? '' : 's'}`;
    },

    // A parent other than 1100, 2000, a bank or a card hands its detail type
    // down: choosing one sets the Type picker to it, and the status says how
    // many sub-accounts follow and that a later move out keeps the type —
    // #428's cascade is silent and does not reverse (#416). Set back to None
    // or a pinned parent, the type a parent gave stays: say so, so the P&L
    // family (#429) is not left below Net Operating Income by surprise. The
    // choice is kept as the one a type change comes back to.
    accountParentChanged() {
        const state = App._accountFormState;
        const sel = $('#account-parent');
        const picker = document.querySelector('select[name=detail_type]');
        const parent = state.tree.byId.get(Number(sel.value));
        state.wanted = parent ? parent.id : null;
        const status = $('#account-parent-status');
        const name = (state.acct.name || '').trim() || 'This account';
        // the sub-accounts that follow are the ones _cascade_detail_type
        // retypes: those sharing the account's own detail type, the "old" the
        // cascade starts from (not the picker's, which may already hold the
        // parent's). A child of another type, and anything under it, is left
        // out (#416).
        const subs = state.acct.id
            ? state.tree.sharing(state.acct, state.acct.detail_type)
            : [];

        if (parent && picker && !picker.disabled
            && !App._PINNED_DETAILS.includes(parent.detail_type)
            && picker.value !== parent.detail_type) {
            picker.value = parent.detail_type;
            let msg = `Type set to ${T(parent.detail_type_label)}, the type of ${parent.name}`;
            if (subs.length) msg += `; ${App._subAccountList(subs)} follow`;
            msg += subs.length
                ? '. Moving it back out later keeps the type; change the Type picker then.'
                : '. Moving it back out later keeps the type.';
            status.textContent = msg;
            return;
        }

        // set to None or a pinned parent: a type handed down earlier stays
        const toNoneOrPinned = !parent || App._PINNED_DETAILS.includes(parent.detail_type);
        const cameFromParent = picker && !picker.disabled
            && !App._PINNED_DETAILS.includes(picker.value)
            && (picker.value !== picker.dataset.was || subs.length > 0);
        if (toNoneOrPinned && cameFromParent) {
            const label = (picker.selectedOptions[0]
                ? picker.selectedOptions[0].textContent : picker.value).trim();
            let msg = `${name} keeps ${label}`;
            if (subs.length) msg += ` (and so do its ${App._subAccountCount(subs)})`;
            // the P&L family sits below Net Operating Income only for the two
            // Other types; any other type just needs the picker
            msg += ['other_income', 'other_expense'].includes(picker.value)
                ? `; change the Type picker to move the family back above the Other lines on the ${T('Profit & Loss')}.`
                : '; change the Type picker to give the family another type.';
            status.textContent = msg;
            return;
        }
        status.textContent = '';
    },

    // A new type re-lists the parents. The parent chosen (or the one the
    // account had) stays while the type allows it and comes back when the
    // type does; one that cannot take the type is let go, the status saying
    // so: a type change and a move out from under the parent in one save.
    accountTypeChanged() {
        const state = App._accountFormState;
        const sel = $('#account-parent');
        const picker = document.querySelector('select[name=detail_type]');
        if (!sel || !picker) return;
        if (!('wanted' in state)) state.wanted = state.acct.parent_id ?? null;
        const wanted = state.wanted;
        const original = picker.value === state.acct.detail_type && wanted === state.acct.parent_id;
        sel.innerHTML = App._accountParentOptions(wanted, picker.value, original);
        const parent = state.tree.byId.get(wanted);
        const fits = parent && sel.value === String(wanted) && (original
            || App._PINNED_DETAILS.includes(parent.detail_type) || parent.detail_type === picker.value);
        const status = $('#account-parent-status');
        if (parent && !fits) {
            sel.value = '';
            status.textContent = `No longer under ${parent.name}, which cannot take this type: saved, it is a top-level account.`;
        } else {
            status.textContent = '';
        }
    },

    // Import a chart of accounts from a file (#139 / #161). Dry run first:
    // the server answers with the plan and writes nothing; the second post,
    // with the same file and the same options, applies exactly that plan.
    showChartImport() {
        openModal('Import Chart of Accounts', `
            <form onsubmit="App.previewChartImport(event)">
                <p class="hint" style="margin-bottom:10px;">
                    A CSV in the columns the export writes (Number, Name, Type, optional Parent and
                    Description), any spreadsheet with those headers, or hledger's account list
                    (<code>hledger accounts</code>, <code>accounts --types</code>, or
                    <code>balance -O csv</code>). Accounts you already have are matched by number or
                    name and renamed to the file's names; the control accounts the software posts to by
                    number are kept and renamed, never duplicated.
                    <a href="/static/downloads/chart-of-accounts-template.csv" download>Download a template</a>
                    with the columns and a few example rows.
                </p>
                <div class="form-group"><label>File</label>
                    <input type="file" name="file" accept=".csv,.txt,.journal" required></div>
                <div class="form-group">
                    <label style="display:flex; gap:8px; align-items:flex-start; font-weight:normal;">
                        <input type="checkbox" name="replace" style="margin-top:2px;">
                        <span>Replace the seeded chart: deactivate every account the file does not name
                        that has never been used. Control accounts and accounts with history stay.</span>
                    </label>
                </div>
                <div id="chart-import-preview"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Preview</button>
                    <button type="button" class="btn btn-primary" id="chart-import-apply" hidden
                        onclick="App.applyChartImport()">Import</button>
                </div>
            </form>`);
    },

    async _postChartImport(form, dryRun) {
        const fd = new FormData();
        fd.append('file', form.file.files[0]);
        const replace = form.replace.checked ? 1 : 0;
        const resp = await fetch(`/api/csv/import/accounts?dry_run=${dryRun ? 1 : 0}&replace=${replace}`,
            { method: 'POST', body: fd, headers: { 'X-Slowbooks-Desktop': '1' } });
        if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
        return resp.json();
    },

    async previewChartImport(e) {
        e.preventDefault();
        const form = e.target;
        App._chartImportForm = form;
        const box = $('#chart-import-preview');
        box.innerHTML = '<p class="hint">Reading the file…</p>';
        try {
            const plan = await App._postChartImport(form, true);
            // each row as the import will write it (#461): the detail type
            // it gets and the parent it sits under, then the change notes
            const label = { create: 'Create', update: 'Update', skip: 'Skip', deactivate: 'Deactivate', keep: 'Keep', error: 'Error' };
            const rows = plan.rows.map(r => `<tr>
                <td>${label[r.action] || r.action}</td>
                <td style="font-family:var(--font-mono);">${escapeHtml(r.number || '')}</td>
                <td>${escapeHtml(r.name)}</td>
                <td>${escapeHtml(r.type || '')}</td>
                <td>${escapeHtml(r.detail_type_label || '')}</td>
                <td>${escapeHtml([r.parent_number, r.parent_name].filter(Boolean).join(' '))}</td>
                <td style="font-size:11px; color:var(--text-muted);">${escapeHtml([...(r.changes || []), r.note].filter(Boolean).join('; '))}</td>
            </tr>`).join('');
            const errs = plan.errors.map(x => `<li>${escapeHtml(x)}</li>`).join('');
            const writes = plan.created + plan.updated + plan.deactivated;
            box.innerHTML = `
                <p style="margin:8px 0;"><strong>${plan.created} to create, ${plan.updated} to update,
                ${plan.skipped} already there${plan.replace ? `, ${plan.deactivated} to deactivate, ${plan.kept} kept` : ''}.</strong>
                Nothing has been written yet.</p>
                ${errs ? `<ul style="color:var(--danger); font-size:11px; margin:0 0 8px 16px;">${errs}</ul>` : ''}
                <div class="table-container" style="max-height:320px; overflow:auto;"><table>
                    <thead><tr><th scope="col">Action</th><th scope="col">Number</th><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Detail type</th><th scope="col">Parent</th><th scope="col">Notes</th></tr></thead>
                    <tbody>${rows}</tbody></table></div>`;
            const apply = $('#chart-import-apply');
            apply.hidden = writes === 0;
            apply.textContent = `Import ${writes} change${writes === 1 ? '' : 's'}`;
        } catch (err) {
            box.innerHTML = `<p style="color:var(--danger);">${escapeHtml(err.message)}</p>`;
            $('#chart-import-apply').hidden = true;
        }
    },

    async applyChartImport() {
        const form = App._chartImportForm;
        if (!form) return;
        try {
            const done = await App._postChartImport(form, false);
            closeModal();
            toast(`Chart imported: ${done.created} created, ${done.updated} updated${done.replace ? `, ${done.deactivated} deactivated` : ''}`);
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },

    async setAccountActive(id, active) {
        try {
            await API.put(`/accounts/${id}`, { is_active: active });
            toast(active ? 'Account reactivated' : 'Account deactivated — it is hidden from new entries');
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Only the id crosses into the attribute. It used to carry the name as
    // well, via JSON.stringify inside a double-quoted onclick — so the JSON's
    // own first quote closed the attribute, the handler was the fragment
    // `App.deleteAccount(5, `, and clicking raised a SyntaxError. Silently:
    // no request, no toast, no dialog, on EVERY row, because the break is in
    // the quoting rather than in any particular name.
    //
    // Found at the GUI by both QA agents on the 2.12.0 gate. @skytech checked
    // launcher.log and established that this application had never issued a
    // single DELETE /api/accounts/* — the button was not being refused, it
    // never asked. Nothing automated caught it: the endpoint is correct and
    // well covered, and no test rendered the row and clicked it.
    //
    // The name is looked up here instead. An attribute that carries only
    // numbers cannot be broken by punctuation in somebody's data.
    async deleteAccount(id) {
        let name = `account ${id}`;
        try {
            name = (await API.get(`/accounts/${id}`)).name || name;
        } catch (err) { /* fall back to the id in the prompt */ }
        // Deleting is for an account that was never used. Anything with
        // history, or anything the books resolve by number, is refused by
        // the server with a reason — deactivating is the answer there.
        if (!confirm(`Delete "${name}"? This only works if nothing has ever posted to it. If it has history, deactivate it instead.`)) return;
        try {
            await API.del(`/accounts/${id}`);
            toast('Account deleted');
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveAccount(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        // The picker's choice sets the base type and the bank kind with it
        // (#390); a locked control account sends neither.
        if (data.detail_type) {
            const sel = e.target.detail_type;
            data.account_type = sel.selectedOptions[0].dataset.accountType;
            // bank_kind only when the choice moves into or out of Bank /
            // Credit Card: an untouched field must never clear a register's
            // kind (the server refuses that for a register in use anyway)
            const kinds = ['bank', 'credit_card'];
            const was = sel.dataset.was || '';
            if (kinds.includes(data.detail_type)) {
                if (data.detail_type !== was) data.bank_kind = data.detail_type;
            } else if (kinds.includes(was)) {
                data.bank_kind = null;
            }
        }
        // the parent goes only when it moves: an untouched one must not set
        // off the sub-account checks on a rename (#416)
        const parentSel = e.target.parent_id;
        if (parentSel) {
            if (parentSel.value === (parentSel.dataset.was || '')) delete data.parent_id;
            else data.parent_id = parentSel.value ? Number(parentSel.value) : null;
        }
        const box = $('#account-form-error');
        if (box) box.textContent = '';
        try {
            if (id) { await API.put(`/accounts/${id}`, data); toast('Account updated'); }
            else { await API.post('/accounts', data); toast('Account created'); }
            closeModal();
            App.navigate('#/accounts');
        } catch (err) {
            // the server's sentence, in the form beside what it is about
            const msg = String(err.message || err);
            if (box) box.textContent = msg.charAt(0).toUpperCase() + msg.slice(1);
            else toast(msg, 'error');
        }
    },

    // Feature 4: Unified Global Search
    _searchTimeout: null,
    // The hits take the keyboard: ArrowDown from the search box lands on
    // the first, the arrows walk them, Enter or Space opens one, Escape
    // goes back to the box. A search is the only way from anywhere to a
    // class page or an account register, so it cannot be mouse-only.
    searchFocusFirst() {
        const first = document.querySelector('#search-results:not(.hidden) .search-item[tabindex]');
        return focusFor(first, true);  // ArrowDown from the box: the keyboard's
    },
    searchItemKey(e) {
        const items = $$('#search-results .search-item[tabindex]');
        const i = items.indexOf(e.currentTarget);
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
        else if (e.key === 'ArrowDown' && items[i + 1]) { e.preventDefault(); focusFor(items[i + 1], true); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); focusFor(i > 0 ? items[i - 1] : $('#global-search'), true); }
        else if (e.key === 'Escape') { e.preventDefault(); closeSearchDropdown(); $('#global-search')?.focus(); }
    },
    async globalSearch(query) {
        const dropdown = $('#search-results');
        if (!dropdown) return;
        clearTimeout(App._searchTimeout);
        if (!query || query.length < 2) { dropdown.classList.add('hidden'); return; }
        App._searchTimeout = setTimeout(async () => {
            try {
                const results = await API.get(`/search?q=${encodeURIComponent(query)}`);
                let html = '';
                // A document reads "number · who · amount", so a search for an
                // amount (612.30) shows which document matched.
                const doc = (num, who, amt) => [num, who, formatCurrency(amt)].filter(Boolean).join(' · ');
                const sections = [
                    { key: 'customers', label: T('Customers'), onClick: (item) => `App.navigate('#/customers');closeSearchDropdown();` },
                    { key: 'vendors', label: 'Vendors', onClick: (item) => `App.navigate('#/vendors');closeSearchDropdown();` },
                    { key: 'items', label: 'Items', onClick: (item) => `App.navigate('#/items');closeSearchDropdown();` },
                    // an account opens its register (#240): the bank register for a
                    // bank or card account, the drill-down for the rest
                    { key: 'accounts', label: 'Accounts', onClick: (item) => `App.openAccountRegister(${Number(item.id)}, ${item.bank_kind ? 1 : 0});closeSearchDropdown();`,
                      text: (a) => `${a.account_number || ''} ${a.name}${a.is_active === false ? ' (inactive)' : ''}`.trim() },
                    // a class opens its own page (#234); only the id crosses into the attribute
                    { key: 'classes', label: T('Classes'), onClick: (item) => `App.navigate('#/classes/${Number(item.id)}');closeSearchDropdown();`,
                      text: (c) => c.is_archived ? `${c.name} (archived)` : c.name },
                    { key: 'invoices', label: T('Invoices'), onClick: (item) => `InvoicesPage.view(${item.id});closeSearchDropdown();`,
                      text: (i) => doc(i.invoice_number, i.customer_name, i.total) },
                    { key: 'sales_receipts', label: T('Sales Receipts'), onClick: (item) => `SalesReceiptsPage.view(${item.id});closeSearchDropdown();`,
                      text: (i) => doc(i.invoice_number, i.customer_name, i.total) },
                    { key: 'estimates', label: 'Estimates', onClick: (item) => `App.navigate('#/estimates');closeSearchDropdown();`,
                      text: (i) => doc(i.estimate_number, i.customer_name, i.total) },
                    { key: 'credit_memos', label: 'Credit Memos', onClick: (item) => `App.navigate('#/credit-memos');closeSearchDropdown();`,
                      text: (i) => doc(i.memo_number, i.customer_name, i.total) },
                    { key: 'bills', label: 'Bills', onClick: (item) => `BillsPage.view(${item.id});closeSearchDropdown();`,
                      text: (i) => doc(i.bill_number, i.vendor_name, i.total) },
                    { key: 'payments', label: 'Payments', onClick: (item) => `PaymentsPage.view(${item.id});closeSearchDropdown();`,
                      text: (i) => doc(formatDate(i.date), i.customer_name, i.amount) },
                ];
                for (const sec of sections) {
                    const items = results[sec.key];
                    if (items && items.length > 0) {
                        html += `<div class="search-section">${sec.label}</div>`;
                        items.forEach(item => {
                            const label = sec.text ? sec.text(item) : (item.display || item.name || item.invoice_number || `#${item.id}`);
                            html += `<div class="search-item" tabindex="0" onclick="${sec.onClick(item)}" onkeydown="App.searchItemKey(event)">${escapeHtml(label)}</div>`;
                        });
                    }
                }
                if (!html) html = `<div class="search-item" style="color:var(--text-muted);">No results</div>`;
                dropdown.innerHTML = html;
                dropdown.classList.remove('hidden');
            } catch (e) {
                // Fallback to old search if unified endpoint not available
                dropdown.classList.add('hidden');
            }
        }, 300);
    },

    // CSV Import/Export page — Feature 14
    async renderCSV() {
        return `
            <div class="page-header">
                <h2>CSV Import / Export</h2>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:24px;">
                <div class="settings-section">
                    <h3>Export</h3>
                    <p style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">Download data as CSV files.</p>
                    <div style="display:flex; flex-direction:column; gap:8px;">
                        <a href="/api/csv/export/customers" class="btn btn-secondary" download>Export ${T('Customers')}</a>
                        <a href="/api/csv/export/vendors" class="btn btn-secondary" download>Export Vendors</a>
                        <a href="/api/csv/export/items" class="btn btn-secondary" download>Export Items</a>
                        <a href="/api/csv/export/invoices" class="btn btn-secondary" download>Export ${T('Invoices')}</a>
                        <a href="/api/csv/export/bills" class="btn btn-secondary" download>Export Bills</a>
                        <a href="/api/csv/export/sales-receipts" class="btn btn-secondary" download>Export ${T('Sales Receipts')}</a>
                        <a href="/api/csv/export/deposits" class="btn btn-secondary" download>Export Deposits</a>
                        <a href="/api/csv/export/classes" class="btn btn-secondary" download>Export ${T('Classes')}</a>
                        <a href="/api/csv/export/jobs" class="btn btn-secondary" download>Export ${T('Jobs')}</a>
                        <a href="/api/csv/export/accounts" class="btn btn-secondary" download>Export Chart of Accounts</a>
                    </div>
                </div>
                <div class="settings-section" data-write>
                    <h3>Import</h3>
                    <p style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">Upload CSV files to import data.</p>
                    <form id="csv-import-form" onsubmit="App.importCSV(event)">
                        <div class="form-group"><label>Entity Type</label>
                            <select name="entity_type" id="csv-entity">
                                <option value="customers">${T('Customers')}</option>
                                <option value="vendors">Vendors</option>
                                <option value="items">Items</option>
                                <option value="accounts">Chart of Accounts</option>
                            </select></div>
                        <div class="form-group"><label>CSV File</label>
                            <input type="file" name="file" accept=".csv" required></div>
                        <button type="submit" class="btn btn-primary">Import</button>
                    </form>
                    <div id="csv-import-results" style="margin-top:12px;"></div>
                </div>
            </div>`;
    },

    async importCSV(e) {
        e.preventDefault();
        const form = e.target;
        const entity = form.entity_type.value;
        const formData = new FormData();
        formData.append('file', form.file.files[0]);
        try {
            // The chart import is a dry run by default; this page applies directly.
            const query = entity === 'accounts' ? '?dry_run=0' : '';
            const resp = await fetch(`/api/csv/import/${entity}${query}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
            const data = await resp.json();
            const n = data.created ?? data.imported ?? 0;
            let html = `<div style="color:var(--text-success); font-size:11px;">Imported ${n} ${entity === 'accounts' ? 'accounts' : entity}${data.updated ? `, updated ${data.updated}` : ''}${data.skipped ? `, ${data.skipped} already there` : ''}.</div>`;
            if (data.errors && data.errors.length > 0) {
                html += `<div style="color:var(--danger); font-size:11px; margin-top:6px;">Errors:<br>${data.errors.map(e => escapeHtml(e)).join('<br>')}</div>`;
            }
            $('#csv-import-results').innerHTML = html;
        } catch (err) {
            $('#csv-import-results').innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
        }
    },

    // Quick Entry mode — batch invoice entry for paper invoice backlog
    async renderQuickEntry() {
        const [customers, items] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
        ]);
        App._qeCustomers = customers;
        App._qeItems = items;
        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        return `
            <div class="page-header">
                <h2>Quick Entry Mode</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Batch invoice entry — for entering paper invoices quickly
                </div>
            </div>
            <div class="quick-entry-info" style="background:var(--primary-light); padding:8px 12px; margin-bottom:12px; border:1px solid var(--qb-gold); font-size:11px;">
                Enter invoice details and press <strong>Save & Next</strong> (or Ctrl+Enter) to save and immediately start a new invoice.
            </div>
            <form id="qe-form" onsubmit="App.saveQuickEntry(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="qe-customer" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" id="qe-date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="qe-terms">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option ${t==='Net 30'?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>PO #</label>
                        <input name="po_number" id="qe-po"></div>
                </div>
                <h3 style="margin:12px 0 8px; font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Description</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="qe-lines">
                        <tr data-qeline="0">
                            <td><select class="line-item" onchange="App.qeItemSelected(0)">${ItemQuickAdd.options(items)}</select></td>
                            <td><input class="line-desc" value=""></td>
                            <td><input class="line-qty" type="number" step="0.01" value="1" oninput="App.qeRecalc()"></td>
                            <td><input class="line-rate" type="number" step="0.01" value="0" oninput="App.qeRecalc()"></td>
                            <td class="col-amount line-amount">$0.00</td>
                        </tr>
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="App.qeAddLine()">+ Add Line</button>
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div id="qe-total" style="font-size:16px; font-weight:700; color:var(--qb-navy);">Total: $0.00</div>
                    <div class="form-actions" style="margin:0;">
                        <button type="submit" class="btn btn-primary" data-write>Save & Next (Ctrl+Enter)</button>
                    </div>
                </div>
            </form>
            <div id="qe-log" style="margin-top:16px;"></div>`;
    },

    _qeLineCount: 1,
    qeAddLine() {
        const idx = App._qeLineCount++;
        $('#qe-lines').insertAdjacentHTML('beforeend', `
            <tr data-qeline="${idx}">
                <td><select class="line-item" onchange="App.qeItemSelected(${idx})">${ItemQuickAdd.options(App._qeItems)}</select></td>
                <td><input class="line-desc" value=""></td>
                <td><input class="line-qty" type="number" step="0.01" value="1" oninput="App.qeRecalc()"></td>
                <td><input class="line-rate" type="number" step="0.01" value="0" oninput="App.qeRecalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
            </tr>`);
    },

    qeItemSelected(idx) {
        const row = $(`[data-qeline="${idx}"]`);
        if (ItemQuickAdd.selected(row.querySelector('.line-item'), App._qeItems, () => App.qeItemSelected(idx))) return;
        const itemId = row.querySelector('.line-item').value;
        const item = App._qeItems.find(i => i.id == itemId);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.rate;
            App.qeRecalc();
        }
    },

    qeRecalc() {
        let total = 0;
        $$('#qe-lines tr').forEach(row => {
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            const amt = qty * rate;
            total += amt;
            const cell = row.querySelector('.line-amount');
            if (cell) cell.textContent = formatCurrency(amt);
        });
        const el = $('#qe-total');
        if (el) el.textContent = `Total: ${formatCurrency(total)}`;
    },

    async saveQuickEntry(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#qe-lines tr').forEach((row, i) => {
            const item_id = ItemQuickAdd.itemId(row.querySelector('.line-item'));
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 1;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            if (rate > 0 || row.querySelector('.line-desc')?.value) {
                lines.push({
                    item_id,
                    description: row.querySelector('.line-desc')?.value || '',
                    quantity: qty, rate: rate, line_order: i,
                });
            }
        });
        if (lines.length === 0) { toast('Add at least one line item', 'error'); return; }
        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            terms: form.terms.value,
            po_number: form.po_number.value || null,
            tax_rate: 0,
            notes: null,
            lines,
        };
        try {
            const inv = await API.post('/invoices', data);
            const log = $('#qe-log');
            log.insertAdjacentHTML('afterbegin',
                `<div style="padding:4px 0; font-size:11px; border-bottom:1px solid var(--gray-200);">
                    <strong>#${escapeHtml(inv.invoice_number)}</strong> created — ${escapeHtml(inv.customer_name || '')} — ${formatCurrency(inv.total)}
                </div>`);
            toast(`${T('Invoice')} #${inv.invoice_number} created`);
            // Reset form for next entry
            form.po_number.value = '';
            $('#qe-lines').innerHTML = `
                <tr data-qeline="0">
                    <td><select class="line-item" onchange="App.qeItemSelected(0)">${ItemQuickAdd.options(App._qeItems)}</select></td>
                    <td><input class="line-desc" value=""></td>
                    <td><input class="line-qty" type="number" step="0.01" value="1" oninput="App.qeRecalc()"></td>
                    <td><input class="line-rate" type="number" step="0.01" value="0" oninput="App.qeRecalc()"></td>
                    <td class="col-amount line-amount">$0.00</td>
                </tr>`;
            App._qeLineCount = 1;
            App.qeRecalc();
            form.customer_id.focus();
        } catch (err) { toast(err.message, 'error'); }
    },

    settings: {},   // one cached copy of /api/settings for the shell (company name, company type)

    // Load company settings: the status-bar name, and the vocabulary
    // (Terms) that every page renders with. Never rejects — pre-login this
    // 401s and auth.js reloads the page after login, same as before.
    async loadCompanySettings() {
        try {
            const s = await API.get('/settings');
            Terms.init(s);
            App.showCompany(s);
        } catch (e) { Terms.init(null); /* business words until signed in */ }
    },

    // The shell's copy of the settings, and the company's name where the
    // shell shows it. Settings calls this after a save, so a rename shows at
    // once; it used to wait for the next start, and the Restore dialog
    // named the company by its old name meanwhile (2.18.0 gate, skytech N4).
    // The sidebar without the items the company hid (#334): a nav-link's
    // page, or a whole section ("section:Payroll & HR"), as the setting
    // lists them. Hidden pages still open by address; a section whose
    // links are all hidden goes with them. Run at start and after a save.
    applyHiddenMenu(s) {
        let hidden = [];
        try { hidden = JSON.parse((s && s.hidden_menu) || '[]'); } catch (e) { hidden = []; }
        if (!Array.isArray(hidden)) hidden = [];
        const set = new Set(hidden.map(String));
        let sectionHidden = false;
        $$('#sidebar li').forEach(li => {
            if (li.classList.contains('nav-section')) {
                sectionHidden = set.has(`section:${li.textContent.trim()}`);
                li.dataset.hiddenByMenu = sectionHidden ? '1' : '';
                return;
            }
            const link = li.querySelector('.nav-link[data-page]');
            if (!link) return;
            li.dataset.hiddenByMenu = (sectionHidden || set.has(link.dataset.page)) ? '1' : '';
        });
        App.syncSidebarHidden();
    },

    // Three things hide a sidebar item, each with its own mark on the <li>,
    // so the order they run in does not matter (#457): the menu setting
    // (data-hidden-by-menu), the company's kind (data-hidden-kind: the
    // nonprofit pages on a business company, set in index.html and by
    // applyTerminology) and the sign-in's role (data-hidden-role: the admin
    // pages for a clerk, set by initSystemInfo). An item shows when none is
    // set; a section shows unless the menu hid it or every link under it is
    // hidden. Settings → Sidebar offers what the kind and the role leave.
    syncSidebarHidden() {
        const items = [...$$('#sidebar li')];
        items.forEach(li => {
            if (li.classList.contains('nav-section') || !li.querySelector('.nav-link[data-page]')) return;
            const d = li.dataset;
            li.hidden = !!(d.hiddenByMenu || d.hiddenKind || d.hiddenRole);
        });
        items.forEach((li, i) => {
            if (!li.classList.contains('nav-section')) return;
            let j = i + 1, any = false;
            while (j < items.length && !items[j].classList.contains('nav-section')) {
                if (!items[j].hidden) any = true;
                j++;
            }
            li.hidden = !!li.dataset.hiddenByMenu || !any;
        });
    },

    // ---- Several companies on one server (#358) ----------------------------
    // A Server Edition host serving more than one company file: the topbar
    // offers the others. One whose sign-in this one's password opened is a
    // click away, with the role that company gives; the rest ask for their
    // own password, this username chosen.
    showCompanySwitch(companies) {
        const old = document.getElementById('topbar-company-switch');
        if (old) old.remove();
        const brand = $('#topbar-company');
        if (!brand) return;
        const serves = Array.isArray(companies) && companies.length > 1;
        // the switch names the company itself
        brand.classList.toggle('hidden', serves);
        if (!serves) return;
        const select = document.createElement('select');
        select.id = 'topbar-company-switch';
        select.className = 'tb-company-switch';
        select.setAttribute('aria-label', 'Switch company');
        select.title = 'Switch company';
        companies.forEach(c => {
            const o = document.createElement('option');
            o.value = c.file;
            o.textContent = c.current || c.opens ? c.name : `${c.name} (sign in)`;
            o.selected = !!c.current;
            select.appendChild(o);
        });
        select.addEventListener('change', () => App.switchServedCompany(select.value));
        brand.insertAdjacentElement('afterend', select);
    },

    async switchServedCompany(file) {
        try {
            const r = await API.post('/auth/company', { file });
            if (!r.authenticated && window.SlowbooksAuth) {
                window.SlowbooksAuth.rememberUsername(r.username || App.username || '');
            }
        } catch (err) {
            toast(err.message, 'error');
            return;
        }
        // another company's books: start from its Home page
        location.hash = '#/';
        location.reload();
    },

    showCompany(s) {
        App.settings = s || {};
        App.applyHiddenMenu(App.settings);
        const name = App.settings.company_name;
        if (!name || name === 'My Company') return;
        const companyEl = $('#status-company');
        if (companyEl) companyEl.textContent = `Company: ${name}`;
        // the window / tab title and the topbar brand say whose books these are
        document.title = `${name} — Slowbooks Pro 2026`;
        const brand = $('#topbar-company');
        if (brand) brand.textContent = name;
    },

    // Rewrites the static shell into the company's words. index.html is
    // served raw, so the sidebar and toolbar arrive as business-worded
    // HTML; this runs once at boot, before the first page renders.
    // Sidebar entries the server serves to admins only (app.main RBAC).
    // Migrate Data too: its dry run and its import are refused to every
    // other role, so a bookkeeper had a page on which nothing worked.
    ADMIN_ONLY_PAGES: ['employees', 'payroll', 'hr-onboarding', 'hr-benefits', 'hr-deductions', 'hr-tax-forms', 'users', 'migrate'],
    // Pages the server refuses a read-only sign-in, reads included.
    NOT_FOR_READONLY_PAGES: ['audit'],

    applyTerminology() {
        for (const r of Object.values(App.routes)) r.label = T(r.label);
        $$('#sidebar .nav-section').forEach(el => { el.textContent = T(el.textContent.trim()); });
        $$('#sidebar .nav-link').forEach(a => {
            const t = [...a.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
            if (t) t.textContent = ' ' + T(t.textContent.trim());
        });
        $$('#topbar .tb-btn[data-action]').forEach(b => { b.textContent = T(b.textContent.trim()); });
        const search = $('#global-search');
        if (search) search.placeholder = Terms.text(search.placeholder);
        const np = Terms.isNonprofit();
        // a sidebar item keeps its own mark for this (see syncSidebarHidden)
        const kind = (el, hide) => {
            if (el.matches('#sidebar li')) el.dataset.hiddenKind = hide ? '1' : '';
            else el.hidden = hide;
        };
        $$('[data-nonprofit]').forEach(el => kind(el, !np));
        $$('[data-business-only]').forEach(el => kind(el, np));
        App.syncSidebarHidden();
    },

    init() {
        window.addEventListener('hashchange', () => App.navigate(location.hash));
        // Back or Forward: the dialog over the page left closes as the
        // history moves, before the page gone to renders (App.navigate
        // closes it too; this is sooner, so nothing of the view left is
        // still showing while the next one loads).
        window.addEventListener('popstate', () => {
            // the way back to the entry a declined move left (App.stayPut):
            // what is open stays open
            if (App._stay) { App.syncBack(); return; }
            const overlay = $('#modal-overlay');
            if (overlay && !overlay.classList.contains('hidden')) closeModal({ keepAddress: true });
            App.syncBack();
        });
        // The session's first entry: nothing of the app's behind it (a
        // reload of a later entry keeps that entry's state, and its Back)
        if (!history.state) history.replaceState({ from: null, n: 0 }, '', location.hash || '#/');
        App.labelBack();
        App.addressShown();

        // A control the keyboard moves to is kept in sight: in the P&L
        // grids, brought in whole and clear of the frozen header row and
        // Account column (ReportsPage._gridReveal; the column's width and
        // the row's height measured again when the window changes); on
        // Settings, scrolled clear of the save bar (SettingsPage._clearSaveBar)
        document.addEventListener('keydown', (e) => {
            if (!App._MODIFIER_KEYS.includes(e.key)) App._input = 'keyboard';
        }, true);
        document.addEventListener('pointerdown', () => { App._input = 'pointer'; }, true);
        document.addEventListener('focusin', (e) => {
            ReportsPage._gridReveal(e);
            App.revealInTable(e);
            SettingsPage._clearSaveBar(e);
        });
        // a scroll box with no control in it, marked a region whenever the
        // page or a dialog changes, or the window (App.markScrollRegions)
        let marking = false;
        const mark = () => {
            if (marking) return;
            marking = true;
            requestAnimationFrame(() => { marking = false; App.markScrollRegions(); });
        };
        if (typeof MutationObserver === 'function') new MutationObserver(mark).observe(document.body, { childList: true, subtree: true });
        window.addEventListener('resize', mark);
        window.addEventListener('resize', () => ReportsPage._gridFit());

        // Load saved theme
        App.loadTheme();

        // Keyboard shortcuts. The Alt letters go by the key's place
        // (e.code: KeyD is the D key on any layout) with Alt alone held
        // (Ctrl+Alt is AltGr on some layouts, Shift another shortcut), not
        // by the character typed: on a Mac, Option-D types "∂", Option-H
        // "˙" and Option-Q "œ", and e.key carried those, so none of the
        // documented shortcuts fired (macOS gate NEW-41). Inside a field —
        // an input, a textarea, a select, an editable region — no Alt
        // letter is a shortcut, on any platform: a Mac's Option is typing
        // there ("∂", or the dead key of Option-N's tilde), and letting
        // the plain letter through for Windows and Linux meant Alt+N over
        // a half-typed bill opened New Invoice in its place and the bill
        // was gone (Windows gate W-6). Ctrl+K (⌘K) finds from anywhere,
        // the search box included. Alt+N, P, Q, B, E and H leave what is
        // open: over a form being filled in (App.editingDialog, as Back
        // is) they do nothing from any of its controls either — one Tab
        // away from the field, on Save or the ×, they opened another form
        // in its place and the typed one was gone (W-6 review); over an
        // addressed dialog (a report, a document, a page) and from the
        // page they fire. Alt+D, the theme, fires anywhere but a field.
        document.addEventListener('keydown', (e) => {
            const alt = e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey;
            const editing = !!(e.target && e.target.closest && e.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
            const letter = (code) => alt && !editing && e.code === code;
            const leaves = (code) => letter(code) && !App.editingDialog();
            // Back: ⌘[ on a Mac, Alt+← elsewhere, while an app page is
            // behind and no form is open over the page (the Mac app's
            // window has no Back of its own, NEW-30)
            if (App.isBackKey(e) && App.backAllowed()) { e.preventDefault(); App.goBack(); return; }
            // Ctrl+Enter: submit quick entry form
            if (e.ctrlKey && e.key === 'Enter') {
                const qeForm = $('#qe-form');
                if (qeForm) { qeForm.requestSubmit(); e.preventDefault(); }
            }
            // Ctrl+S: save current modal form (Feature 13)
            if (e.ctrlKey && e.key === 's') {
                const modalForm = document.querySelector('#modal-body form');
                if (modalForm) { modalForm.requestSubmit(); e.preventDefault(); }
            }
            // Alt+N / Alt+P / Alt+Q / Alt+B / Alt+E start new entries: a
            // read-only sign-in is told why nothing opens, rather than
            // handed a blank locked form
            const entry = leaves('KeyN') || leaves('KeyP') || leaves('KeyQ') || leaves('KeyB') || leaves('KeyE');
            if (entry && App.isReadOnly()) {
                toast(App.READ_ONLY_MESSAGE, 'info'); e.preventDefault(); return;
            }
            // Alt+N: new invoice
            if (leaves('KeyN')) { InvoicesPage.showForm(); e.preventDefault(); }
            // Alt+P: receive payment
            if (leaves('KeyP')) { PaymentsPage.showForm(); e.preventDefault(); }
            // Alt+Q: quick entry
            if (leaves('KeyQ')) { App.navigate('#/quick-entry'); e.preventDefault(); }
            // Alt+B: enter a bill; Alt+E: enter an expense (#264). By the
            // key's place, as the others: Option-B types "∫" and Option-E
            // is a dead key on a Mac, and e.code is KeyB / KeyE either way
            if (leaves('KeyB')) { BillsPage.showForm(); e.preventDefault(); }
            if (leaves('KeyE')) { ExpensesPage.showForm(); e.preventDefault(); }
            // Alt+H: home/dashboard
            if (leaves('KeyH')) { App.navigate('#/'); e.preventDefault(); }
            // Alt+D: toggle dark mode (Feature 12)
            if (letter('KeyD')) { App.toggleTheme(); e.preventDefault(); }
            // Escape: close modal (not the Escape that closes a date
            // field's calendar: utils.js escapeLeavesPicker, NEW-33)
            if (e.key === 'Escape' && !escapeLeavesPicker(e)) { closeModal(); }
            // Ctrl+K (⌘K on a Mac, which did nothing: NEW-41) or /: focus
            // search (when not in an input). Not while a dialog is open: it
            // is modal, the search lies under its overlay, and its hits
            // opened behind the dialog where they could be neither read nor
            // clicked (macOS gate NEW-45). Close the dialog, then search.
            if (((e.ctrlKey || e.metaKey) && !e.altKey && e.key === 'k') || (e.key === '/' && !e.target.closest('input,textarea,select'))) {
                const overlay = $('#modal-overlay');
                if (overlay && !overlay.classList.contains('hidden')) { if (e.key === 'k') e.preventDefault(); return; }
                const search = $('#global-search');
                if (search) { search.focus(); e.preventDefault(); }
            }
        });

        // Close search dropdown on click outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#global-search') && !e.target.closest('#search-results')) {
                const dd = $('#search-results');
                if (dd) dd.classList.add('hidden');
            }
        });

        // Start clock — ticks once a second
        App.updateClock();
        setInterval(App.updateClock, 60000);

        // Settings first: the vocabulary and the nonprofit nav items must
        // be in place before the first page paints (no flash of "Customers"
        // on a donor's screen). loadCompanySettings never rejects. The
        // sign-in's state too (the version, the role, a one-time password
        // to replace): the first page waited for neither, so a sign-in that
        // must set its password got the dashboard's 403 painted over the
        // form (2.24.0 round 2, NEW-149 on the Mac).
        Promise.all([App.loadCompanySettings(), App.initSystemInfo()]).then(() => {
            App.applyTerminology();
            App.renderOpenTabs();  // the session's open screens (#336)
            App.navigate(location.hash || '#/');
        });
    },

    /**
     * Footer version + update check. Raw fetch (not the API wrapper) on
     * purpose: before first login these return 401, and the wrapper's 401
     * handler would pop the auth prompt — auth.js already owns that, and
     * it reloads the page after login so this runs again authenticated.
     * The whole thing is best-effort; failures leave the footer as-is.
     */
    async initSystemInfo() {
        try {
            let res = await fetch('/api/system', { credentials: 'same-origin' });
            // A refused /api/system (a sign-in that must set its password
            // first, before 2.24.0 round 2) used to end here, before the
            // sign-in's own state was read, so the password page never
            // drew (W-18 / NEW-149): the version is skipped, the rest goes on.
            const info = res.ok ? await res.json() : {};
            const label = info.version
                ? (info.server_mode ? `v${info.version} · Server` : `v${info.version}`)
                : null;
            if (label) {
                // The version now shows at the top of the sidebar as well as
                // in the footer — knowing which version you are running is
                // half of "is there a newer one".
                [$('#app-version'), $('#app-version-footer')].forEach(el => {
                    if (el) el.textContent = label;
                });
            }
            if (info.server_mode) {
                // Serving the LAN: the deployment announces itself.
                document.querySelectorAll('.sidebar-edition, .splash-subtitle')
                    .forEach(el => { el.textContent = 'Server Edition'; });
            }

            // Multi-user: always-visible identity chip in the topbar.
            const auth = await fetch('/api/auth/status', { credentials: 'same-origin' });
            if (auth.ok) {
                const a = await auth.json();
                if (a.user) App.setRole(a.user.role, a.user.username);
                if (a.user && a.user.must_change_password) {
                    // the first page (init awaits this) draws the form
                    App.mustChangePassword = true;
                }
                // the copy itself takes journal entries and nothing else:
                // the pages are locked the way the Accountant role's are
                // (W-20), and the list of open screens is this company's
                App.inCopy = !!a.accountants_copy;
                App.copyDividingDate = a.accountants_copy ? a.accountants_copy.dividing_date : '';
                if (App.inCopy) { App.setRole(App.role, App.username); }
                App._tabsOwnerChanged(a);
                App.showCompanySwitch(a.companies);
                if (a.accountants_copy && !document.getElementById('ac-banner')) {
                    // an accountant's copy says so on every page (#346)
                    const banner = document.createElement('div');
                    banner.id = 'ac-banner';
                    banner.setAttribute('role', 'status');
                    banner.style.cssText = 'background:var(--primary-light); border-bottom:1px solid var(--gray-300); padding:6px 16px; font-size:12px;';
                    banner.innerHTML = `Accountant's copy of <strong>${escapeHtml(a.accountants_copy.company_name)}</strong>: journal entries dated on or before <strong>${escapeHtml(a.accountants_copy.dividing_date)}</strong> only. <a href="#/companies">Send changes back</a> when they are in.`;
                    const main = document.getElementById('page-content');
                    if (main && main.parentElement) main.parentElement.insertBefore(banner, main);
                }
                if (a.multi_user && a.user && a.user.role !== 'admin') {
                    // HR and payroll are admin functions; the server refuses
                    // them for other roles, so do not offer the pages.
                    App.ADMIN_ONLY_PAGES.forEach(page => {
                        const link = document.querySelector(`#sidebar .nav-link[data-page="${page}"]`);
                        if (link && link.parentElement) link.parentElement.dataset.hiddenRole = '1';
                    });
                    App.syncSidebarHidden();
                }
                if (a.multi_user && a.user) {
                    const right = document.querySelector('.topbar-right');
                    if (right && !document.getElementById('user-chip')) {
                        const chip = document.createElement('span');
                        chip.id = 'user-chip';
                        chip.className = 'topbar-clock';
                        chip.textContent =
                            `${a.user.display_name || a.user.username} · ${a.user.role}`;
                        right.prepend(chip);
                    }
                }
            }
            if (!info.update_check_enabled) return;

            res = await fetch('/api/system/update-check', { credentials: 'same-origin' });
            if (!res.ok) return;
            const check = await res.json();
            if (!check.update_available || !check.download_url) return;
            // Top of the sidebar, not the footer: in the footer this was
            // only seen by someone who scrolled the whole menu, so people
            // stayed on old versions without knowing. Deliberately a quiet
            // banner rather than a dialog — visible on open, never blocking.
            const mount = $('#sidebar-update') || $('#sidebar-footer');
            if (!mount || mount.querySelector('.update-badge')) return;
            const link = document.createElement('a');
            // External URL: pywebview hands target="_blank" links that leave
            // 127.0.0.1 to the system browser (see desktop_shim.js).
            link.href = check.download_url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.className = 'update-badge';
            link.title = `You are on v${info.version || '?'} — opens the download page`;
            link.innerHTML =
                `<span class="update-badge__arrow" aria-hidden="true">&#8593;</span>`
                + `<span class="update-badge__text">Version ${escapeHtml(check.latest_version)} is available`
                + `<span class="update-badge__cta">See what changed &rarr;</span></span>`;
            mount.appendChild(link);
        } catch (e) { /* offline or pre-auth — footer stays as shipped */ }
    },
};

// Top-level `const` creates a global *lexical* binding, not a window
// property — but bootstrap.js guards its listeners with `window.App && ...`
// (theme toggle, About, search, data-nav). Without this export every one of
// those guards short-circuits and the static-shell buttons silently no-op.
window.App = App;

document.addEventListener('DOMContentLoaded', () => App.init());
