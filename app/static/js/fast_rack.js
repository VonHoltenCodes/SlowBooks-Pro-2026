/**
 * The Fast Rack (#912): the row of buttons in the top bar, between the
 * company's name and the search box, chosen and ordered by each sign-in.
 *
 * It was a fixed row of seven (Home, New Customer, Create Invoice, Receive
 * Payment, Quick Entry, Bill Quick Entry, Reports) that nobody could change.
 * Now each sign-in picks up to eight from the catalog below, every page the
 * sidebar offers and every new-document form the app opens, in its own
 * order, in Settings -> Preferences -> My Preferences -> Fast Rack (and
 * More -> Customize Fast Rack…). The shipped set is the old seven, so no
 * screen changes on upgrade.
 *
 * Kept with the sign-in on the server, the preferences store's `fast_rack`
 * key ({"items": [ids]}), so it follows the person to another computer; the
 * single-password operator shares one row, as with the other preferences.
 * A failed read shows the shipped set.
 *
 * A sign-in is offered only what it may use, and a saved id it may not use
 * is skipped where the rack is drawn: a read-only or accountant's sign-in
 * gets no new-document forms (the accountant keeps New Journal Entry) and
 * none of the pages that only enter things (the sidebar's data-write
 * pages); a sign-in other than the administrator's none of the
 * administrator's pages (App.ADMIN_ONLY_PAGES); a read-only sign-in not the
 * audit log; a business company none of the nonprofit pages.
 *
 * Every rack button is the same width and height (style.css "FAST RACK");
 * a two-word name wraps onto two lines. The bar folds the rack's buttons
 * into More, last first, when the window is short of room (topbar_fit.js).
 * The Alt letters work whether or not their button is on the rack (app.js);
 * a button that has one says so in its title and aria-keyshortcuts.
 */
