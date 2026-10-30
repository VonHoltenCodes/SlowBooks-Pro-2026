/**
 * Collections — the open invoices, what reminder each has had and which is
 * next, with buttons to send the due reminders and the monthly statements
 * (#653). The reminder rules are edited under Settings -> Collections.
 * The As of date is for looking at the list; every send is dated today,
 * so looking ahead never sends the final reminder early.
 */
const CollectionsPage = {
    // the page's view: kept while it is open, reset by render()
    _asOf: '',
    _overdueOnly: false,
    _customerId: '',
    _sort: { key: 'days_overdue', dir: 'desc' },
    _data: null,
    _pending: null,
    _force: false,

    async render() {
        CollectionsPage._asOf = todayISO();
        CollectionsPage._overdueOnly = false;
        CollectionsPage._customerId = '';
        CollectionsPage._sort = { key: 'days_overdue', dir: 'desc' };
        const customers = await API.get('/customers?active_only=true').catch(() => []);
        const opts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        setTimeout(() => CollectionsPage.reload(), 0);
        return `
            <div class="page-header">
                <h2>Collections</h2>
                <div class="btn-group" data-write>
                    <button class="btn btn-primary" onclick="CollectionsPage.sendDueReminders()">Send due reminders</button>
                    <button class="btn btn-secondary" onclick="CollectionsPage.sendStatements()">Send monthly statements</button>
                </div>
            </div>
            <div class="toolbar">
                <label for="coll-as-of" title="Changes what the list shows. Reminders and statements are always sent as of today.">As of</label>
                <input type="date" id="coll-as-of" value="${CollectionsPage._asOf}" onchange="CollectionsPage.filterChanged()">
                <select id="coll-filter" aria-label="Show" onchange="CollectionsPage.filterChanged()">
                    <option value="all">All open</option>
                    <option value="overdue">Overdue only</option>
                </select>
                <select id="coll-customer" aria-label="${escapeHtml(T('Customer'))}" onchange="CollectionsPage.filterChanged()">
                    <option value="">All ${escapeHtml(T('Customers').toLowerCase())}</option>${opts}
                </select>
                <span style="margin-left:auto; font-size:12px;">
                    <a href="#/reports/ar-aging">${escapeHtml(T('A/R Aging'))}</a> &middot;
                    <a href="#/settings" onclick="try { sessionStorage.setItem('settings_focus', 'settings-h-reminder-rules'); } catch (e) {}">Reminder rules</a>
                </span>
            </div>
            <div id="coll-totals" role="status" style="margin:8px 0; font-size:13px;"></div>
            <div id="coll-results"></div>`;
    },

    // Read the controls, fetch, and redraw the results
    filterChanged() {
        CollectionsPage._asOf = $('#coll-as-of')?.value || todayISO();
        CollectionsPage._overdueOnly = $('#coll-filter')?.value === 'overdue';
        CollectionsPage._customerId = $('#coll-customer')?.value || '';
        CollectionsPage.reload();
    },

    async reload() {
        const box = $('#coll-results');
        if (!box) return;
        const q = new URLSearchParams({ as_of_date: CollectionsPage._asOf, overdue_only: CollectionsPage._overdueOnly });
        if (CollectionsPage._customerId) q.set('customer_id', CollectionsPage._customerId);
        try {
            CollectionsPage._data = await API.get(`/collections/invoices?${q}`);
        } catch (err) {
            box.innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
            return;
        }
        CollectionsPage.draw();
    },

    draw() {
        const d = CollectionsPage._data;
        const box = $('#coll-results');
        if (!d || !box) return;
        const t = d.totals;
        $('#coll-totals').innerHTML = `<strong>${t.count}</strong> open ${escapeHtml(T(t.count === 1 ? 'invoice' : 'invoices'))},
            <strong>${formatCurrency(t.balance_due)}</strong> owed &middot;
            <span${t.overdue_count ? ' style="color:var(--text-danger)"' : ''}><strong>${t.overdue_count}</strong> overdue, <strong>${formatCurrency(t.overdue_balance)}</strong></span>`;
        if (!d.items.length) {
            box.innerHTML = `<div class="empty-state"><p>${escapeHtml(Terms.text('No open invoices to collect on.'))}</p></div>`;
            return;
        }
        const { key, dir } = CollectionsPage._sort;
        const sign = dir === 'desc' ? -1 : 1;
        const rows = d.items.slice().sort((a, b) => sign * CollectionsPage._compare(a, b, key));
        const cols = [
            ['customer_name', T('Customer'), ''], ['invoice_number', T('Invoice'), ''], ['date', 'Date', ''],
            ['due_date', 'Due', ''], ['days_overdue', 'Days overdue', 'amount'], ['balance_due', 'Balance', 'amount'],
        ];
        const ths = cols.map(([k, label, cls]) => {
            const active = key === k;
            return `<th scope="col" class="sortable ${cls} ${active ? 'sort-active' : ''}" onclick="CollectionsPage.sortBy('${k}')">${escapeHtml(label)}${active ? ` <span class="sort-arrow">${dir === 'asc' ? '▲' : '▼'}</span>` : ''}</th>`;
        }).join('');
        box.innerHTML = `<div class="table-container"><table>
            <thead><tr>${ths}<th scope="col">Last reminder</th><th scope="col">Next reminder</th><th scope="col">Actions</th></tr></thead>
            <tbody>${rows.map(r => CollectionsPage._rowHtml(r)).join('')}</tbody></table></div>`;
    },

    _compare(a, b, key) {
        const av = a[key], bv = b[key];
        if (typeof av === 'number' && typeof bv === 'number') return av - bv;
        return String(av ?? '').toLowerCase().localeCompare(String(bv ?? '').toLowerCase(), undefined, { numeric: true });
    },

    sortBy(key) {
        const s = CollectionsPage._sort;
        if (s.key === key) s.dir = s.dir === 'asc' ? 'desc' : 'asc';
        else CollectionsPage._sort = { key, dir: key === 'customer_name' || key === 'invoice_number' ? 'asc' : 'desc' };
        CollectionsPage.draw();
    },

    // Negative days are not due yet: "in N days"; overdue ones in red
    _daysHtml(n) {
        if (n > 0) return `<span style="color:var(--text-danger);font-weight:600">${n}</span>`;
        if (n < 0) return `in ${-n} day${n === -1 ? '' : 's'}`;
        return 'due today';
    },

    _lastHtml(last) {
        if (!last) return '—';
        const when = formatDate(last.sent_at);
        return last.status === 'failed'
            ? `<span style="color:var(--text-danger)">${escapeHtml(last.label)}: failed ${escapeHtml(when)}</span>`
            : `${escapeHtml(last.label)}<div style="font-size:11px;color:var(--text-muted)">${escapeHtml(when)}</div>`;
    },

    _nextHtml(r) {
        const n = r.next_reminder;
        if (!r.reminders_enabled) return `<span style="color:var(--text-muted)" title="${escapeHtml(r.skip_reason || '')}">off</span>`;
        if (!n) return '—';
        const when = n.ready ? '<strong>ready</strong>' : escapeHtml(formatDate(n.due_on));
        // A tenant billed with its property is reminded wherever Settings says; the row names who
        const to = r.billed_to_name && r.reminder_to ? `<div style="font-size:11px;color:var(--text-muted)">to ${escapeHtml(r.reminder_to)}</div>` : '';
        return `${escapeHtml(n.label)}<div style="font-size:11px;color:var(--text-muted)">${when}</div>${to}`;
    },

    _rowHtml(r) {
        const num = r.invoice_number || `#${r.invoice_id}`;
        return `<tr>
            <td><a href="#/customers/${r.customer_id}">${escapeHtml(r.customer_name)}</a>${r.billed_to_name ? `<div style="font-size:11px;color:var(--text-muted)">billed to ${escapeHtml(r.billed_to_name)}</div>` : ''}</td>
            <td><a href="#/invoices/${r.invoice_id}">${escapeHtml(num)}</a></td>
            <td>${escapeHtml(formatDate(r.date))}</td>
            <td>${escapeHtml(formatDate(r.due_date))}</td>
            <td class="amount">${CollectionsPage._daysHtml(r.days_overdue)}</td>
            <td class="amount">${formatCurrency(r.balance_due)}</td>
            <td>${CollectionsPage._lastHtml(r.last_reminder)}</td>
            <td>${CollectionsPage._nextHtml(r)}</td>
            <td class="actions"><button type="button" class="btn btn-sm btn-secondary" data-write onclick="CollectionsPage.remind(${r.invoice_id}, this)">Remind now</button></td>
        </tr>`;
    },

    // One invoice, now
    async remind(invoiceId, btn) {
        if (btn) btn.disabled = true;
        try {
            const res = await API.post(`/collections/invoices/${invoiceId}/remind`);
            toast(`Reminder sent: ${res.label}`);
        } catch (err) {
            toast(err.message, 'error'); // the API's sentence is meant for the screen
        }
        CollectionsPage.reload();
    },

    // "3 no email, 1 customer has reminders off" from the skipped reasons
    _reasonSummary(list) {
        const counts = {};
        for (const s of list) counts[s.reason] = (counts[s.reason] || 0) + 1;
        return Object.entries(counts).map(([reason, n]) => `${n} ${reason}`).join(', ');
    },

    _confirmDialog(title, bodyHtml, yesLabel, onYes) {
        CollectionsPage._pending = onYes;
        openModal(title, `${bodyHtml}
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="button" class="btn btn-primary" onclick="CollectionsPage._yes()">${escapeHtml(yesLabel)}</button>
            </div>`);
    },

    _yes() {
        const go = CollectionsPage._pending;
        CollectionsPage._pending = null;
        CollectionsPage._force = !!$('#coll-force')?.checked; // read before the dialog goes
        closeModal();
        if (go) go();
    },

    // Show a batch's result: a toast, and the errors listed when there are any
    _showResult(title, res) {
        let headline = `Sent ${res.sent}, failed ${res.failed}, skipped ${res.skipped}`;
        if (res.taken) headline += ` (${res.taken} sent by another window)`;
        const errors = res.errors || [];
        if (errors.length && typeof ReportsPage !== 'undefined' && ReportsPage._sendResult) {
            ReportsPage._sendResult(title, headline + '.', errors);
        } else {
            toast(errors.length ? `${headline}. ${errors.join(' ')}` : headline, res.failed ? 'warning' : 'success');
        }
        CollectionsPage.reload();
    },

    // "Acme · 1001 · 7 days overdue → acme@example.com", for the confirm dialogs
    _dueLine(i) {
        return `<li>${escapeHtml(i.customer_name)} &middot; ${escapeHtml(i.invoice_number || '#' + i.invoice_id)} &middot; ${escapeHtml(i.label)}` +
            `<span style="color:var(--text-muted)"> &rarr; ${escapeHtml(i.recipient || '')}</span></li>`;
    },

    // What a run would send today: the list and the dialog body, shared with
    // Settings' switch-on preview. Resolves null when nothing is due.
    async dueSummary() {
        const due = await API.get('/collections/reminders/due');
        if (!due.items.length) return { due, html: '' };
        const lines = due.items.slice(0, 8).map(i => CollectionsPage._dueLine(i)).join('');
        const more = due.items.length > 8 ? `<li>and ${due.items.length - 8} more</li>` : '';
        const skipped = due.skipped.length
            ? `<p style="color:var(--text-muted)">${due.skipped.length} left out: ${escapeHtml(CollectionsPage._reasonSummary(due.skipped))}.</p>` : '';
        return { due, html: `<ul style="margin:8px 0 12px 20px;">${lines}${more}</ul>${skipped}` };
    },

    async sendDueReminders() {
        let summary;
        try { summary = await CollectionsPage.dueSummary(); } catch (err) { toast(err.message, 'error'); return; }
        const due = summary.due;
        if (!due.items.length) {
            const why = due.skipped.length ? ` ${due.skipped.length} left out: ${CollectionsPage._reasonSummary(due.skipped)}.` : '';
            toast(`No reminders are due today.${why}`, 'info');
            return;
        }
        const today = CollectionsPage._asOf !== todayISO()
            ? `<p style="color:var(--text-muted)">Reminders are sent as of today, not the As of date shown.</p>` : '';
        CollectionsPage._confirmDialog('Send due reminders', `
            <p>Send <strong>${due.items.length}</strong> reminder${due.items.length === 1 ? '' : 's'} by email?</p>${summary.html}${today}`,
            `Send ${due.items.length}`, async () => {
                try {
                    const res = await API.post('/collections/reminders/run', { invoice_ids: null });
                    CollectionsPage._showResult('Reminders', res);
                } catch (err) { toast(err.message, 'error'); }
            });
    },

    async sendStatements() {
        let due;
        try { due = await API.get('/collections/statements/due'); } catch (err) { toast(err.message, 'error'); return; }
        const ready = due.items.filter(i => i.will_send);
        const held = due.items.filter(i => !i.will_send);
        const already = held.filter(i => i.reason === 'already sent this month').length;
        if (!due.items.length) {
            toast('No customer is set up for a monthly statement, or none has activity to report.', 'info');
            return;
        }
        const skipped = held.length
            ? `<p style="color:var(--text-muted)">${held.length} left out: ${escapeHtml(CollectionsPage._reasonSummary(held.map(h => ({ reason: h.reason || 'held' }))))}.</p>` : '';
        const force = already
            ? `<p><label style="font-weight:normal;"><input type="checkbox" id="coll-force"> Include customers already sent this month (${already})</label></p>` : '';
        CollectionsPage._confirmDialog('Send monthly statements', `
            <p>Email a statement to <strong>${ready.length}</strong> customer${ready.length === 1 ? '' : 's'}?</p>${skipped}${force}`,
            'Send statements', async () => {
                try {
                    const res = await API.post('/collections/statements/run', { force: CollectionsPage._force });
                    CollectionsPage._showResult('Statements', res);
                } catch (err) { toast(err.message, 'error'); }
            });
    },
};
window.CollectionsPage = CollectionsPage;
