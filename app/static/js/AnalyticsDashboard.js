// ============================================================================
// Slowbooks Pro 2026 — Analytics Dashboard
// Vanilla ES2020. No framework, no build step, no external chart lib (yet).
// Hits GET /api/analytics/dashboard on init and renders KPIs + two tables.
// ============================================================================

class AnalyticsDashboard {
    constructor() {
        this.data = null;
    }

    async init() {
        const root = document.getElementById('analytics-root');
        try {
            const response = await fetch('/api/analytics/dashboard');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            this.data = await response.json();
            this.render();
        } catch (err) {
            if (root) {
                root.innerHTML = `
                    <div style="padding:20px;background:#2a1e1e;border:1px solid #ff4757;border-radius:8px;color:#ff9f9f;">
                        <strong>Failed to load analytics.</strong>
                        <div style="margin-top:8px;font-size:12px;opacity:0.8;">${err.message}</div>
                    </div>`;
            }
            // eslint-disable-next-line no-console
            console.error('[Analytics] load failed:', err);
        }
    }

    fmt(n) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 0,
        }).format(n || 0);
    }

    sum(obj) {
        return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
    }

    render() {
        this.renderKPIs();
        this.renderRevenueTable();
        this.renderArAging();
    }

    renderKPIs() {
        const totalRevenue = this.sum(this.data.revenue_by_customer);
        const totalExpenses = this.sum(this.data.expenses_by_category);
        const dso = typeof this.data.dso === 'number' ? this.data.dso : 0;
        const margin = totalRevenue > 0
            ? ((totalRevenue - totalExpenses) / totalRevenue) * 100
            : 0;

        const html = `
            <div class="kpi-grid">
                <div class="kpi-card">
                    <div class="kpi-label">Revenue (MTD)</div>
                    <div class="kpi-value kpi-green">${this.fmt(totalRevenue)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Expenses (MTD)</div>
                    <div class="kpi-value kpi-red">${this.fmt(totalExpenses)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">DSO (Days)</div>
                    <div class="kpi-value kpi-blue">${dso.toFixed(1)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Margin %</div>
                    <div class="kpi-value kpi-purple">${margin.toFixed(1)}%</div>
                </div>
            </div>
        `;
        document.getElementById('kpis').innerHTML = html;
    }

    renderRevenueTable() {
        const entries = Object.entries(this.data.revenue_by_customer || {})
            .sort((a, b) => b[1] - a[1]);

        if (entries.length === 0) {
            document.getElementById('revenue-table').innerHTML =
                '<div class="empty">No paid invoices this period.</div>';
            return;
        }

        const rows = entries.map(([customer, revenue]) => `
            <tr>
                <td>${this.escape(customer)}</td>
                <td class="num green">${this.fmt(revenue)}</td>
            </tr>
        `).join('');

        document.getElementById('revenue-table').innerHTML = `
            <table class="analytics-table">
                <thead>
                    <tr>
                        <th>Customer</th>
                        <th class="num">Revenue</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
    }

    renderArAging() {
        const aging = this.data.ar_aging || {};
        const customers = new Set([
            ...Object.keys(aging.current || {}),
            ...Object.keys(aging['30'] || {}),
            ...Object.keys(aging['60'] || {}),
            ...Object.keys(aging['90'] || {}),
        ]);

        if (customers.size === 0) {
            document.getElementById('ar-aging').innerHTML =
                '<div class="empty">No outstanding A/R.</div>';
            return;
        }

        const rows = Array.from(customers).sort().map(customer => {
            const cur = (aging.current && aging.current[customer]) || 0;
            const d30 = (aging['30'] && aging['30'][customer]) || 0;
            const d60 = (aging['60'] && aging['60'][customer]) || 0;
            const d90 = (aging['90'] && aging['90'][customer]) || 0;
            return `
                <tr>
                    <td>${this.escape(customer)}</td>
                    <td class="num">${this.fmt(cur)}</td>
                    <td class="num">${this.fmt(d30)}</td>
                    <td class="num">${this.fmt(d60)}</td>
                    <td class="num red">${this.fmt(d90)}</td>
                </tr>
            `;
        }).join('');

        document.getElementById('ar-aging').innerHTML = `
            <table class="analytics-table">
                <thead>
                    <tr>
                        <th>Customer</th>
                        <th class="num">Current</th>
                        <th class="num">30+</th>
                        <th class="num">60+</th>
                        <th class="num">90+</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;
    }

    escape(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
}

const dashboard = new AnalyticsDashboard();
dashboard.init();
