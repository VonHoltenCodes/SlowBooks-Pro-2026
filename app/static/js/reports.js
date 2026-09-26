/**
 * Reports — every report is plain SQL on the backend; this viewer
 * renders the tables, print/PDF is WeasyPrint server-side.
 */
const ReportsPage = {
    // Map of report_type → opener method, for re-opening saved reports.
    // Keys here MUST match the report_type strings the openers pass to
    // openPeriodModal so save-then-reload roundtrips cleanly.
    _OPENERS: {
        profit_loss:        (params) => ReportsPage.profitLoss(params),
        balance_sheet:      (params) => ReportsPage.balanceSheet(params),
        ar_aging:           (params) => ReportsPage.arAging(params),
        ap_aging:           (params) => ReportsPage.apAging(params),
        sales_tax:          (params) => ReportsPage.salesTax(params),
        general_ledger:     (params) => ReportsPage.generalLedger(params),
        income_by_customer: (params) => ReportsPage.incomeByCustomer(params),
        cash_flow:          (params) => ReportsPage.cashFlow(params),
        statement_of_financial_position: (params) => ReportsPage.statementOfFinancialPosition(params),
        statement_of_activities:         (params) => ReportsPage.statementOfActivities(params),
        fund_balances:                   (params) => ReportsPage.fundBalances(params),
        functional_expenses:             (params) => ReportsPage.functionalExpenses(params),
        pledges:                         (params) => ReportsPage.pledges(params),
    },

    async render() {
        // Fetch saved reports separately so the page still renders if the
        // call fails (network blip, table missing, etc.).
        let savedHtml = '';
        try {
            const saved = await API.get('/saved-reports');
            if (saved && saved.length) {
                // A list, not a wall of cards: thirty saved reports must not
                // push the Report Center off the screen. Collapsed by default
                // once there are more than a handful; the choice sticks.
                let collapsed = false;
                try { collapsed = localStorage.getItem('sb_saved_reports_collapsed') === '1'; } catch (e) { /* ignore */ }
                if (saved.length > 6 && localStorage.getItem('sb_saved_reports_collapsed') === null) collapsed = true;
                const period = (p) => !p ? '' : (p.as_of_date ? `as of ${escapeHtml(p.as_of_date)}` : (p.start_date ? `${escapeHtml(p.start_date)} → ${escapeHtml(p.end_date || '')}` : (p.period ? escapeHtml(String(p.period).replace(/_/g, ' ')) : '')));
                const rows = saved.slice().sort((a, b) => a.name.localeCompare(b.name)).map(s => `
                    <tr class="saved-report-row">
                        <td><a href="javascript:void(0)" onclick="ReportsPage.openSaved(${s.id})" style="font-weight:600;">${escapeHtml(s.name)}</a></td>
                        <td>${escapeHtml(Terms.text(s.report_type.replace(/_/g, ' ')))}</td>
                        <td style="color:var(--text-muted);">${period(s.parameters)}</td>
                        <td class="actions">
                            <button class="btn btn-sm btn-secondary" onclick="ReportsPage.openSaved(${s.id})">Open</button>
                            <button class="btn btn-sm btn-secondary" aria-label="Delete saved report" onclick="ReportsPage.deleteSaved(${s.id})">Delete</button>
                        </td>
                    </tr>`).join('');
                savedHtml = `
                    <div style="display:flex; align-items:center; gap:10px; margin:0 0 8px;">
                        <button type="button" class="btn btn-sm btn-secondary" id="saved-reports-toggle" aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="saved-reports-list" onclick="ReportsPage.toggleSaved()">${collapsed ? '▸' : '▾'}</button>
                        <h3 style="font-size:13px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted); margin:0;">Saved Reports (${saved.length})</h3>
                        ${saved.length > 8 ? `<input type="text" id="saved-reports-filter" placeholder="Filter…" style="width:160px;" oninput="ReportsPage.filterSaved(this.value)">` : ''}
                    </div>
                    <div id="saved-reports-list" ${collapsed ? 'hidden' : ''} style="margin-bottom:20px;">
                        <div class="table-container"><table>
                            <thead><tr><th scope="col">Name</th><th scope="col">Report</th><th scope="col">Period</th><th scope="col">Actions</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table></div>
                    </div>`;
            }
        } catch (e) { /* render anyway */ }

        return `
            <div class="page-header"><h2>Reports</h2></div>
            ${savedHtml}
            <div class="card-grid">
                ${Terms.isNonprofit() ? ReportsPage._nonprofitCards() : `
                <div class="card" style="cursor:pointer" onclick="ReportsPage.profitLoss()">
                    <div class="card-header">${T('Profit & Loss')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Income vs expenses for a period')}</p>
                </div>`}
                <div class="card" style="cursor:pointer" onclick="ReportsPage.profitLossByClass()">
                    <div class="card-header">${T('P&L by Class')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Income vs expenses split by class')}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.jobBudgetVsActual()">
                    <div class="card-header">${T('Job Budget vs Actual')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Budget, committed, actual, projected, variance per job')}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.jobProfitability()">
                    <div class="card-header">${T('Job Profitability')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Income, costs and margin per job')}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.financialStatementsPdf()">
                    <div class="card-header">Financial Statements Pack (PDF)</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Profit & Loss + Balance Sheet + Trial Balance, one audit-ready PDF')}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.fixedAssetReconciliation()">
                    <div class="card-header">Fixed Asset Reconciliation</div>
                    <p style="font-size:13px; color:var(--gray-500);">Register totals vs GL by asset type</p>
                </div>
                ${Terms.isNonprofit() ? '' : `
                <div class="card" style="cursor:pointer" onclick="ReportsPage.balanceSheet()">
                    <div class="card-header">${T('Balance Sheet')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Assets, liabilities, and equity')}</p>
                </div>`}
                <div class="card" style="cursor:pointer" onclick="ReportsPage.arAging()">
                    <div class="card-header">${T('A/R Aging')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">Outstanding receivables by age</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.apAging()">
                    <div class="card-header">A/P Aging</div>
                    <p style="font-size:13px; color:var(--gray-500);">Outstanding payables by age</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.salesTax()">
                    <div class="card-header">Sales Tax</div>
                    <p style="font-size:13px; color:var(--gray-500);">Tax collected by invoice</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.generalLedger()">
                    <div class="card-header">General Ledger</div>
                    <p style="font-size:13px; color:var(--gray-500);">All journal entries by account</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.incomeByCustomer()">
                    <div class="card-header">${T('Income by Customer')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.isNonprofit() ? 'Contribution totals per donor' : 'Sales totals per customer'}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.customerStatementPicker()">
                    <div class="card-header">${T('Customer Statement')}</div>
                    <p style="font-size:13px; color:var(--gray-500);">${Terms.text('Invoice/payment history PDF')}</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.trialBalance()">
                    <div class="card-header">Trial Balance</div>
                    <p style="font-size:13px; color:var(--gray-500);">Debits and credits by account</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.cashFlow()">
                    <div class="card-header">Cash Flow</div>
                    <p style="font-size:13px; color:var(--gray-500);">Operating, investing, financing</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.report1099()">
                    <div class="card-header">1099 Summary</div>
                    <p style="font-size:13px; color:var(--gray-500);">Vendor payments for 1099 filing</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="BudgetsPage.showVariance()">
                    <div class="card-header">Budget vs Actual</div>
                    <p style="font-size:13px; color:var(--gray-500);">Monthly budget variance analysis</p>
                </div>
            </div>`;
    },

    // ----- Saved Reports (Phase 11) -----

    toggleSaved() {
        const list = $('#saved-reports-list');
        const btn = $('#saved-reports-toggle');
        if (!list || !btn) return;
        const nowHidden = !list.hidden;
        list.hidden = nowHidden;
        btn.textContent = nowHidden ? '▸' : '▾';
        btn.setAttribute('aria-expanded', nowHidden ? 'false' : 'true');
        try { localStorage.setItem('sb_saved_reports_collapsed', nowHidden ? '1' : '0'); } catch (e) { /* ignore */ }
    },

    filterSaved(q) {
        const needle = (q || '').trim().toLowerCase();
        $$('.saved-report-row').forEach(tr => { tr.hidden = needle !== '' && !tr.textContent.toLowerCase().includes(needle); });
    },

    async openSaved(id) {
        try {
            const all = await API.get('/saved-reports');
            const saved = all.find(s => s.id === id);
            if (!saved) { toast('Saved report not found', 'error'); return; }
            const opener = ReportsPage._OPENERS[saved.report_type];
            if (!opener) {
                toast(`No opener registered for "${saved.report_type}"`, 'error');
                return;
            }
            await opener(saved.parameters || {});
        } catch (err) { toast(err.message || 'Failed to open', 'error'); }
    },

    async saveCurrent(reportType, params) {
        const name = prompt('Name for this saved report:');
        if (!name || !name.trim()) return;
        try {
            await API.post('/saved-reports', {
                name: name.trim(),
                report_type: reportType,
                parameters: params || {},
            });
            toast(`Saved as "${name.trim()}" — it is listed under Saved Reports at the top of the Report Center`);
            // Refresh the page so the new one shows in the Saved section
            App.navigate(location.hash);
        } catch (err) { toast(err.message || 'Save failed', 'error'); }
    },

    async deleteSaved(id) {
        if (!confirm('Delete this saved report?')) return;
        try {
            await API.del(`/saved-reports/${id}`);
            toast('Deleted');
            App.navigate(location.hash);
        } catch (err) { toast(err.message || 'Delete failed', 'error'); }
    },

    // ----- Drill-down (Phase 11) -----
    // Hits /api/reports/account-transactions for one account in the date
    // range and shows the journal entries that rolled up into the row the
    // user clicked. Each entry's source_link routes to the originating
    // invoice / bill / payment / journal entry.
    async openDrillDown(accountId, accountName, startDate, endDate) {
        if (!accountId) { toast('No account_id on this row', 'error'); return; }
        const params = new URLSearchParams();
        params.set('account_id', accountId);
        if (startDate) params.set('start_date', startDate);
        if (endDate) params.set('end_date', endDate);

        openModal(`Drill-down — ${accountName}`, `
            <div id="drilldown-body" style="font-size:11px; color:var(--gray-500);">Loading…</div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>
        `);

        try {
            const data = await API.get(`/reports/account-transactions?${params.toString()}`);
            const rows = (data.entries || []).map(e => {
                const src = e.source_link
                    ? `<a href="${escapeHtml(e.source_link)}" style="color:var(--qb-blue,#0066cc); text-decoration:none;">${escapeHtml(e.source_type || '')} #${e.source_id}</a>`
                    : escapeHtml(e.source_type || '');
                return `<tr>
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td>${escapeHtml(e.description || '')}</td>
                    <td>${src}</td>
                    <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ''}</td>
                    <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ''}</td>
                    <td class="amount">${formatCurrency(e.running_balance)}</td>
                </tr>`;
            }).join('');

            $('#drilldown-body').innerHTML = `
                <p style="margin-bottom:8px; color:var(--gray-500); font-size:12px;">
                    ${escapeHtml(data.account.number || '')} · ${escapeHtml(data.account.name)}
                    &middot; ${formatDate(data.start_date)} → ${formatDate(data.end_date)}
                    &middot; Net: <strong>${formatCurrency(data.period_net)}</strong>
                </p>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">Date</th><th scope="col">Ref</th><th scope="col">Description</th><th scope="col">Source</th>
                        <th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Running</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="7" style="text-align:center; color:var(--gray-400);">No entries in range</td></tr>'}</tbody>
                </table></div>`;
        } catch (err) {
            $('#drilldown-body').innerHTML =
                `<div class="empty-state"><p>${escapeHtml(err.message || 'Failed to load drill-down')}</p></div>`;
        }
    },

    periodOptions(selected) {
        const options = [
            ["this_month", "This Month"],
            ["this_quarter", "This Quarter"],
            ["this_year", "This Year"],
            ["this_year_to_date", "This Year to Date"],
            ["last_month", "Last Month"],
            ["last_quarter", "Last Quarter"],
            ["last_year", "Last Year"],
            ["last_year_to_date", "Last Year to Date"],
            ["custom", "Custom Date"],
        ];
        return options.map(([value, label]) =>
            `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
        ).join("");
    },

    _pad(value) {
        return String(value).padStart(2, "0");
    },

    _isoDate(dateObj) {
        return `${dateObj.getFullYear()}-${ReportsPage._pad(dateObj.getMonth() + 1)}-${ReportsPage._pad(dateObj.getDate())}`;
    },

    _quarterStart(monthIndex) {
        return Math.floor(monthIndex / 3) * 3;
    },

    getDateRange(period, customStart = null, customEnd = null) {
        const today = new Date();
        const year = today.getFullYear();
        const month = today.getMonth();
        const day = today.getDate();
        let start;
        let end;

        switch (period) {
            case "this_month":
                start = new Date(year, month, 1);
                end = new Date(year, month + 1, 0);
                break;
            case "this_quarter": {
                const qStart = ReportsPage._quarterStart(month);
                start = new Date(year, qStart, 1);
                end = new Date(year, qStart + 3, 0);
                break;
            }
            case "this_year":
                start = new Date(year, 0, 1);
                end = new Date(year, 11, 31);
                break;
            case "this_year_to_date":
                start = new Date(year, 0, 1);
                end = today;
                break;
            case "last_month":
                start = new Date(year, month - 1, 1);
                end = new Date(year, month, 0);
                break;
            case "last_quarter": {
                const thisQuarterStart = ReportsPage._quarterStart(month);
                start = new Date(year, thisQuarterStart - 3, 1);
                end = new Date(year, thisQuarterStart, 0);
                break;
            }
            case "last_year":
                start = new Date(year - 1, 0, 1);
                end = new Date(year - 1, 11, 31);
                break;
            case "last_year_to_date":
                start = new Date(year - 1, 0, 1);
                end = new Date(year - 1, month, Math.min(day, new Date(year - 1, month + 1, 0).getDate()));
                break;
            case "custom":
                return {
                    start: customStart || ReportsPage._isoDate(new Date(year, 0, 1)),
                    end: customEnd || ReportsPage._isoDate(today),
                };
            default:
                start = new Date(year, 0, 1);
                end = today;
                break;
        }

        return {
            start: ReportsPage._isoDate(start),
            end: ReportsPage._isoDate(end),
        };
    },

    getAsOfDate(period, customEnd = null) {
        if (period === "custom") return customEnd || todayISO();
        return ReportsPage.getDateRange(period).end;
    },

    customRangeHtml(initialStart, initialEnd) {
        return `
            <div id="report-custom-range" style="display:none; margin:4px 0 12px 0; font-size:11px; align-items:center; gap:8px;">
                <label for="report-custom-start">From:</label>
                <input id="report-custom-start" type="date" value="${initialStart}">
                <label for="report-custom-end">To:</label>
                <input id="report-custom-end" type="date" value="${initialEnd}">
            </div>`;
    },

    toggleCustomRange() {
        const select = $("#report-period-select");
        const row = $("#report-custom-range");
        if (!select || !row) return;
        row.style.display = select.value === "custom" ? "flex" : "none";
    },

    async openPeriodModal(title, initialPeriod, loadContent, label = "Dates", useAsOfOnly = false, opts = {}) {
        // opts.reportType (string) — when set, adds an "Add to Saved Reports" button
        // that captures the current period/range as parameters.
        // opts.prefill ({period?, start_date?, end_date?, as_of_date?}) —
        // used when reopening a saved report; overrides initialPeriod and
        // pre-populates the date inputs.
        const reportType = opts.reportType || null;
        const prefill = opts.prefill || {};

        const currentYear = new Date().getFullYear();
        const defaultCustomStart = prefill.start_date || `${currentYear}-01-01`;
        const defaultCustomEnd = prefill.end_date || prefill.as_of_date || todayISO();
        const startingPeriod = prefill.period || initialPeriod;

        const saveBtn = reportType
            ? `<button class="btn btn-secondary" id="report-save-btn">Add to Saved Reports…</button>`
            : '';

        openModal(title, `
            <div class="form-grid" style="margin-bottom:4px;">
                <div class="form-group">
                    <label>${label}</label>
                    <select id="report-period-select">${ReportsPage.periodOptions(startingPeriod)}</select>
                </div>
            </div>
            ${ReportsPage.customRangeHtml(defaultCustomStart, defaultCustomEnd)}
            <div id="report-content">
                <div style="font-size:11px; color:var(--gray-500);">Loading report...</div>
            </div>
            <div class="form-actions">
                ${saveBtn}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);

        const select = $("#report-period-select");
        const startInput = $("#report-custom-start");
        const endInput = $("#report-custom-end");
        const content = $("#report-content");

        // Track current params so the Save button captures fresh values.
        let currentParams = {};

        const render = async () => {
            ReportsPage.toggleCustomRange();
            content.innerHTML = `<div style="font-size:11px; color:var(--gray-500);">Loading report...</div>`;
            try {
                if (useAsOfOnly) {
                    const asOfDate = ReportsPage.getAsOfDate(select.value, endInput.value || todayISO());
                    currentParams = { period: select.value, as_of_date: asOfDate };
                    content.innerHTML = await loadContent(select.value, { as_of_date: asOfDate });
                } else {
                    const range = ReportsPage.getDateRange(select.value, startInput.value, endInput.value);
                    currentParams = { period: select.value, start_date: range.start, end_date: range.end };
                    content.innerHTML = await loadContent(select.value, range);
                }
            } catch (err) {
                content.innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
            }
        };

        select.addEventListener("change", render);
        startInput.addEventListener("change", () => { if (select.value === "custom" && !useAsOfOnly) render(); });
        endInput.addEventListener("change", () => { if (select.value === "custom") render(); });

        if (reportType) {
            const sb = $("#report-save-btn");
            if (sb) sb.addEventListener("click", () => {
                ReportsPage.saveCurrent(reportType, currentParams);
            });
        }

        await render();
    },

    async profitLoss(prefill) {
        await ReportsPage.openPeriodModal(T("Profit & Loss"), "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/profit-loss?start_date=${range.start}&end_date=${range.end}`);
            const pdfBtn = ReportsPage._exportButtons('profit-loss', `start_date=${range.start}&end_date=${range.end}`);
            // Build the onclick payload outside the template so we can
            // HTML-escape the embedded double quotes from JSON.stringify().
            // Otherwise the inner " breaks the outer onclick="…" attribute.
            const drillCall = (i) => escapeHtml(
                `ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_name)},${JSON.stringify(range.start)},${JSON.stringify(range.end)})`
            );
            const section = (items) => {
                if (!items.length) return `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
                return items.map(i =>
                    `<tr><td style="padding-left:24px;">
                        <a href="javascript:void(0)" style="color:var(--qb-blue,#0066cc); text-decoration:none;"
                           onclick="${drillCall(i)}">${escapeHtml(i.account_name)}</a>
                        </td><td class="amount">${formatCurrency(i.amount)}</td></tr>`
                ).join("");
            };
            return `${pdfBtn}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>
                        <tr><td><strong>${T('Income')}</strong></td><td></td></tr>
                        ${section(data.income)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>${T('Total Income')}</td><td class="amount">${formatCurrency(data.total_income)}</td></tr>
                        <tr><td><strong>Cost of Goods Sold</strong></td><td></td></tr>
                        ${section(data.cogs)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Gross Profit</td><td class="amount">${formatCurrency(data.gross_profit)}</td></tr>
                        <tr><td><strong>Expenses</strong></td><td></td></tr>
                        ${section(data.expenses)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Expenses</td><td class="amount">${formatCurrency(data.total_expenses)}</td></tr>
                        <tr style="font-weight:700; font-size:15px; background:var(--primary-light);"><td>${T('Net Income')}</td><td class="amount">${formatCurrency(data.net_income)}</td></tr>
                    </tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'profit_loss', prefill });
    },

    async balanceSheet(prefill) {
        await ReportsPage.openPeriodModal(T("Balance Sheet"), "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/balance-sheet?as_of_date=${params.as_of_date}`);
            const pdfBtn = ReportsPage._exportButtons('balance-sheet', `as_of_date=${params.as_of_date}`);
            const drillCall = (i) => escapeHtml(
                `ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_name)},null,${JSON.stringify(params.as_of_date)})`
            );
            const section = (items) => items.map(i =>
                `<tr><td style="padding-left:24px;">
                    <a href="javascript:void(0)" style="color:var(--qb-blue,#0066cc); text-decoration:none;"
                       onclick="${drillCall(i)}">${escapeHtml(i.account_name)}</a>
                    </td><td class="amount">${formatCurrency(i.amount)}</td></tr>`
            ).join("") || `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
            return `${pdfBtn}
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Assets</strong></td><td></td></tr>
                        ${section(data.assets)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Assets</td><td class="amount">${formatCurrency(data.total_assets)}</td></tr>
                        <tr><td><strong>Liabilities</strong></td><td></td></tr>
                        ${section(data.liabilities)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Liabilities</td><td class="amount">${formatCurrency(data.total_liabilities)}</td></tr>
                        <tr><td><strong>${T('Equity')}</strong></td><td></td></tr>
                        ${section(data.equity)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>${T('Total Equity')}</td><td class="amount">${formatCurrency(data.total_equity)}</td></tr>
                    </tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'balance_sheet', prefill });
    },

    async salesTax(prefill) {
        await ReportsPage.openPeriodModal("Sales Tax Report", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/sales-tax?start_date=${range.start}&end_date=${range.end}`);
            const rows = data.items.map(i =>
                `<tr>
                    <td>${formatDate(i.date)}</td>
                    <td>${escapeHtml(i.invoice_number)}</td>
                    <td>${escapeHtml(i.customer_name)}</td>
                    <td class="amount">${formatCurrency(i.subtotal)}</td>
                    <td class="amount">${(i.tax_rate * 100).toFixed(2)}%</td>
                    <td class="amount">${formatCurrency(i.tax_amount)}</td>
                </tr>`
            ).join("");
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Date</th><th scope="col">${T('Invoice')}</th><th scope="col">${T('Customer')}</th><th scope="col" class="amount">Sales</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Tax</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No taxable sales</td></tr>'}</tbody>
                </table></div>
                <div style="margin-top:12px; padding:8px; background:var(--gray-50); border:1px solid var(--gray-200);">
                    <div style="display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px;">
                        <span>Total Sales: <strong>${formatCurrency(data.total_sales)}</strong></span>
                        <span>Taxable: <strong>${formatCurrency(data.total_taxable)}</strong></span>
                        <span>Non-Taxable: <strong>${formatCurrency(data.total_non_taxable)}</strong></span>
                    </div>
                    <div style="font-size:14px; font-weight:700; color:var(--qb-navy);">Tax Collected: ${formatCurrency(data.total_tax)}</div>
                </div>`;
        }, "Dates", false, { reportType: 'sales_tax', prefill });
    },

    async generalLedger(prefill) {
        await ReportsPage.openPeriodModal("General Ledger", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/general-ledger?start_date=${range.start}&end_date=${range.end}`);
            let html = `${ReportsPage._exportButtons('general-ledger', `start_date=${range.start}&end_date=${range.end}`)}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>`;
            if (data.accounts.length === 0) {
                html += `<div class="empty-state"><p>No journal entries found</p></div>`;
            } else {
                for (const acct of data.accounts) {
                    html += `<h3 style="margin:12px 0 4px; font-size:12px; color:var(--qb-navy);">${escapeHtml(acct.account_number)} &mdash; ${escapeHtml(acct.account_name)}</h3>`;
                    html += `<div class="table-container"><table>
                        <thead><tr><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Reference</th><th scope="col">Source</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Balance</th></tr></thead><tbody>`;
                    html += `<tr style="color:var(--gray-500);"><td></td><td colspan="5">Balance brought forward</td><td class="amount">${formatCurrency(acct.opening_balance)}</td></tr>`;
                    for (const e of acct.entries) {
                        html += `<tr>
                            <td>${formatDate(e.date)}</td>
                            <td>${escapeHtml(e.description)}</td>
                            <td>${escapeHtml(e.reference)}</td>
                            <td style="font-size:10px; color:var(--gray-500);">${escapeHtml(e.source_type)}</td>
                            <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ""}</td>
                            <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ""}</td>
                            <td class="amount">${formatCurrency(e.running_balance)}</td>
                        </tr>`;
                    }
                    html += `<tr style="font-weight:600; background:var(--gray-50);">
                        <td colspan="4">Period total</td>
                        <td class="amount">${formatCurrency(acct.total_debit)}</td>
                        <td class="amount">${formatCurrency(acct.total_credit)}</td>
                        <td class="amount">${formatCurrency(acct.closing_balance)}</td>
                    </tr></tbody></table></div>`;
                }
            }
            return html;
        }, "Dates", false, { reportType: 'general_ledger', prefill });
    },

    async incomeByCustomer(prefill) {
        await ReportsPage.openPeriodModal(T("Income by Customer"), "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/income-by-customer?start_date=${range.start}&end_date=${range.end}`);
            let rows = data.items.map(i =>
                `<tr>
                    <td>${escapeHtml(i.customer_name)}</td>
                    <td class="amount">${i.invoice_count}</td>
                    <td class="amount">${formatCurrency(i.total_sales)}</td>
                    <td class="amount">${formatCurrency(i.total_paid)}</td>
                    <td class="amount">${formatCurrency(i.total_balance)}</td>
                </tr>`
            ).join("");
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td>TOTAL</td>
                <td class="amount">${data.items.reduce((sum, item) => sum + item.invoice_count, 0)}</td>
                <td class="amount">${formatCurrency(data.total_sales)}</td>
                <td class="amount">${formatCurrency(data.total_paid)}</td>
                <td class="amount">${formatCurrency(data.total_balance)}</td>
            </tr>`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">${T('Customer')}</th><th scope="col" class="amount">${T('Invoices')}</th><th scope="col" class="amount">Sales</th><th scope="col" class="amount">Paid</th><th scope="col" class="amount">Balance</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="5" style="text-align:center; color:var(--gray-400);">No sales data</td></tr>'}</tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'income_by_customer', prefill });
    },

    async customerStatementPicker() {
        const customers = await API.get("/customers?active_only=true");
        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
        openModal(T("Customer Statement"), `
            <form onsubmit="ReportsPage.openStatement(event)" data-readonly-ok>
                <!-- minmax(0, …) and width:100%: a select sizes itself to its
                     longest option, and a 120-character customer name pushed
                     As of past the dialog's edge (macbase1, F22). The picked
                     name still shows in full in the open list. -->
                <div class="form-grid" style="grid-template-columns:minmax(0, 2fr) minmax(0, 1fr);">
                    <div class="form-group" style="min-width:0;"><label>${T('Customer')} *</label>
                        <select name="customer_id" required style="width:100%; min-width:0; max-width:100%;"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>As of Date</label>
                        <input name="as_of_date" type="date" value="${todayISO()}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Generate PDF</button>
                </div>
            </form>`);
    },

    openStatement(e) {
        e.preventDefault();
        const form = e.target;
        const cid = form.customer_id.value;
        const asOf = form.as_of_date.value || todayISO();
        window.open(`/api/reports/customer-statement/${cid}/pdf?as_of_date=${asOf}`, "_blank");
        closeModal();
    },

    async arAging(prefill) {
        await ReportsPage.openPeriodModal(T("Accounts Receivable Aging"), "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/ar-aging?as_of_date=${params.as_of_date}`);
            let rows = data.items.map(i =>
                `<tr>
                    <td>${escapeHtml(i.customer_name)}</td>
                    <td class="amount">${formatCurrency(i.current)}</td>
                    <td class="amount">${formatCurrency(i.over_30)}</td>
                    <td class="amount">${formatCurrency(i.over_60)}</td>
                    <td class="amount">${formatCurrency(i.over_90)}</td>
                    <td class="amount" style="font-weight:600;">${formatCurrency(i.total)}</td>
                </tr>`
            ).join("");
            const t = data.totals;
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td>TOTAL</td>
                <td class="amount">${formatCurrency(t.current)}</td>
                <td class="amount">${formatCurrency(t.over_30)}</td>
                <td class="amount">${formatCurrency(t.over_60)}</td>
                <td class="amount">${formatCurrency(t.over_90)}</td>
                <td class="amount">${formatCurrency(t.total)}</td>
            </tr>`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div style="margin-bottom:12px; display:flex; gap:8px;">
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.applyLateFees()">Apply Late Fees</button>
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.batchEmailStatements()">Email All Overdue</button>
                    <select id="collection-letter-type" style="font-size:11px; padding:2px 6px;">
                        <option value="30">30-Day Letter</option>
                        <option value="60">60-Day Letter</option>
                        <option value="90">90-Day Letter</option>
                    </select>
                    <button class="btn btn-sm btn-secondary" onclick="ReportsPage.sendCollectionLetters()">Send Collection Letters</button>
                </div>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">${T('Customer')}</th><th scope="col" class="amount">Current</th><th scope="col" class="amount">1-30</th>
                        <th scope="col" class="amount">31-60</th><th scope="col" class="amount">61-90+</th><th scope="col" class="amount">Total</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No outstanding receivables</td></tr>'}</tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'ar_aging', prefill });
    },

    async apAging(prefill) {
        await ReportsPage.openPeriodModal("Accounts Payable Aging", "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/ap-aging?as_of_date=${params.as_of_date}`);
            let rows = data.items.map(i =>
                `<tr>
                    <td>${escapeHtml(i.vendor_name)}</td>
                    <td class="amount">${formatCurrency(i.current)}</td>
                    <td class="amount">${formatCurrency(i.over_30)}</td>
                    <td class="amount">${formatCurrency(i.over_60)}</td>
                    <td class="amount">${formatCurrency(i.over_90)}</td>
                    <td class="amount" style="font-weight:600;">${formatCurrency(i.total)}</td>
                </tr>`
            ).join("");
            const t = data.totals;
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td>TOTAL</td>
                <td class="amount">${formatCurrency(t.current)}</td>
                <td class="amount">${formatCurrency(t.over_30)}</td>
                <td class="amount">${formatCurrency(t.over_60)}</td>
                <td class="amount">${formatCurrency(t.over_90)}</td>
                <td class="amount">${formatCurrency(t.total)}</td>
            </tr>`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div class="table-container"><table>
                    <thead><tr>
                        <th scope="col">Vendor</th><th scope="col" class="amount">Current</th><th scope="col" class="amount">1-30</th>
                        <th scope="col" class="amount">31-60</th><th scope="col" class="amount">61-90+</th><th scope="col" class="amount">Total</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No outstanding payables</td></tr>'}</tbody>
                </table></div>`;
        }, "As Of", true, { reportType: 'ap_aging', prefill });
    },

    async trialBalance() {
        await ReportsPage.openPeriodModal("Trial Balance", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/trial-balance?start_date=${range.start}&end_date=${range.end}`);
            let rows = data.items.map(i =>
                `<tr>
                    <td>${escapeHtml(i.account_number)}</td>
                    <td>${escapeHtml(i.account_name)}</td>
                    <td style="font-size:10px; color:var(--gray-400);">${i.account_type}</td>
                    <td class="amount">${i.total_debit > 0 ? formatCurrency(i.total_debit) : ''}</td>
                    <td class="amount">${i.total_credit > 0 ? formatCurrency(i.total_credit) : ''}</td>
                    <td class="amount">${formatCurrency(i.net_balance)}</td>
                </tr>`
            ).join('');
            const diffColor = Math.abs(data.difference) < 0.01 ? 'var(--success)' : 'var(--danger)';
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="3">TOTALS</td>
                <td class="amount">${formatCurrency(data.total_debit)}</td>
                <td class="amount">${formatCurrency(data.total_credit)}</td>
                <td class="amount" style="color:${diffColor}">${formatCurrency(data.difference)}</td>
            </tr>`;
            return `${ReportsPage._exportButtons('trial-balance', `start_date=${range.start}&end_date=${range.end}`)}
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Number</th><th scope="col">Account</th><th scope="col">Type</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col" class="amount">Net</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        });
    },

    async cashFlow(prefill) {
        await ReportsPage.openPeriodModal("Cash Flow Statement", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/cash-flow?start_date=${range.start}&end_date=${range.end}`);
            const section = (title, items, total) => {
                let html = `<tr><td><strong>${title}</strong></td><td></td></tr>`;
                if (items.length === 0) {
                    html += `<tr><td style="padding-left:24px; color:var(--gray-400);">None</td><td></td></tr>`;
                } else {
                    html += items.map(i =>
                        `<tr><td style="padding-left:24px;">${escapeHtml(i.account_name)}</td><td class="amount">${formatCurrency(i.amount)}</td></tr>`
                    ).join('');
                }
                html += `<tr style="font-weight:600; background:var(--gray-50);"><td>Total ${title}</td><td class="amount">${formatCurrency(total)}</td></tr>`;
                return html;
            };
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>
                        ${section('Operating Activities', data.operating, data.total_operating)}
                        ${section('Investing Activities', data.investing, data.total_investing)}
                        ${section('Financing Activities', data.financing, data.total_financing)}
                        <tr style="font-weight:700; font-size:15px; background:var(--primary-light);">
                            <td>Net Change in Cash</td><td class="amount">${formatCurrency(data.net_change)}</td>
                        </tr>
                    </tbody>
                </table></div>`;
        }, "Dates", false, { reportType: 'cash_flow', prefill });
    },

    async report1099() {
        const currentYear = new Date().getFullYear();
        openModal('1099 Summary', `
            <div class="form-grid" style="margin-bottom:12px;">
                <div class="form-group"><label>Year</label>
                    <input id="report-1099-year" type="number" value="${currentYear}" style="width:100px;"></div>
                <div class="form-group" style="align-self:end;">
                    <button class="btn btn-primary" onclick="ReportsPage.load1099()">Generate</button></div>
            </div>
            <div id="report-1099-content"><div style="font-size:11px; color:var(--gray-500);">Select year and click Generate</div></div>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async load1099() {
        const year = $('#report-1099-year').value;
        const content = $('#report-1099-content');
        content.innerHTML = '<div style="font-size:11px; color:var(--gray-500);">Loading...</div>';
        try {
            const data = await API.get(`/reports/1099-summary?year=${year}`);
            if (data.items.length === 0) {
                content.innerHTML = '<div class="empty-state"><p>No 1099 vendors found. Flag vendors as 1099 in the Vendors page.</p></div>';
                return;
            }
            let rows = data.items.map(i =>
                `<tr${i.above_threshold ? ' style="background:var(--primary-light);"' : ''}>
                    <td>${escapeHtml(i.vendor_name)}</td>
                    <td>${escapeHtml(i.tax_id)}</td>
                    <td>${escapeHtml(i.vendor_1099_type)}</td>
                    <td class="amount">${formatCurrency(i.total_paid)}</td>
                    <td>${i.above_threshold ? '<span style="color:var(--danger); font-weight:700;">REPORT</span>' : ''}</td>
                </tr>`
            ).join('');
            rows += `<tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="3">TOTAL</td><td class="amount">${formatCurrency(data.total)}</td>
                <td>${data.vendors_above_threshold} vendor(s) above $${data.threshold}</td></tr>`;
            content.innerHTML = `
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Vendor</th><th scope="col">Tax ID</th><th scope="col">Type</th><th scope="col" class="amount">Total Paid</th><th scope="col">Status</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        } catch (err) { content.innerHTML = `<div style="color:var(--danger);">${escapeHtml(err.message)}</div>`; }
    },

    async applyLateFees() {
        if (!confirm('Apply late fees to all overdue invoices past the grace period?')) return;
        try {
            const result = await API.post('/invoices/apply-late-fees');
            toast(`Late fees applied to ${result.applied} of ${result.total_overdue} overdue invoices`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async batchEmailStatements() {
        if (!confirm('Email statements to all customers with overdue invoices?')) return;
        try {
            const result = await API.post('/reports/batch-email-statements');
            let msg = `Sent ${result.sent} statements`;
            if (result.failed > 0) msg += `, ${result.failed} failed`;
            toast(msg);
        } catch (err) { toast(err.message, 'error'); }
    },

    async sendCollectionLetters() {
        const letterType = $('#collection-letter-type')?.value || '30';
        if (!confirm(`Send ${letterType}-day collection letters to all qualifying customers?`)) return;
        try {
            const result = await API.post('/reports/collection-letters', {
                letter_type: letterType,
                send_email: true,
            });
            toast(`Generated ${result.generated} letters, emailed ${result.emailed}`);
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Class tracking: Profit & Loss split by the class dimension.
ReportsPage.profitLossByClass = async function () {
    await ReportsPage.openPeriodModal(T("P&L by Class"), "this_year_to_date", async (_period, range) => {
        const data = await API.get(`/reports/profit-loss-by-class?start_date=${range.start}&end_date=${range.end}`);
        const rows = data.classes.map(c => `<tr>
            <td>${escapeHtml(c.class_name)}</td>
            <td class="amount">${formatCurrency(c.income)}</td>
            <td class="amount">${formatCurrency(c.cogs)}</td>
            <td class="amount">${formatCurrency(c.gross_profit)}</td>
            <td class="amount">${formatCurrency(c.expenses)}</td>
            <td class="amount" style="font-weight:700;">${formatCurrency(c.net_income)}</td>
        </tr>`).join('');
        return `
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Class')}</th><th scope="col" class="amount">${T('Income')}</th><th scope="col" class="amount">COGS</th>
                <th scope="col" class="amount">Gross Profit</th><th scope="col" class="amount">Expenses</th><th scope="col" class="amount">${T('Net Income')}</th></tr></thead>
                <tbody>${rows.length ? rows : '<tr><td colspan="6">No activity in this period</td></tr>'}</tbody>
                <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                    <td>Total</td>
                    <td class="amount">${formatCurrency(data.total_income)}</td>
                    <td></td><td></td>
                    <td class="amount">${formatCurrency(data.total_expenses)}</td>
                    <td class="amount">${formatCurrency(data.total_net_income)}</td>
                </tr></tfoot>
            </table></div>`;
    });
};

// Fixed assets: register totals per type for GL reconciliation.
ReportsPage.fixedAssetReconciliation = async function () {
    const data = await API.get('/fixed-assets/reports/reconciliation');
    const rows = data.types.map(t => `<tr>
        <td>${escapeHtml(t.asset_type)}</td>
        <td class="amount">${t.asset_count}</td>
        <td class="amount">${formatCurrency(t.cost)}</td>
        <td class="amount">${formatCurrency(t.accumulated_depreciation)}</td>
        <td class="amount">${formatCurrency(t.book_value)}</td>
    </tr>`).join('');
    openModal('Fixed Asset Reconciliation', `
        <div class="table-container"><table>
            <thead><tr><th scope="col">Asset Type</th><th scope="col" class="amount">Assets</th><th scope="col" class="amount">Cost</th>
            <th scope="col" class="amount">Accum. Depr.</th><th scope="col" class="amount">Book Value</th></tr></thead>
            <tbody>${rows.length ? rows : '<tr><td colspan="5">No registered assets</td></tr>'}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td>Total</td><td></td>
                <td class="amount">${formatCurrency(data.total_cost)}</td>
                <td class="amount">${formatCurrency(data.total_accumulated)}</td>
                <td class="amount">${formatCurrency(data.total_book_value)}</td>
            </tr></tfoot>
        </table></div>
        <div style="font-size:11px; color:var(--gray-500); margin-top:8px;">
            Compare against the mapped fixed-asset and accumulated-depreciation
            GL accounts — differences mean unposted acquisitions or manual GL edits.
        </div>`);
};

// Financial statements pack — one PDF with P&L, Balance Sheet, Trial Balance.
ReportsPage.financialStatementsPdf = async function () {
    await ReportsPage.openPeriodModal("Financial Statements Pack", "this_year_to_date", async (_period, range) => {
        window.open(`/api/reports/financial-statements/pdf?start_date=${range.start}&end_date=${range.end}`, '_blank');
        return `<div style="font-size:12px;">The statements pack opened in a new tab —
            ${T('P&L')} and Trial Balance for ${escapeHtml(range.start)} — ${escapeHtml(range.end)},
            ${T('Balance Sheet')} as of ${escapeHtml(range.end)}. Paper size follows
            Settings → Report PDF Paper Size.</div>`;
    });
};

ReportsPage.jobProfitability = async function () {
    await ReportsPage.openPeriodModal(T("Job Profitability"), "this_year_to_date", async (_period, range) => {
        const data = await API.get(`/reports/job-profitability?start_date=${range.start}&end_date=${range.end}`);
        const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(1)}%`;
        const rows = data.jobs.map(j => `<tr ${j.job_id ? `style="cursor:pointer" onclick="closeModal();App.navigate('#/jobs');JobsPage.showDetails(${j.job_id})"` : ''}>
            <td>${escapeHtml(j.customer_name || '')}</td>
            <td>${escapeHtml(j.job_name)}</td>
            <td class="amount">${j.contract_amount !== null && j.contract_amount !== undefined ? formatCurrency(j.contract_amount) : ''}</td>
            <td class="amount">${formatCurrency(j.income)}</td>
            <td class="amount">${formatCurrency(j.total_costs)}</td>
            <td class="amount" style="font-weight:700;">${formatCurrency(j.net_income)}</td>
            <td class="amount">${pct(j.margin_pct)}</td>
        </tr>`).join('');
        return `
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                ${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)} · ${Terms.text('"No job" holds untagged activity')} <em>and</em> ${Terms.text('the applied-cost credits behind Job Cost Entries (labor, equipment, overhead applied to jobs), so its costs can be negative and the totals still match the P&L')}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">${T('Job')}</th><th scope="col" class="amount">Contract</th><th scope="col" class="amount">${T('Income')}</th>
                <th scope="col" class="amount">Costs</th><th scope="col" class="amount">Net</th><th scope="col" class="amount">Margin</th></tr></thead>
                <tbody>${rows.length ? rows : '<tr><td colspan="7">No activity in this period</td></tr>'}</tbody>
                <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                    <td colspan="3">Total</td>
                    <td class="amount">${formatCurrency(data.total_income)}</td>
                    <td class="amount">${formatCurrency(data.total_costs)}</td>
                    <td class="amount">${formatCurrency(data.total_net_income)}</td>
                    <td></td>
                </tr></tfoot>
            </table></div>`;
    });
};

ReportsPage.jobBudgetVsActual = async function () {
    await ReportsPage.openPeriodModal(T("Job Budget vs Actual"), "this_year_to_date", async (_period, range) => {
        const data = await API.get(`/jobs/budget-vs-actual?start_date=${range.start}&end_date=${range.end}`);
        const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(1)}%`;
        const t = { revised: 0, committed: 0, actual: 0, projected: 0, variance: 0, act_revenue: 0 };
        const rows = data.map(j => {
            for (const k of Object.keys(t)) t[k] += j[k] || 0;
            return `<tr style="cursor:pointer" onclick="closeModal();App.navigate('#/jobs/${j.job_id}')">
            <td>${escapeHtml(j.customer_name || '')}</td>
            <td>${escapeHtml(j.job_name)}</td>
            <td class="amount">${formatCurrency(j.revised)}</td>
            <td class="amount">${formatCurrency(j.committed)}</td>
            <td class="amount">${formatCurrency(j.actual)}</td>
            <td class="amount">${formatCurrency(j.projected)}</td>
            <td class="amount" style="font-weight:700;color:${j.revised && j.variance < 0 ? '#a4242b' : 'inherit'}">${formatCurrency(j.variance)}</td>
            <td class="amount">${pct(j.pct_used)}</td>
            <td class="amount">${formatCurrency(j.act_revenue)}</td>
        </tr>`; }).join('');
        return `
            <div style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">
                Actuals for ${escapeHtml(range.start)} — ${escapeHtml(range.end)}; budgets and committed cost are job-to-date. Click a job to drill down.
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">${T('Job')}</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th>
                <th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th><th scope="col" class="amount">Revenue</th></tr></thead>
                <tbody>${rows.length ? rows : '<tr><td colspan="9">No jobs</td></tr>'}</tbody>
                <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                    <td colspan="2">Total</td>
                    <td class="amount">${formatCurrency(t.revised)}</td><td class="amount">${formatCurrency(t.committed)}</td>
                    <td class="amount">${formatCurrency(t.actual)}</td><td class="amount">${formatCurrency(t.projected)}</td>
                    <td class="amount">${formatCurrency(t.variance)}</td><td></td><td class="amount">${formatCurrency(t.act_revenue)}</td>
                </tr></tfoot>
            </table></div>`;
    });
};



// ---------------------------------------------------------------------------
// Nonprofit statements — shown in place of the P&L and Balance Sheet cards
// when Settings -> Company Type is nonprofit. Each reconciles to the plain
// P&L / balance sheet (the server computes both from the same lines).
// ---------------------------------------------------------------------------
ReportsPage._nonprofitCards = function () {
    return `
        <div class="card" style="cursor:pointer" onclick="ReportsPage.statementOfActivities()">
            <div class="card-header">Statement of Activities</div>
            <p style="font-size:13px; color:var(--gray-500);">Revenue, releases and expenses, with and without donor restrictions</p>
        </div>
        <div class="card" style="cursor:pointer" onclick="ReportsPage.statementOfFinancialPosition()">
            <div class="card-header">Statement of Financial Position</div>
            <p style="font-size:13px; color:var(--gray-500);">Assets, liabilities, and net assets by restriction</p>
        </div>
        <div class="card" style="cursor:pointer" onclick="ReportsPage.fundBalances()">
            <div class="card-header">Fund Balances</div>
            <p style="font-size:13px; color:var(--gray-500);">Each restricted fund: beginning, contributions, spent, released, ending</p>
        </div>
        <div class="card" style="cursor:pointer" onclick="ReportsPage.functionalExpenses()">
            <div class="card-header">Statement of Functional Expenses</div>
            <p style="font-size:13px; color:var(--gray-500);">Program / management / fundraising by expense account (Form 990 Part IX)</p>
        </div>
        <div class="card" style="cursor:pointer" onclick="ReportsPage.pledges()">
            <div class="card-header">Pledge Report</div>
            <p style="font-size:13px; color:var(--gray-500);">Promised, received, written off and outstanding by donor and campaign</p>
        </div>
        <div class="card" style="cursor:pointer" onclick="ReportsPage.givingStatements()">
            <div class="card-header">Year-End Giving Statements</div>
            <p style="font-size:13px; color:var(--gray-500);">One statement per donor for the tax year — print the stack or email them all</p>
        </div>`;
};

ReportsPage.givingStatements = function () {
    const y = new Date().getFullYear();
    const opts = [y, y - 1, y - 2].map(v => `<option value="${v}" ${v === y - 1 ? 'selected' : ''}>${v}</option>`).join('');
    openModal('Year-End Giving Statements', `
        <div class="form-grid">
            <div class="form-group"><label>Tax year</label><select id="gs-year">${opts}</select></div>
        </div>
        <p style="font-size:12px;color:var(--gray-500);margin:8px 0;">Every ${T('customer')} with a gift in the year gets a statement: cash contributions with the deductible portion, non-cash gifts described without a value. ${T('Customers')} who opted out in their record are skipped when emailing.</p>
        <div id="gs-result" style="font-size:12px;margin:8px 0;"></div>
        <div class="form-actions">
            <button class="btn btn-secondary" onclick="window.open('/api/donors/giving-statements/pdf?year=' + $('#gs-year').value, '_blank')">Download all (PDF)</button>
            <button class="btn btn-primary" onclick="ReportsPage.emailGivingStatements()">Email all</button>
            <button class="btn btn-secondary" onclick="closeModal()">Close</button>
        </div>`);
};

ReportsPage.emailGivingStatements = async function () {
    const year = parseInt($('#gs-year').value);
    if (!confirm(`Email ${year} giving statements to every ${T('customer')} with a gift that year?`)) return;
    const box = $('#gs-result');
    box.textContent = 'Sending…';
    try {
        const r = await API.post('/donors/giving-statements/batch-email', { year });
        box.innerHTML = `Sent ${r.sent}, failed ${r.failed}, skipped ${r.skipped}.` + (r.errors.length ? `<ul>${r.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : '');
    } catch (err) { box.textContent = err.message; }
};

ReportsPage.pledges = async function (prefill) {
    await ReportsPage.openPeriodModal("Pledge Report", "this_year_to_date", async (_period, range) => {
        const qs = `start_date=${range.start}&end_date=${range.end}`;
        const d = await API.get(`/reports/pledges?${qs}`);
        const cols = ['pledged', 'invoiced', 'not_yet_invoiced', 'received', 'written_off', 'outstanding'];
        const cells = (r) => cols.map(c => `<td class="amount">${formatCurrency(r[c])}</td>`).join('');
        const donors = d.by_donor.map(g => `<tr style="font-weight:600; background:var(--gray-50);"><td>${escapeHtml(g.customer_name)}</td>${cells(g)}</tr>`
            + g.pledges.map(p => `<tr><td style="padding-left:24px;">${escapeHtml(p.label)} <span style="color:var(--gray-500)">· ${escapeHtml(p.class_name)}</span></td>${cells(p)}</tr>`).join('')).join('');
        const classes = d.by_class.map(g => `<tr><td>${escapeHtml(g.class_name)}</td>${cells(g)}</tr>`).join('');
        const head = `<thead><tr><th scope="col"></th><th scope="col" class="amount">Pledged</th><th scope="col" class="amount">Invoiced</th><th scope="col" class="amount">Not yet invoiced</th><th scope="col" class="amount">Received</th><th scope="col" class="amount">Written off</th><th scope="col" class="amount">Outstanding</th></tr></thead>`;
        const foot = `<tfoot><tr style="font-weight:700; background:var(--gray-50);"><td>Total</td>${cells(d.totals)}</tr></tfoot>`;
        return `${ReportsPage._exportButtons('pledges', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}</p>
            <h4 style="margin:8px 0 4px;font-size:12px;">By ${T('customer')}</h4>
            <div class="table-container"><table>${head}<tbody>${donors || `<tr><td colspan="7" style="color:var(--gray-400);">No pledges in this period</td></tr>`}</tbody>${foot}</table></div>
            <h4 style="margin:12px 0 4px;font-size:12px;">By campaign (${T('class')})</h4>
            <div class="table-container"><table>${head}<tbody>${classes || `<tr><td colspan="7" style="color:var(--gray-400);">—</td></tr>`}</tbody></table></div>`;
    }, "Dates", false, { reportType: 'pledges', prefill });
};

ReportsPage._exportButtons = function (path, qs) {
    return `<div style="text-align:right; margin-bottom:6px; margin-left:auto;">
        <button class="btn btn-sm btn-secondary" onclick="window.open('/api/reports/${path}/pdf?${qs}','_blank')">Save PDF</button>
        <button class="btn btn-sm btn-secondary" onclick="window.open('/api/reports/${path}/csv?${qs}','_blank')">Save CSV</button>
    </div>`;
};

// Prior-year comparison for the two statements a treasurer reads side by
// side. The choice is remembered for the session and rides on the export
// links, so the PDF and CSV carry the same columns as the screen.
ReportsPage._compare = { statement_of_activities: false, functional_expenses: false };
ReportsPage.compareToggleHtml = function (key) {
    return `<label style="font-weight:normal; font-size:12px; margin-right:auto;"><input type="checkbox" ${ReportsPage._compare[key] ? 'checked' : ''} onchange="ReportsPage._compare['${key}']=this.checked; $('#report-period-select').dispatchEvent(new Event('change'))"> Compare to prior year</label>`;
};

ReportsPage.statementOfActivities = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Activities", "this_year_to_date", async (_period, range) => {
        const cmp = ReportsPage._compare.statement_of_activities;
        const qs = `start_date=${range.start}&end_date=${range.end}${cmp ? '&compare=prior_year' : ''}`;
        const d = await API.get(`/reports/statement-of-activities?${qs}`);
        const t = d.totals;
        const pt = cmp && d.prior ? d.prior.totals : {};
        const cols = cmp ? 6 : 4;
        const line = (label, w, r, tot, prior, style = '') => `<tr style="${style}"><td>${label}</td><td class="amount">${formatCurrency(w)}</td><td class="amount">${formatCurrency(r)}</td><td class="amount">${formatCurrency(tot)}</td>${cmp ? `<td class="amount">${formatCurrency(prior || 0)}</td><td class="amount">${formatCurrency((tot || 0) - (prior || 0))}</td>` : ''}</tr>`;
        const rows = (items) => items.length ? items.map(i => line(`<span style="padding-left:24px">${escapeHtml(i.account_name)}</span>`, i.without, i.with, i.total, i.prior_total)).join('') : `<tr><td colspan="${cols}" style="color:var(--gray-400);">None</td></tr>`;
        const head = (label) => `<tr><td><strong>${label}</strong></td>${'<td></td>'.repeat(cols - 1)}</tr>`;
        return `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">${ReportsPage.compareToggleHtml('statement_of_activities')}${ReportsPage._exportButtons('statement-of-activities', qs)}</div>
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}${cmp && d.prior ? ` · prior year ${formatDate(d.prior.start_date)} &mdash; ${formatDate(d.prior.end_date)}` : ''}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col"></th><th scope="col" class="amount">Without Donor Restrictions</th><th scope="col" class="amount">With Donor Restrictions</th><th scope="col" class="amount">Total</th>${cmp ? '<th scope="col" class="amount">Prior year</th><th scope="col" class="amount">Change</th>' : ''}</tr></thead>
                <tbody>
                    ${head('Revenue &amp; Support')}
                    ${rows(d.revenue)}
                    ${line('Total Revenue &amp; Support', t.revenue_without, t.revenue_with, t.revenue, pt.revenue, 'font-weight:600; background:var(--gray-50);')}
                    ${line('Net assets released from restrictions', d.releases.without, d.releases.with, 0, 0)}
                    ${head('Expenses')}
                    ${rows(d.expenses)}
                    ${line('Total Expenses', t.expenses, 0, t.expenses, pt.expenses, 'font-weight:600; background:var(--gray-50);')}
                    ${line('Change in Net Assets', t.change_without, t.change_with, t.change_total, pt.change_total, 'font-weight:700; font-size:15px; background:var(--primary-light);')}
                </tbody>
            </table></div>`;
    }, "Dates", false, { reportType: 'statement_of_activities', prefill });
};

