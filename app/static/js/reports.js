/**
 * Decompiled from QBW32.EXE!CReportEngine + CReportViewer  Offset: 0x00210000
 * The original report engine was actually impressive - it had its own query
 * language ("QBReportQuery") that got compiled to Btrieve API calls. The
 * P&L report alone generated 14 separate Btrieve operations. We just use SQL.
 * CReportViewer was an OLE container that hosted a Crystal Reports 8.5 OCX
 * for print preview. We do not miss Crystal Reports.
 */
const ReportsPage = {
    async render() {
        return `
            <div class="page-header"><h2>Reports</h2></div>
            <div class="card-grid">
                <div class="card" style="cursor:pointer" onclick="ReportsPage.profitLoss()">
                    <div class="card-header">Profit & Loss</div>
                    <p style="font-size:13px; color:var(--gray-500);">Income vs expenses for a period</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.balanceSheet()">
                    <div class="card-header">Balance Sheet</div>
                    <p style="font-size:13px; color:var(--gray-500);">Assets, liabilities, and equity</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.arAging()">
                    <div class="card-header">A/R Aging</div>
                    <p style="font-size:13px; color:var(--gray-500);">Outstanding receivables by age</p>
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
                    <div class="card-header">Income by Customer</div>
                    <p style="font-size:13px; color:var(--gray-500);">Sales totals per customer</p>
                </div>
                <div class="card" style="cursor:pointer" onclick="ReportsPage.customerStatementPicker()">
                    <div class="card-header">Customer Statement</div>
                    <p style="font-size:13px; color:var(--gray-500);">Invoice/payment history PDF</p>
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

    async openPeriodModal(title, initialPeriod, loadContent, label = "Dates", useAsOfOnly = false) {
        const currentYear = new Date().getFullYear();
        const defaultCustomStart = `${currentYear}-01-01`;
        const defaultCustomEnd = todayISO();

        openModal(title, `
            <div class="form-grid" style="margin-bottom:4px;">
                <div class="form-group">
                    <label>${label}</label>
                    <select id="report-period-select">${ReportsPage.periodOptions(initialPeriod)}</select>
                </div>
            </div>
            ${ReportsPage.customRangeHtml(defaultCustomStart, defaultCustomEnd)}
            <div id="report-content">
                <div style="font-size:11px; color:var(--gray-500);">Loading report...</div>
            </div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);

        const select = $("#report-period-select");
        const startInput = $("#report-custom-start");
        const endInput = $("#report-custom-end");
        const content = $("#report-content");

        const render = async () => {
            ReportsPage.toggleCustomRange();
            content.innerHTML = `<div style="font-size:11px; color:var(--gray-500);">Loading report...</div>`;
            try {
                if (useAsOfOnly) {
                    const asOfDate = ReportsPage.getAsOfDate(select.value, endInput.value || todayISO());
                    content.innerHTML = await loadContent(select.value, { as_of_date: asOfDate });
                } else {
                    const range = ReportsPage.getDateRange(select.value, startInput.value, endInput.value);
                    content.innerHTML = await loadContent(select.value, range);
                }
            } catch (err) {
                content.innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
            }
        };

        select.addEventListener("change", render);
        startInput.addEventListener("change", () => { if (select.value === "custom" && !useAsOfOnly) render(); });
        endInput.addEventListener("change", () => { if (select.value === "custom") render(); });
        await render();
    },

    async profitLoss() {
        await ReportsPage.openPeriodModal("Profit & Loss", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/profit-loss?start_date=${range.start}&end_date=${range.end}`);
            const section = (items) => {
                if (!items.length) return `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
                return items.map(i =>
                    `<tr><td style="padding-left:24px;">${escapeHtml(i.account_name)}</td><td class="amount">${formatCurrency(Math.abs(i.amount))}</td></tr>`
                ).join("");
            };
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th>Account</th><th class="amount">Amount</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Income</strong></td><td></td></tr>
                        ${section(data.income)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Income</td><td class="amount">${formatCurrency(data.total_income)}</td></tr>
                        <tr><td><strong>Cost of Goods Sold</strong></td><td></td></tr>
                        ${section(data.cogs)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Gross Profit</td><td class="amount">${formatCurrency(data.gross_profit)}</td></tr>
                        <tr><td><strong>Expenses</strong></td><td></td></tr>
                        ${section(data.expenses)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Expenses</td><td class="amount">${formatCurrency(data.total_expenses)}</td></tr>
                        <tr style="font-weight:700; font-size:15px; background:var(--primary-light);"><td>Net Income</td><td class="amount">${formatCurrency(data.net_income)}</td></tr>
                    </tbody>
                </table></div>`;
        });
    },

    async balanceSheet() {
        await ReportsPage.openPeriodModal("Balance Sheet", "this_year_to_date", async (_period, params) => {
            const data = await API.get(`/reports/balance-sheet?as_of_date=${params.as_of_date}`);
            const section = (items) => items.map(i =>
                `<tr><td style="padding-left:24px;">${escapeHtml(i.account_name)}</td><td class="amount">${formatCurrency(Math.abs(i.amount))}</td></tr>`
            ).join("") || `<tr><td colspan="2" style="color:var(--gray-400);">None</td></tr>`;
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">As of ${formatDate(data.as_of_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th>Account</th><th class="amount">Amount</th></tr></thead>
                    <tbody>
                        <tr><td><strong>Assets</strong></td><td></td></tr>
                        ${section(data.assets)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Assets</td><td class="amount">${formatCurrency(data.total_assets)}</td></tr>
                        <tr><td><strong>Liabilities</strong></td><td></td></tr>
                        ${section(data.liabilities)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Liabilities</td><td class="amount">${formatCurrency(data.total_liabilities)}</td></tr>
                        <tr><td><strong>Equity</strong></td><td></td></tr>
                        ${section(data.equity)}
                        <tr style="font-weight:600; background:var(--gray-50);"><td>Total Equity</td><td class="amount">${formatCurrency(data.total_equity)}</td></tr>
                    </tbody>
                </table></div>`;
        }, "As Of", true);
    },

    async salesTax() {
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
                    <thead><tr><th>Date</th><th>Invoice</th><th>Customer</th><th class="amount">Sales</th><th class="amount">Rate</th><th class="amount">Tax</th></tr></thead>
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
        });
    },

    async generalLedger() {
        await ReportsPage.openPeriodModal("General Ledger", "this_year_to_date", async (_period, range) => {
            const data = await API.get(`/reports/general-ledger?start_date=${range.start}&end_date=${range.end}`);
            let html = `<p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>`;
            if (data.accounts.length === 0) {
                html += `<div class="empty-state"><p>No journal entries found</p></div>`;
            } else {
                for (const acct of data.accounts) {
                    html += `<h3 style="margin:12px 0 4px; font-size:12px; color:var(--qb-navy);">${escapeHtml(acct.account_number)} &mdash; ${escapeHtml(acct.account_name)}</h3>`;
                    html += `<div class="table-container"><table>
                        <thead><tr><th>Date</th><th>Description</th><th>Reference</th><th class="amount">Debit</th><th class="amount">Credit</th></tr></thead><tbody>`;
                    for (const e of acct.entries) {
                        html += `<tr>
                            <td>${formatDate(e.date)}</td>
                            <td>${escapeHtml(e.description)}</td>
                            <td>${escapeHtml(e.reference)}</td>
                            <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ""}</td>
                            <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ""}</td>
                        </tr>`;
                    }
                    html += `<tr style="font-weight:600; background:var(--gray-50);">
                        <td colspan="3">Total</td>
                        <td class="amount">${formatCurrency(acct.total_debit)}</td>
                        <td class="amount">${formatCurrency(acct.total_credit)}</td>
                    </tr></tbody></table></div>`;
                }
            }
            return html;
        });
    },

    async incomeByCustomer() {
        await ReportsPage.openPeriodModal("Income by Customer", "this_year_to_date", async (_period, range) => {
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
                    <thead><tr><th>Customer</th><th class="amount">Invoices</th><th class="amount">Sales</th><th class="amount">Paid</th><th class="amount">Balance</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="5" style="text-align:center; color:var(--gray-400);">No sales data</td></tr>'}</tbody>
                </table></div>`;
        });
    },

    async customerStatementPicker() {
        const customers = await API.get("/customers?active_only=true");
        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
        openModal("Customer Statement", `
            <form onsubmit="ReportsPage.openStatement(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required><option value="">Select...</option>${custOpts}</select></div>
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

    async arAging() {
        await ReportsPage.openPeriodModal("Accounts Receivable Aging", "this_year_to_date", async (_period, params) => {
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
                        <th>Customer</th><th class="amount">Current</th><th class="amount">1-30</th>
                        <th class="amount">31-60</th><th class="amount">61-90+</th><th class="amount">Total</th>
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="6" style="text-align:center; color:var(--gray-400);">No outstanding receivables</td></tr>'}</tbody>
                </table></div>`;
        }, "As Of", true);
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
            return `
                <p style="margin-bottom:12px; color:var(--gray-500);">${formatDate(data.start_date)} &mdash; ${formatDate(data.end_date)}</p>
                <div class="table-container"><table>
                    <thead><tr><th>Number</th><th>Account</th><th>Type</th><th class="amount">Debit</th><th class="amount">Credit</th><th class="amount">Net</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`;
        });
    },

    async cashFlow() {
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
                    <thead><tr><th>Account</th><th class="amount">Amount</th></tr></thead>
                    <tbody>
                        ${section('Operating Activities', data.operating, data.total_operating)}
                        ${section('Investing Activities', data.investing, data.total_investing)}
                        ${section('Financing Activities', data.financing, data.total_financing)}
                        <tr style="font-weight:700; font-size:15px; background:var(--primary-light);">
                            <td>Net Change in Cash</td><td class="amount">${formatCurrency(data.net_change)}</td>
                        </tr>
                    </tbody>
                </table></div>`;
        });
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
                    <thead><tr><th>Vendor</th><th>Tax ID</th><th>Type</th><th class="amount">Total Paid</th><th>Status</th></tr></thead>
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