const FastRack = {
    KEY: 'fast_rack',
    MAX: 8,
    // the shipped set: the bar as it was before #912
    DEFAULT: ['home', 'new_customer', 'create_invoice', 'receive_payment', 'quick_entry', 'bill_entry', 'reports'],

    // id: stable, stored; label: the button's words (in the company's
    // vocabulary at render, Terms.text); kind: nav (a page) or action (a
    // new document's form); target: the address, or the form's opener as
    // the bar's data-action has it; alt: its Alt letter; page: the
    // sidebar's data-page; write: only enters things; accountant: an
    // accountant's sign-in may use it; admin: the administrator's;
    // nonprofit: a nonprofit company's.
    CATALOG: [
        // Pages, in the sidebar's order
        { id: 'home', label: 'Home', kind: 'nav', target: '#/', page: 'dashboard', alt: 'H' },
        { id: 'customers', label: 'Customers', kind: 'nav', target: '#/customers', page: 'customers' },
        { id: 'jobs', label: 'Jobs', kind: 'nav', target: '#/jobs', page: 'jobs' },
        { id: 'invoices', label: 'Invoices', kind: 'nav', target: '#/invoices', page: 'invoices' },
        { id: 'quick_entry', label: 'Quick Entry', kind: 'nav', target: '#/quick-entry', page: 'quick-entry', alt: 'Q', write: true },
        { id: 'sales_receipts', label: 'Sales Receipts', kind: 'nav', target: '#/sales-receipts', page: 'sales-receipts' },
        { id: 'in_kind_gifts', label: 'In-Kind Gifts', kind: 'nav', target: '#/in-kind-gifts', page: 'in-kind-gifts', nonprofit: true },
        { id: 'estimates', label: 'Estimates', kind: 'nav', target: '#/estimates', page: 'estimates' },
        { id: 'payments', label: 'Payments', kind: 'nav', target: '#/payments', page: 'payments' },
        { id: 'collections', label: 'Collections', kind: 'nav', target: '#/collections', page: 'collections' },
        { id: 'recurring', label: 'Recurring Invoices', kind: 'nav', target: '#/recurring', page: 'recurring' },
        { id: 'memorized', label: 'Memorized Transactions', kind: 'nav', target: '#/memorized', page: 'memorized' },
        { id: 'credit_memos', label: 'Credit Memos', kind: 'nav', target: '#/credit-memos', page: 'credit-memos' },
        { id: 'batch_payments', label: 'Batch Payments', kind: 'nav', target: '#/batch-payments', page: 'batch-payments', write: true },
        { id: 'vendors', label: 'Vendors', kind: 'nav', target: '#/vendors', page: 'vendors' },
        { id: 'purchase_orders', label: 'Purchase Orders', kind: 'nav', target: '#/purchase-orders', page: 'purchase-orders' },
        { id: 'bills', label: 'Bills', kind: 'nav', target: '#/bills', page: 'bills' },
        { id: 'bill_entry', label: 'Bill Quick Entry', kind: 'nav', target: '#/bill-entry', page: 'bill-entry', write: true, lines: ['Bill', 'Quick Entry'] },
        { id: 'vendor_credits', label: 'Vendor Credits', kind: 'nav', target: '#/vendor-credits', page: 'vendor-credits' },
        { id: 'job_costs', label: 'Job Costs', kind: 'nav', target: '#/job-costs', page: 'job-costs' },
        { id: 'expenses', label: 'Expenses', kind: 'nav', target: '#/expenses', page: 'expenses' },
        { id: 'items', label: 'Items & Services', kind: 'nav', target: '#/items', page: 'items' },
        { id: 'employees', label: 'Employees', kind: 'nav', target: '#/employees', page: 'employees' },
        { id: 'payroll', label: 'Payroll', kind: 'nav', target: '#/payroll', page: 'payroll' },
        { id: 'hr_onboarding', label: 'Onboarding', kind: 'nav', target: '#/hr/onboarding', page: 'hr-onboarding' },
        { id: 'hr_time_entries', label: 'Time Entries', kind: 'nav', target: '#/hr/time-entries', page: 'hr-time-entries' },
        { id: 'hr_pto', label: 'Time Off', kind: 'nav', target: '#/hr/pto', page: 'hr-pto' },
        { id: 'hr_benefits', label: 'Benefits', kind: 'nav', target: '#/hr/benefits', page: 'hr-benefits' },
        { id: 'hr_deductions', label: 'Garnishments', kind: 'nav', target: '#/hr/deductions', page: 'hr-deductions' },
        { id: 'hr_tax_forms', label: 'Tax Forms', kind: 'nav', target: '#/hr/tax-forms', page: 'hr-tax-forms' },
        { id: 'reseller_permits', label: 'Reseller Permits', kind: 'nav', target: '#/reseller-permits', page: 'reseller-permits' },
        { id: 'banking', label: 'Bank Accounts', kind: 'nav', target: '#/banking', page: 'banking' },
        { id: 'bank_rules', label: 'Bank Rules', kind: 'nav', target: '#/bank-rules', page: 'bank-rules' },
        { id: 'fixed_assets', label: 'Fixed Assets', kind: 'nav', target: '#/fixed-assets', page: 'fixed-assets' },
        { id: 'opening_balances', label: 'Opening Balances', kind: 'nav', target: '#/opening-balances', page: 'opening-balances', write: true },
        { id: 'migrate', label: 'Migrate Data', kind: 'nav', target: '#/migrate', page: 'migrate', write: true },
        { id: 'deposits', label: 'Make Deposits', kind: 'nav', target: '#/deposits', page: 'deposits' },
        { id: 'cc_charges', label: 'CC Charges', kind: 'nav', target: '#/cc-charges', page: 'cc-charges' },
        { id: 'accounts', label: 'Chart of Accounts', kind: 'nav', target: '#/accounts', page: 'accounts' },
        { id: 'journal', label: 'Journal Entries', kind: 'nav', target: '#/journal', page: 'journal' },
        { id: 'classes', label: 'Classes', kind: 'nav', target: '#/classes', page: 'classes' },
        { id: 'releases', label: 'Releases from Restriction', kind: 'nav', target: '#/releases', page: 'releases', nonprofit: true },
        { id: 'functional_allocations', label: 'Functional Allocations', kind: 'nav', target: '#/functional-allocations', page: 'functional-allocations', nonprofit: true },
        { id: 'budgets', label: 'Budgets', kind: 'nav', target: '#/budgets', page: 'budgets' },
        { id: 'reports', label: 'Reports', kind: 'nav', target: '#/reports', page: 'reports' },
        { id: 'analytics', label: 'Analytics & AI', kind: 'nav', target: '#/analytics', page: 'analytics' },
        { id: 'tax', label: 'Tax Reports', kind: 'nav', target: '#/tax', page: 'tax' },
        { id: 'iif', label: 'Interoperability', kind: 'nav', target: '#/iif', page: 'iif' },
        { id: 'csv', label: 'CSV Import/Export', kind: 'nav', target: '#/csv', page: 'csv' },
        { id: 'settings', label: 'Settings', kind: 'nav', target: '#/settings', page: 'settings' },
        { id: 'audit', label: 'Audit Log', kind: 'nav', target: '#/audit', page: 'audit' },
        { id: 'companies', label: 'Companies', kind: 'nav', target: '#/companies', page: 'companies' },
        // New documents: each opens its blank form over the page shown
        { id: 'new_customer', label: 'New Customer', kind: 'action', target: 'CustomersPage.showForm', open: () => CustomersPage.showForm(), write: true },
        { id: 'create_invoice', label: 'Create Invoice', kind: 'action', target: 'InvoicesPage.showForm', open: () => InvoicesPage.showForm(), write: true, alt: 'N' },
        { id: 'receive_payment', label: 'Receive Payment', kind: 'action', target: 'PaymentsPage.showForm', open: () => PaymentsPage.showForm(), write: true, alt: 'P' },
        { id: 'new_sales_receipt', label: 'New Sales Receipt', kind: 'action', target: 'SalesReceiptsPage.showForm', open: () => SalesReceiptsPage.showForm(), write: true },
        { id: 'new_estimate', label: 'New Estimate', kind: 'action', target: 'EstimatesPage.showForm', open: () => EstimatesPage.showForm(), write: true },
        { id: 'new_credit_memo', label: 'New Credit Memo', kind: 'action', target: 'CreditMemosPage.showForm', open: () => CreditMemosPage.showForm(), write: true },
        { id: 'new_recurring', label: 'New Recurring Invoice', kind: 'action', target: 'RecurringPage.showForm', open: () => RecurringPage.showForm(), write: true },
        { id: 'new_in_kind_gift', label: 'New In-Kind Gift', kind: 'action', target: 'InKindPage.showForm', open: () => InKindPage.showForm(), write: true, nonprofit: true },
        { id: 'new_job', label: 'New Job', kind: 'action', target: 'JobsPage.showForm', open: () => JobsPage.showForm(), write: true },
        { id: 'new_vendor', label: 'New Vendor', kind: 'action', target: 'VendorsPage.showForm', open: () => VendorsPage.showForm(), write: true },
        { id: 'enter_bill', label: 'Enter Bill', kind: 'action', target: 'BillsPage.showForm', open: () => BillsPage.showForm(), write: true, alt: 'B' },
        { id: 'enter_expense', label: 'Enter Expense', kind: 'action', target: 'ExpensesPage.showForm', open: () => ExpensesPage.showForm(), write: true, alt: 'E' },
        { id: 'new_purchase_order', label: 'New Purchase Order', kind: 'action', target: 'PurchaseOrdersPage.showForm', open: () => PurchaseOrdersPage.showForm(), write: true },
        { id: 'new_vendor_credit', label: 'New Vendor Credit', kind: 'action', target: 'VendorCreditsPage.showForm', open: () => VendorCreditsPage.showForm(), write: true },
        { id: 'new_job_cost', label: 'New Job Cost', kind: 'action', target: 'JobCostsPage.showForm', open: () => JobCostsPage.showForm(), write: true },
        { id: 'new_cc_charge', label: 'New CC Charge', kind: 'action', target: 'CCChargesPage.showForm', open: () => CCChargesPage.showForm(), write: true },
        { id: 'new_item', label: 'New Item', kind: 'action', target: 'ItemsPage.showForm', open: () => ItemsPage.showForm(), write: true },
        { id: 'new_journal_entry', label: 'New Journal Entry', kind: 'action', target: 'JournalPage.showForm', open: () => JournalPage.showForm(), write: true, accountant: true },
        { id: 'new_bank_rule', label: 'New Bank Rule', kind: 'action', target: 'BankRulesPage.showForm', open: () => BankRulesPage.showForm(), write: true },
        { id: 'new_release', label: 'New Release', kind: 'action', target: 'ReleasesPage.showForm', open: () => ReleasesPage.showForm(), write: true, nonprofit: true },
        { id: 'new_time_entry', label: 'New Time Entry', kind: 'action', target: 'TimeEntriesPage.showForm', open: () => TimeEntriesPage.showForm(), write: true },
        { id: 'new_employee', label: 'New Employee', kind: 'action', target: 'EmployeesPage.showForm', open: () => EmployeesPage.showForm(), write: true, admin: true },
        { id: 'new_reseller_permit', label: 'New Reseller Permit', kind: 'action', target: 'ResellerPermitsPage.showForm', open: () => ResellerPermitsPage.showForm(), write: true },
    ],

    // the saved list, as read (null: none saved, or not read: the shipped set)
    items: null,
    // the list being arranged in Settings, before Save
    draft: null,

    byId(id) { return FastRack.CATALOG.find(c => c.id === id) || null; },

    // Known ids, once each, at most MAX
    normalize(list) {
        const out = [];
        (Array.isArray(list) ? list : []).forEach(id => {
            if (typeof id === 'string' && FastRack.byId(id) && !out.includes(id) && out.length < FastRack.MAX) out.push(id);
        });
        return out;
    },

    // May this sign-in, in this company, use it?
    allowed(item) {
        if (!item) return false;
        const app = typeof window !== 'undefined' ? window.App : null;
        if (app) {
            if (item.write && app.isLocked() && !(item.accountant && app.isAccountant() && !app.isReadOnly())) return false;
            if (item.admin && !app.isAdmin()) return false;
            if (item.page && (app.ADMIN_ONLY_PAGES || []).includes(item.page) && !app.isAdmin()) return false;
            if (item.page && (app.NOT_FOR_READONLY_PAGES || []).includes(item.page) && app.isReadOnly()) return false;
        }
        if (item.nonprofit && !(typeof Terms !== 'undefined' && Terms.isNonprofit())) return false;
        return true;
    },

    label(item) {
        return typeof Terms !== 'undefined' && Terms.text ? Terms.text(item.label) : item.label;
    },

    // A name of more than one word on two lines, as even as the words
    // allow ("New / Customer"); one word on one. An item may say where its
    // own break goes when the even split reads wrong ("Bill / Quick Entry",
    // not "Bill Quick / Entry"), for the shipped label only: a company's
    // own word for it (Terms.text) is split by the rule.
    lines(name, item) {
        if (item && item.lines && name === item.label) return item.lines.slice();
        const words = String(name).split(/\s+/).filter(Boolean);
        if (words.length < 2) return [words.join('')];
        let best = 1, worst = Infinity;
        for (let k = 1; k < words.length; k++) {
            const longer = Math.max(words.slice(0, k).join(' ').length, words.slice(k).join(' ').length);
            if (longer < worst) { worst = longer; best = k; }
        }
        return [words.slice(0, best).join(' '), words.slice(best).join(' ')];
    },

    // The button's words, a line to a span: its text (and so its name) is
    // still the name, the lines a space apart
    fillButton(b, name, item) {
        b.textContent = '';
        FastRack.lines(name, item).forEach((line, i) => {
            if (i) b.appendChild(document.createTextNode(' '));
            const span = document.createElement('span');
            span.className = 'fr-line';
            span.textContent = line;
            b.appendChild(span);
        });
    },

    // The ids saved (or shipped), and of them what this sign-in may use
    saved() { return FastRack.items ? FastRack.items.slice() : FastRack.DEFAULT.slice(); },
    shown() { return FastRack.saved().filter(id => FastRack.allowed(FastRack.byId(id))); },

    _companyHeaders() {
        let company = null;
        try { company = localStorage.getItem('slowbooks_company'); } catch (e) { /* no storage */ }
        return company ? { 'X-Company-Id': company } : {};
    },

    // Read once at start (App.init). A raw fetch, as the other preferences
    // App.init reads: the API wrapper's 401 handler would put up the
    // sign-in. Never rejects; anything but a saved list keeps the shipped set.
    load() {
        if (typeof fetch !== 'function') return Promise.resolve();
        return fetch('/api/preferences/' + FastRack.KEY, { credentials: 'same-origin', headers: FastRack._companyHeaders() })
            .then(res => (res.ok ? res.json() : null))
            .then(data => {
                const value = data && data.value;
                FastRack.items = value && Array.isArray(value.items) ? FastRack.normalize(value.items) : null;
            })
            .catch(() => { /* the shipped set */ });
    },

    // The rack drawn from the list, in place of what the bar had
    render() {
        const rack = document.getElementById('fast-rack');
        if (!rack) return;
        const mac = window.App && App.isMac && App.isMac();
        rack.textContent = '';
        const ids = FastRack.shown();
        ids.forEach(id => {
            const item = FastRack.byId(id);
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'tb-btn fr-btn';
            b.dataset.rackId = item.id;
            if (item.kind === 'nav') b.dataset.nav = item.target;
            else b.dataset.action = item.target;
            const name = FastRack.label(item);
            FastRack.fillButton(b, name, item);
            if (item.alt) {
                b.title = `${name} (${mac ? '⌥' : 'Alt+'}${item.alt})`;
                b.setAttribute('aria-keyshortcuts', 'Alt+' + item.alt);
            }
            b.addEventListener('click', () => FastRack.go(item));
            rack.appendChild(b);
        });
        rack.classList.toggle('fr-empty', !ids.length);
        rack.dataset.rendered = '1';
        if (window.SlowbooksTopbar) window.SlowbooksTopbar.fit();
    },

    go(item) {
        if (item.kind === 'nav') {
            if (window.App && App.navigate) App.navigate(item.target);
            return;
        }
        if (!FastRack.allowed(item)) {  // never drawn so; said, not opened
            if (typeof toast === 'function' && window.App) toast(App.lockedMessage(), 'info');
            return;
        }
        item.open();
    },

    // More -> Customize Fast Rack…: Settings, at the Fast Rack's block
    customize() {
        const onSettings = (location.hash || '').replace(/\?.*$/, '') === '#/settings'
            && document.getElementById('settings-h-fast-rack') && typeof SettingsPage !== 'undefined';
        if (onSettings) {
            SettingsPage.showSetting('settings-h-fast-rack');
            FastRack._landOnEditor();
            return;
        }
        try { sessionStorage.setItem('settings_focus', 'settings-h-fast-rack'); } catch (e) { /* opens at the top */ }
        if (window.App && App.navigate) App.navigate('#/settings');
    },

    // The block itself in view at the top of the page's content (Settings
    // lands on its section's top). The browser places it, so the Text
    // size's zoom is no matter; the window's shell, which scrollIntoView
    // moves too (its body hides its overflow but still scrolls), is put
    // back where it was.
    _bringIntoView(el) {
        const content = document.getElementById('content');
        const shell = [];
        for (let a = content ? content.parentElement : null; a; a = a.parentElement) {
            shell.push([a, a.scrollTop, a.scrollLeft]);
        }
        el.scrollIntoView({ block: 'start' });
        shell.forEach(([a, top, left]) => { a.scrollTop = top; a.scrollLeft = left; });
    },

    _landOnEditor() {
        const block = document.getElementById('settings-fast-rack');
        const head = document.getElementById('settings-h-fast-rack');
        if (!block) return;
        FastRack._bringIntoView(block);
        if (head) {
            if (!head.hasAttribute('tabindex')) head.setAttribute('tabindex', '-1');
            head.focus({ preventScroll: true });
        }
    },

    // ---- Settings -> My Preferences -> Fast Rack ---------------------------

    editorHtml() {
        return '<div class="fast-rack-editor" id="settings-fast-rack" role="group" aria-labelledby="settings-h-fast-rack" data-readonly-ok>'
            + '<h4 id="settings-h-fast-rack" data-land-block>Fast Rack</h4>'
            + '<p id="fr-hint" class="fr-hint">The buttons in the top bar, in their order: up to ' + FastRack.MAX + '. '
            + 'Kept with your sign-in. When the window is narrow they move into More, the last first.</p>'
            + '<ol class="fr-list" id="fr-list" aria-label="Fast Rack buttons"></ol>'
            + '<p class="fr-count" id="fr-count" aria-live="polite"></p>'
            + '<div class="fr-add-row">'
            + '<label for="fr-add">Add a button</label> '
            + '<select id="fr-add" data-no-search data-readonly-ok aria-describedby="fr-count"></select> '
            + '<button type="button" class="btn btn-sm btn-secondary" id="fr-add-btn" data-readonly-ok onclick="FastRack.addChosen()">Add</button>'
            + '</div>'
            + '<div class="fr-actions">'
            + '<button type="button" class="btn btn-sm btn-secondary" id="fr-reset" data-readonly-ok onclick="FastRack.resetDraft()">Reset to the shipped set</button> '
            + '<button type="button" class="btn btn-sm btn-primary" id="fr-save" data-readonly-ok onclick="FastRack.saveDraft()">Save Fast Rack</button>'
            + '</div></div>';
    },

    // Settings drew the block: fill it from the list in use
    loadEditor() {
        if (!document.getElementById('fr-list')) return;
        FastRack.draft = FastRack.shown();
        FastRack.drawEditor();
    },

    drawEditor(focus) {
        const list = document.getElementById('fr-list');
        if (!list) return;
        const draft = FastRack.draft || [];
        const n = draft.length;
        list.innerHTML = draft.map((id, i) => {
            const name = escapeHtml(FastRack.label(FastRack.byId(id)));
            return `<li class="fr-row" data-rack-id="${escapeHtml(id)}">`
                + `<span class="fr-name">${name}</span>`
                + `<button type="button" class="btn btn-sm btn-secondary" data-readonly-ok data-fr="up" aria-label="Move up: ${name}" onclick="FastRack.move(${i}, -1)"${i === 0 ? ' disabled' : ''}>Move up</button>`
                + `<button type="button" class="btn btn-sm btn-secondary" data-readonly-ok data-fr="down" aria-label="Move down: ${name}" onclick="FastRack.move(${i}, 1)"${i === n - 1 ? ' disabled' : ''}>Move down</button>`
                + `<button type="button" class="btn btn-sm btn-secondary" data-readonly-ok data-fr="remove" aria-label="Remove: ${name}" onclick="FastRack.remove(${i})">Remove</button>`
                + '</li>';
        }).join('') || '<li class="fr-row fr-row--none">No buttons: the rack is empty.</li>';
        const count = document.getElementById('fr-count');
        const full = n >= FastRack.MAX;
        if (count) {
            count.textContent = `${n} of ${FastRack.MAX}` + (full ? ': remove one to add another.' : '');
        }
        const add = document.getElementById('fr-add');
        const addBtn = document.getElementById('fr-add-btn');
        if (add) {
            const offered = FastRack.CATALOG.filter(c => !draft.includes(c.id) && FastRack.allowed(c));
            const group = (kind, title) => {
                const opts = offered.filter(c => c.kind === kind)
                    .map(c => `<option value="${escapeHtml(c.id)}">${escapeHtml(FastRack.label(c))}</option>`).join('');
                return opts ? `<optgroup label="${title}">${opts}</optgroup>` : '';
            };
            add.innerHTML = '<option value="">Choose a page or a new document…</option>'
                + group('nav', 'Pages') + group('action', 'New documents');
            add.disabled = full;
            if (addBtn) addBtn.disabled = full;
        }
        if (focus) {
            const el = typeof focus === 'function' ? focus() : document.querySelector(focus);
            if (el && !el.disabled) el.focus();
            else if (add && !add.disabled) add.focus();
        }
    },

    move(i, by) {
        const d = FastRack.draft || [];
        const j = i + by;
        if (j < 0 || j >= d.length) return;
        [d[i], d[j]] = [d[j], d[i]];
        // the moved button keeps the focus, on its new row
        FastRack.drawEditor(() => {
            const rows = document.querySelectorAll('#fr-list .fr-row');
            const row = rows[j];
            const want = row && row.querySelector(`[data-fr="${by < 0 ? 'up' : 'down'}"]`);
            return want && !want.disabled ? want : row && row.querySelector(`[data-fr="${by < 0 ? 'down' : 'up'}"]`);
        });
    },

    remove(i) {
        const d = FastRack.draft || [];
        d.splice(i, 1);
        FastRack.drawEditor(() => {
            const rows = document.querySelectorAll('#fr-list .fr-row [data-fr="remove"]');
            return rows[Math.min(i, rows.length - 1)] || null;
        });
    },

    addChosen() {
        const add = document.getElementById('fr-add');
        const id = add && add.value;
        const d = FastRack.draft || [];
        if (!id) { if (typeof toast === 'function') toast('Choose a page or a new document to add', 'info'); return; }
        if (d.length >= FastRack.MAX || d.includes(id) || !FastRack.allowed(FastRack.byId(id))) return;
        d.push(id);
        FastRack.drawEditor('#fr-add');
    },

    resetDraft() {
        FastRack.draft = FastRack.DEFAULT.filter(id => FastRack.allowed(FastRack.byId(id)));
        FastRack.drawEditor('#fr-save');
    },

    async saveDraft() {
        const items = FastRack.normalize(FastRack.draft || []);
        try {
            await API.put('/preferences/' + FastRack.KEY, { value: { items } });
        } catch (err) {
            toast(err.message || 'The Fast Rack was not saved', 'error');
            return;
        }
        FastRack.items = items;
        FastRack.render();
        toast('Fast Rack saved');
    },
};

// bootstrap.js and the inline handlers Settings draws find it here
if (typeof window !== 'undefined') window.FastRack = FastRack;
