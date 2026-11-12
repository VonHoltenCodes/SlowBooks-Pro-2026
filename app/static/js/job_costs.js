/**
 * Job Cost Entries — costs that are not a vendor bill: internal labor at
 * a loaded rate, owned-equipment hours, mileage, small tools, burden,
 * overhead allocations, corrections. Each line debits a job cost account
 * (tagged to the job, code and type) and credits an offset account
 * (payroll clearing, applied equipment, applied overhead…) that Settings
 * → Cost Types provides by default. Allocations spread one amount across
 * many jobs in a single entry.
 */
const JobCostsPage = {
    _jobs: [],
    _codes: [],
    _types: [],
    _accounts: [],
    _employees: [],
    _equipment: [],
    _lineCount: 0,

    async render() {
        const entries = await API.get('/job-costs');
        const rows = entries.map(jc => `<tr class="clickable${jc.status === 'void' ? ' row--dim' : ''}" onclick="JobCostsPage.view(${jc.id})">
            <td>${escapeHtml(jc.number)}</td>
            <td>${escapeHtml(jc.date)}</td>
            <td>${escapeHtml(jc.job_name || (jc.source === 'allocation' ? `${jc.lines.length} jobs (allocation)` : ''))}</td>
            <td>${escapeHtml({ manual: 'Entry', time_entry: 'Time', allocation: 'Allocation' }[jc.source] || jc.source)}</td>
            <td>${escapeHtml(jc.memo || '')}</td>
            <td class="amount">${formatCurrency(jc.total)}</td>
            <td>${jc.status === 'void' ? '<span style="color:var(--text-danger)">void</span>' : 'posted'}</td>
        </tr>`).join('');
        return `
            <div class="page-header">
                <h2>${T('Job Cost Entries')}</h2>
                <div>
                    <button class="btn btn-secondary" onclick="JobCostsPage.showAllocate()">Allocate a Cost</button>
                    <button class="btn btn-primary" onclick="JobCostsPage.showForm()">+ ${T('Job')} Cost Entry</button>
                </div>
            </div>
            <div class="toolbar" style="font-size:11px; color:var(--gray-500);">
                Labor, equipment hours, mileage, burden, overhead — any job cost that isn't a bill. Offsets come from Settings → Cost Types.
            </div>
            ${entries.length === 0 ? `<div class="empty-state"><p>No job cost entries yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">${T('Job')}</th><th scope="col">Source</th><th scope="col">Memo</th><th scope="col" class="amount">Total</th><th scope="col">Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`}`;
    },

    async _loadRefs() {
        const [jobs, codes, types, accounts, employees, equipment] = await Promise.all([
            API.get('/jobs'), API.get('/cost-codes'), API.get('/cost-types'), API.get('/accounts'),
            API.get('/employees?active_only=true').catch(() => []), API.get('/equipment').catch(() => []),
        ]);
        Object.assign(JobCostsPage, { _jobs: jobs, _codes: codes, _types: types, _accounts: accounts, _employees: employees, _equipment: equipment });
    },

    _opt(list, valueKey, labelFn, selected, blank = '--') {
        return `<option value="">${blank}</option>` + list.map(x => `<option value="${x[valueKey]}" ${String(selected ?? '') === String(x[valueKey]) ? 'selected' : ''}>${escapeHtml(labelFn(x))}</option>`).join('');
    },

    // ---- Accruals (#897) ------------------------------------------------------
    // A cost booked before the vendor's bill arrives credits 2010 Accrued Job
    // Costs (never Accounts Payable, which the server refuses) and reverses
    // itself on its Reverses on date: the first day of the next month unless
    // the person picks another or clears it.
    _accruedAccount() {
        return (JobCostsPage._accounts || []).find(a => String(a.account_number) === '2010'
            && String(a.account_type || '').toLowerCase() === 'liability') || null;
    },

    // The offset choices: the chart, and 2010 when the chart has none yet
    // (the server makes it on the first accrual).
    offsetOptions() {
        const opts = JobCostsPage._opt(JobCostsPage._accounts, 'id', a => accountWords(a, null, ' '), null, 'default');
        return JobCostsPage._accruedAccount() ? opts
            : opts + `<option value="accrue">${escapeHtml(Terms.text('2010 Accrued Job Costs'))} (bill not in yet)</option>`;
    },

    isAccrualOffset(value) {
        if (!value) return false;
        if (value === 'accrue') return true;
        const acct = JobCostsPage._accruedAccount();
        return !!acct && String(acct.id) === String(value);
    },

    firstOfNextMonth(iso) {
        const m = /^(\d{4})-(\d{2})-\d{2}$/.exec(iso || '');
        if (!m) return '';
        let y = parseInt(m[1], 10), mo = parseInt(m[2], 10) + 1;
        if (mo > 12) { mo = 1; y += 1; }
        return `${y}-${String(mo).padStart(2, '0')}-01`;
    },

    // Show the Reverses on date while a line's offset is 2010, hidden
    // otherwise; its default follows the entry's date until it is edited.
    accrualChanged() {
        const wrap = $('#jc-reverses-wrap');
        if (!wrap) return;
        const accrues = $$('#jc-lines .jc-credit').some(sel => JobCostsPage.isAccrualOffset(sel.value));
        wrap.hidden = !accrues;
        const input = wrap.querySelector('input[name="reverses_on"]');
        if (accrues && input && !input.dataset.touched) {
            const date = wrap.closest('form')?.querySelector('input[name="date"]')?.value;
            input.value = JobCostsPage.firstOfNextMonth(date);
        }
    },

    reversesTouched(input) { input.dataset.touched = '1'; },

    // The request body the form posts: an accrual line says accrue (or names
    // 2010's id); reverses_on is sent only for an accrual, null when cleared.
    payload(form, lines) {
        const body = { date: form.date.value, job_id: parseInt(form.job_id.value), memo: form.memo.value || null, lines };
        if (lines.some(l => l.accrue || JobCostsPage.isAccrualOffset(l.credit_account_id))) {
            body.reverses_on = form.reverses_on?.value || null;
        }
        return body;
    },

    // ---- New entry --------------------------------------------------------------
    async showForm(id = null, jobId = null) {
        await JobCostsPage._loadRefs();
        if (!JobCostsPage._jobs.length) { toast(Terms.text('Create a job first'), 'error'); return; }
        const jobOpts = JobCostsPage._opt(JobCostsPage._jobs, 'id', j => j.full_name, jobId, 'Select a job…');
        JobCostsPage._lineCount = 0;
        openModal(Terms.text('Job Cost Entry'), `
            <form onsubmit="JobCostsPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Job')} *</label>
                        <select name="job_id" required>${jobOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}" onchange="JobCostsPage.accrualChanged()"></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="memo" placeholder="What this cost is"></div>
                    <div class="form-group" id="jc-reverses-wrap" hidden><label for="jc-reverses-on">Reverses on</label>
                        <input id="jc-reverses-on" name="reverses_on" type="date" oninput="JobCostsPage.reversesTouched(this)">
                        <div style="font-size:11px;color:var(--gray-500)">The accrual books itself out on this date; enter the bill the normal way when it comes. Clear it to keep the accrual open.</div></div>
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Cost lines</h3>
                <div style="font-size:11px;color:var(--gray-500);margin-bottom:6px">
                    Pick equipment to charge its hourly rate; pick an employee for internal labor (enter the hours and loaded rate). Leave the accounts blank to use the cost code's / cost type's defaults. The bill not in yet? Pick ${Terms.text('2010 Accrued Job Costs')} as the offset, never Accounts Payable.
                </div>
                <div class="table-container table-container--scroll"><table class="line-items-table line-items-table--wide">
                    <thead><tr><th scope="col">Cost code</th><th scope="col">Type</th><th scope="col">Description</th><th scope="col">Employee / Equipment</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th><th scope="col">Cost acct</th><th scope="col">Offset acct</th><th scope="col" title="Billable">Bill?</th><th scope="col"></th></tr></thead>
                    <tbody id="jc-lines">${JobCostsPage.lineHtml(0)}</tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="JobCostsPage.addLine()">+ Add Line</button>
                <div style="margin-top:8px;text-align:right;font-weight:700" id="jc-total">Total: $0.00</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post ${T('Job')} Cost</button>
                </div>
            </form>`, { wide: true });
        JobCostsPage._lineCount = 1;
    },

    lineHtml(idx) {
        const codeOpts = JobCostsPage._opt(JobCostsPage._codes, 'id', c => `${'  '.repeat(c.depth || 0)}${c.label}`, null);
        const typeOpts = JobCostsPage._opt(JobCostsPage._types, 'code', t => t.name, null, 'auto');
        const who = `<option value="">--</option>`
            + (JobCostsPage._employees.length ? `<optgroup label="Employees">${JobCostsPage._employees.map(e => `<option value="emp:${e.id}">${escapeHtml(e.first_name + ' ' + e.last_name)}</option>`).join('')}</optgroup>` : '')
            + (JobCostsPage._equipment.length ? `<optgroup label="Equipment">${JobCostsPage._equipment.map(q => `<option value="eq:${q.id}" data-rate="${q.hourly_rate}">${escapeHtml(q.name)} (${formatCurrency(q.hourly_rate)}/hr)</option>`).join('')}</optgroup>` : '');
        const acctOpts = JobCostsPage._opt(JobCostsPage._accounts, 'id', a => accountWords(a, null, ' '), null, 'default');
        const offsetOpts = JobCostsPage.offsetOptions();
        return `<tr data-jcline="${idx}">
            <td><select class="jc-code" style="min-width:150px;max-width:190px">${codeOpts}</select></td>
            <td><select class="jc-type">${typeOpts}</select></td>
            <td><input class="jc-desc" style="min-width:160px"></td>
            <td><select class="jc-who" style="min-width:160px" onchange="JobCostsPage.whoChanged(this)">${who}</select></td>
            <td><input class="jc-qty" type="number" step="0.01" value="1" oninput="JobCostsPage.recalc()"></td>
            <td><input class="jc-rate" type="number" step="0.0001" value="0" oninput="JobCostsPage.recalc()"></td>
            <td class="col-amount jc-amount">$0.00</td>
            <td><select class="jc-debit" style="min-width:140px;max-width:170px">${acctOpts}</select></td>
            <td><select class="jc-credit" style="min-width:140px;max-width:170px" onchange="JobCostsPage.accrualChanged()">${offsetOpts}</select></td>
            <td style="text-align:center"><input type="checkbox" class="jc-billable"></td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="this.closest('tr').remove();JobCostsPage.recalc();JobCostsPage.accrualChanged()">X</button></td>
        </tr>`;
    },

    addLine() {
        $('#jc-lines').insertAdjacentHTML('beforeend', JobCostsPage.lineHtml(JobCostsPage._lineCount++));
    },

    whoChanged(sel) {
        const row = sel.closest('tr');
        const opt = sel.selectedOptions[0];
        if (opt && opt.dataset.rate) {
            row.querySelector('.jc-rate').value = opt.dataset.rate;
            const typeSel = row.querySelector('.jc-type');
            if (typeSel && !typeSel.value) typeSel.value = 'equipment';
        } else if (sel.value.startsWith('emp:')) {
            const typeSel = row.querySelector('.jc-type');
            if (typeSel && !typeSel.value) typeSel.value = 'labor';
        }
        JobCostsPage.recalc();
    },

    recalc() {
        let total = 0;
        $$('#jc-lines tr').forEach(row => {
            const qty = parseFloat(row.querySelector('.jc-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.jc-rate')?.value) || 0;
            const amt = Math.round(qty * rate * 100) / 100;
            total += amt;
            const cell = row.querySelector('.jc-amount');
            if (cell) cell.textContent = formatCurrency(amt);
        });
        const el = $('#jc-total');
        if (el) el.textContent = `Total: ${formatCurrency(total)}`;
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#jc-lines tr').forEach(row => {
            const who = row.querySelector('.jc-who')?.value || '';
            const qty = parseFloat(row.querySelector('.jc-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.jc-rate')?.value) || 0;
            if (!qty || !rate) return;
            const v = sel => { const x = row.querySelector(sel)?.value; return x ? (isNaN(parseInt(x)) ? x : parseInt(x)) : null; };
            const accrue = row.querySelector('.jc-credit')?.value === 'accrue';
            lines.push({
                cost_code_id: v('.jc-code'),
                cost_type: row.querySelector('.jc-type')?.value || null,
                description: row.querySelector('.jc-desc')?.value || null,
                quantity: qty, rate,
                debit_account_id: v('.jc-debit'),
                credit_account_id: accrue ? null : v('.jc-credit'),
                accrue,
                employee_id: who.startsWith('emp:') ? parseInt(who.slice(4)) : null,
                equipment_id: who.startsWith('eq:') ? parseInt(who.slice(3)) : null,
                is_billable: !!row.querySelector('.jc-billable')?.checked,
            });
        });
        if (!lines.length) { toast('Add at least one line with a quantity and rate', 'error'); return; }
        if (!(await TxnWarn.ok(form))) return;
        try {
            const jc = await API.post('/job-costs', JobCostsPage.payload(form, lines));
            toastPosted(`${jc.number} posted: ${formatCurrency(jc.total)}${jc.reverses_on ? `, reverses on ${jc.reverses_on}` : ''}`);
            closeModal();
            JobCostsPage._afterChange();
        } catch (err) { toast(err.message, 'error'); }
    },

    _afterChange() {
        const path = location.hash.replace('#', '');
        if (path === '/job-costs') App.navigate('#/job-costs');
        else if (path.startsWith('/jobs/') && window.JobsPage) { JobsPage._tree = null; JobsPage.setTab(JobsPage._tab); }
    },

    // ---- Allocate -------------------------------------------------------------
    async showAllocate() {
        await JobCostsPage._loadRefs();
        const jobOpts = JobCostsPage._jobs.map(j => `<label style="display:block;font-weight:normal"><input type="checkbox" class="alloc-job" value="${j.id}" checked> ${escapeHtml(j.full_name)} <input type="number" class="alloc-weight" step="0.01" value="1" style="width:70px;margin-left:6px" title="Weight (percent method)"></label>`).join('');
        const codeOpts = JobCostsPage._opt(JobCostsPage._codes, 'id', c => c.label, null);
        const typeOpts = JobCostsPage._opt(JobCostsPage._types, 'code', t => t.name, 'other', 'auto');
        const acctOpts = JobCostsPage._opt(JobCostsPage._accounts, 'id', a => accountWords(a, null, ' '), null, 'default');
        openModal(`Allocate a Cost Across ${T('Jobs')}`, `
            <form onsubmit="JobCostsPage.saveAllocate(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" class="money" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group"><label>Spread by</label>
                        <select name="method">
                            <option value="hours">Labor hours in the period</option>
                            <option value="revenue">Revenue in the period</option>
                            <option value="costs">Costs to date in the period</option>
                            <option value="equal">Equally</option>
                            <option value="percent">Weights below</option>
                        </select></div>
                    <div class="form-group"><label>Period start (for hours / revenue / costs)</label><input name="start_date" type="date"></div>
                    <div class="form-group"><label>Period end</label><input name="end_date" type="date"></div>
                    <div class="form-group"><label>Cost code</label><select name="cost_code_id">${codeOpts}</select></div>
                    <div class="form-group"><label>Cost type</label><select name="cost_type">${typeOpts}</select></div>
                    <div class="form-group"><label>Cost account</label><select name="debit_account_id">${acctOpts}</select></div>
                    <div class="form-group"><label>Offset account</label><select name="credit_account_id">${acctOpts}</select></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo" placeholder="e.g. July small tools & consumables"></div>
                    <div class="form-group full-width"><label>${T('Jobs')}</label><div style="max-height:220px;overflow:auto;border:1px solid var(--gray-200);padding:6px">${jobOpts || '<em>No active jobs</em>'}</div></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Allocate</button>
                </div>
            </form>`);
    },

    async saveAllocate(e) {
        e.preventDefault();
        const form = e.target;
        const targets = [];
        $$('.alloc-job').forEach(cb => { if (cb.checked) targets.push({ job_id: parseInt(cb.value), weight: parseFloat(cb.parentElement.querySelector('.alloc-weight')?.value) || 1 }); });
        const num = n => form[n].value ? parseInt(form[n].value) : null;
        if (!(await TxnWarn.ok(form))) return;
        try {
            const jc = await API.post('/job-costs/allocate', {
                date: form.date.value, amount: parseFloat(form.amount.value), method: form.method.value,
                start_date: form.start_date.value || null, end_date: form.end_date.value || null,
                cost_code_id: num('cost_code_id'), cost_type: form.cost_type.value || null,
                debit_account_id: num('debit_account_id'), credit_account_id: num('credit_account_id'),
                memo: form.memo.value || null, targets,
            });
            toast(`${jc.number}: ${formatCurrency(jc.total)} spread over ${jc.lines.length} job${jc.lines.length === 1 ? '' : 's'}`);
            closeModal();
            JobCostsPage._afterChange();
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- View / void --------------------------------------------------------------
    async view(id) {
        let jc;
        try { jc = await API.get(`/job-costs/${id}`); } catch (err) { toast(err.message, 'error'); return; }
        const rows = jc.lines.map(l => `<tr>
            <td>${escapeHtml(l.job_name || '')}</td>
            <td>${escapeHtml(l.cost_code_label || '')}</td>
            <td>${escapeHtml(l.cost_type || '')}${l.is_burden ? ' <span class="badge" style="font-size:9px">burden</span>' : ''}${l.is_billable ? ' <span class="badge" style="font-size:9px">billable</span>' : ''}</td>
            <td>${escapeHtml(l.description || '')}</td>
            <td class="amount">${l.quantity}</td><td class="amount">${l.rate}</td>
            <td class="amount">${formatCurrency(l.amount)}</td>
            <td style="font-size:11px">${escapeHtml(l.debit_account_name || '')} / ${escapeHtml(l.credit_account_name || '')}</td>
        </tr>`).join('');
        openModal(`${T('Job')} Cost ${jc.number}`, `
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="font-size:13px">
                    <div><strong>${escapeHtml(jc.date)}</strong> · ${escapeHtml(jc.job_name || 'allocation across jobs')} · ${escapeHtml({ manual: 'Entry', time_entry: 'From time entry', allocation: 'Allocation' }[jc.source] || jc.source)}</div>
                    ${jc.memo ? `<div style="color:var(--text-muted)">${escapeHtml(jc.memo)}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div style="font-size:20px;font-weight:700">${formatCurrency(jc.total)}</div>
                    <div>${jc.status === 'void' ? '<span style="color:var(--text-danger);font-weight:600">VOID</span>' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="JobCostsPage.voidEntry(${jc.id})">Void</button>`}</div>
                </div>
            </div>
            ${JobCostsPage.accrualHtml(jc)}
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">${T('Job')}</th><th scope="col">Cost code</th><th scope="col">Type</th><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th><th scope="col">Cost / offset</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            ${typeof activityPanelHtml === 'undefined' ? '' : activityPanelHtml('job_cost', jc.id)}`);
    },

    // An accrual's line on the entry's view: when it reversed, or its date,
    // editable (and clearable) until it does.
    accrualHtml(jc) {
        if (!(parseFloat(jc.accrued) > 0)) return '';
        if (jc.reversed_on) {
            return `<div class="jc-accrual" style="font-size:12px;margin-bottom:8px">Accrued ${formatCurrency(jc.accrued)} to ${escapeHtml(Terms.text('2010 Accrued Job Costs'))}; reversed on ${escapeHtml(jc.reversed_on)}.</div>`;
        }
        if (jc.status === 'void') return '';
        return `<form class="jc-accrual" style="font-size:12px;margin-bottom:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap" onsubmit="JobCostsPage.saveReversesOn(event, ${jc.id})">
            <span>Accrued ${formatCurrency(jc.accrued)} to ${escapeHtml(Terms.text('2010 Accrued Job Costs'))}.</span>
            <label for="jc-view-reverses-on">Reverses on</label>
            <input id="jc-view-reverses-on" name="reverses_on" type="date" value="${escapeHtml(jc.reverses_on || '')}">
            <button type="submit" class="btn btn-sm btn-secondary">Save date</button>
            ${jc.reverses_on ? '' : '<span style="color:var(--text-muted)">No date: the accrual stays open.</span>'}
        </form>`;
    },

    async saveReversesOn(e, id) {
        e.preventDefault();
        try {
            const jc = await API.put(`/job-costs/${id}/reverses-on`, { reverses_on: e.target.reverses_on.value || null });
            toast(jc.reverses_on ? `${jc.number} reverses on ${jc.reverses_on}` : `${jc.number} stays open until a date is set`);
            closeModal();
            JobCostsPage.view(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidEntry(id) {
        if (!confirm('Void this job cost entry? A reversing entry is posted; the original stays in the ledger.')) return;
        try {
            await API.post(`/job-costs/${id}/void`, {});
            toast(`${T('Job')} cost voided`);
            closeModal();
            JobCostsPage._afterChange();
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.JobCostsPage = JobCostsPage;
