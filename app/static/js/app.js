/**
 * App shell — the left-panel Navigator (the icon sidebar everyone
 * remembers from QuickBooks) plus hash-based routing to each page.
 */
const App = {
    routes: {
        '/':              { page: 'dashboard',       label: 'Dashboard',          render: () => DashboardPage.render() },
        '/customers':     { page: 'customers',       label: 'Customer Center',    render: () => CustomersPage.render() },
        '/jobs':          { page: 'jobs',            label: 'Jobs',               render: () => JobsPage.render() },
        '/jobs/:id':      { page: 'jobs',            label: 'Job',                render: (id) => JobsPage.renderDetail(id) },
        '/job-costs':     { page: 'job-costs',       label: 'Job Cost Entries',   render: () => JobCostsPage.render() },
        '/releases':      { page: 'releases',        label: 'Releases from Restriction', nonprofit: true, render: () => ReleasesPage.render() },
        '/functional-allocations': { page: 'functional-allocations', label: 'Functional Allocations', nonprofit: true, render: () => AllocationsPage.render() },
        '/vendors':       { page: 'vendors',         label: 'Vendor Center',      render: () => VendorsPage.render() },
        '/items':         { page: 'items',           label: 'Item List',          render: () => ItemsPage.render() },
        '/invoices':      { page: 'invoices',        label: 'Create Invoices',    render: () => InvoicesPage.render() },
        '/sales-receipts': { page: 'sales-receipts', label: 'Enter Sales Receipts', render: () => SalesReceiptsPage.render() },
        '/in-kind-gifts': { page: 'in-kind-gifts',   label: 'In-Kind Gifts',      nonprofit: true, render: () => InKindPage.render() },
        '/estimates':     { page: 'estimates',       label: 'Create Estimates',   render: () => EstimatesPage.render() },
        '/payments':      { page: 'payments',        label: 'Receive Payments',   render: () => PaymentsPage.render() },
        '/banking':       { page: 'banking',         label: 'Banking',            render: () => BankingPage.render() },
        '/banking/:id':   { page: 'banking',         label: 'Register',           render: (id) => BankingPage.renderRegister(id) },
        '/accounts':      { page: 'accounts',        label: 'Chart of Accounts',  render: () => App.renderAccounts() },
        '/reports':       { page: 'reports',         label: 'Report Center',      render: () => ReportsPage.render() },
        '/settings':      { page: 'settings',        label: 'Company Settings',   render: () => SettingsPage.render() },
        '/iif':           { page: 'iif',             label: 'QuickBooks Interop', render: () => IIFPage.render() },
        '/quick-entry':   { page: 'quick-entry',     label: 'Quick Entry',        render: () => App.renderQuickEntry() },
        // Phase 1: Foundation
        '/audit':         { page: 'audit',           label: 'Audit Log',          render: () => AuditPage.render() },
        // Phase 2: Accounts Payable
        '/purchase-orders': { page: 'purchase-orders', label: 'Purchase Orders',  render: () => PurchaseOrdersPage.render() },
        '/bills':         { page: 'bills',           label: 'Bills',              render: () => BillsPage.render() },
        '/credit-memos':  { page: 'credit-memos',    label: 'Credit Memos',       render: () => CreditMemosPage.render() },
        '/vendor-credits':{ page: 'vendor-credits',  label: 'Vendor Credits',     render: () => VendorCreditsPage.render() },
        '/vendor-credits/:id': { page: 'vendor-credits', label: 'Vendor Credit',  render: (id) => VendorCreditsPage.view(id) },
        // Phase 3: Productivity
        '/recurring':     { page: 'recurring',       label: 'Recurring Invoices', render: () => RecurringPage.render() },
        '/batch-payments': { page: 'batch-payments', label: 'Batch Payments',     render: () => BatchPaymentsPage.render() },
        // Phase 4: CSV Import/Export
        '/csv':           { page: 'csv',             label: 'CSV Import/Export',  render: () => App.renderCSV() },
        // Phase 8: QuickBooks Online
        '/qbo':           { page: 'qbo',             label: 'QuickBooks Online',  render: () => QBOPage.render() },
        // Phase 5: Advanced Integration
        '/tax':           { page: 'tax',             label: 'Tax Reports',        render: () => TaxPage.render() },
        // Phase 6: Ambitious
        '/companies':     { page: 'companies',       label: 'Companies',          render: () => CompaniesPage.render() },
        '/employees':     { page: 'employees',       label: 'Employees',          render: () => EmployeesPage.render() },
        '/payroll':       { page: 'payroll',         label: 'Payroll',            render: () => PayrollPage.render() },
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
        '/deposits':      { page: 'deposits',        label: 'Make Deposits',      render: () => DepositsPage.render() },
        // The Check Register page is the Banking register now (2.10); old bookmarks land there.
        '/check-register': { page: 'banking',         label: 'Banking',            render: () => { App.navigate('#/banking'); return ''; } },
        '/cc-charges':    { page: 'cc-charges',      label: 'CC Charges',         render: () => CCChargesPage.render() },
        '/expenses':      { page: 'expenses',        label: 'Enter Expenses',     render: () => ExpensesPage.render() },
        // Phase 10: Quick Wins + Medium Effort Features
        '/budgets':       { page: 'budgets',         label: 'Budget vs Actual',   render: () => BudgetsPage.render() },
        '/bank-rules':    { page: 'bank-rules',      label: 'Bank Rules',         render: () => BankRulesPage.render() },
        '/fixed-assets':  { page: 'fixed-assets',    label: 'Fixed Assets',       render: () => FixedAssetsPage.render() },
        '/migrate':       { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render() },
        '/xero-import':   { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render('xero') },
        '/myob-import':   { page: 'migrate',         label: 'Migrate Data',       render: () => MigrationPage.render('myob') },
        '/opening-balances': { page: 'opening-balances', label: 'Opening Balances', render: () => OpeningBalancesPage.render() },
    },

    async navigate(hash) {
        const path = hash.replace('#', '') || '/';
        let route = App.routes[path];
        let param = null;
        if (!route) {
            // One-segment parameter routes: '/jobs/:id' matches '/jobs/12'
            for (const [key, r] of Object.entries(App.routes)) {
                const i = key.indexOf('/:');
                if (i > 0 && path.startsWith(key.slice(0, i + 1)) && !path.slice(i + 1).includes('/')) {
                    route = r; param = decodeURIComponent(path.slice(i + 1)); break;
                }
            }
        }
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

        try {
            const html = await route.render(param);
            $('#page-content').innerHTML = html;
            App.setStatus(`${route.label} — Ready`);
        } catch (err) {
            // Server-side detail (err.message and stack) goes to console
            // for devs; the DOM gets a clean user-facing error with a
            // recovery action. Avoid leaking framework internals into
            // the rendered page (S1 audit finding).
            console.error(err);
            $('#page-content').innerHTML = `<div class="empty-state">
                <h3>Couldn't load this page</h3>
                <p>${escapeHtml(err.message || 'An unexpected error occurred.')}</p>
                <p style="margin-top:12px;">
                    <a href="#/" class="btn btn-secondary">Return to Dashboard</a>
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

    // ---- Read-only sign-ins (Server Edition) -------------------------------
    // The server refuses every write from the readonly role with a 403 —
    // that stays the enforcement. But every page offered "+ New", and a
    // whole form could be filled in before the refusal arrived (2.17.3
    // exploratory test, W-L17). Once /api/auth/status names the role, the
    // create buttons are hidden and every form a dialog opens is shown
    // locked, with a sentence saying why.
    role: 'admin',
    READ_ONLY_MESSAGE: 'Your sign-in is read-only: you can look, but not save changes. '
        + 'An administrator can change your role under Settings → Users.',

    isReadOnly() { return App.role === 'readonly'; },

    setRole(role) {
        App.role = role || 'admin';
        document.body.classList.toggle('role-readonly', App.isReadOnly());
        const page = document.getElementById('page-content');
        if (!App.isReadOnly() || !page) return;
        // the toolbar's shortcuts to new documents, and batch entry
        document.querySelectorAll('#topbar .tb-btn[data-action], #topbar .tb-btn[data-nav="#/quick-entry"]')
            .forEach(b => b.classList.add('hidden'));
        App.hideWriteControls(page);
        if (!App._roObserver) {
            // pages re-render in place (tabs, filters): keep them clean
            App._roObserver = new MutationObserver(() => App.hideWriteControls(page));
            App._roObserver.observe(page, { childList: true, subtree: true });
        }
    },

    // "+ New Invoice", "+ Record Payment", "New Account": a create button is
    // labelled "+ …", or is the page header's primary action.
    hideWriteControls(root) {
        if (!root || !App.isReadOnly()) return;
        root.querySelectorAll('button, a.btn').forEach(el => {
            const label = (el.textContent || '').trim();
            const headerAction = el.classList.contains('btn-primary') && el.closest('.page-header');
            if (label.startsWith('+') || headerAction) el.classList.add('hidden');
        });
    },

    // Called by openModal(). A form that only opens a document (the
    // customer statement) carries data-readonly-ok and stays usable.
    lockForms(root) {
        if (!root || !App.isReadOnly()) return;
        root.querySelectorAll('form:not([data-readonly-ok])').forEach(form => {
            form.querySelectorAll('input, select, textarea').forEach(el => { el.disabled = true; });
            form.querySelectorAll('button').forEach(b => {
                if (/closeModal\(/.test(b.getAttribute('onclick') || '')) return;
                b.disabled = true;
                b.style.opacity = '0.5';
                b.style.cursor = 'not-allowed';
            });
            form.onsubmit = (e) => { e.preventDefault(); toast(App.READ_ONLY_MESSAGE, 'error'); return false; };
            if (!form.querySelector('.readonly-note')) {
                form.insertAdjacentHTML('afterbegin',
                    `<div class="hint hint--locked readonly-note" style="margin-bottom:10px;">${escapeHtml(App.READ_ONLY_MESSAGE)}</div>`);
            }
        });
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

    async renderAccounts() {
        const accounts = await API.get('/accounts');
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
            <div class="page-header">
                <h2>Chart of Accounts</h2>
                <div>
                    ${inactiveCount ? `<button class="btn btn-sm btn-secondary" onclick="App.toggleInactiveAccounts()">${App._showInactiveAccounts ? 'Hide' : 'Show'} ${inactiveCount} inactive</button> ` : ''}
                    <button class="btn btn-secondary" onclick="App.showChartImport()">Import…</button>
                    <button class="btn btn-primary" onclick="App.showAccountForm()">New Account</button>
                </div>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col" style="width:80px;">Number</th><th scope="col">Name</th><th scope="col" style="width:100px;">Type</th><th scope="col" class="amount" style="width:100px;">Balance</th><th scope="col" style="width:190px;">Actions</th></tr></thead>
                <tbody>`;

        for (const type of typeOrder) {
            const accts = grouped[type] || [];
            if (accts.length === 0) continue;
            html += `<tr style="background:linear-gradient(180deg, #e8ecf2 0%, #dde2ea 100%);"><td colspan="5" style="font-weight:700; color:var(--qb-navy); font-size:11px; padding:4px 10px;">${typeNames[type]}</td></tr>`;
            for (const a of accts) {
                const inactive = a.is_active === false;
                html += `<tr${inactive ? ' style="opacity:.55;"' : ''}>
                    <td style="font-family:var(--font-mono);">${escapeHtml(a.account_number || '')}</td>
                    <td><strong>${escapeHtml(a.name)}</strong>${a.is_control ? ` <span class="badge-control" title="${escapeHtml(a.control_purpose || 'the software finds this account by its number')}">control</span>` : ''}${inactive ? ' <span class="badge badge-draft">inactive</span>' : ''}</td>
                    <td>${a.account_type}</td>
                    <td class="amount">${formatCurrency(a.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="App.showAccountForm(${a.id})">Edit</button>
                        ${inactive
                            ? `<button class="btn btn-sm btn-secondary" onclick="App.setAccountActive(${a.id}, true)">Reactivate</button>`
                            : `<button class="btn btn-sm btn-secondary" onclick="App.setAccountActive(${a.id}, false)">Deactivate</button>`}
                        ${a.is_control ? '' : `<button class="btn btn-sm btn-secondary" onclick="App.deleteAccount(${a.id})">Delete</button>`}
                    </td>
                </tr>`;
            }
        }
        html += `</tbody></table></div>`;
        return html;
    },

    async showAccountForm(id = null) {
        let acct = { name: '', account_number: '', account_type: 'expense', description: '' };
        if (id) acct = await API.get(`/accounts/${id}`);

        const types = ['asset','liability','equity','income','cogs','expense'];
        // A control account is found by its number when a document posts, so the
        // number and the type are fixed and the API refuses to change them (400).
        // Renaming is allowed and is the point — say so instead of hiding the form.
        const locked = !!acct.is_control;
        // A number is required (digits; 6150.1 or 6150-01 for a sub-account),
        // except on an account an importer brought in without one, which can
        // still be renamed.
        const numberRequired = !locked && (!id || !!acct.account_number);
        const lockNote = locked
            ? `<div class="form-group full-width"><div class="hint hint--locked">
                   <strong>${escapeHtml(acct.account_number || '')} ${escapeHtml(acct.name)} is a control account.</strong>
                   The software finds it by its number to post ${escapeHtml(acct.control_purpose || 'part of the books')},
                   so the number and type cannot change — a document that could not find it would have nowhere to post.
                   <em>You can rename it.</em>
               </div></div>`
            : '';
        openModal(id ? 'Edit Account' : 'New Account', `
            <form onsubmit="App.saveAccount(event, ${id})">
                <div class="form-grid">
                    ${lockNote}
                    <div class="form-group"><label>Account Number${numberRequired ? ' *' : ''}</label>
                        <input name="account_number" value="${escapeHtml(acct.account_number || '')}"${locked ? ' readonly disabled' : ''}
                            ${numberRequired ? 'required' : ''} pattern="\\d+([.\\-]\\d+)*" maxlength="20" placeholder="e.g. 6150"
                            title="Digits, like 6150. A sub-account can use 6150.1 or 6150-01."></div>
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(acct.name)}"></div>
                    <div class="form-group"><label>Type *</label>
                        <select name="account_type"${locked ? ' disabled' : ''}>
                            ${types.map(t => `<option value="${t}" ${acct.account_type===t?'selected':''}>${t.charAt(0).toUpperCase()+t.slice(1)}</option>`).join('')}
                        </select></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description">${escapeHtml(acct.description || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Account</button>
                </div>
            </form>`);
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
            const label = { create: 'Create', update: 'Update', skip: 'Skip', deactivate: 'Deactivate', keep: 'Keep', error: 'Error' };
            const rows = plan.rows.map(r => `<tr>
                <td>${label[r.action] || r.action}</td>
                <td style="font-family:var(--font-mono);">${escapeHtml(r.number || '')}</td>
                <td>${escapeHtml(r.name)}</td>
                <td>${escapeHtml(r.type || '')}</td>
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
                    <thead><tr><th scope="col">Action</th><th scope="col">Number</th><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Detail</th></tr></thead>
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
        try {
            if (id) { await API.put(`/accounts/${id}`, data); toast('Account updated'); }
            else { await API.post('/accounts', data); toast('Account created'); }
            closeModal();
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Feature 4: Unified Global Search
    _searchTimeout: null,
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
                            html += `<div class="search-item" onclick="${sec.onClick(item)}">${escapeHtml(label)}</div>`;
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
                <div class="settings-section">
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
            let html = `<div style="color:var(--success); font-size:11px;">Imported ${n} ${entity === 'accounts' ? 'accounts' : entity}${data.updated ? `, updated ${data.updated}` : ''}${data.skipped ? `, ${data.skipped} already there` : ''}.</div>`;
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
        const itemOpts = items.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');

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
                            <td><select class="line-item" onchange="App.qeItemSelected(0)"><option value="">--</option>${itemOpts}</select></td>
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
                        <button type="submit" class="btn btn-primary">Save & Next (Ctrl+Enter)</button>
                    </div>
                </div>
            </form>
            <div id="qe-log" style="margin-top:16px;"></div>`;
    },

    _qeLineCount: 1,
    qeAddLine() {
        const idx = App._qeLineCount++;
        const itemOpts = App._qeItems.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');
        $('#qe-lines').insertAdjacentHTML('beforeend', `
            <tr data-qeline="${idx}">
                <td><select class="line-item" onchange="App.qeItemSelected(${idx})"><option value="">--</option>${itemOpts}</select></td>
                <td><input class="line-desc" value=""></td>
                <td><input class="line-qty" type="number" step="0.01" value="1" oninput="App.qeRecalc()"></td>
                <td><input class="line-rate" type="number" step="0.01" value="0" oninput="App.qeRecalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
            </tr>`);
    },

    qeItemSelected(idx) {
        const row = $(`[data-qeline="${idx}"]`);
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
            const item_id = row.querySelector('.line-item')?.value;
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 1;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            if (rate > 0 || row.querySelector('.line-desc')?.value) {
                lines.push({
                    item_id: item_id ? parseInt(item_id) : null,
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
                    <td><select class="line-item" onchange="App.qeItemSelected(0)"><option value="">--</option>${App._qeItems.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('')}</select></td>
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
            App.settings = s || {};
            Terms.init(s);
            const companyEl = $('#status-company');
            if (companyEl && s.company_name && s.company_name !== 'My Company') {
                companyEl.textContent = `Company: ${s.company_name}`;
                // the window / tab title and the topbar brand say whose books these are
                document.title = `${s.company_name} — Slowbooks Pro 2026`;
                const brand = $('#topbar-company');
                if (brand) brand.textContent = s.company_name;
            }
        } catch (e) { Terms.init(null); /* business words until signed in */ }
    },

    // Rewrites the static shell into the company's words. index.html is
    // served raw, so the sidebar and toolbar arrive as business-worded
    // HTML; this runs once at boot, before the first page renders.
    // Sidebar entries the server serves to admins only (app.main RBAC).
    ADMIN_ONLY_PAGES: ['employees', 'payroll', 'hr-onboarding', 'hr-benefits', 'hr-deductions', 'hr-tax-forms', 'users'],

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
        $$('[data-nonprofit]').forEach(el => { el.hidden = !np; });
        $$('[data-business-only]').forEach(el => { el.hidden = np; });
    },

    init() {
        window.addEventListener('hashchange', () => App.navigate(location.hash));

        // Load saved theme
        App.loadTheme();

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
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
            // Alt+N: new invoice
            if (e.altKey && e.key === 'n') { InvoicesPage.showForm(); e.preventDefault(); }
            // Alt+P: receive payment
            if (e.altKey && e.key === 'p') { PaymentsPage.showForm(); e.preventDefault(); }
            // Alt+Q: quick entry
            if (e.altKey && e.key === 'q') { App.navigate('#/quick-entry'); e.preventDefault(); }
            // Alt+H: home/dashboard
            if (e.altKey && e.key === 'h') { App.navigate('#/'); e.preventDefault(); }
            // Alt+D: toggle dark mode (Feature 12)
            if (e.altKey && e.key === 'd') { App.toggleTheme(); e.preventDefault(); }
            // Escape: close modal
            if (e.key === 'Escape') { closeModal(); }
            // Ctrl+K or /: focus search (when not in an input)
            if ((e.ctrlKey && e.key === 'k') || (e.key === '/' && !e.target.closest('input,textarea,select'))) {
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

        // Real version in the footer + update badge on desktop installs
        App.initSystemInfo();

        // Settings first: the vocabulary and the nonprofit nav items must
        // be in place before the first page paints (no flash of "Customers"
        // on a donor's screen). loadCompanySettings never rejects.
        App.loadCompanySettings().then(() => {
            App.applyTerminology();
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
            if (!res.ok) return;
            const info = await res.json();
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
                if (a.user) App.setRole(a.user.role);
                if (a.multi_user && a.user && a.user.role !== 'admin') {
                    // HR and payroll are admin functions; the server refuses
                    // them for other roles, so do not offer the pages.
                    App.ADMIN_ONLY_PAGES.forEach(page => {
                        const link = document.querySelector(`#sidebar .nav-link[data-page="${page}"]`);
                        if (link && link.parentElement) link.parentElement.hidden = true;
                    });
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
            if (!info.desktop) return;

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
