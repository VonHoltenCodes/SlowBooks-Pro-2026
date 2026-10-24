/**
 * Classes — the tracking dimension as an entity of its own (#234, R4).
 *
 * Two screens, outside Settings (where a class is still renamed, archived
 * and, in nonprofit mode, given its fund fields):
 *   #/classes        every class with its income, cost of goods, expenses
 *                    and net for a period; Active / Archived / All; a row
 *                    opens the class.
 *   #/classes/<id>   the class's page: Overview (the class's P&L figures,
 *                    account by account, with the company's net beside
 *                    them, each account opening its transactions for this
 *                    class) and Transactions (every posted line tagged to
 *                    the class across every account — the QuickReport —
 *                    each opening its document).
 *
 * Both have an address that reproduces them (docs/dev/report-views.md):
 * #/classes?period=last_month&start_date=…&end_date=…&show=all and
 * #/classes/5?tab=transactions&period=…&start_date=…&end_date=…. A change
 * on the page (tab, period, Show) replaces the address; opening a class,
 * a document or a report pushes, so Back returns to the page as it was.
 * Every figure comes from the same queries as P&L by Class, so the list's
 * total of every class is the Profit & Loss for the dates, and a filtered
 * list says it is one.
 */
const ClassesPage = {
    TABS: { overview: 'Overview', transactions: 'Transactions' },
    // the fiscal year to date, as the reports open (#444)
    get DEFAULT_PERIOD() { return ReportsPage.defaultPeriod(); },

    // the list's state and the open class's, both from the address
    _list: { period: 'this_year_to_date', start: '', end: '', show: 'active' },
    _page: { id: null, tab: 'overview', period: 'this_year_to_date', start: '', end: '' },
    _cls: null,

    // ---- the address --------------------------------------------------------

    _url(path, params) {
        const qs = new URLSearchParams();
        for (const [k, v] of Object.entries(params || {})) {
            if (v === null || v === undefined || v === '') continue;
            if (k === 'period' && v === 'custom') continue;
            qs.set(k, String(v));
        }
        const q = qs.toString();
        return `${path}${q ? `?${q}` : ''}`;
    },

    // The period from a query: a preset names the dates; dates alone are a
    // custom range; a period or date that is not one is said so and ignored.
    _periodFrom(query, fallback) {
        const q = query || {};
        const known = ReportsPage.periodOptions('').includes(`value="${q.period}"`);
        if (q.period && !known) toast(`period in the address is not one of the choices (${q.period}) — ignored`, 'error');
        const { start_date: start, end_date: end } = datesFromQuery(q);
        const period = known ? q.period : ((start || end) ? 'custom' : fallback);
        const range = ReportsPage.getDateRange(period, start || null, end || null);
        return { period, start: range.start, end: range.end };
    },

    _replaceAddress(url) {
        if ((location.hash || '#/') === url) return;
        history.replaceState(history.state, '', url);
    },

    _listUrl() {
        const s = ClassesPage._list;
        return ClassesPage._url('#/classes', {
            period: s.period, start_date: s.start, end_date: s.end,
            show: s.show === 'active' ? '' : s.show,
        });
    },

    _pageUrl() {
        const s = ClassesPage._page;
        return ClassesPage._url(`#/classes/${s.id}`, {
            tab: s.tab === 'overview' ? '' : s.tab,
            period: s.period, start_date: s.start, end_date: s.end,
        });
    },

    // ---- shared pieces ------------------------------------------------------

    _periodBarHtml(idPrefix, state, onChange) {
        return `
            <label for="${idPrefix}-period" style="font-size:11px;">Period</label>
            <select id="${idPrefix}-period" data-no-search onchange="${onChange}">${ReportsPage.periodOptions(state.period)}</select>
            <span id="${idPrefix}-custom" style="display:${state.period === 'custom' ? 'inline-flex' : 'none'}; gap:6px; align-items:center; font-size:11px;">
                <label for="${idPrefix}-start">From</label>
                <input type="date" id="${idPrefix}-start" value="${escapeHtml(state.start)}" onchange="${onChange}">
                <label for="${idPrefix}-end">To</label>
                <input type="date" id="${idPrefix}-end" value="${escapeHtml(state.end)}" onchange="${onChange}">
            </span>`;
    },

    // The period bar as typed. From after To is refused the way every
    // period report refuses it (W-3: the list took it, the address with
    // it, and the note read "nets to $0.00"): said, the boxes put back to
    // the range in use, and `kept` so the caller leaves the page, and its
    // address, on that range.
    _readPeriod(idPrefix, state) {
        const sel = $(`#${idPrefix}-period`);
        const period = sel ? sel.value : state.period;
        const custom = $(`#${idPrefix}-custom`);
        if (custom) custom.style.display = period === 'custom' ? 'inline-flex' : 'none';
        const startEl = $(`#${idPrefix}-start`);
        const endEl = $(`#${idPrefix}-end`);
        const start = startEl?.value || state.start;
        const end = endEl?.value || state.end;
        if (period === 'custom' && reversedRangeRefused(start, end, state)) {
            if (startEl) startEl.value = state.start;
            if (endEl) endEl.value = state.end;
            return { period: state.period, start: state.start, end: state.end, kept: true };
        }
        const range = ReportsPage.getDateRange(period, start, end);
        return { period, start: range.start, end: range.end };
    },

    money(v) { return formatCurrency(v || 0); },

    _settingsLink(text) {
        // Settings scrolls to the section named in sessionStorage
        return `<a href="#/settings" class="btn btn-secondary" onclick="try { sessionStorage.setItem('settings_focus', 'settings-h-classes'); } catch (e) {}">${text}</a>`;
    },

    _reportLink(view, params, text) {
        return `<a href="${escapeHtml(ReportsPage.viewUrl(view, params))}" class="btn btn-secondary">${text}</a>`;
    },

    // ---- the list -----------------------------------------------------------

    async render(query) {
        const q = query || {};
        const p = ClassesPage._periodFrom(q, ClassesPage.DEFAULT_PERIOD);
        let show = 'active';
        if (['active', 'archived', 'all'].includes(q.show)) show = q.show;
        else {
            try { const v = localStorage.getItem('slowbooks-show-classes'); if (['active', 'archived', 'all'].includes(v)) show = v; } catch (e) { /* the default */ }
        }
        ClassesPage._list = { ...p, show };
        ClassesPage._replaceAddress(ClassesPage._listUrl());
        return `
            <div class="page-header">
                <h2>${T('Classes')}</h2>
                <div>
                    ${ClassesPage._reportLink('profit-loss-by-class', { period: p.period, start_date: p.start, end_date: p.end }, T('P&L by Class'))}
                    ${ClassesPage._settingsLink(`Manage ${T('Classes')}…`)}
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                ${ClassesPage._periodBarHtml('classes', p, 'ClassesPage.listChanged()')}
                <label for="classes-show" class="list-show-label" style="margin-left:auto;">Show</label>
                <select id="classes-show" data-no-search onchange="ClassesPage.listChanged()">
                    <option value="active"${show === 'active' ? ' selected' : ''}>Active</option>
                    <option value="archived"${show === 'archived' ? ' selected' : ''}>Archived</option>
                    <option value="all"${show === 'all' ? ' selected' : ''}>All</option>
                </select>
            </div>
            <div id="classes-table">${await ClassesPage._listTableHtml()}</div>`;
    },

    async listChanged() {
        const p = ClassesPage._readPeriod('classes', ClassesPage._list);
        if (p.kept) return;  // the list on the range before it is still showing
        const show = $('#classes-show')?.value || 'active';
        try { localStorage.setItem('slowbooks-show-classes', show); } catch (e) { /* this view only */ }
        ClassesPage._list = { ...p, show };
        ClassesPage._replaceAddress(ClassesPage._listUrl());
        const link = document.querySelector('#page-content .page-header a[href^="#/reports/profit-loss-by-class"]');
        if (link) link.setAttribute('href', ReportsPage.viewUrl('profit-loss-by-class', { period: p.period, start_date: p.start, end_date: p.end }));
        const el = $('#classes-table');
        if (el) { el.innerHTML = '<p style="color:var(--gray-500);">Loading…</p>'; el.innerHTML = await ClassesPage._listTableHtml(); }
    },

    async _listTableHtml() {
        const s = ClassesPage._list;
        let data;
        try {
            data = await API.get(`/classes/activity?start_date=${s.start}&end_date=${s.end}&include_archived=true`);
        } catch (err) {
            return `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
        }
        const all = data.classes;
        if (!all.some(c => s.show === 'all' || (s.show === 'archived') === !!c.is_archived)) {
            return `<div class="empty-state"><p>${s.show === 'archived' ? `No archived ${T('classes').toLowerCase()}.` : Terms.text('No classes yet. Add them under Settings → Classes; every entry form then offers them.')}</p></div>`;
        }
        // income and expenses count Other Income / Other Expense (#390),
        // so each row's four figures add up to net
        const rows = all.filter(c => s.show === 'all' || (s.show === 'archived') === !!c.is_archived);
        const keys = ['income', 'cogs', 'expenses', 'net_income'];
        const sum = {};
        for (const k of keys) sum[k] = rows.reduce((t, c) => t + (c[k] || 0), 0);
        const body = rows.map(c => `<tr class="clickable${c.is_archived ? ' row--dim' : ''}" onclick="App.navigate('${ClassesPage._pageHref(c.id)}')">
            <td><a href="${ClassesPage._pageHref(c.id)}" onclick="event.stopPropagation()" style="font-weight:600;">${escapeHtml(c.name)}</a>${c.is_system_default ? ' <span style="font-size:9px;color:var(--text-muted);">(default)</span>' : ''}</td>
            <td>${c.is_archived ? '<span class="badge badge-draft">Archived</span>' : 'Active'}</td>
            <td class="amount">${ClassesPage.money(c.income)}</td>
            <td class="amount">${ClassesPage.money(c.cogs)}</td>
            <td class="amount">${ClassesPage.money(c.expenses)}</td>
            <td class="amount" style="font-weight:700;">${ClassesPage.money(c.net_income)}</td>
        </tr>`).join('');
        const filtered = rows.length !== all.length;
        const note = filtered
            ? `Showing ${rows.length} of ${all.length} ${T('classes').toLowerCase()}: this total is theirs alone. Every ${T('class').toLowerCase()}, archived included, nets to <strong>${ClassesPage.money(data.company.net_income)}</strong>, the ${T('Profit & Loss')} ${T('Net Income').toLowerCase()} for these dates.`
            : `Every ${T('class').toLowerCase()} is shown: the total is the ${T('Profit & Loss')} for these dates (${T('Net Income').toLowerCase()} <strong>${ClassesPage.money(data.company.net_income)}</strong>).`;
        return `<div class="table-container"><table>
            <thead><tr><th scope="col">${T('Class')}</th><th scope="col">Status</th>
                <th scope="col" class="amount">${T('Income')}</th><th scope="col" class="amount">Cost of Goods Sold</th>
                <th scope="col" class="amount">Expenses</th><th scope="col" class="amount">${T('Net Income')}</th></tr></thead>
            <tbody>${body}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="2">Total${filtered ? ` of the ${rows.length} shown` : ` (${rows.length})`}</td>
                <td class="amount">${ClassesPage.money(sum.income)}</td>
                <td class="amount">${ClassesPage.money(sum.cogs)}</td>
                <td class="amount">${ClassesPage.money(sum.expenses)}</td>
                <td class="amount">${ClassesPage.money(sum.net_income)}</td>
            </tr></tfoot>
        </table></div>
        <p id="classes-total-note" style="font-size:11px; color:var(--gray-500); margin-top:6px;">${escapeHtml(data.start_date)} — ${escapeHtml(data.end_date)}. ${note}</p>`;
    },

    _pageHref(id) {
        const s = ClassesPage._list;
        return ClassesPage._url(`#/classes/${id}`, { period: s.period, start_date: s.start, end_date: s.end });
    },

    // ---- the class's page -----------------------------------------------------

    async renderDetail(id, query) {
        const q = query || {};
        const p = ClassesPage._periodFrom(q, ClassesPage.DEFAULT_PERIOD);
        const tab = Object.hasOwn(ClassesPage.TABS, q.tab || '') ? q.tab : 'overview';
        ClassesPage._page = { id: parseInt(id, 10), tab, ...p };
        try {
            ClassesPage._cls = await API.get(`/classes/${ClassesPage._page.id}`);
        } catch (err) {
            return `<div class="empty-state"><p>${escapeHtml(err.message)}</p><a href="#/classes">Back to ${T('classes').toLowerCase()}</a></div>`;
        }
        ClassesPage._replaceAddress(ClassesPage._pageUrl());
        const c = ClassesPage._cls;
        const dates = { period: p.period, start_date: p.start, end_date: p.end };
        return `
            <div class="page-header">
                <div>
                    <div style="font-size:11px;"><a href="${ClassesPage._listUrlFor(p)}">${T('Classes')}</a> › ${escapeHtml(c.name)}</div>
                    <h2 style="margin:2px 0 0 0;">${escapeHtml(c.name)}
                        ${c.is_system_default ? `<span class="badge" style="font-size:11px; vertical-align:middle;" title="${escapeHtml(Terms.text('Every posting with no class lands here'))}">default</span>` : ''}
                        ${c.is_archived ? '<span class="badge badge-draft" style="font-size:11px; vertical-align:middle;">Archived</span>' : ''}
                    </h2>
                </div>
                <div>
                    ${ClassesPage._reportLink('profit-loss-class', { class_id: c.id, start_date: p.start, end_date: p.end }, `${T('Profit & Loss')} for this ${T('class').toLowerCase()}`)}
                    ${ClassesPage._reportLink('profit-loss-by-class', { ...dates, jump: c.id }, T('P&L by Class'))}
                    ${ClassesPage._settingsLink(c.is_system_default ? 'Settings' : 'Rename / Archive…')}
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                ${Object.entries(ClassesPage.TABS).map(([t, label]) => `
                    <button type="button" class="btn btn-sm ${tab === t ? 'btn-primary' : 'btn-secondary'}" data-classtab="${t}" aria-pressed="${tab === t}" onclick="ClassesPage.setTab('${t}')">${label}</button>`).join('')}
                <span style="margin-left:auto; display:flex; gap:6px; align-items:center;">
                    ${ClassesPage._periodBarHtml('class', p, 'ClassesPage.periodChanged()')}
                </span>
            </div>
            <div id="class-tab-body">${await ClassesPage.tabHtml()}</div>`;
    },

    _listUrlFor(p) {
        return ClassesPage._url('#/classes', { period: p.period, start_date: p.start, end_date: p.end });
    },

    async setTab(tab) {
        if (!Object.hasOwn(ClassesPage.TABS, tab)) return;
        ClassesPage._page.tab = tab;
        $$('[data-classtab]').forEach(b => {
            b.className = `btn btn-sm ${b.dataset.classtab === tab ? 'btn-primary' : 'btn-secondary'}`;
            b.setAttribute('aria-pressed', String(b.dataset.classtab === tab));
        });
        ClassesPage._replaceAddress(ClassesPage._pageUrl());
        const body = $('#class-tab-body');
        if (body) { body.innerHTML = '<p style="color:var(--gray-500);">Loading…</p>'; body.innerHTML = await ClassesPage.tabHtml(); }
    },

    async periodChanged() {
        const p = ClassesPage._readPeriod('class', ClassesPage._page);
        if (p.kept) return;  // the tab on the range before it is still showing
        Object.assign(ClassesPage._page, p);
        ClassesPage._replaceAddress(ClassesPage._pageUrl());
        // the report links carry the page's dates
        const c = ClassesPage._cls;
        const links = document.querySelectorAll('#page-content .page-header a[href^="#/reports/"]');
        links.forEach(a => {
            const href = a.getAttribute('href');
            if (href.startsWith('#/reports/profit-loss-class')) a.setAttribute('href', ReportsPage.viewUrl('profit-loss-class', { class_id: c.id, start_date: p.start, end_date: p.end }));
            else if (href.startsWith('#/reports/profit-loss-by-class')) a.setAttribute('href', ReportsPage.viewUrl('profit-loss-by-class', { jump: c.id, period: p.period, start_date: p.start, end_date: p.end }));
        });
        await ClassesPage.setTab(ClassesPage._page.tab);
    },

    _qs() {
        const s = ClassesPage._page;
        return `start_date=${s.start}&end_date=${s.end}`;
    },

    async tabHtml() {
        try {
            return ClassesPage._page.tab === 'transactions'
                ? await ClassesPage.transactionsHtml()
                : await ClassesPage.overviewHtml();
        } catch (err) {
            return `<div style="color:var(--text-danger);">${escapeHtml(err.message)}</div>`;
        }
    },

    // ---- Overview: the class's P&L figures ------------------------------------
    async overviewHtml() {
        const s = ClassesPage._page;
        const d = await API.get(`/classes/${s.id}/summary?${ClassesPage._qs()}`);
        const c = d.class;
        const stat = (label, value, color, title) => `<div style="min-width:130px;" title="${escapeHtml(title || '')}">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${label}</div>
            <div style="font-size:18px;font-weight:700;${color ? `color:${color}` : ''}">${value}</div></div>`;
        // An account opens its drill-down for this class, with the page as
        // the way back (from=classes): the hop goes through App.navigate so
        // the entry records the page as it was left, and the drill-down's
        // "Back to <class>" returns here through history. The href stays
        // for a new tab, a copied link or the keyboard.
        const drill = (i) => escapeHtml(ReportsPage.viewUrl('account-transactions', {
            account_id: i.account_id, class_id: c.id, start_date: s.start, end_date: s.end, from: 'classes',
        }));
        const section = (items) => items.length
            ? items.map(i => `<tr><td style="padding-left:24px;"><a href="${drill(i)}" onclick="event.preventDefault(); App.navigate(this.getAttribute('href'))" style="color:var(--text-link); text-decoration:none;">${escapeHtml(i.account_label || `${i.account_number ? i.account_number + ' - ' : ''}${i.account_name}`)}</a></td><td class="amount">${ClassesPage.money(i.amount)}</td></tr>`).join('')
            : '<tr><td colspan="2" style="padding-left:24px; color:var(--gray-400);">None</td></tr>';
        const subtotal = (title, amount) => `<tr style="font-weight:600; background:var(--gray-50);"><td>${title}</td><td class="amount">${ClassesPage.money(amount)}</td></tr>`;
        // The class's share of the company's net: a percentage only where
        // one means something — the company in the black and the class
        // not in the red. Signed division read "-0.3% of the company's
        // -$391,838.18" for a profitable class in a losing company, and
        // "103.5%" for the one that lost the most (NEW-31); those say the
        // company's figure beside the class's instead.
        const company = d.company_net_income;
        const share = company > 0 && d.net_income >= 0
            ? `${(d.net_income / company * 100).toFixed(1)}% of the company's ${ClassesPage.money(company)}`
            : company > 0
                ? `a loss against the company's ${ClassesPage.money(company)}`
                : `while the company's ${T('net income').toLowerCase()} for these dates is ${ClassesPage.money(company)}`;
        const fund = Terms.isNonprofit() ? ClassesPage._fundHtml(c) : '';
        return `
            <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:16px" id="class-stats">
                ${stat(T('Income'), ClassesPage.money(d.total_operating_income))}
                ${stat('Cost of goods', ClassesPage.money(d.total_cogs))}
                ${stat('Gross profit', ClassesPage.money(d.operating_gross_profit))}
                ${stat('Expenses', ClassesPage.money(d.total_operating_expenses))}
                ${(d.total_other_income || d.total_other_expense)
                    ? stat('Net other income', ClassesPage.money(d.net_other_income), d.net_other_income < 0 ? 'var(--text-danger)' : null)
                    : ''}
                ${stat(T('Net Income'), ClassesPage.money(d.net_income), d.net_income < 0 ? 'var(--text-danger)' : null, Terms.text(`Net income by class: ${share}`))}
            </div>
            <p id="class-net-note" style="font-size:11px; color:var(--gray-500); margin:0 0 10px;">
                ${formatDate(d.start_date)} — ${formatDate(d.end_date)} · ${T('Net Income')} <strong>${ClassesPage.money(d.net_income)}</strong>, ${escapeHtml(share)}
                (the ${T('P&L by Class')} column for this ${T('class').toLowerCase()}; every ${T('class').toLowerCase()} together is the ${T('Profit & Loss')}).
            </p>
            <div style="display:grid;grid-template-columns:${fund ? '2fr 1fr' : '1fr'};gap:16px;align-items:start;">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">By account — click one for the transactions behind it</h4>
                    <div class="table-container"><table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th></tr></thead>
                        <tbody>
                            <tr><td><strong>${T('Income')}</strong></td><td></td></tr>
                            ${section(d.income)}
                            ${subtotal(T('Total Income'), d.total_operating_income)}
                            <tr><td><strong>Cost of Goods Sold</strong></td><td></td></tr>
                            ${section(d.cogs)}
                            ${subtotal('Gross Profit', d.operating_gross_profit)}
                            <tr><td><strong>Expenses</strong></td><td></td></tr>
                            ${section(d.expenses)}
                            ${subtotal('Total Expenses', d.total_operating_expenses)}
                            ${ReportsPage._plOther(d, (label) => `<tr><td><strong>${label}</strong></td><td></td></tr>`, section, (label, key) => subtotal(label, d[key]))}
                            <tr style="font-weight:700; background:var(--primary-light);"><td>${T('Net Income')}</td><td class="amount">${ClassesPage.money(d.net_income)}</td></tr>
                        </tbody>
                    </table></div>
                </div>
                ${fund}
            </div>`;
    },

    // Nonprofit: a class is a fund; its restriction, default function and
    // donor are set in Settings → Funds (SettingsPage.editFund).
    _fundHtml(c) {
        const labels = (typeof SettingsPage !== 'undefined' && SettingsPage.RESTRICTION_LABELS) || {};
        const functions = (typeof SettingsPage !== 'undefined' && SettingsPage.FUNCTION_LABELS) || {};
        const row = (label, value) => `<div style="margin-bottom:6px;"><div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${label}</div><div>${value || '—'}</div></div>`;
        return `<div style="font-size:13px" id="class-fund">
            <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Fund</h4>
            ${row('Restriction', escapeHtml(labels[c.restriction] || c.restriction || ''))}
            ${row('Default function', escapeHtml(functions[c.default_function] || ''))}
            ${row('Donor / grantor', escapeHtml(c.donor_name || ''))}
            ${row('Purpose', escapeHtml(c.purpose || ''))}
            <div style="margin-top:8px;">${ClassesPage._settingsLink('Edit in Settings…').replace('class="btn btn-secondary"', 'class="btn btn-sm btn-secondary"')}</div>
        </div>`;
    },

    // ---- Transactions: every line tagged to the class --------------------------
    async transactionsHtml() {
        const s = ClassesPage._page;
        const d = await API.get(`/classes/${s.id}/transactions?${ClassesPage._qs()}`);
        if (!d.entries.length) {
            return `<div class="empty-state"><p>Nothing posted to this ${T('class').toLowerCase()} in the period.</p>
                <p style="font-size:12px;color:var(--text-muted)">${Terms.text('Pick the class on an invoice, bill, expense or journal entry, or on one of its lines, to bring it here.')}</p></div>`;
        }
        const label = (t) => (typeof JobsPage !== 'undefined' && JobsPage.sourceLabel) ? JobsPage.sourceLabel(t) : (t || 'Entry');
        const rows = d.entries.map(e => {
            const href = e.source_link || `#/journal/${e.transaction_id}`;
            return `<tr class="clickable" onclick="App.navigate('${escapeHtml(href.replace(/^\//, ''))}')" style="${e.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(e.date)}</td>
                <td><a href="${escapeHtml(href)}" onclick="event.stopPropagation()">${escapeHtml(label(e.source_type))}${e.reference ? ` ${escapeHtml(e.reference)}` : ''}</a></td>
                <td>${e.account_number ? escapeHtml(e.account_number) + ' - ' : ''}${escapeHtml(e.account_name)}</td>
                <td>${escapeHtml(e.description || '')}</td>
                <td class="amount">${e.debit > 0 ? formatCurrency(e.debit) : ''}</td>
                <td class="amount">${e.credit > 0 ? formatCurrency(e.credit) : ''}</td>
            </tr>`;
        }).join('');
        return `<div class="table-container"><table class="data-table" style="font-size:12px" id="class-transactions">
            <thead><tr><th scope="col">Date</th><th scope="col">Source</th><th scope="col">Account</th><th scope="col">Memo</th><th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="4">${d.entries.length} line${d.entries.length === 1 ? '' : 's'}</td>
                <td class="amount">${formatCurrency(d.total_debit)}</td>
                <td class="amount">${formatCurrency(d.total_credit)}</td>
            </tr></tfoot>
        </table></div>
        <p id="class-transactions-note" style="font-size:11px; color:var(--gray-500); margin-top:6px;">
            ${formatDate(s.start)} — ${formatDate(s.end)} · every account, balance-sheet lines included. The income, cost and expense lines net to <strong>${formatCurrency(d.net_income)}</strong>, this ${T('class').toLowerCase()}'s ${T('Net Income').toLowerCase()}. A line opens its document.
        </p>`;
    },
};

window.ClassesPage = ClassesPage;
