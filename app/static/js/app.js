/**
 * Decompiled from QBW32.EXE!CMainFrame + CQBNavigator  Offset: 0x00042000
 * Original was an MFC CFrameWnd with a custom left-panel "Navigator" control
 * (the icon sidebar everyone remembers). CMainFrame::OnNavigate() dispatched
 * to individual CFormView subclasses via a 31-entry function pointer table.
 * We replaced the Win32 message pump with hash-based routing because, again,
 * it is no longer 2003. WM_COMMAND 0x8001 through 0x801F, rest in peace.
 */
const App = {
    routes: {
        '/':              { page: 'dashboard',       label: 'Dashboard',          render: () => App.renderDashboard() },
        '/customers':     { page: 'customers',       label: 'Customer Center',    render: () => CustomersPage.render() },
        '/vendors':       { page: 'vendors',         label: 'Vendor Center',      render: () => VendorsPage.render() },
        '/items':         { page: 'items',           label: 'Item List',          render: () => ItemsPage.render() },
        '/invoices':      { page: 'invoices',        label: 'Create Invoices',    render: () => InvoicesPage.render() },
        '/estimates':     { page: 'estimates',       label: 'Create Estimates',   render: () => EstimatesPage.render() },
        '/payments':      { page: 'payments',        label: 'Receive Payments',   render: () => PaymentsPage.render() },
        '/banking':       { page: 'banking',         label: 'Bank Accounts',      render: () => BankingPage.render() },
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
        // Phase 9: Forum Bug Fixes & Missing Features
        '/journal':       { page: 'journal',         label: 'Journal Entries',    render: () => JournalPage.render() },
        '/deposits':      { page: 'deposits',        label: 'Make Deposits',      render: () => DepositsPage.render() },
        '/check-register': { page: 'check-register', label: 'Check Register',     render: () => CheckRegisterPage.render() },
        '/cc-charges':    { page: 'cc-charges',      label: 'CC Charges',         render: () => CCChargesPage.render() },
        // Phase 10: Quick Wins + Medium Effort Features
        '/budgets':       { page: 'budgets',         label: 'Budget vs Actual',   render: () => BudgetsPage.render() },
        '/bank-rules':    { page: 'bank-rules',      label: 'Bank Rules',         render: () => BankRulesPage.render() },
    },

    async navigate(hash) {
        const path = hash.replace('#', '') || '/';
        const route = App.routes[path];
        if (!route) { $('#page-content').innerHTML = '<p>Page not found</p>'; return; }

        // Update active nav
        $$('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.page === route.page);
        });

        // Status bar
        App.setStatus(`Loading ${route.label}...`);

        try {
            const html = await route.render();
            $('#page-content').innerHTML = html;
            App.setStatus(`${route.label} — Ready`);
        } catch (err) {
            console.error(err);
            $('#page-content').innerHTML = `<div class="empty-state">
                <p><strong>Error 0x8004:</strong> ${escapeHtml(err.message)}</p>
                <p style="font-size:10px; color:var(--text-muted);">CQBView::OnActivate() failed at offset 0x00042A10</p>
            </div>`;
            App.setStatus('Error — see console for details');
        }
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
    },

    loadTheme() {
        const saved = localStorage.getItem('slowbooks-theme');
        if (saved === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
            const btn = $('#theme-toggle');
            if (btn) btn.innerHTML = '&#9788;';
        }
    },

    async renderDashboard() {
        const data = await API.get('/dashboard');

        let recentInv = data.recent_invoices.map(inv =>
            `<tr>
                <td><strong>${escapeHtml(inv.invoice_number)}</strong></td>
                <td>${formatDate(inv.date)}</td>
                <td>${statusBadge(inv.status)}</td>
                <td class="amount">${formatCurrency(inv.total)}</td>
            </tr>`
        ).join('') || '<tr><td colspan="4" style="color:var(--text-muted); font-size:11px;">No invoices yet &mdash; use Create Invoice to get started</td></tr>';

        let recentPay = data.recent_payments.map(p =>
            `<tr>
                <td>${formatDate(p.date)}</td>
                <td>${escapeHtml(p.method || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`
        ).join('') || '<tr><td colspan="3" style="color:var(--text-muted); font-size:11px;">No payments recorded yet</td></tr>';

        let bankCards = data.bank_balances.map(ba =>
            `<div class="card" style="cursor:pointer" onclick="App.navigate('#/banking')">
                <div class="card-header">${escapeHtml(ba.name)}</div>
                <div class="card-value">${formatCurrency(ba.balance)}</div>
            </div>`
        ).join('');

        if (!bankCards) {
            bankCards = `<div class="card">
                <div class="card-header">No Bank Accounts</div>
                <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">
                    Go to Banking to set up an account</div>
            </div>`;
        }

        // Feature 3: Dashboard Charts
        let chartsHtml = '';
        try {
            const charts = await API.get('/dashboard/charts');
            // AR Aging Bar Chart
            const agingTotal = (charts.aging_current || 0) + (charts.aging_30 || 0) + (charts.aging_60 || 0) + (charts.aging_90 || 0);
            if (agingTotal > 0) {
                const pctCurrent = ((charts.aging_current / agingTotal) * 100).toFixed(1);
                const pct30 = ((charts.aging_30 / agingTotal) * 100).toFixed(1);
                const pct60 = ((charts.aging_60 / agingTotal) * 100).toFixed(1);
                const pct90 = ((charts.aging_90 / agingTotal) * 100).toFixed(1);
                chartsHtml += `
                    <div class="dashboard-section">
                        <h3>AR Aging</h3>
                        <div class="chart-bar-container">
                            <div class="chart-bar" style="display:flex; height:28px; border-radius:4px; overflow:hidden;">
                                ${pctCurrent > 0 ? `<div style="width:${pctCurrent}%; background:var(--success);" title="Current: ${formatCurrency(charts.aging_current)}"></div>` : ''}
                                ${pct30 > 0 ? `<div style="width:${pct30}%; background:var(--qb-gold);" title="1-30 days: ${formatCurrency(charts.aging_30)}"></div>` : ''}
                                ${pct60 > 0 ? `<div style="width:${pct60}%; background:#f97316;" title="31-60 days: ${formatCurrency(charts.aging_60)}"></div>` : ''}
                                ${pct90 > 0 ? `<div style="width:${pct90}%; background:var(--danger);" title="61+ days: ${formatCurrency(charts.aging_90)}"></div>` : ''}
                            </div>
                            <div class="chart-legend" style="display:flex; gap:12px; margin-top:6px; font-size:10px;">
                                <span><span style="color:var(--success);">&#9632;</span> Current ${formatCurrency(charts.aging_current)}</span>
                                <span><span style="color:var(--qb-gold);">&#9632;</span> 1-30 ${formatCurrency(charts.aging_30)}</span>
                                <span><span style="color:#f97316;">&#9632;</span> 31-60 ${formatCurrency(charts.aging_60)}</span>
                                <span><span style="color:var(--danger);">&#9632;</span> 61+ ${formatCurrency(charts.aging_90)}</span>
                            </div>
                        </div>
                    </div>`;
            }

            // Monthly Revenue Trend
            if (charts.monthly_revenue && charts.monthly_revenue.length > 0) {
                const maxRev = Math.max(...charts.monthly_revenue.map(m => m.amount), 1);
                const bars = charts.monthly_revenue.map(m => {
                    const pct = Math.max((m.amount / maxRev) * 100, 2);
                    return `<div class="chart-bar-col" style="flex:1; text-align:center;">
                        <div style="height:100px; display:flex; align-items:flex-end; justify-content:center;">
                            <div style="width:80%; background:var(--qb-blue); height:${pct}%; border-radius:2px 2px 0 0;"
                                 title="${m.month}: ${formatCurrency(m.amount)}"></div>
                        </div>
                        <div style="font-size:9px; color:var(--text-muted); margin-top:4px;">${m.month}</div>
                    </div>`;
                }).join('');
                chartsHtml += `
                    <div class="dashboard-section">
                        <h3>Monthly Revenue (Last 12 Months)</h3>
                        <div style="display:flex; gap:2px; align-items:flex-end;">${bars}</div>
                    </div>`;
            }
        } catch (e) { /* charts endpoint not available yet — that's fine */ }

        return `
            <div class="page-header">
                <h2>Company Snapshot</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Slowbooks Pro 2026 &mdash; Build 12.0.3190-R
                </div>
            </div>

            <div class="card-grid">
                <div class="card">
                    <div class="card-header">Total Receivables</div>
                    <div class="card-value">${formatCurrency(data.total_receivables)}</div>
                </div>
                <div class="card">
                    <div class="card-header">Overdue Invoices</div>
                    <div class="card-value" ${data.overdue_count > 0 ? 'style="color:var(--qb-red)"' : ''}>${data.overdue_count}</div>
                </div>
                <div class="card">
                    <div class="card-header">Active Customers</div>
                    <div class="card-value">${data.customer_count}</div>
                </div>
                ${data.total_payables !== undefined ? `<div class="card">
                    <div class="card-header">Total Payables</div>
                    <div class="card-value">${formatCurrency(data.total_payables)}</div>
                </div>` : ''}
            </div>

            <div class="dashboard-section">
                <h3>Bank Balances</h3>
                <div class="card-grid">${bankCards}</div>
            </div>

            ${chartsHtml}

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
                <div class="dashboard-section">
                    <h3>Recent Invoices</h3>
                    <div class="table-container"><table>
                        <thead><tr><th>#</th><th>Date</th><th>Status</th><th class="amount">Total</th></tr></thead>
                        <tbody>${recentInv}</tbody>
                    </table></div>
                </div>
                <div class="dashboard-section">
                    <h3>Recent Payments</h3>
                    <div class="table-container"><table>
                        <thead><tr><th>Date</th><th>Method</th><th class="amount">Amount</th></tr></thead>
                        <tbody>${recentPay}</tbody>
                    </table></div>
                </div>
            </div>`;
    },

    async renderAccounts() {
        const accounts = await API.get('/accounts');
        const grouped = {};
        for (const a of accounts) {
            if (!grouped[a.account_type]) grouped[a.account_type] = [];
            grouped[a.account_type].push(a);
        }

        const typeOrder = ['asset', 'liability', 'equity', 'income', 'cogs', 'expense'];
        const typeNames = { asset: 'Assets', liability: 'Liabilities', equity: 'Equity',
            income: 'Income', cogs: 'Cost of Goods Sold', expense: 'Expenses' };

        let html = `
            <div class="page-header">
                <h2>Chart of Accounts</h2>
                <button class="btn btn-primary" onclick="App.showAccountForm()">New Account</button>
            </div>
            <div class="table-container"><table>
                <thead><tr><th style="width:80px;">Number</th><th>Name</th><th style="width:100px;">Type</th><th class="amount" style="width:100px;">Balance</th><th style="width:60px;">Actions</th></tr></thead>
                <tbody>`;

        for (const type of typeOrder) {
            const accts = grouped[type] || [];
            if (accts.length === 0) continue;
            html += `<tr style="background:linear-gradient(180deg, #e8ecf2 0%, #dde2ea 100%);"><td colspan="5" style="font-weight:700; color:var(--qb-navy); font-size:11px; padding:4px 10px;">${typeNames[type]}</td></tr>`;
            for (const a of accts) {
                html += `<tr>
                    <td style="font-family:var(--font-mono);">${escapeHtml(a.account_number || '')}</td>
                    <td>${a.is_system ? '' : ''}<strong>${escapeHtml(a.name)}</strong></td>
                    <td>${a.account_type}</td>
                    <td class="amount">${formatCurrency(a.balance)}</td>
                    <td class="actions">
                        ${!a.is_system ? `<button class="btn btn-sm btn-secondary" onclick="App.showAccountForm(${a.id})">Edit</button>` : ''}
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
        openModal(id ? 'Edit Account' : 'New Account', `
            <form onsubmit="App.saveAccount(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Account Number</label>
                        <input name="account_number" value="${escapeHtml(acct.account_number || '')}"></div>
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(acct.name)}"></div>
                    <div class="form-group"><label>Type *</label>
                        <select name="account_type">
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

    // Feature 4: Unified Global Search — replaces CQBSearchEngine @ 0x00250000
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
                const sections = [
                    { key: 'customers', label: 'Customers', onClick: (item) => `App.navigate('#/customers');closeSearchDropdown();` },
                    { key: 'vendors', label: 'Vendors', onClick: (item) => `App.navigate('#/vendors');closeSearchDropdown();` },
                    { key: 'items', label: 'Items', onClick: (item) => `App.navigate('#/items');closeSearchDropdown();` },
                    { key: 'invoices', label: 'Invoices', onClick: (item) => `InvoicesPage.view(${item.id});closeSearchDropdown();` },
                    { key: 'estimates', label: 'Estimates', onClick: (item) => `App.navigate('#/estimates');closeSearchDropdown();` },
                    { key: 'payments', label: 'Payments', onClick: (item) => `App.navigate('#/payments');closeSearchDropdown();` },
                ];
                for (const sec of sections) {
                    const items = results[sec.key];
                    if (items && items.length > 0) {
                        html += `<div class="search-section">${sec.label}</div>`;
                        items.forEach(item => {
                            const label = item.display || item.name || item.invoice_number || `#${item.id}`;
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
                        <a href="/api/csv/export/customers" class="btn btn-secondary" download>Export Customers</a>
                        <a href="/api/csv/export/vendors" class="btn btn-secondary" download>Export Vendors</a>
                        <a href="/api/csv/export/items" class="btn btn-secondary" download>Export Items</a>
                        <a href="/api/csv/export/invoices" class="btn btn-secondary" download>Export Invoices</a>
                        <a href="/api/csv/export/accounts" class="btn btn-secondary" download>Export Chart of Accounts</a>
                    </div>
                </div>
                <div class="settings-section">
                    <h3>Import</h3>
                    <p style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">Upload CSV files to import data.</p>
                    <form id="csv-import-form" onsubmit="App.importCSV(event)">
                        <div class="form-group"><label>Entity Type</label>
                            <select name="entity_type" id="csv-entity">
                                <option value="customers">Customers</option>
                                <option value="vendors">Vendors</option>
                                <option value="items">Items</option>
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
            const resp = await fetch(`/api/csv/import/${entity}`, { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            let html = `<div style="color:var(--success); font-size:11px;">Imported ${data.imported} ${entity}.</div>`;
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
                    <div class="form-group"><label>Customer *</label>
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
                    <thead><tr><th>Item</th><th>Description</th><th class="col-qty">Qty</th><th class="col-rate">Rate</th><th class="col-amount">Amount</th></tr></thead>
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
            toast(`Invoice #${inv.invoice_number} created`);
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

    // Load company name from settings for status bar
    async loadCompanyName() {
        try {
            const s = await API.get('/settings');
            const companyEl = $('#status-company');
            if (companyEl && s.company_name && s.company_name !== 'My Company') {
                companyEl.textContent = `Company: ${s.company_name}`;
            }
        } catch (e) { /* ignore on load */ }
    },

    init() {
        window.addEventListener('hashchange', () => App.navigate(location.hash));

        // Load saved theme
        App.loadTheme();

        // Keyboard shortcuts — CAcceleratorTable @ 0x00042800
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

        // Start clock — CMainFrame::OnTimer() at 1-second interval (WM_TIMER id=1)
        App.updateClock();
        setInterval(App.updateClock, 60000);

        // Load company name into status bar
        App.loadCompanyName();

        // Navigate after splash closes
        App.navigate(location.hash || '#/');
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
