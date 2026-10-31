/**
 * Memorized transactions (#605): every schedule in one place (recurring
 * invoices, memorized bills, memorized journal entries) with Enter now,
 * pause and resume, edit and delete, and a Due filter.
 *
 * A memorized bill's form draws Enter Bill's line grid (BillsPage.lineHtml,
 * recalc, collectLines); a memorized journal entry's draws the journal
 * form's (JournalPage.addLine, fillLine, recalc, collectLines). The list's
 * "invoice" rows are recurring invoices: their edit form is RecurringPage's,
 * their API /recurring; the rest live under /memorized.
 */
const MemorizedPage = {
    _rows: [],
    _filter: 'all',
    // the scheduler run the page has already reported (its ran_at)
    _shownRun: null,

    // The How often choices: key, words, and the frequency + interval each means.
    FREQUENCIES: [
        ['daily', 'Daily', 'daily', 1],
        ['weekly', 'Weekly', 'weekly', 1],
        ['biweekly', 'Every two weeks', 'weekly', 2],
        ['semimonthly', 'Twice a month', 'semimonthly', 1],
        ['monthly', 'Monthly', 'monthly', 1],
        ['bimonthly', 'Every two months', 'monthly', 2],
        ['quarterly', 'Quarterly', 'quarterly', 1],
        ['semiannual', 'Twice a year', 'monthly', 6],
        ['yearly', 'Yearly', 'yearly', 1],
        ['custom', 'Every N days, weeks or months...', null, null],
    ],

    async render() {
        const [rows, settings, lastRun] = await Promise.all([
            API.get('/memorized'),
            API.get('/settings').catch(() => ({})),
            API.get('/memorized/entered-recently').catch(() => null),
        ]);
        MemorizedPage._rows = rows;
        const due = rows.filter(r => r.is_due).length;
        const autoEnter = !settings || settings.auto_enter_on_open !== 'false';
        return `
            <div class="page-header">
                <h2>Memorized Transactions</h2>
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="MemorizedPage.showBillForm()">+ Memorized Bill</button>
                    <button class="btn btn-primary" onclick="MemorizedPage.showJournalForm()">+ Memorized Journal Entry</button>
                    <button class="btn btn-secondary" onclick="RecurringPage.showForm()">+ Recurring ${T('Invoice')}</button>
                    <button id="mem-enter-all" class="btn btn-secondary" title="Enter every schedule that is due, recurring invoices included, every missed date in order" onclick="MemorizedPage.enterAllDue(this)">Enter All Due</button>
                </div>
            </div>
            ${MemorizedPage.whileAwayHtml(lastRun)}
            <div class="filters" style="display:flex; gap:12px; align-items:center; margin-bottom:12px;">
                <label for="mem-filter" style="font-weight:700;">Show</label>
                <select id="mem-filter" data-no-search onchange="MemorizedPage.setFilter(this.value)">
                    <option value="all" ${MemorizedPage._filter === 'all' ? 'selected' : ''}>All schedules (${rows.length})</option>
                    <option value="due" ${MemorizedPage._filter === 'due' ? 'selected' : ''}>Due now (${due})</option>
                </select>
                ${due ? `<span class="badge badge-partial" title="Schedules whose next date is today or earlier">${due} due</span>` : ''}
            </div>
            <div id="mem-table">${MemorizedPage.tableHtml(rows)}</div>
            <div class="form-group" style="margin-top:12px;">
                <label style="font-weight:normal;"><input type="checkbox" id="mem-auto-enter" name="auto_enter_on_open" value="true" data-admin style="width:auto; vertical-align:middle; margin-right:6px;" ${autoEnter ? 'checked' : ''} onchange="MemorizedPage.setAutoEnter(this)">Enter due memorized bills and journal entries when the app opens and once a day</label>
            </div>
            <p class="hint" style="margin-top:8px;">An auto-enter memorized bill or journal entry is entered on its date: the app enters every due one when it opens and once a day while it runs, every missed date in order, once someone has signed in. Recurring ${T('invoices')} wait, as before, for Generate or Enter All Due, which enters every due schedule of both kinds at once. A remind schedule waits on the Due now list until you press Enter now. Paused schedules enter nothing; resuming one skips the dates it missed. A server install can run scripts/run_recurring.py from cron instead.</p>`;
    },

    // What the app entered by itself (when it opened, or its daily run)
    // since the page last said so: a sentence, and each document as a link.
    // A run that entered nothing but had to skip something says what could
    // not be entered, and why; a run that did neither says nothing.
    whileAwayHtml(run) {
        if (!run || !run.ran_at || run.ran_at === MemorizedPage._shownRun) return '';
        const n = (run.invoices_created || 0) + (run.entered_count || 0);
        const held = run.skipped || [];
        if (!n && !held.length) return '';
        MemorizedPage._shownRun = run.ran_at;
        const sentence = n
            ? `Entered ${n} scheduled document${n === 1 ? '' : 's'} while you were away`
            : `${held.length} scheduled document${held.length === 1 ? '' : 's'} could not be entered`;
        if (n) toastPosted(sentence);
        const items = (run.entered || []).map(e => {
            const href = e.kind === 'bill' ? `#/bills/${e.document_id}` : `#/journal/${e.document_id}`;
            return `<li><a href="${href}">${escapeHtml(e.name)}</a> dated ${formatDate(e.date)}</li>`;
        });
        (run.invoice_ids || []).forEach(id => items.push(`<li><a href="#/invoices/${id}">Recurring ${T('invoice')}</a></li>`));
        return `<div class="hint" role="status" style="margin-bottom:12px;">
            <strong>${sentence}</strong>${run.origin === 'daily' ? " (today's run)" : ''}.
            ${items.length ? `<ul style="margin:6px 0 0 18px;">${items.join('')}</ul>` : ''}
            ${held.length ? `${n ? '<div style="margin-top:6px;">Not entered:</div>' : ''}<ul style="margin:${n ? '0' : '6px'} 0 0 18px;">${held.map(s => `<li>${escapeHtml(s.message)}</li>`).join('')}</ul>` : ''}
        </div>`;
    },

    // The setting: the app enters due schedules when it opens and once a day.
    async setAutoEnter(box) {
        try {
            await API.put('/settings', { auto_enter_on_open: box.checked ? 'true' : 'false' });
            toast(box.checked ? 'Due memorized bills and journal entries are entered when the app opens and once a day' : 'Due schedules wait for Enter All Due or Enter now');
        } catch (err) {
            box.checked = !box.checked;
            toast(err.message, 'error');
        }
    },

    setFilter(value) {
        MemorizedPage._filter = value;
        const box = $('#mem-table');
        if (box) box.innerHTML = MemorizedPage.tableHtml(MemorizedPage._rows);
    },

    tableHtml(rows) {
        const shown = MemorizedPage._filter === 'due' ? rows.filter(r => r.is_due) : rows;
        if (shown.length === 0) {
            return `<div class="empty-state"><p>${MemorizedPage._filter === 'due' ? 'Nothing is due.' : 'No memorized transactions yet. Memorize a bill or a journal entry from its page, or start one here.'}</p></div>`;
        }
        return `<div class="table-container"><table>
            <thead><tr>
                <th scope="col">Name</th><th scope="col">Type</th><th scope="col">Next date</th><th scope="col">How often</th>
                <th scope="col">Mode</th><th scope="col">Last entered</th><th scope="col">Status</th><th scope="col" class="amount">Amount</th><th scope="col">Actions</th>
            </tr></thead>
            <tbody>${shown.map(r => MemorizedPage.rowHtml(r)).join('')}</tbody>
        </table></div>`;
    },

    kindWord(r) {
        if (r.kind === 'invoice') return `Recurring ${T('invoice')}`;
        return r.kind === 'bill' ? 'Memorized bill' : 'Memorized journal entry';
    },

    // The Mode column. A recurring invoice's row (mode 'generate') is made
    // only when someone asks for it, or by a server's cron: the app's own
    // runs, when it opens and once a day, never make one (2.31.1 gate W-86:
    // the column said Auto-enter).
    modeHtml(r) {
        if (r.mode === 'generate') {
            return `<span title="Made when you press Generate or Enter All Due (or Enter now here), or by the server's cron. The app does not make recurring ${T('invoices')} on its own.">Generated</span>`;
        }
        return r.mode === 'auto' ? 'Auto-enter' : 'Remind';
    },

    statusHtml(r) {
        if (r.status === 'ended') return '<span class="badge badge-draft">Ended</span>';
        if (r.status === 'paused') return '<span class="badge badge-draft">Paused</span>';
        if (r.is_due) return '<span class="badge badge-partial">Due</span>';
        return '<span class="badge badge-paid">Active</span>';
    },

    // The document entered last, as a link to it.
    lastEnteredHtml(r) {
        if (!r.last_entered_on) return '<span style="color:var(--text-muted);">never</span>';
        const href = r.last_document_id
            ? (r.kind === 'invoice' ? `#/invoices/${r.last_document_id}` : r.kind === 'bill' ? `#/bills/${r.last_document_id}` : `#/journal/${r.last_document_id}`)
            : null;
        const when = formatDate(r.last_entered_on);
        return href ? `<a href="${href}">${when}</a>` : when;
    },

    rowHtml(r) {
        const args = `'${r.kind}', ${r.id}`;
        const ended = r.status === 'ended';
        return `<tr data-kind="${r.kind}" data-id="${r.id}">
            <td><strong>${escapeHtml(r.name)}</strong>${r.party && r.kind !== 'invoice' ? `<div style="font-size:11px; color:var(--text-muted);">${escapeHtml(r.party)}</div>` : ''}</td>
            <td>${MemorizedPage.kindWord(r)}</td>
            <td>${formatDate(r.next_date)}${r.end_date ? `<div style="font-size:11px; color:var(--text-muted);">until ${formatDate(r.end_date)}</div>` : ''}${r.occurrences ? `<div style="font-size:11px; color:var(--text-muted);">${r.entered_count} of ${r.occurrences} entered</div>` : ''}</td>
            <td>${escapeHtml(r.frequency_label)}</td>
            <td>${MemorizedPage.modeHtml(r)}</td>
            <td>${MemorizedPage.lastEnteredHtml(r)}</td>
            <td>${MemorizedPage.statusHtml(r)}</td>
            <td class="amount" style="font-family:var(--font-mono);">${formatCurrency(r.amount)}</td>
            <td class="actions">
                ${ended || r.status === 'paused' ? '' : `<button class="btn btn-sm btn-primary" onclick="MemorizedPage.enter(${args})">Enter now</button>`}
                ${ended ? '' : (r.status === 'paused'
                    ? `<button class="btn btn-sm btn-secondary" onclick="MemorizedPage.resume(${args})">Resume</button>`
                    : `<button class="btn btn-sm btn-secondary" onclick="MemorizedPage.pause(${args})">Pause</button>`)}
                <button class="btn btn-sm btn-secondary" onclick="MemorizedPage.edit(${args})">Edit</button>
                <button data-destructive class="btn btn-sm btn-danger" onclick="MemorizedPage.del(${args})">Delete</button>
            </td>
        </tr>`;
    },

    row(kind, id) {
        return MemorizedPage._rows.find(r => r.kind === kind && r.id === Number(id)) || null;
    },

    // ----------------------------------------------------------- actions

    // Enter now: the occurrence this row shows, dated its scheduled date or
    // today. The request names that occurrence, so one entered meanwhile
    // (another window, Enter All Due, the scheduler) is refused, not the
    // next one posted early.
    enter(kind, id) {
        const r = MemorizedPage.row(kind, id);
        const scheduled = r ? r.next_date : todayISO();
        openModal(`Enter ${r ? escapeHtml(r.name) : 'now'}`, `
            <form onsubmit="MemorizedPage.doEnter(event, '${kind}', ${Number(id)})">
                <input type="hidden" name="occurrence" value="${r ? escapeHtml(r.next_date) : ''}">
                <p style="margin-bottom:8px;">Enter the ${kind === 'invoice' ? T('invoice') : kind === 'bill' ? 'bill' : 'journal entry'} dated:</p>
                <div class="form-group">
                    <label style="font-weight:normal;"><input type="radio" name="dated" value="" checked> The scheduled date, ${formatDate(scheduled)}</label>
                </div>
                <div class="form-group">
                    <label style="font-weight:normal;"><input type="radio" name="dated" value="${todayISO()}"> Today, ${formatDate(todayISO())}</label>
                </div>
                <div class="hint">Either way the schedule moves on from ${formatDate(scheduled)}.</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Enter</button>
                </div>
            </form>`);
    },

    async doEnter(e, kind, id) {
        e.preventDefault();
        const form = e.target;
        // one request at a time: a second click (or Enter) while the first
        // is in flight entered the same occurrence twice
        const button = form.querySelector('button[type="submit"]');
        if (button && button.disabled) return;
        if (button) button.disabled = true;
        const dated = form.dated.value || null;
        const occurrence = form.occurrence.value || null;
        try {
            if (kind === 'invoice') {
                const query = [occurrence ? `occurrence=${occurrence}` : '', dated ? `dated=${dated}` : ''].filter(Boolean).join('&');
                const result = await API.post(`/recurring/${id}/generate` + (query ? `?${query}` : ''));
                toastPosted(`${T('Invoice')} created; next on ${formatDate(result.next_due)}`);
            } else {
                const body = {};
                if (occurrence) body.occurrence = occurrence;
                if (dated) body.date = dated;
                const result = await API.post(`/memorized/${id}/enter`, body);
                toastPosted(result.message);
            }
            closeModal();
            App.navigate('#/memorized');
        } catch (err) {
            toast(err.message, 'error');
            if (button) button.disabled = false;
            // entered meanwhile (another window, the scheduler), or ended or
            // paused: show the schedule as it now stands
            if (err.status === 409) { closeModal(); App.navigate('#/memorized'); }
        }
    },

    async pause(kind, id) {
        try {
            if (kind === 'invoice') await API.put(`/recurring/${id}`, { is_active: false });
            else await API.post(`/memorized/${id}/pause`);
            toast('Paused');
            App.navigate('#/memorized');
        } catch (err) { toast(err.message, 'error'); }
    },

    async resume(kind, id) {
        try {
            const r = kind === 'invoice'
                ? await API.put(`/recurring/${id}`, { is_active: true })
                : await API.post(`/memorized/${id}/resume`);
            toast(`Resumed; next on ${formatDate(r.next_date || r.next_due)}`);
            App.navigate('#/memorized');
        } catch (err) { toast(err.message, 'error'); }
    },

    edit(kind, id) {
        if (kind === 'invoice') return RecurringPage.showForm(id);
        if (kind === 'bill') return MemorizedPage.showBillForm(id);
        return MemorizedPage.showJournalForm(id);
    },

    async del(kind, id) {
        const r = MemorizedPage.row(kind, id);
        if (!confirm(`Delete ${r ? r.name : 'this schedule'}? What it has already entered stays in the books.`)) return;
        try {
            if (kind === 'invoice') await API.del(`/recurring/${id}`);
            else await API.del(`/memorized/${id}`);
            toast('Deleted');
            App.navigate('#/memorized');
        } catch (err) { toast(err.message, 'error'); }
    },

    // The scheduler's run by hand: due recurring invoices and due auto-enter
    // memorized transactions, every missed date in order. Remind schedules
    // stay on the list. The button is disabled while the run is in flight.
    async enterAllDue(button) {
        if (button && button.disabled) return;
        if (button) button.disabled = true;
        try {
            const run = await API.post('/memorized/enter-due');
            toastPosted(`Entered ${run.invoices_created} ${T('invoice')}(s) and ${run.entered_count} memorized transaction(s)`);
            (run.skipped || []).forEach(s => toast(s.message, 'error'));
            App.navigate('#/memorized');
        } catch (err) {
            toast(err.message, 'error');
            if (button) button.disabled = false;
        }
    },

    // ----------------------------------------------------------- the schedule fields

    choiceFor(frequency, interval) {
        const hit = MemorizedPage.FREQUENCIES.find(f => f[2] === frequency && f[3] === Number(interval || 1));
        return hit ? hit[0] : 'custom';
    },

    scheduleFieldsHtml(mem, isNew) {
        const choice = MemorizedPage.choiceFor(mem.frequency, mem.interval);
        const unit = ['daily', 'weekly', 'monthly'].includes(mem.frequency) ? mem.frequency : 'monthly';
        return `
            <div class="form-group"><label>Name *</label>
                <input name="name" required maxlength="120" value="${escapeHtml(mem.name || '')}" placeholder="Rent, Monthly depreciation..."></div>
            <div class="form-group"><label>How to enter it</label>
                <select name="mode" data-no-search>
                    <option value="remind" ${mem.mode !== 'auto' ? 'selected' : ''}>Remind me: listed as due until I enter it</option>
                    <option value="auto" ${mem.mode === 'auto' ? 'selected' : ''}>Auto-enter: posted on its date (when the app opens, or its daily run)</option>
                </select></div>
            <div class="form-group"><label>How often *</label>
                <select name="frequency_choice" data-no-search onchange="MemorizedPage.frequencyChanged(this)">
                    ${MemorizedPage.FREQUENCIES.map(f => `<option value="${f[0]}" ${f[0] === choice ? 'selected' : ''}>${f[1]}</option>`).join('')}
                </select>
                <div id="mem-custom" style="display:${choice === 'custom' ? 'flex' : 'none'}; gap:6px; align-items:center; margin-top:6px;">
                    Every <input name="interval" type="number" min="1" step="1" value="${Number(mem.interval) || 1}" style="width:70px;" aria-label="Every how many">
                    <select name="unit" data-no-search aria-label="Days, weeks or months">
                        <option value="daily" ${unit === 'daily' ? 'selected' : ''}>days</option>
                        <option value="weekly" ${unit === 'weekly' ? 'selected' : ''}>weeks</option>
                        <option value="monthly" ${unit === 'monthly' ? 'selected' : ''}>months</option>
                    </select>
                </div>
                <div class="hint">Twice a month is the next date's day and fifteen days later (the 1st and the 16th; the 15th and the 30th). A monthly schedule begun on the 31st is entered on the last day of a shorter month.</div></div>
            <div class="form-group"><label>${isNew ? 'First date *' : 'Next date *'}</label>
                <input name="next_date" type="date" required value="${mem.next_date || todayISO()}"></div>
            <div class="form-group"><label>End date</label>
                <input name="end_date" type="date" value="${mem.end_date || ''}"></div>
            <div class="form-group"><label>Number of times</label>
                <input name="occurrences" type="number" min="1" step="1" value="${mem.occurrences || ''}" placeholder="no limit" title="Stop after this many entries"></div>`;
    },

    frequencyChanged(select) {
        const box = $('#mem-custom');
        if (box) box.style.display = select.value === 'custom' ? 'flex' : 'none';
    },

    // The schedule as the API takes it, from the form's fields.
    scheduleFromForm(form, isNew) {
        const choice = MemorizedPage.FREQUENCIES.find(f => f[0] === form.frequency_choice.value);
        const custom = !choice || choice[0] === 'custom';
        const data = {
            name: form.name.value.trim(),
            mode: form.mode.value,
            frequency: custom ? form.unit.value : choice[2],
            interval: custom ? (parseInt(form.interval.value, 10) || 1) : choice[3],
            end_date: form.end_date.value || null,
            occurrences: form.occurrences.value ? parseInt(form.occurrences.value, 10) : null,
        };
        data[isNew ? 'start_date' : 'next_date'] = form.next_date.value;
        return data;
    },

    // ----------------------------------------------------------- a memorized bill

    async showBillForm(id = null, prefill = null) {
        const [vendors, items, accounts, mem] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/accounts'),
            id ? API.get(`/memorized/${id}`) : Promise.resolve(null),
        ]);
        const m = mem || Object.assign({ mode: 'remind', frequency: 'monthly', interval: 1, lines: [] }, prefill || {});
        if (m.vendor_id && !vendors.some(v => v.id === m.vendor_id)) {
            vendors.push({ id: m.vendor_id, name: m.vendor_name || `Vendor #${m.vendor_id}` });
        }
        // Enter Bill's grid draws from BillsPage's own state
        BillsPage._items = items;
        BillsPage._accounts = PurchaseAccounts.filter(accounts, ...m.lines.map(l => l.account_id));
        BillsPage._vendors = vendors;
        BillsPage._defaultExpenseAccountId = null;
        BillsPage._taxRate = Number(m.tax_rate) || 0;
        BillsPage._summaryFor = null;
        BillsPage.lineCount = 0;
        const classGroup = await classFormGroupHtml(m.class_id);
        const jobGroup = await jobFormGroupHtml(m.job_id || null);
        await CostCodes.load();
        await CostTypes.load();
        await Nonprofit.loadFunds();
        const terms = m.terms || '';
        // the terms list (#664); each bill's due date follows its rule
        await PaymentTerms.load();

        openModal(id ? `Edit Memorized Bill: ${escapeHtml(m.name)}` : 'New Memorized Bill', `
            <form id="mem-form" aria-labelledby="modal-title" data-skip-to="#bill-lines" data-skip-text="Skip to the line items" onsubmit="MemorizedPage.saveBill(event, ${id ? Number(id) : 'null'})">
                <div class="form-grid">
                    ${MemorizedPage.scheduleFieldsHtml(m, !id)}
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'mem-vendor', onchange: 'BillsPage.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label>Bill Number</label>
                        <input name="bill_number" placeholder="left blank: the date and the vendor's initials" value="${escapeHtml(m.bill_number || '')}" aria-describedby="mem-bill-number-hint">
                        <div class="hint" id="mem-bill-number-hint">Left blank, each bill is numbered the date and the vendor's initials, as Enter Bill numbers one. A number typed here is used as it is on the first bill and -2, -3... after that, since a vendor's bill number cannot repeat. Memorize from a bill leaves it blank on purpose.</div></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" title="The due date follows from the terms">
                            ${PaymentTerms.optionsHtml(terms, "The vendor's terms")}
                        </select></div>
                    ${classGroup}${jobGroup}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <div class="table-container table-container--scroll"><table class="line-items-table bill-lines-table">
                    <thead><tr><th scope="col">Item</th><th scope="col" title="Where the line is recorded">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}${CostTypes.headHtml()}${Nonprofit.headHtml()}<th scope="col" title="Billable to the job's customer">Bill?</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="bill-lines"></tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="BillsPage.addLine()">+ Add Line</button>
                <div class="invoice-totals">
                    ${BillsPage._taxRate ? `<div class="total-row"><span class="label">Subtotal</span><span class="value" id="bill-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax (${Number((BillsPage._taxRate * 100).toFixed(4))}%)</span><span class="value" id="bill-tax">$0.00</span></div>` : ''}
                    <div class="total-row grand-total"><span class="label">Each bill</span><span class="value" id="bill-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(m.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Save Changes' : 'Memorize'}</button>
                </div>
            </form>`, { wide: true });
        typeof LineOrder !== 'undefined' && LineOrder.attach('#bill-lines', { add: () => BillsPage.addLine(), changed: () => BillsPage.recalc() });
        const vendorSel = document.getElementById('mem-vendor');
        if (vendorSel && m.vendor_id) {
            vendorSel.value = m.vendor_id;
            BillsPage.vendorSelected(m.vendor_id);
            // the template's own terms, not the vendor's, when it has them
            const form = $('#mem-form');
            if (form && form.terms) form.terms.value = terms;
        }
        if (m.lines.length) m.lines.forEach(l => BillsPage.addLine(l));
        else BillsPage.addLine();
        BillsPage.recalc();
    },

    async saveBill(e, id) {
        e.preventDefault();
        const form = e.target;
        const { lines, missing } = BillsPage.collectLines();
        // a commitment link is a converted bill's, never a template's
        lines.forEach(l => { delete l.purchase_order_line_id; });
        if (BillsPage.recalc() <= 0) {
            toast('Enter what the vendor charges: an amount, or a quantity and rate, on at least one line.', 'error');
            return;
        }
        if (missing) {
            toast(`Choose an account for line ${missing.n}: where should it be recorded?`, 'error');
            missing.sel.focus();
            return;
        }
        if (!(await ClassWarn.ok(form, { rows: '#bill-lines tr', cls: 'line-function' }))) return;
        try {
            const vendorId = await VendorQuickAdd.ensure('mem-vendor');
            const body = {
                ...MemorizedPage.scheduleFromForm(form, !id),
                vendor_id: vendorId,
                // the rate the totals were shown with (a bill memorized from one that carried a rate)
                tax_rate: BillsPage._taxRate || 0,
                bill_number: form.bill_number.value.trim() || null,
                terms: form.terms.value || null,
                notes: form.notes.value || null,
                class_id: classIdFromForm(form),
                job_id: jobIdFromForm(form),
                lines,
            };
            if (id) { await API.put(`/memorized/${id}`, body); toast('Memorized bill updated'); }
            else { await API.post('/memorized', { kind: 'bill', ...body }); toast('Bill memorized'); }
            closeModal();
            App.navigate('#/memorized');
        } catch (err) { toast(err.message, 'error'); }
    },

    // From a bill's page: the same form, filled from the bill.
    async memorizeBill(billId) {
        const bill = await API.get(`/bills/${billId}`);
        closeModal();
        MemorizedPage.showBillForm(null, {
            name: `${bill.vendor_name || 'Bill'}${bill.vendor_name ? ' bill' : ''}`,
            vendor_id: bill.vendor_id,
            vendor_name: bill.vendor_name,
            terms: bill.terms,
            tax_rate: bill.tax_rate,
            notes: bill.notes,
            class_id: bill.class_id,
            job_id: bill.job_id,
            lines: bill.lines,
        });
    },

    // ----------------------------------------------------------- a memorized journal entry

    async showJournalForm(id = null, prefill = null) {
        const [accounts, mem] = await Promise.all([
            API.get('/accounts'),
            id ? API.get(`/memorized/${id}`) : Promise.resolve(null),
        ]);
        const m = mem || Object.assign({ mode: 'remind', frequency: 'monthly', interval: 1, basis: 'both', lines: [] }, prefill || {});
        const classGroup = await classFormGroupHtml(m.class_id);
        const jobGroup = await jobFormGroupHtml(m.job_id || null);
        await CostCodes.load();
        await CostTypes.load();
        await Nonprofit.loadFunds();
        // the journal form's grid draws from JournalPage's own state
        JournalPage._accounts = accounts;
        JournalPage._lineCount = 0;

        openModal(id ? `Edit Memorized Journal Entry: ${escapeHtml(m.name)}` : 'New Memorized Journal Entry', `
            <form id="mem-form" aria-labelledby="modal-title" data-accountant-ok data-skip-to="#je-lines" data-skip-text="Skip to the lines" onsubmit="MemorizedPage.saveJournal(event, ${id ? Number(id) : 'null'})">
                <div class="form-grid">
                    ${MemorizedPage.scheduleFieldsHtml(m, !id)}
                    <div class="form-group"><label>Reference</label>
                        <input name="reference" value="${escapeHtml(m.reference || '')}"></div>
                    ${classGroup}${jobGroup}
                    <div class="form-group full-width"><label>Description *</label>
                        <input name="description" required value="${escapeHtml(m.description || '')}"></div>
                    <div class="form-group full-width">
                        <label style="display:flex; align-items:center; gap:6px; font-weight:400;">
                            <input type="checkbox" name="is_adjusting" ${m.is_adjusting ? 'checked' : ''}>
                            Adjusting entry
                        </label>
                    </div>
                    <div class="form-group">
                        <label for="je-basis">Basis</label>
                        <select id="je-basis" name="basis" data-no-search>
                            <option value="both" ${!m.basis || m.basis === 'both' ? 'selected' : ''}>Both</option>
                            <option value="accrual" ${m.basis === 'accrual' ? 'selected' : ''}>Accrual only</option>
                            <option value="cash" ${m.basis === 'cash' ? 'selected' : ''}>Cash only</option>
                        </select>
                    </div>
                </div>
                <h3 style="margin:12px 0 8px; font-size:14px;">Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}${CostTypes.headHtml()}${Nonprofit.headHtml()}<th scope="col" class="col-rate">Debit</th><th scope="col" class="col-rate">Credit</th><th scope="col" class="col-actions"></th></tr></thead>
                    <tbody id="je-lines"></tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="JournalPage.addLine()">+ Add Line</button>
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span id="je-totals" style="font-size:11px;">Debits: $0.00 | Credits: $0.00</span>
                        <span id="je-balance" style="font-size:11px; margin-left:12px; font-weight:700;"></span>
                    </div>
                    <div class="form-actions" style="margin:0;">
                        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                        <button type="submit" class="btn btn-primary">${id ? 'Save Changes' : 'Memorize'}</button>
                    </div>
                </div>
            </form>`);
        const body = $('#je-lines');
        const lines = m.lines.length ? m.lines : [null, null];
        lines.forEach(l => {
            JournalPage.addLine();
            if (l && body) JournalPage.fillLine(body.lastElementChild, l);
        });
        JournalPage.recalc();
    },

    async saveJournal(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = JournalPage.collectLines();
        if (lines.length < 2) { toast('At least 2 lines required', 'error'); return; }
        if (!(await ClassWarn.ok(form, { rows: '#je-lines tr', cls: 'je-function' }))) return;
        const body = {
            ...MemorizedPage.scheduleFromForm(form, !id),
            description: form.description.value,
            reference: form.reference.value || null,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            is_adjusting: !!(form.is_adjusting && form.is_adjusting.checked),
            basis: form.basis ? form.basis.value : 'both',
            lines,
        };
        try {
            if (id) { await API.put(`/memorized/${id}`, body); toast('Memorized journal entry updated'); }
            else { await API.post('/memorized', { kind: 'journal', ...body }); toast('Journal entry memorized'); }
            closeModal();
            App.navigate('#/memorized');
        } catch (err) { toast(err.message, 'error'); }
    },

    // From a journal entry's page: the same form, filled from the entry.
    async memorizeJournal(entryId) {
        const entry = await API.get(`/journal/${entryId}`);
        closeModal();
        MemorizedPage.showJournalForm(null, {
            name: entry.description || `Journal entry #${entry.id}`,
            description: entry.description,
            reference: entry.reference,
            is_adjusting: entry.is_adjusting,
            basis: entry.basis,
            class_id: entry.class_id,
            job_id: entry.job_id,
            lines: entry.lines,
        });
    },
};
