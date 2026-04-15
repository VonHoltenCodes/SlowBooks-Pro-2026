// ============================================================================
// Slowbooks Pro 2026 — Analytics Dashboard (Phase 9, enhanced)
// Vanilla ES2020. No framework, no build, no external chart library.
// Renders: period selector, refresh, CSV download, theme toggle, 4 KPI cards,
// 12-month revenue sparkline, revenue-by-customer + expenses tables,
// A/R + A/P aging tables, 14-week cash-flow forecast table.
// ============================================================================

(function () {
    'use strict';

    const THEME_KEY = 'slowbooks-theme';
    const DEFAULT_PERIOD = 'month';

    class AnalyticsDashboard {
        constructor() {
            this.period = DEFAULT_PERIOD;
            this.data = null;
            this.loading = false;
        }

        async init() {
            this.bindControls();
            this.syncThemeButton();
            await this.load();
        }

        // ------------------------------------------------------------------
        // Controls
        // ------------------------------------------------------------------

        bindControls() {
            const sel = document.getElementById('period-select');
            if (sel) {
                sel.value = this.period;
                sel.addEventListener('change', () => {
                    this.period = sel.value;
                    this.load();
                });
            }

            const refresh = document.getElementById('refresh-btn');
            if (refresh) refresh.addEventListener('click', () => this.load());

            const csv = document.getElementById('csv-btn');
            if (csv) csv.addEventListener('click', () => this.downloadCsv());

            const theme = document.getElementById('theme-btn');
            if (theme) theme.addEventListener('click', () => this.toggleTheme());

            // Keyboard shortcuts: R=refresh, T=theme
            document.addEventListener('keydown', (e) => {
                if (e.target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
                if (e.key === 'r' || e.key === 'R') { this.load(); e.preventDefault(); }
                if (e.key === 't' || e.key === 'T') { this.toggleTheme(); e.preventDefault(); }
            });
        }

        toggleTheme() {
            const root = document.documentElement;
            const current = root.getAttribute('data-theme') || 'dark';
            const next = current === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
            this.syncThemeButton();
        }

        syncThemeButton() {
            const btn = document.getElementById('theme-btn');
            if (!btn) return;
            const current = document.documentElement.getAttribute('data-theme') || 'dark';
            btn.textContent = current === 'dark' ? '☀ Light' : '☾ Dark';
        }

        // ------------------------------------------------------------------
        // Data loading
        // ------------------------------------------------------------------

        async load() {
            if (this.loading) return;
            this.loading = true;
            const errBox = document.getElementById('error-container');
            if (errBox) errBox.innerHTML = '';

            const refreshBtn = document.getElementById('refresh-btn');
            if (refreshBtn) refreshBtn.disabled = true;

            try {
                const resp = await fetch(`/api/analytics/dashboard?period=${encodeURIComponent(this.period)}`);
                if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
                this.data = await resp.json();
                this.render();
                this.updateTimestamp();
            } catch (err) {
                this.showError(err);
            } finally {
                this.loading = false;
                if (refreshBtn) refreshBtn.disabled = false;
            }
        }

        downloadCsv() {
            // The browser will follow the Content-Disposition header from
            // the server and trigger a download.
            const url = `/api/analytics/export.csv?period=${encodeURIComponent(this.period)}`;
            window.location.assign(url);
        }

        showError(err) {
            const errBox = document.getElementById('error-container');
            if (!errBox) return;
            errBox.innerHTML = `
                <div class="error-box">
                    <strong>Failed to load analytics.</strong>
                    <div>${this.escape(err && err.message ? err.message : String(err))}</div>
                </div>
            `;
            // eslint-disable-next-line no-console
            console.error('[Analytics] load failed:', err);
        }

        updateTimestamp() {
            const el = document.getElementById('updated-at');
            if (!el) return;
            const now = new Date();
            const t = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            const period = (this.data && this.data.period) || {};
            const label = period.name
                ? `${period.name.toUpperCase()} (${period.start} → ${period.end})`
                : '';
            el.textContent = `${label} · updated ${t}`;
        }

        // ------------------------------------------------------------------
        // Render
        // ------------------------------------------------------------------

        render() {
            this.renderKPIs();
            this.renderSparkline();
            this.renderRevenueTable();
            this.renderExpensesTable();
            this.renderArAging();
            this.renderApAging();
            this.renderCashForecast();
        }

        renderKPIs() {
            const totalRevenue = this.sum(this.data.revenue_by_customer);
            const totalExpenses = this.sum(this.data.expenses_by_category);
            const dso = typeof this.data.dso === 'number' ? this.data.dso : 0;
            const margin = totalRevenue > 0
                ? ((totalRevenue - totalExpenses) / totalRevenue) * 100
                : 0;

            const html = `
                <div class="kpi-card">
                    <div class="kpi-label">Revenue</div>
                    <div class="kpi-value kpi-green">${this.fmt(totalRevenue)}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Expenses</div>
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
            `;
            document.getElementById('kpis').innerHTML = html;
        }

        renderSparkline() {
            const trend = this.data.revenue_trend || {};
            const labels = Object.keys(trend);
            const values = labels.map(k => Number(trend[k]) || 0);
            const host = document.getElementById('revenue-trend');
            if (!host) return;

            if (values.length === 0) {
                host.innerHTML = '<div class="empty">No revenue data.</div>';
                return;
            }

            const W = 800, H = 140, PAD_X = 20, PAD_TOP = 20, PAD_BOTTOM = 24;
            const innerW = W - PAD_X * 2;
            const innerH = H - PAD_TOP - PAD_BOTTOM;
            const barW = innerW / values.length;
            const gap = Math.max(2, Math.min(6, barW * 0.15));
            const max = Math.max(...values, 1);

            const bars = values.map((v, i) => {
                const x = PAD_X + i * barW + gap / 2;
                const w = barW - gap;
                const bh = max > 0 ? (v / max) * innerH : 0;
                const y = PAD_TOP + innerH - bh;
                const cls = v > 0 ? 'bar' : 'bar-zero';
                const title = `${labels[i]}: ${this.fmt(v)}`;
                return `<rect class="${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(1, bh).toFixed(1)}" rx="2"><title>${this.escape(title)}</title></rect>`;
            }).join('');

            const xTicks = labels.map((l, i) => {
                const x = PAD_X + i * barW + barW / 2;
                const show = l.slice(5); // "MM"
                return `<text x="${x.toFixed(1)}" y="${(H - 6).toFixed(1)}" text-anchor="middle">${this.escape(show)}</text>`;
            }).join('');

            // Max label
            const maxLabelY = PAD_TOP - 4;
            const maxLabel = `<text x="${PAD_X}" y="${maxLabelY}" class="bar-label">max ${this.fmt(max)}</text>`;

            host.innerHTML = `
                <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
                    ${maxLabel}
                    ${bars}
                    ${xTicks}
                </svg>
            `;
        }

        renderRevenueTable() {
            const entries = Object.entries(this.data.revenue_by_customer || {})
                .sort((a, b) => b[1] - a[1]);
            const host = document.getElementById('revenue-table');
            if (!host) return;

            if (entries.length === 0) {
                host.innerHTML = this.tableWrap('Revenue by Customer',
                    '<div class="empty">No paid invoices this period.</div>');
                return;
            }

            const rows = entries.map(([customer, revenue]) => `
                <tr>
                    <td>${this.escape(customer)}</td>
                    <td class="num green">${this.fmt(revenue)}</td>
                </tr>
            `).join('');

            host.innerHTML = `
                <table class="analytics-table">
                    <thead><tr><th>Customer</th><th class="num">Revenue</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            `;
        }

        renderExpensesTable() {
            const entries = Object.entries(this.data.expenses_by_category || {})
                .sort((a, b) => b[1] - a[1]);
            const host = document.getElementById('expenses-table');
            if (!host) return;

            if (entries.length === 0) {
                host.innerHTML = '<div class="empty">No paid bills this period.</div>';
                return;
            }

            const rows = entries.map(([cat, amount]) => `
                <tr>
                    <td>${this.escape(cat)}</td>
                    <td class="num red">${this.fmt(amount)}</td>
                </tr>
            `).join('');

            host.innerHTML = `
                <table class="analytics-table">
                    <thead><tr><th>Category</th><th class="num">Expense</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            `;
        }

        renderArAging() {
            this.renderAgingTable('ar-aging', this.data.ar_aging, 'Customer');
        }

        renderApAging() {
            this.renderAgingTable('ap-aging', this.data.ap_aging, 'Vendor');
        }

        renderAgingTable(elementId, aging, nameHeader) {
            const host = document.getElementById(elementId);
            if (!host) return;
            aging = aging || {};

            const names = new Set([
                ...Object.keys(aging.current || {}),
                ...Object.keys(aging['30'] || {}),
                ...Object.keys(aging['60'] || {}),
                ...Object.keys(aging['90'] || {}),
            ]);

            if (names.size === 0) {
                host.innerHTML = '<div class="empty">Nothing outstanding.</div>';
                return;
            }

            // Sort rows by total balance descending so the worst offenders float up.
            const rows = Array.from(names).map(name => {
                const cur = (aging.current && aging.current[name]) || 0;
                const d30 = (aging['30'] && aging['30'][name]) || 0;
                const d60 = (aging['60'] && aging['60'][name]) || 0;
                const d90 = (aging['90'] && aging['90'][name]) || 0;
                return { name, cur, d30, d60, d90, total: cur + d30 + d60 + d90 };
            }).sort((a, b) => b.total - a.total);

            let totalCur = 0, total30 = 0, total60 = 0, total90 = 0, totalAll = 0;
            const bodyRows = rows.map(r => {
                totalCur += r.cur; total30 += r.d30; total60 += r.d60; total90 += r.d90;
                totalAll += r.total;
                return `
                    <tr>
                        <td>${this.escape(r.name)}</td>
                        <td class="num">${this.fmt(r.cur)}</td>
                        <td class="num amber">${this.fmt(r.d30)}</td>
                        <td class="num amber">${this.fmt(r.d60)}</td>
                        <td class="num red">${this.fmt(r.d90)}</td>
                        <td class="num green"><strong>${this.fmt(r.total)}</strong></td>
                    </tr>
                `;
            }).join('');

            const totalsRow = `
                <tr>
                    <td><strong>TOTAL</strong></td>
                    <td class="num"><strong>${this.fmt(totalCur)}</strong></td>
                    <td class="num"><strong>${this.fmt(total30)}</strong></td>
                    <td class="num"><strong>${this.fmt(total60)}</strong></td>
                    <td class="num"><strong>${this.fmt(total90)}</strong></td>
                    <td class="num green"><strong>${this.fmt(totalAll)}</strong></td>
                </tr>
            `;

            host.innerHTML = `
                <table class="analytics-table">
                    <thead>
                        <tr>
                            <th>${this.escape(nameHeader)}</th>
                            <th class="num">Current</th>
                            <th class="num">30+</th>
                            <th class="num">60+</th>
                            <th class="num">90+</th>
                            <th class="num">Total</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}${totalsRow}</tbody>
                </table>
            `;
        }

        renderCashForecast() {
            const rows = this.data.cash_forecast || [];
            const host = document.getElementById('cash-forecast');
            if (!host) return;

            if (rows.length === 0) {
                host.innerHTML = '<div class="empty">No forecast data.</div>';
                return;
            }

            const bodyRows = rows.map(r => {
                const net = Number(r.net) || 0;
                const netClass = net >= 0 ? 'green' : 'red';
                return `
                    <tr>
                        <td>${this.escape(r.date)}</td>
                        <td class="num green">${this.fmt(r.collections)}</td>
                        <td class="num red">${this.fmt(r.payments)}</td>
                        <td class="num ${netClass}"><strong>${this.fmt(net)}</strong></td>
                    </tr>
                `;
            }).join('');

            host.innerHTML = `
                <table class="analytics-table">
                    <thead>
                        <tr>
                            <th>Due by</th>
                            <th class="num">Expected Collections</th>
                            <th class="num">Expected Payments</th>
                            <th class="num">Net</th>
                        </tr>
                    </thead>
                    <tbody>${bodyRows}</tbody>
                </table>
            `;
        }

        // ------------------------------------------------------------------
        // Helpers
        // ------------------------------------------------------------------

        fmt(n) {
            const num = Number(n);
            if (!isFinite(num)) return '$0';
            return new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD',
                maximumFractionDigits: 0,
            }).format(num);
        }

        sum(obj) {
            return Object.values(obj || {}).reduce((a, b) => a + (Number(b) || 0), 0);
        }

        escape(s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        tableWrap(title, inner) { return inner; }
    }

    // Auto-init on DOM ready (or immediately if already parsed).
    function start() {
        const dashboard = new AnalyticsDashboard();
        dashboard.init();
        // Expose for debugging/console use.
        window.AnalyticsDashboardInstance = dashboard;
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
