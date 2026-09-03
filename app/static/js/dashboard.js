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

    async _load() {
        const ids = DashboardPage._order.join(',');
        DashboardPage._data = ids ? await API.get(`/dashboard/data?ids=${encodeURIComponent(ids)}`) : {};
    },

    _pageHtml() {
        const editing = DashboardPage._editing;
        return `
            <div class="page-header">
                <h2>Company Snapshot</h2>
                <div style="display:flex; gap:8px; align-items:center;">
                    ${editing ? `
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.showAdd()">+ Add a card</button>
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.resetLayout()" title="Back to the standard overview">Reset</button>
                        <button class="btn btn-sm btn-secondary" onclick="DashboardPage.cancelEdit()">Cancel</button>
                        <button class="btn btn-sm btn-primary" onclick="DashboardPage.saveLayout()">Save layout</button>`
                    : `<button class="btn btn-sm btn-secondary" onclick="DashboardPage.startEdit()" title="Choose which cards show and in what order">Customize</button>`}
                </div>
            </div>
            ${editing ? '<div style="font-size:11px;color:var(--gray-500);margin-bottom:8px;">Use the arrows to reorder, × to hide. Your layout is remembered for your login.</div>' : ''}
            <div id="dash-grid" class="dash-grid">${DashboardPage._gridHtml()}</div>`;
    },

    _gridHtml() {
        if (!DashboardPage._order.length) {
            return `<div class="empty-state" style="grid-column:1/-1"><p>No cards on your overview.</p><button class="btn btn-primary" onclick="DashboardPage.startEdit();DashboardPage.showAdd()">Add a card</button></div>`;
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
        else if (data.error) body = `<div style="color:#a4242b;font-size:12px">${escapeHtml(data.error)}</div>`;
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
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">#</th><th scope="col">Customer</th><th scope="col" class="amount">Due</th><th scope="col" class="amount">Days</th></tr></thead>
                <tbody>${d.items.map(i => `<tr class="clickable" onclick="InvoicesPage.view(${i.id})"><td>${escapeHtml(i.invoice_number)}</td><td>${escapeHtml(i.customer)}</td><td class="amount">${formatCurrency(i.balance_due)}</td><td class="amount" style="color:var(--qb-red)">${i.days_overdue}</td></tr>`).join('')}</tbody></table>
                ${d.count > d.items.length ? `<div style="font-size:11px;margin-top:4px"><a href="#/invoices">${d.count} overdue in all →</a></div>` : ''}`;
        },
        bank_balances(d) {
            if (!d.accounts.length) return '<div style="color:var(--gray-500);font-size:12px">No bank accounts yet. <a href="#/banking">Add one</a>.</div>';
            return `<div class="card-grid" style="margin:0">${d.accounts.map(b => `<div class="card" style="cursor:pointer" onclick="App.navigate('#/banking')">
                <div class="card-header">${escapeHtml(b.name)}</div><div class="card-value">${formatCurrency(b.balance)}</div></div>`).join('')}</div>`;
        },
        ar_aging(d) {
            if (!d.total) return '<div style="color:var(--gray-500);font-size:12px">No open receivables.</div>';
            const seg = (v, color, label) => v > 0 ? `<div style="width:${(v / d.total * 100).toFixed(1)}%;background:${color}" title="${label}: ${formatCurrency(v)}"></div>` : '';
            return `<div style="display:flex;height:28px;border-radius:4px;overflow:hidden">${seg(d.current, 'var(--success)', 'Current')}${seg(d.d30, 'var(--qb-gold)', '1-30')}${seg(d.d60, '#f97316', '31-60')}${seg(d.d90, 'var(--danger)', '61+')}</div>
                <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;flex-wrap:wrap">
                    <span><span style="color:var(--success)">■</span> Current ${formatCurrency(d.current)}</span>
                    <span><span style="color:var(--qb-gold)">■</span> 1-30 ${formatCurrency(d.d30)}</span>
                    <span><span style="color:#f97316">■</span> 31-60 ${formatCurrency(d.d60)}</span>
                    <span><span style="color:var(--danger)">■</span> 61+ ${formatCurrency(d.d90)}</span>
                </div>`;
        },
        monthly_revenue(d) {
            const max = Math.max(...d.months.map(m => m.amount), 1);
            return `<div style="display:flex;align-items:flex-end;gap:4px;height:120px">${d.months.map(m => `<div style="flex:1;text-align:center;height:100%;display:flex;flex-direction:column;justify-content:flex-end" title="${m.month} ${m.year}: ${formatCurrency(m.amount)}">
                <div style="width:80%;margin:0 auto;background:var(--qb-blue);height:${Math.max(2, m.amount / max * 100)}%;border-radius:2px 2px 0 0"></div>
                <div style="font-size:9px;color:var(--gray-500);margin-top:2px">${m.month}</div></div>`).join('')}</div>`;
        },
        recent_invoices(d) {
            if (!d.items.length) return '<div style="color:var(--gray-500);font-size:12px">No invoices yet.</div>';
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">#</th><th scope="col">Customer</th><th scope="col">Date</th><th scope="col">Status</th><th scope="col" class="amount">Total</th></tr></thead>
                <tbody>${d.items.map(i => `<tr class="clickable" onclick="InvoicesPage.view(${i.id})"><td>${escapeHtml(i.invoice_number)}</td><td>${escapeHtml(i.customer)}</td><td>${escapeHtml(i.date)}</td><td>${escapeHtml(i.status)}</td><td class="amount">${formatCurrency(i.total)}</td></tr>`).join('')}</tbody></table>`;
        },
        recent_payments(d) {
            if (!d.items.length) return '<div style="color:var(--gray-500);font-size:12px">No payments yet.</div>';
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">Date</th><th scope="col">Customer</th><th scope="col">Method</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${d.items.map(p => `<tr><td>${escapeHtml(p.date)}</td><td>${escapeHtml(p.customer)}</td><td>${escapeHtml(p.method || '')}</td><td class="amount">${formatCurrency(p.amount)}</td></tr>`).join('')}</tbody></table>`;
        },
        pnl_month(d) {
            const row = (m) => `<tr><td>${escapeHtml(m.label)}</td><td class="amount">${formatCurrency(m.income)}</td><td class="amount">${formatCurrency(m.expenses)}</td><td class="amount" style="font-weight:700;color:${m.net < 0 ? '#a4242b' : '#1f7a36'}">${formatCurrency(m.net)}</td></tr>`;
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col"></th><th scope="col" class="amount">Income</th><th scope="col" class="amount">Expenses</th><th scope="col" class="amount">Net</th></tr></thead>
                <tbody>${row(d.this_month)}${row(d.last_month)}</tbody></table>
                <div style="font-size:11px;margin-top:4px;color:${d.net_change < 0 ? '#a4242b' : '#1f7a36'}">${d.net_change >= 0 ? '▲' : '▼'} ${formatCurrency(Math.abs(d.net_change))} vs last month · <a href="#/reports">Full P&L</a></div>`;
        },
        cash_position(d) {
            return `<div class="card-value">${formatCurrency(d.cash)}</div>
                <div style="font-size:11px;color:var(--gray-500)">in the bank today</div>
                <table class="data-table" style="font-size:12px;margin-top:8px"><tbody>
                    <tr><td>+ Receivables due within 30 days</td><td class="amount">${formatCurrency(d.ar_due_30)}</td></tr>
                    <tr><td>− Payables due within 30 days</td><td class="amount">${formatCurrency(d.ap_due_30)}</td></tr>
                    <tr style="font-weight:700"><td>30-day forecast</td><td class="amount" style="color:${d.forecast_30 < 0 ? '#a4242b' : '#1f7a36'}">${formatCurrency(d.forecast_30)}</td></tr>
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
            if (!d.count) return '<div style="color:var(--gray-500);font-size:12px">No jobs with a budget or activity yet. <a href="#/jobs">Jobs →</a></div>';
            const pct = v => v === null || v === undefined ? '—' : `${v.toFixed(0)}%`;
            return `<table class="data-table" style="font-size:12px"><thead><tr><th scope="col">Job</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th><th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th></tr></thead>
                <tbody>${d.items.map(j => `<tr class="clickable" onclick="App.navigate('#/jobs/${j.job_id}')"><td>${escapeHtml(j.customer_name)}: ${escapeHtml(j.job_name)}</td><td class="amount">${formatCurrency(j.revised)}</td><td class="amount">${formatCurrency(j.committed)}</td><td class="amount">${formatCurrency(j.actual)}</td><td class="amount">${formatCurrency(j.projected)}</td><td class="amount" style="font-weight:700;color:${j.revised && j.variance < 0 ? '#a4242b' : '#1f7a36'}">${formatCurrency(j.variance)}</td><td class="amount">${pct(j.pct_used)}</td></tr>`).join('')}</tbody>
                <tfoot><tr style="font-weight:700;background:var(--gray-50)"><td>All active jobs</td><td class="amount">${formatCurrency(d.totals.revised)}</td><td class="amount">${formatCurrency(d.totals.committed)}</td><td class="amount">${formatCurrency(d.totals.actual)}</td><td class="amount">${formatCurrency(d.totals.projected)}</td><td class="amount">${formatCurrency(d.totals.variance)}</td><td></td></tr></tfoot></table>`;
        },
    },
};
window.DashboardPage = DashboardPage;