ReportsPage.statementOfFinancialPosition = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Financial Position", "this_year_to_date", async (_period, params) => {
        const qs = `as_of_date=${params.as_of_date}`;
        const d = await API.get(`/reports/statement-of-financial-position?${qs}`);
        const drillCall = (i) => escapeHtml(`ReportsPage.openDrillDown(${i.account_id},${JSON.stringify(i.account_name)},null,${JSON.stringify(params.as_of_date)})`);
        const section = (items) => items.map(i => `<tr><td style="padding-left:24px;">
                ${i.account_id ? `<a href="javascript:void(0)" style="color:var(--qb-blue,#0066cc); text-decoration:none;" onclick="${drillCall(i)}">${escapeHtml(i.account_name)}</a>` : escapeHtml(i.account_name)}
                </td><td class="amount">${formatCurrency(i.amount)}</td></tr>`).join('') || `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
        const sub = (label, v) => `<tr style="font-weight:600; background:var(--gray-50);"><td>${label}</td><td class="amount">${formatCurrency(v)}</td></tr>`;
        return `${ReportsPage._exportButtons('statement-of-financial-position', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(d.as_of_date)}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>
                    <tr><td><strong>Assets</strong></td><td></td></tr>${section(d.assets)}${sub('Total Assets', d.total_assets)}
                    <tr><td><strong>Liabilities</strong></td><td></td></tr>${section(d.liabilities)}${sub('Total Liabilities', d.total_liabilities)}
                    <tr><td><strong>Net Assets</strong></td><td></td></tr>${section(d.net_assets)}
                    ${sub('Net Assets Without Donor Restrictions', d.net_assets_without)}
                    ${sub('Net Assets With Donor Restrictions', d.net_assets_with)}
                    ${sub('Total Net Assets', d.total_net_assets)}
                    <tr style="font-weight:700; font-size:15px; background:var(--primary-light);"><td>Liabilities + Net Assets</td><td class="amount">${formatCurrency(d.total_liabilities_and_net_assets)}</td></tr>
                </tbody>
            </table></div>`;
    }, "As of", true, { reportType: 'statement_of_financial_position', prefill });
};

ReportsPage.fundBalances = async function (prefill) {
    await ReportsPage.openPeriodModal("Fund Balances", "this_year_to_date", async (_period, range) => {
        const qs = `start_date=${range.start}&end_date=${range.end}`;
        const d = await API.get(`/reports/fund-balances?${qs}`);
        const keys = ['beginning', 'contributions', 'expenses', 'releases', 'ending', 'unreleased'];
        const row = (f, style = '') => `<tr style="${style}"><td>${escapeHtml(f.class_name)}${f.donor_name ? `<div style="font-size:10px;color:var(--gray-500)">${escapeHtml(f.donor_name)}</div>` : ''}</td>${keys.map(k => `<td class="amount">${formatCurrency(f[k])}</td>`).join('')}</tr>`;
        const rows = d.funds.map(f => row(f)).join('') + (d.unassigned ? row(d.unassigned, 'font-style:italic') : '');
        return `${ReportsPage._exportButtons('fund-balances', qs)}
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)} · restricted ${T('classes')} only</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Class')}</th><th scope="col" class="amount">Beginning</th><th scope="col" class="amount">Contributions</th><th scope="col" class="amount">Spent</th><th scope="col" class="amount">Released</th><th scope="col" class="amount">Ending</th><th scope="col" class="amount" title="Spent but not yet released">Unreleased</th></tr></thead>
                <tbody>${rows || `<tr><td colspan="7" style="color:var(--gray-400);">No restricted ${T('classes')} yet</td></tr>`}</tbody>
                <tfoot>${row({ class_name: 'Total', ...d.totals }, 'font-weight:700; background:var(--gray-50);')}</tfoot>
            </table></div>`;
    }, "Dates", false, { reportType: 'fund_balances', prefill });
};

ReportsPage.functionalExpenses = async function (prefill) {
    await ReportsPage.openPeriodModal("Statement of Functional Expenses", "this_year_to_date", async (_period, range) => {
        const cmp = ReportsPage._compare.functional_expenses;
        const qs = `start_date=${range.start}&end_date=${range.end}${cmp ? '&compare=prior_year' : ''}`;
        const d = await API.get(`/reports/functional-expenses?${qs}`);
        const keys = ['total', 'program', 'management', 'fundraising', 'unassigned'];
        const pt = cmp && d.prior ? d.prior.totals : {};
        const row = (label, r, style = '', prior = null) => { const pv = prior != null ? prior : (r.prior_total || 0); return `<tr style="${style}"><td>${label}</td>${keys.map(k => `<td class="amount">${formatCurrency(r[k])}</td>`).join('')}${cmp ? `<td class="amount">${formatCurrency(pv)}</td><td class="amount">${formatCurrency(r.total - pv)}</td>` : ''}</tr>`; };
        const rows = d.rows.map(r => row(escapeHtml(`${r.account_number || ''} ${r.account_name}`.trim()), r)).join('');
        const programs = d.programs.length ? `<h4 style="margin:12px 0 4px;font-size:12px;">Program services by program</h4>
            <div class="table-container"><table><thead><tr><th scope="col">Program</th><th scope="col" class="amount">Amount</th></tr></thead>
            <tbody>${d.programs.map(p => `<tr><td>${escapeHtml(p.class_name)}</td><td class="amount">${formatCurrency(p.amount)}</td></tr>`).join('')}</tbody></table></div>` : '';
        return `<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">${ReportsPage.compareToggleHtml('functional_expenses')}${ReportsPage._exportButtons('functional-expenses', qs)}</div>
            <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(d.start_date)} &mdash; ${formatDate(d.end_date)}${cmp && d.prior ? ` · prior year ${formatDate(d.prior.start_date)} &mdash; ${formatDate(d.prior.end_date)}` : ''}${d.totals.unassigned ? ` · <span style="color:var(--danger)">${formatCurrency(d.totals.unassigned)} still unassigned — run an allocation rule</span>` : ''}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Expense</th><th scope="col" class="amount">Total</th><th scope="col" class="amount">Program</th><th scope="col" class="amount">Management</th><th scope="col" class="amount">Fundraising</th><th scope="col" class="amount">Unassigned</th>${cmp ? '<th scope="col" class="amount">Prior year</th><th scope="col" class="amount">Change</th>' : ''}</tr></thead>
                <tbody>${rows || `<tr><td colspan="${cmp ? 8 : 6}" style="color:var(--gray-400);">No expenses in this period</td></tr>`}</tbody>
                <tfoot>${row('Total', d.totals, 'font-weight:700; background:var(--gray-50);', pt.total || 0)}</tfoot>
            </table></div>${programs}`;
    }, "Dates", false, { reportType: 'functional_expenses', prefill });
};
