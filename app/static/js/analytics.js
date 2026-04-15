/**
 * ============================================================================
 * Slowbooks Pro 2026 — Analytics SPA Page (Phase 9, inline integration)
 *
 * Lives inside the main SPA shell as a hash-routed page (#/analytics),
 * following the same conventions as reports.js / bills.js / banking.js:
 *
 *   AnalyticsPage.render()  →  returns an HTML string. The router
 *                              assigns it to #page-content.innerHTML.
 *
 * Because Chart.js needs canvas elements to exist in the DOM before
 * Chart(ctx, ...) is called, render() queues a microtask (via
 * setTimeout 0) that fires after the router's assignment, attaches
 * event listeners, and builds the charts.
 *
 * Chart types used (all from self-hosted /static/js/chart.umd.js — no CDN):
 *   * Revenue trend ........ line chart (12 calendar months)
 *   * Expenses ............. doughnut chart (by account)
 *   * A/R + A/P aging ...... stacked horizontal bar chart
 *   * Cash forecast ........ dual-line (collections vs payments) + net bar
 * ============================================================================
 */

const AnalyticsPage = {
    // Persistent state — survives route navigation so users return to their
    // last-selected period.
    state: {
        period: 'month',
        data: null,
        charts: {},
    },

    // ------------------------------------------------------------------
    // Entry point — router calls this, we return an HTML string and then
    // finish wiring up the page in a post-render microtask.
    // ------------------------------------------------------------------
    async render() {
        let data;
        let errMsg = null;
        try {
            data = await API.get(
                `/analytics/dashboard?period=${encodeURIComponent(this.state.period)}`
            );
            this.state.data = data;
        } catch (err) {
            errMsg = err && err.message ? err.message : String(err);
            data = null;
        }

        // Schedule post-innerHTML work: bind listeners + init charts.
        setTimeout(() => this._postRender(), 0);

        return this._buildHtml(data, errMsg);
    },

    // ------------------------------------------------------------------
    // HTML skeleton
    // ------------------------------------------------------------------
    _buildHtml(data, errMsg) {
        if (errMsg) {
            return `
                <div class="page-header"><h2>Analytics</h2></div>
                <div class="analytics-error">
                    <strong>Failed to load analytics.</strong>
                    <div>${escapeHtml(errMsg)}</div>
                </div>`;
        }

        const period = (data && data.period) || {};
        const periodLabel = period.name
            ? `${period.name.toUpperCase()} &middot; ${period.start} &rarr; ${period.end}`
            : '';

        const totalRevenue = this._sum(data.revenue_by_customer);
        const totalExpenses = this._sum(data.expenses_by_category);
        const dso = Number(data.dso) || 0;
        const margin = totalRevenue > 0
            ? ((totalRevenue - totalExpenses) / totalRevenue) * 100
            : 0;

        return `
            <div class="page-header analytics-header">
                <h2>Analytics</h2>
                <div class="analytics-controls">
                    <label for="analytics-period">Period</label>
                    <select id="analytics-period">
                        <option value="month"${this.state.period === 'month' ? ' selected' : ''}>Month to Date</option>
                        <option value="quarter"${this.state.period === 'quarter' ? ' selected' : ''}>Quarter to Date</option>
                        <option value="year"${this.state.period === 'year' ? ' selected' : ''}>Year to Date</option>
                    </select>
                    <button class="btn btn-secondary btn-sm" id="analytics-refresh" title="Refresh (R)">&#x21bb; Refresh</button>
                    <button class="btn btn-secondary btn-sm" id="analytics-csv" title="Export CSV">Export CSV</button>
                    <button class="btn btn-primary btn-sm"   id="analytics-pdf" title="Export PDF">Export PDF</button>
                </div>
            </div>

            <div class="analytics-meta">${periodLabel}</div>

            <div class="analytics-kpi-grid">
                <div class="analytics-kpi"><div class="analytics-kpi-label">Revenue</div><div class="analytics-kpi-value kpi-green">${formatCurrency(totalRevenue)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">Expenses</div><div class="analytics-kpi-value kpi-red">${formatCurrency(totalExpenses)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">DSO (Days)</div><div class="analytics-kpi-value kpi-blue">${dso.toFixed(1)}</div></div>
                <div class="analytics-kpi"><div class="analytics-kpi-label">Margin %</div><div class="analytics-kpi-value kpi-purple">${margin.toFixed(1)}%</div></div>
            </div>

            <div class="analytics-section-title">Revenue Trend &mdash; Last 12 Months</div>
            <div class="analytics-card"><div class="chart-wrap"><canvas id="chart-revenue-trend"></canvas></div></div>

            <div class="analytics-two-col">
                <div class="analytics-card">
                    <div class="analytics-section-title">Revenue by Customer</div>
                    ${this._revenueTable(data.revenue_by_customer)}
                </div>
                <div class="analytics-card">
                    <div class="analytics-section-title">Expenses by Category</div>
                    <div class="chart-wrap chart-wrap-sm"><canvas id="chart-expenses"></canvas></div>
                    ${this._expensesTable(data.expenses_by_category)}
                </div>
            </div>

            <div class="analytics-section-title">Accounts Receivable Aging</div>
            <div class="analytics-card">
                <div class="chart-wrap chart-wrap-sm"><canvas id="chart-ar-aging"></canvas></div>
                ${this._agingTable(data.ar_aging, 'Customer')}
            </div>

            <div class="analytics-section-title">Accounts Payable Aging</div>
            <div class="analytics-card">
                <div class="chart-wrap chart-wrap-sm"><canvas id="chart-ap-aging"></canvas></div>
                ${this._agingTable(data.ap_aging, 'Vendor')}
            </div>

            <div class="analytics-section-title">90-Day Cash Forecast</div>
            <div class="analytics-card">
                <div class="chart-wrap"><canvas id="chart-cash-forecast"></canvas></div>
                ${this._cashForecastTable(data.cash_forecast)}
            </div>
        `;
    },

    // ------------------------------------------------------------------
    // Tables (rendered inside _buildHtml, so no DOM lookups needed)
    // ------------------------------------------------------------------
    _revenueTable(byCustomer) {
        const entries = Object.entries(byCustomer || {}).sort((a, b) => b[1] - a[1]);
        if (entries.length === 0) {
            return '<div class="analytics-empty">No paid invoices this period.</div>';
        }
        const rows = entries.map(([c, r]) => `
            <tr><td>${escapeHtml(c)}</td><td class="amount green">${formatCurrency(r)}</td></tr>
        `).join('');
        return `
            <div class="table-container">
                <table>
                    <thead><tr><th>Customer</th><th class="amount">Revenue</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    },

    _expensesTable(byCategory) {
        const entries = Object.entries(byCategory || {}).sort((a, b) => b[1] - a[1]);
        if (entries.length === 0) {
            return '<div class="analytics-empty">No paid bills this period.</div>';
        }
        const rows = entries.map(([c, a]) => `
            <tr><td>${escapeHtml(c)}</td><td class="amount red">${formatCurrency(a)}</td></tr>
        `).join('');
        return `
            <div class="table-container">
                <table>
                    <thead><tr><th>Category</th><th class="amount">Expense</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    },

    _agingTable(aging, nameHeader) {
        aging = aging || {};
        const names = new Set([
            ...Object.keys(aging.current || {}),
            ...Object.keys(aging['30']   || {}),
            ...Object.keys(aging['60']   || {}),
            ...Object.keys(aging['90']   || {}),
        ]);
        if (names.size === 0) {
            return '<div class="analytics-empty">Nothing outstanding.</div>';
        }
        const rows = Array.from(names).map(name => {
            const cur = (aging.current && aging.current[name]) || 0;
            const d30 = (aging['30']   && aging['30'][name])   || 0;
            const d60 = (aging['60']   && aging['60'][name])   || 0;
            const d90 = (aging['90']   && aging['90'][name])   || 0;
            return { name, cur, d30, d60, d90, total: cur + d30 + d60 + d90 };
        }).sort((a, b) => b.total - a.total);

        let tCur = 0, t30 = 0, t60 = 0, t90 = 0, tAll = 0;
        const bodyRows = rows.map(r => {
            tCur += r.cur; t30 += r.d30; t60 += r.d60; t90 += r.d90; tAll += r.total;
            return `
                <tr>
                    <td>${escapeHtml(r.name)}</td>
                    <td class="amount">${formatCurrency(r.cur)}</td>
                    <td class="amount amber">${formatCurrency(r.d30)}</td>
                    <td class="amount amber">${formatCurrency(r.d60)}</td>
                    <td class="amount red">${formatCurrency(r.d90)}</td>
                    <td class="amount green"><strong>${formatCurrency(r.total)}</strong></td>
                </tr>
            `;
        }).join('');

        const totalsRow = `
            <tr class="totals-row">
                <td><strong>TOTAL</strong></td>
                <td class="amount"><strong>${formatCurrency(tCur)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t30)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t60)}</strong></td>
                <td class="amount"><strong>${formatCurrency(t90)}</strong></td>
                <td class="amount green"><strong>${formatCurrency(tAll)}</strong></td>
            </tr>
        `;

        return `
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>${escapeHtml(nameHeader)}</th>
                            <th class="amount">Current</th>
                            <th class="amount">30+</th>
                            <th class="amount">60+</th>
                            <th class="amount">90+</th>
                            <th class="amount">Total</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}${totalsRow}</tbody>
                </table>
            </div>
        `;
    },

    _cashForecastTable(forecast) {
        const rows = forecast || [];
        if (rows.length === 0) {
            return '<div class="analytics-empty">No forecast data.</div>';
        }
        const bodyRows = rows.map(r => {
            const net = Number(r.net) || 0;
            const netCls = net >= 0 ? 'green' : 'red';
            return `
                <tr>
                    <td>${escapeHtml(r.date)}</td>
                    <td class="amount green">${formatCurrency(r.collections)}</td>
                    <td class="amount red">${formatCurrency(r.payments)}</td>
                    <td class="amount ${netCls}"><strong>${formatCurrency(net)}</strong></td>
                </tr>
            `;
        }).join('');
        return `
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Due by</th>
                            <th class="amount">Expected Collections</th>
                            <th class="amount">Expected Payments</th>
                            <th class="amount">Net</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}</tbody>
                </table>
            </div>
        `;
    },

    // ------------------------------------------------------------------
    // Post-render: listeners + Chart.js init
    // ------------------------------------------------------------------
    _postRender() {
        // Attach control listeners. Guard every lookup so a missing id
        // during error rendering doesn't throw.
        const sel = $('#analytics-period');
        if (sel) {
            sel.addEventListener('change', () => {
                this.state.period = sel.value;
                this._reload();
            });
        }
        const refresh = $('#analytics-refresh');
        if (refresh) refresh.addEventListener('click', () => this._reload());
        const csv = $('#analytics-csv');
        if (csv) csv.addEventListener('click', () => this._download('csv'));
        const pdf = $('#analytics-pdf');
        if (pdf) pdf.addEventListener('click', () => this._download('pdf'));

        // Charts
        if (this.state.data && typeof Chart !== 'undefined') {
            this._destroyCharts();
            this._renderRevenueChart();
            this._renderExpensesChart();
            this._renderAgingChart('chart-ar-aging', this.state.data.ar_aging, 'A/R', '#00c48f');
            this._renderAgingChart('chart-ap-aging', this.state.data.ap_aging, 'A/P', '#ff6b6b');
            this._renderCashForecastChart();
        }
    },

    async _reload() {
        App.setStatus('Loading Analytics...');
        const html = await this.render();
        $('#page-content').innerHTML = html;
        App.setStatus('Analytics — Ready');
    },

    _download(kind) {
        const url = `/api/analytics/export.${kind}?period=${encodeURIComponent(this.state.period)}`;
        // Use a transient anchor so the browser honors Content-Disposition.
        const a = document.createElement('a');
        a.href = url;
        a.download = '';
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => a.remove(), 100);
        toast(`${kind.toUpperCase()} download started`, 'success');
    },

    _destroyCharts() {
        Object.values(this.state.charts).forEach(c => {
            try { c.destroy(); } catch (_) {}
        });
        this.state.charts = {};
    },

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------
    _chartDefaults() {
        // Read theme from <html data-theme> to pick label/grid colors.
        const isDark = (document.documentElement.getAttribute('data-theme') || 'light') === 'dark';
        return {
            text: isDark ? '#e6e6f0' : '#1a1a28',
            grid: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
            muted: isDark ? '#a0a0b8' : '#5a5a70',
        };
    },

    _renderRevenueChart() {
        const ctx = $('#chart-revenue-trend');
        if (!ctx) return;
        const trend = this.state.data.revenue_trend || {};
        const labels = Object.keys(trend);
        const values = labels.map(k => Number(trend[k]) || 0);
        const theme = this._chartDefaults();

        this.state.charts.revenue = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Monthly Paid Revenue',
                    data: values,
                    borderColor: '#00c48f',
                    backgroundColor: 'rgba(0,196,143,0.15)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 4,
                    pointHoverRadius: 6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: theme.text } },
                    tooltip: {
                        callbacks: {
                            label: (c) => formatCurrency(c.parsed.y),
                        },
                    },
                },
                scales: {
                    x: { ticks: { color: theme.muted }, grid: { color: theme.grid } },
                    y: {
                        ticks: {
                            color: theme.muted,
                            callback: (v) => formatCurrency(v),
                        },
                        grid: { color: theme.grid },
                        beginAtZero: true,
                    },
                },
            },
        });
    },

    _renderExpensesChart() {
        const ctx = $('#chart-expenses');
        if (!ctx) return;
        const byCat = this.state.data.expenses_by_category || {};
        const entries = Object.entries(byCat).sort((a, b) => b[1] - a[1]);
        if (entries.length === 0) return;
        const theme = this._chartDefaults();

        const palette = ['#ff6b6b', '#5b7fff', '#ffa94d', '#a855f7',
                         '#00c48f', '#e879f9', '#38bdf8', '#facc15'];

        this.state.charts.expenses = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: entries.map(e => e[0]),
                datasets: [{
                    data: entries.map(e => e[1]),
                    backgroundColor: entries.map((_, i) => palette[i % palette.length]),
                    borderColor: 'transparent',
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'right', labels: { color: theme.text } },
                    tooltip: {
                        callbacks: {
                            label: (c) => `${c.label}: ${formatCurrency(c.parsed)}`,
                        },
                    },
                },
            },
        });
    },

    _renderAgingChart(canvasId, aging, label, hueHint) {
        const ctx = $('#' + canvasId);
        if (!ctx) return;
        aging = aging || {};
        const buckets = ['current', '30', '60', '90'];
        const names = Array.from(new Set(
            buckets.flatMap(b => Object.keys(aging[b] || {}))
        ));
        if (names.length === 0) return;

        const theme = this._chartDefaults();
        const bucketColors = {
            current: '#00c48f',
            '30': '#ffa94d',
            '60': '#ff922b',
            '90': '#ff4757',
        };
        const bucketLabels = {
            current: 'Current',
            '30': '30+ days',
            '60': '60+ days',
            '90': '90+ days',
        };

        const datasets = buckets.map(b => ({
            label: bucketLabels[b],
            data: names.map(n => (aging[b] && aging[b][n]) || 0),
            backgroundColor: bucketColors[b],
            borderColor: 'transparent',
            stack: 'aging',
        }));

        this.state.charts[canvasId] = new Chart(ctx, {
            type: 'bar',
            data: { labels: names, datasets },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'top', labels: { color: theme.text } },
                    tooltip: {
                        callbacks: {
                            label: (c) => `${c.dataset.label}: ${formatCurrency(c.parsed.x)}`,
                        },
                    },
                    title: {
                        display: true,
                        text: `${label} Aging (stacked balances)`,
                        color: theme.text,
                    },
                },
                scales: {
                    x: {
                        stacked: true,
                        ticks: { color: theme.muted, callback: (v) => formatCurrency(v) },
                        grid: { color: theme.grid },
                    },
                    y: {
                        stacked: true,
                        ticks: { color: theme.muted },
                        grid: { color: theme.grid },
                    },
                },
            },
        });
    },

    _renderCashForecastChart() {
        const ctx = $('#chart-cash-forecast');
        if (!ctx) return;
        const forecast = this.state.data.cash_forecast || [];
        if (forecast.length === 0) return;
        const theme = this._chartDefaults();

        const labels = forecast.map(r => r.date);
        const collections = forecast.map(r => Number(r.collections) || 0);
        const payments = forecast.map(r => Number(r.payments) || 0);
        const net = forecast.map(r => Number(r.net) || 0);

        this.state.charts.cash = new Chart(ctx, {
            data: {
                labels,
                datasets: [
                    {
                        type: 'line',
                        label: 'Collections (cumulative)',
                        data: collections,
                        borderColor: '#00c48f',
                        backgroundColor: 'rgba(0,196,143,0.15)',
                        fill: false,
                        tension: 0.3,
                        pointRadius: 3,
                    },
                    {
                        type: 'line',
                        label: 'Payments (cumulative)',
                        data: payments,
                        borderColor: '#ff6b6b',
                        backgroundColor: 'rgba(255,107,107,0.15)',
                        fill: false,
                        tension: 0.3,
                        pointRadius: 3,
                    },
                    {
                        type: 'bar',
                        label: 'Net',
                        data: net,
                        backgroundColor: net.map(v => v >= 0 ? 'rgba(0,196,143,0.35)' : 'rgba(255,107,107,0.35)'),
                        borderColor: 'transparent',
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: theme.text } },
                    tooltip: {
                        callbacks: {
                            label: (c) => `${c.dataset.label}: ${formatCurrency(c.parsed.y)}`,
                        },
                    },
                },
                scales: {
                    x: { ticks: { color: theme.muted }, grid: { color: theme.grid } },
                    y: {
                        ticks: { color: theme.muted, callback: (v) => formatCurrency(v) },
                        grid: { color: theme.grid },
                        beginAtZero: true,
                    },
                },
            },
        });
    },

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
    _sum(obj) {
        return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
    },
};
