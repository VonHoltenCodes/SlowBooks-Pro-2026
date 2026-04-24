/**
 * Budget vs Actual — spreadsheet-style budget entry with variance reporting
 * Phase 10: Quick Wins + Medium Effort Features
 */
const BudgetsPage = {
    _year: new Date().getFullYear(),

    async render() {
        const year = BudgetsPage._year;
        const accounts = await API.get('/accounts');
        const budgetAccounts = accounts.filter(a => ['expense', 'income', 'cogs'].includes(a.account_type));
        const budgets = await API.get(`/budgets?year=${year}`);

        // Build lookup: account_id -> { month: amount }
        const budgetMap = {};
        for (const b of budgets) {
            if (!budgetMap[b.account_id]) budgetMap[b.account_id] = {};
            budgetMap[b.account_id][b.month] = b.amount;
        }

        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        let rows = '';
        for (const acct of budgetAccounts) {
            rows += `<tr data-acct="${acct.id}">
                <td style="font-weight:600; white-space:nowrap;">${escapeHtml(acct.account_number)} ${escapeHtml(acct.name)}</td>`;
            for (let m = 1; m <= 12; m++) {
                const val = budgetMap[acct.id]?.[m] || '';
                rows += `<td><input type="number" step="0.01" class="budget-cell" data-acct="${acct.id}" data-month="${m}"
                    value="${val}" style="width:70px; padding:2px 4px; font-size:11px; text-align:right;"></td>`;
            }
            rows += '</tr>';
        }

        return `
            <div class="page-header">
                <h2>Budget vs Actual</h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="BudgetsPage.changeYear(-1)">&laquo; ${year-1}</button>
                    <span style="padding:6px 12px; font-weight:700;">${year}</span>
                    <button class="btn btn-secondary" onclick="BudgetsPage.changeYear(1)">${year+1} &raquo;</button>
                    <button class="btn btn-primary" onclick="BudgetsPage.saveAll()">Save All</button>
                    <button class="btn btn-secondary" onclick="BudgetsPage.showVariance()">View Variance</button>
                </div>
            </div>
            <p style="font-size:11px; color:var(--text-muted); margin-bottom:8px;">
                Enter monthly budget amounts for each account. Click "Save All" to persist changes.
            </p>
            <div class="table-container" style="overflow-x:auto;">
                <table style="font-size:11px;">
                    <thead><tr>
                        <th style="min-width:180px;">Account</th>
                        ${months.map(m => `<th class="amount" style="min-width:80px;">${m}</th>`).join('')}
                    </tr></thead>
                    <tbody>${rows || '<tr><td colspan="13">No budgetable accounts found</td></tr>'}</tbody>
                </table>
            </div>`;
    },

    changeYear(delta) {
        BudgetsPage._year += delta;
        App.navigate('#/budgets');
    },

    async saveAll() {
        const items = [];
        $$('.budget-cell').forEach(input => {
            const val = parseFloat(input.value);
            if (!isNaN(val) && val !== 0) {
                items.push({
                    account_id: parseInt(input.dataset.acct),
                    year: BudgetsPage._year,
                    month: parseInt(input.dataset.month),
                    amount: val,
                });
            }
        });
        try {
            const result = await API.post('/budgets/bulk', items);
            toast(`Saved ${result.saved} budget entries`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showVariance() {
        const year = BudgetsPage._year;
        await ReportsPage.openPeriodModal(`Budget vs Actual — ${year}`, 'this_year', async () => {
            const data = await API.get(`/budgets/variance?year=${year}`);
            if (data.accounts.length === 0) {
                return '<div class="empty-state"><p>No budgets set for this year. Enter budgets first.</p></div>';
            }
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            let html = '<div style="overflow-x:auto;">';
            for (const acct of data.accounts) {
                html += `<h3 style="margin:12px 0 4px; font-size:12px; color:var(--qb-navy);">${escapeHtml(acct.account_number)} — ${escapeHtml(acct.account_name)}</h3>`;
                html += `<table style="font-size:10px; margin-bottom:12px;"><thead><tr><th></th>`;
                html += months.map(m => `<th class="amount">${m}</th>`).join('');
                html += '<th class="amount" style="font-weight:700;">Total</th></tr></thead><tbody>';
                html += '<tr><td>Budget</td>';
                html += acct.months.map(m => `<td class="amount">${formatCurrency(m.budget)}</td>`).join('');
                html += `<td class="amount" style="font-weight:700;">${formatCurrency(acct.total_budget)}</td></tr>`;
                html += '<tr><td>Actual</td>';
                html += acct.months.map(m => `<td class="amount">${formatCurrency(m.actual)}</td>`).join('');
                html += `<td class="amount" style="font-weight:700;">${formatCurrency(acct.total_actual)}</td></tr>`;
                html += '<tr style="font-weight:600;"><td>Variance</td>';
                html += acct.months.map(m => {
                    const color = m.variance >= 0 ? 'var(--success)' : 'var(--danger)';
                    return `<td class="amount" style="color:${color}">${formatCurrency(m.variance)}</td>`;
                }).join('');
                const totalColor = acct.total_variance >= 0 ? 'var(--success)' : 'var(--danger)';
                html += `<td class="amount" style="font-weight:700; color:${totalColor}">${formatCurrency(acct.total_variance)}</td></tr>`;
                html += '</tbody></table>';
            }
            html += '</div>';
            return html;
        });
    },
};
