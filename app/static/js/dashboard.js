/**
 * Dashboard (Company Snapshot) — the customizable overview.
 *
 * Every card is a widget from the server's catalog (/api/dashboard/widgets).
 * The layout — which cards, in what order — is a per-user preference
 * ("dashboard": {order: [...]}); nothing saved means the classic overview.
 * Customize mode adds hide / move controls to each card and an "Add a
 * card" picker for the rest of the catalog. Data for the visible cards
 * comes in one request (/api/dashboard/data?ids=...), so a bad card
 * shows its error in place instead of taking the page down.
 */
const DashboardPage = {
    _catalog: [],
    _order: [],
    _data: {},
    _editing: false,
    _dirty: false,

    async render() {
        const [catalog, pref] = await Promise.all([
            API.get('/dashboard/widgets'),
            API.get('/preferences/dashboard').catch(() => ({ value: null })),
        ]);
        DashboardPage._catalog = catalog.widgets;
        const known = new Set(catalog.widgets.map(w => w.id));
        const saved = pref && pref.value && Array.isArray(pref.value.order) ? pref.value.order.filter(id => known.has(id)) : null;
        DashboardPage._order = saved && saved.length ? saved : catalog.default_order.slice();
        DashboardPage._editing = false;
        DashboardPage._dirty = false;
        await DashboardPage._load();
        return DashboardPage._pageHtml();
    },

    _company: '',

    async _load() {
        const ids = DashboardPage._order.join(',');
        const [data, settings] = await Promise.all([
            ids ? API.get(`/dashboard/data?ids=${encodeURIComponent(ids)}`) : Promise.resolve({}),
            API.get('/settings').catch(() => ({})),
        ]);
        DashboardPage._data = data;
        const name = (settings && settings.company_name) || '';
        DashboardPage._company = name && name !== 'My Company' ? name : '';
    },

    _title() {
        return DashboardPage._company ? `${escapeHtml(DashboardPage._company)} Snapshot` : 'Company Snapshot';
    },

    _pageHtml() {
        const editing = DashboardPage._editing;
        return `
            <div class="page-header">
                <h2>${DashboardPage._title()}</h2>
                <div style="display:flex; gap:8px; align-items:center;">
                    ${editing ? `
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.showAdd()">+ Add a card</button>
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.resetLayout()" title="Back to the standard overview">Reset</button>
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.cancelEdit()">Cancel</button>
                        <button class="btn btn-sm btn-primary" onclick="DashboardPage.saveLayout()">Save layout</button>`
                    : `<button class="btn btn-sm btn-secondary" data-write onclick="DashboardPage.startEdit()" title="Choose which cards show and in what order">Customize</button>`}
                </div>
            </div>
            ${editing ? '<div style="font-size:11px;color:var(--gray-500);margin-bottom:8px;">Use the arrows to reorder, × to hide. Your layout is remembered for your login.</div>' : ''}
            <div id="dash-grid" class="dash-grid">${DashboardPage._gridHtml()}</div>`;
    },

    _gridHtml() {
        if (!DashboardPage._order.length) {
            return `<div class="empty-state" style="grid-column:1/-1"><p>No cards on your overview.</p><button class="btn btn-primary" data-write onclick="DashboardPage.startEdit();DashboardPage.showAdd()">Add a card</button></div>`;
        }
        return DashboardPage._order.map((id, i) => DashboardPage._cardHtml(id, i)).join('');
    },

    _meta(id) { return DashboardPage._catalog.find(w => w.id === id) || { id, title: id, size: 'half' }; },

    _cardHtml(id, index) {
        const meta = DashboardPage._meta(id);
        const data = DashboardPage._data[id];
        const editing = DashboardPage._editing;
        const controls = editing ? `<span class="dash-card__controls">
            <button type="button" class="btn btn-sm btn-secondary" title="Move up" ${index === 0 ? 'disabled' : ''} onclick="DashboardPage.move('${id}', -1)">▲</button>
            <button type="button" class="btn btn-sm btn-secondary" title="Move down" ${index === DashboardPage._order.length - 1 ? 'disabled' : ''} onclick="DashboardPage.move('${id}', 1)">▼</button>
            <button aria-label="Hide this card" type="button" class="btn btn-sm btn-secondary" title="Hide this card" onclick="DashboardPage.hide('${id}')">×</button>
        </span>` : '';
        let body;
        if (!data) body = '<div style="color:var(--gray-500);font-size:12px">Loading…</div>';
        else if (data.error) body = `<div style="color:var(--text-danger);font-size:12px">${escapeHtml(data.error)}</div>`;
        else body = (DashboardPage.renderers[id] || DashboardPage.renderers._default)(data, meta);
        const stat = meta.size === 'stat';
        return `<div class="card dash-card dash-card--${meta.size}" data-widget="${id}">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                <span>${escapeHtml(meta.title)}</span>${controls}
            </div>
            ${stat ? body : `<div class="dash-card__body">${body}</div>`}
        </div>`;
    },

    // ---- customize -------------------------------------------------------------
    startEdit() { DashboardPage._editing = true; DashboardPage._repaint(); },
    async cancelEdit() { await DashboardPage._reload(); },
    async _reload() { $('#page-content').innerHTML = await DashboardPage.render(); },
    _repaint() { $('#page-content').innerHTML = DashboardPage._pageHtml(); },

    move(id, delta) {
        const o = DashboardPage._order;
        const i = o.indexOf(id);
        const j = i + delta;
        if (i < 0 || j < 0 || j >= o.length) return;
        [o[i], o[j]] = [o[j], o[i]];
        DashboardPage._dirty = true;
        DashboardPage._repaint();
    },

    hide(id) {
        DashboardPage._order = DashboardPage._order.filter(x => x !== id);
        DashboardPage._dirty = true;
        DashboardPage._repaint();
    },

    showAdd() {
        const shown = new Set(DashboardPage._order);
        const rest = DashboardPage._catalog.filter(w => !shown.has(w.id));
        if (!rest.length) { toast('Every card is already on the page'); return; }
        openModal('Add a card', `
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Card</th><th scope="col">What it shows</th><th scope="col"></th></tr></thead>
                <tbody>${rest.map(w => `<tr>
                    <td><strong>${escapeHtml(w.title)}</strong></td>
                    <td style="color:var(--gray-600)">${escapeHtml(w.description || '')}</td>
                    <td><button class="btn btn-sm btn-primary" onclick="DashboardPage.add('${w.id}')">Add</button></td>
                </tr>`).join('')}</tbody>
            </table></div>`);
    },

    async add(id) {
        closeModal();
        DashboardPage._order.push(id);
        DashboardPage._dirty = true;
        try {
            const d = await API.get(`/dashboard/data?ids=${encodeURIComponent(id)}`);
            Object.assign(DashboardPage._data, d);
        } catch (e) { DashboardPage._data[id] = { error: e.message }; }
        DashboardPage._repaint();
        const el = document.querySelector(`[data-widget="${id}"]`);
        if (el) el.scrollIntoView({ block: 'center' });
    },

    async saveLayout() {
        try {
            await API.put('/preferences/dashboard', { value: { order: DashboardPage._order } });
            toast('Layout saved');
            await DashboardPage._reload();
        } catch (err) { toast(err.message, 'error'); }
    },

    async resetLayout() {
        if (!confirm('Reset to the standard overview?')) return;
        try {
            await API.del('/preferences/dashboard');
            toast('Layout reset');
            await DashboardPage._reload();
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- renderers (one per widget id) --------------------------------------------
    renderers: {
        _default(d) { return `<pre style="font-size:11px;white-space:pre-wrap">${escapeHtml(JSON.stringify(d, null, 1))}</pre>`; },

        receivables(d) {
            return `<div class="card-value">${formatCurrency(d.total)}</div>
                <div style="font-size:11px;color:${d.overdue_count ? 'var(--qb-red)' : 'var(--gray-500)'}">${d.overdue_count} overdue</div>`;
        },
        active_customers(d) { return `<div class="card-value">${d.count}</div>`; },
        payables(d) {
            return `<div class="card-value">${formatCurrency(d.total)}</div>
                <div style="font-size:11px;color:${d.overdue_count ? 'var(--qb-red)' : 'var(--gray-500)'}">${d.overdue_count} overdue</div>`;
        },
        overdue_invoices(d) {
            if (!d.count) return '<div style="color:var(--gray-500);font-size:12px">Nothing overdue.</div>';
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">#</th><th scope="col">${T('Customer')}</th><th scope="col" class="amount">Due</th><th scope="col" class="amount">Days</th></tr></thead>
                <tbody>${d.items.map(i => `<tr class="clickable" onclick="InvoicesPage.view(${i.id})"><td>${escapeHtml(i.invoice_number)}</td><td>${escapeHtml(i.customer)}</td><td class="amount">${formatCurrency(i.balance_due)}</td><td class="amount" style="color:var(--qb-red)">${i.days_overdue}</td></tr>`).join('')}</tbody></table>
                ${d.count > d.items.length ? `<div style="font-size:11px;margin-top:4px"><a href="#/invoices">${d.count} overdue in all →</a></div>` : ''}`;
        },
        bank_balances(d) {
            if (!d.accounts.length) return '<div style="color:var(--gray-500);font-size:12px">No bank accounts yet. <a href="#/banking">Add one</a>.</div>';
            // each a link to the account's register, named by the account,
            // its balance its description (as Banking's cards)
            return `<div class="card-grid" style="margin:0">${d.accounts.map(b => `<a class="card card-link" href="#/banking/${b.id}" data-row-key="bank:${b.id}"
                aria-label="${escapeHtml(b.name)}" aria-describedby="dash-bank-${b.id}-value${b.kind === 'credit_card' ? ` dash-bank-${b.id}-owed` : ''}"
                onclick="ReportsPage._leaveFrom(this)" onkeydown="ReportsPage._cardKey(event)">
                <div class="card-header">${escapeHtml(b.name)}${b.kind === 'credit_card' ? ` <span id="dash-bank-${b.id}-owed" style="font-size:10px;color:var(--gray-400)">owed</span>` : ''}</div><div class="card-value" id="dash-bank-${b.id}-value">${formatCurrency(b.balance)}</div></a>`).join('')}</div>`;
        },
        ar_aging(d) {
            // The A/R Aging report's own figures: the buckets, the credits
            // customers hold (payments and credit memos not yet applied) and
            // the total, which is Total Receivables. Current + 1-30 + 31-60
            // + 61+ − credits = Total, as on the report page.
            const owed = d.current + d.d30 + d.d60 + d.d90;
            if (!owed && !d.credits) return '<div style="color:var(--gray-500);font-size:12px">No open receivables.</div>';
            // each segment carries its figure when it is wide enough to hold
            // one (#265), in the ink or white that reads on it — none where
            // neither reaches AA; the legend under the bar has every figure
            const dark = (document.documentElement.getAttribute('data-theme') || 'light') === 'dark';
            // a 2px seam of the card between slices, so two bands of one
            // lightness (the deeper orange and the red) still read as two
            // without their hue (2.23.4 gate, NEW-133)
            let drawn = 0;
            const seg = (v, key, label) => {
                if (!(v > 0)) return '';
                const text = figureTextOn(cssVar(`--aging-${key}`), dark);
                const figure = v / owed >= 0.14 && text.ratio >= 4.5 ? compactMoney(v) : '';
                const seam = drawn++ ? 'border-left:2px solid var(--panel-bg);box-sizing:border-box;' : '';
                return `<div style="width:${(v / owed * 100).toFixed(1)}%;${seam}background:var(--aging-${key});overflow:hidden;white-space:nowrap;text-align:center;font-size:10px;line-height:28px;color:${text.fill}" title="${label}: ${formatCurrency(v)}">${figure}</div>`;
            };
            return `<div style="display:flex;height:28px;border-radius:4px;overflow:hidden">${seg(d.current, 'current', 'Current')}${seg(d.d30, '30', '1-30')}${seg(d.d60, '60', '31-60')}${seg(d.d90, '90', '61+')}</div>
                <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;flex-wrap:wrap">
                    <span><span aria-hidden="true" class="aging-swatch" style="background:var(--aging-current)"></span> Current ${formatCurrency(d.current)}</span>
                    <span><span aria-hidden="true" class="aging-swatch" style="background:var(--aging-30)"></span> 1-30 ${formatCurrency(d.d30)}</span>
                    <span><span aria-hidden="true" class="aging-swatch" style="background:var(--aging-60)"></span> 31-60 ${formatCurrency(d.d60)}</span>
                    <span><span aria-hidden="true" class="aging-swatch" style="background:var(--aging-90)"></span> 61+ ${formatCurrency(d.d90)}</span>
                </div>
                <table class="data-table" style="font-size:12px;margin-top:6px"><tbody>
                    ${d.credits ? `<tr><td>Credits not yet applied</td><td class="amount">${formatCurrency(-d.credits)}</td></tr>` : ''}
                    <tr style="font-weight:700"><td>Total</td><td class="amount">${formatCurrency(d.total)}</td></tr>
                </tbody></table>`;
        },
        monthly_revenue(d) {
            const max = Math.max(...d.months.map(m => m.amount), 1);
            // the figure above each bar (#265); the bar keeps 80% of the height
            return `<div style="display:flex;align-items:flex-end;gap:4px;height:132px">${d.months.map(m => `<div style="flex:1;text-align:center;height:100%;display:flex;flex-direction:column;justify-content:flex-end" title="${m.month} ${m.year}: ${formatCurrency(m.amount)}">
                <div style="font-size:9px;color:var(--gray-500);line-height:12px">${m.amount ? compactMoney(m.amount) : ''}</div>
                <div style="width:80%;margin:0 auto;background:var(--qb-blue);height:${Math.max(2, m.amount / max * 80)}%;border-radius:2px 2px 0 0"></div>
                <div style="font-size:9px;color:var(--gray-500);margin-top:2px">${m.month}</div></div>`).join('')}</div>`;
        },
        recent_invoices(d) {
            if (!d.items.length) return '<div style="color:var(--gray-500);font-size:12px">No invoices yet.</div>';
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">#</th><th scope="col">${T('Customer')}</th><th scope="col">Date</th><th scope="col">Status</th><th scope="col" class="amount">Total</th></tr></thead>
                <tbody>${d.items.map(i => `<tr class="clickable" onclick="InvoicesPage.view(${i.id})"><td>${escapeHtml(i.invoice_number)}</td><td>${escapeHtml(i.customer)}</td><td>${escapeHtml(i.date)}</td><td>${escapeHtml(i.status)}</td><td class="amount">${formatCurrency(i.total)}</td></tr>`).join('')}</tbody></table>`;
        },
        recent_payments(d) {
            if (!d.items.length) return '<div style="color:var(--gray-500);font-size:12px">No payments yet.</div>';
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">Date</th><th scope="col">${T('Customer')}</th><th scope="col">Method</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${d.items.map(p => `<tr><td>${escapeHtml(p.date)}</td><td>${escapeHtml(p.customer)}</td><td>${escapeHtml(p.method || '')}</td><td class="amount">${formatCurrency(p.amount)}</td></tr>`).join('')}</tbody></table>`;
        },
        pnl_month(d) {
            const row = (m) => `<tr><td>${escapeHtml(m.label)}</td><td class="amount">${formatCurrency(m.income)}</td><td class="amount">${formatCurrency(m.expenses)}</td><td class="amount" style="font-weight:700;color:${m.net < 0 ? 'var(--text-danger)' : 'var(--text-success)'}">${formatCurrency(m.net)}</td></tr>`;
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col"></th><th scope="col" class="amount">${T('Income')}</th><th scope="col" class="amount">Expenses</th><th scope="col" class="amount">Net</th></tr></thead>
                <tbody>${row(d.this_month)}${row(d.last_month)}</tbody></table>
                <div style="font-size:11px;margin-top:4px;color:${d.net_change < 0 ? 'var(--text-danger)' : 'var(--text-success)'}">${d.net_change >= 0 ? '▲' : '▼'} ${formatCurrency(Math.abs(d.net_change))} vs last month · <a href="${DashboardPage._reportLink('pnl', 'this_month')}">Full ${T('P&L')}</a></div>`;
        },
        pnl_ytd(d) {
            const max = Math.max(...d.months.map(m => Math.abs(m.cumulative)), 1);
            const bars = d.months.map(m => `<div style="flex:1;text-align:center;height:100%;display:flex;flex-direction:column;justify-content:flex-end" title="${m.month} cumulative: ${formatCurrency(m.cumulative)}">
                <div style="width:80%;margin:0 auto;background:${m.cumulative < 0 ? '#a4242b' : 'var(--qb-blue)'};height:${Math.max(2, Math.abs(m.cumulative) / max * 100)}%;border-radius:2px 2px 0 0"></div>
                <div style="font-size:9px;color:var(--gray-500);margin-top:2px">${m.month}</div></div>`).join('');
            return `<div class="card-value" style="color:${d.net < 0 ? 'var(--text-danger)' : 'var(--text-success)'}">${formatCurrency(d.net)}</div>
                <div style="font-size:11px;color:var(--gray-500);margin-bottom:6px">net · ${d.year} year to date</div>
                <table class="data-table" style="font-size:12px"><tbody>
                    <tr><td>${T('Income')}</td><td class="amount">${formatCurrency(d.income)}</td></tr>
                    <tr><td>Expenses</td><td class="amount">${formatCurrency(d.expenses)}</td></tr>
                </tbody></table>
                <div style="display:flex;align-items:flex-end;gap:4px;height:70px;margin-top:8px">${bars}</div>
                <div style="font-size:10px;color:var(--gray-500);margin-top:2px">cumulative net by month · <a href="${DashboardPage._reportLink('pnl', 'this_year_to_date')}">Full ${T('P&L')}</a></div>`;
        },
        cash_position(d) {
            return `<div class="card-value">${formatCurrency(d.cash)}</div>
                <div style="font-size:11px;color:var(--gray-500)">in the bank today</div>
                <table class="data-table" style="font-size:12px;margin-top:8px"><tbody>
                    <tr><td>+ ${T('Receivables')} due within 30 days</td><td class="amount">${formatCurrency(d.ar_due_30)}</td></tr>
                    <tr><td>− Payables due within 30 days</td><td class="amount">${formatCurrency(d.ap_due_30)}</td></tr>
                    <tr style="font-weight:700"><td>30-day forecast</td><td class="amount" style="color:${d.forecast_30 < 0 ? 'var(--text-danger)' : 'var(--text-success)'}">${formatCurrency(d.forecast_30)}</td></tr>
                </tbody></table>
                <div style="font-size:10px;color:var(--gray-500);margin-top:4px">Assumes customers pay on the due date.</div>`;
        },
        open_pos(d) {
            if (!d.count) return '<div style="color:var(--gray-500);font-size:12px">No open purchase orders.</div>';
            return `<div class="card-value">${formatCurrency(d.total)}</div><div style="font-size:11px;color:var(--gray-500)">${d.count} open · committed, not yet billed</div>
                <table class="data-table" style="font-size:12px;margin-top:6px"><tbody>${d.items.map(p => `<tr class="clickable" onclick="App.navigate('#/purchase-orders')"><td>${escapeHtml(p.po_number)}</td><td>${escapeHtml(p.vendor)}</td><td>${escapeHtml(p.job || '')}</td><td>${escapeHtml(p.status)}</td><td class="amount">${formatCurrency(p.total)}</td></tr>`).join('')}</tbody></table>`;
        },
        receipts_review(d) {
            if (!d.count) return '<div style="color:var(--gray-500);font-size:12px">Nothing waiting. Scan a receipt from a Bill or Expense form.</div>';
            return `<div class="card-value">${d.count}</div><div style="font-size:11px;color:var(--gray-500)">scanned, not yet on a document · they expire after ${d.ttl_hours} h</div>
                <ul style="font-size:12px;margin:6px 0 0 0;padding-left:16px">${d.items.map(r => `<li>${escapeHtml(r.filename || r.intake_id)} <span style="color:var(--gray-500)">(${r.expires_in_hours} h left)</span></li>`).join('')}</ul>
                <div style="font-size:11px;margin-top:4px"><a href="#/expenses">Enter Expenses →</a></div>`;
        },
        job_budget_vs_actual(d) {
            if (!d.count) return '<div style="color:var(--gray-500);font-size:12px">' + Terms.text('No jobs with a budget or activity yet.') + ' <a href="#/jobs">' + T('Jobs') + ' →</a></div>';
            const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(0)}%`;
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">${T('Job')}</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th><th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th></tr></thead>
                <tbody>${d.items.map(j => `<tr class="clickable" onclick="App.navigate('#/jobs/${j.job_id}')"><td>${escapeHtml(j.customer_name)}: ${escapeHtml(j.job_name)}</td><td class="amount">${formatCurrency(j.revised)}</td><td class="amount">${formatCurrency(j.committed)}</td><td class="amount">${formatCurrency(j.actual)}</td><td class="amount">${formatCurrency(j.projected)}</td><td class="amount" style="font-weight:700;color:${j.revised && j.variance < 0 ? 'var(--text-danger)' : 'var(--text-success)'}">${formatCurrency(j.variance)}</td><td class="amount">${pct(j.pct_used)}</td></tr>`).join('')}</tbody>
                <tfoot><tr style="font-weight:700;background:var(--gray-50)"><td>All active jobs</td><td class="amount">${formatCurrency(d.totals.revised)}</td><td class="amount">${formatCurrency(d.totals.committed)}</td><td class="amount">${formatCurrency(d.totals.actual)}</td><td class="amount">${formatCurrency(d.totals.projected)}</td><td class="amount">${formatCurrency(d.totals.variance)}</td><td></td></tr></tfoot></table>`;
        },
        balance_sheet_trend(d) {
            if (!d.months || !d.months.length) return '<div style="color:var(--gray-500);font-size:12px">No data yet.</div>';
            // Chart.js needs the canvas in the DOM before Chart(ctx, ...) runs.
            // setTimeout(0) is a macrotask: it fires after the caller's
            // innerHTML assignment (a microtask continuation) has landed.
            setTimeout(() => DashboardPage._renderBsTrendChart(), 0);
            const last = d.months[d.months.length - 1];
            const chip = c => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${c};vertical-align:middle"></span>`;
            return `<div style="display:flex;gap:16px;font-size:11px;margin-bottom:4px;flex-wrap:wrap">
                    <span>${chip('var(--qb-blue)')} Assets ${formatCurrency(last.assets)}</span>
                    <span>${chip('var(--chart-red)')} Liabilities ${formatCurrency(last.liabilities)}</span>
                    <span>${chip('var(--chart-green)')} ${T('Equity')} ${formatCurrency(last.equity)}</span>
                </div>
                <div style="position:relative;height:200px"><canvas id="chart-bs-trend"></canvas></div>
                <div style="font-size:10px;color:var(--gray-500);margin-top:2px">Month-end balances, last 12 months (this month to date) · <a href="${DashboardPage._reportLink('bs')}">Full ${T('Balance Sheet')}</a></div>`;
        },
    },

    // The card's report, dated as the card is (R7): this month's or the
    // year-to-date P&L, the balance sheet as of today — the Statement of
    // Activities / of Financial Position for a nonprofit. The period rides
    // with the dates, so a bookmark of the link is "this month" next month
    // too.
    _reportLink(which, period) {
        const np = Terms.isNonprofit();
        if (which === 'bs') {
            return ReportsPage.viewUrl(np ? 'statement-of-financial-position' : 'balance-sheet', { as_of_date: todayISO() });
        }
        const range = ReportsPage.getDateRange(period);
        return ReportsPage.viewUrl(np ? 'statement-of-activities' : 'profit-loss', { period, start_date: range.start, end_date: range.end });
    },

    _bsChart: null,

    _renderBsTrendChart() {
        const canvas = document.getElementById('chart-bs-trend');
        if (!canvas) return;
        const d = (DashboardPage._data || {}).balance_sheet_trend;
        if (!d || !d.months || typeof Chart === 'undefined') return;
        if (DashboardPage._bsChart) {
            try { DashboardPage._bsChart.destroy(); } catch (e) { /* detached */ }
            DashboardPage._bsChart = null;
        }
        const isDark = (document.documentElement.getAttribute('data-theme') || 'light') === 'dark';
        const theme = {
            text: isDark ? '#e6e6f0' : '#1a1a28',
            grid: isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)',
        };
        const blue = (getComputedStyle(document.documentElement).getPropertyValue('--qb-blue') || '').trim() || '#4a7ebb';
        const labels = d.months.map(m => `${m.month} ${String(m.year).slice(2)}`);
        DashboardPage._bsChart = new Chart(canvas, {
            type: 'line',
            plugins: [chartFigures()],
            data: {
                labels,
                datasets: [
                    { label: 'Assets', data: d.months.map(m => m.assets), borderColor: blue, backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, borderWidth: 2 },
                    { label: 'Liabilities', data: d.months.map(m => m.liabilities), borderColor: chartColor('red'), backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, borderWidth: 2 },
                    { label: T('Equity'), data: d.months.map(m => m.equity), borderColor: chartColor('green'), backgroundColor: 'transparent', tension: 0.3, pointRadius: 2, borderWidth: 2 },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: theme.text, font: { size: 9 } }, grid: { color: theme.grid } },
                    y: { ticks: { color: theme.text, font: { size: 9 } }, grid: { color: theme.grid } },
                },
            },
        });
    },
};
window.DashboardPage = DashboardPage;
// A theme toggle with the overview on screen: redraw the trend so its axis
// text, grid and the Assets line pick up the new theme's colours.
// A theme toggle with the overview on screen: repaint the cards (the aging
// bar's figures pick ink or white for the theme they are drawn in — gate
// round 1, NEW-60) and redraw the trend into the new canvas.
document.addEventListener('slowbooks:themechange', () => {
    let cards = false;
    try { cards = !!document.querySelector('[data-widget]'); } catch (e) { /* no DOM (a probe) */ }
    if (cards) DashboardPage._repaint();
    DashboardPage._renderBsTrendChart();
});
