/**
 * Payroll — pay runs and pay stubs
 * Feature 17: Process payroll with withholding calculations
 */
const PayrollPage = {
    showAll() { PayrollPage._showAll = true; App.navigate(location.hash); },

    // An S corporation (#488 / #557): the owner's pay is W-2 wages here,
    // before any distribution; the sentence is TaxPage's, shown once.
    _sCorpNote() {
        const entity = (typeof App !== 'undefined' && App.settings && App.settings.entity) || {};
        if (entity.return_form !== 'Form 1120-S' || typeof TaxPage === 'undefined') return '';
        return `<div style="font-size:10px;color:var(--text-muted);margin-bottom:12px;">${escapeHtml(TaxPage.S_CORP_NOTE)}</div>`;
    },

    async render() {
        const { rows: runs, note: capNote } = await listRows(PayrollPage, '/payroll', 'PayrollPage.showAll()', 'pay runs', 200);
        let html = `
            <div class="page-header">
                <h2>Payroll</h2>
                <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                    <a href="/api/payroll/opening-ytd/template" download style="font-size:12px">Opening balance template</a>
                    <button class="btn btn-secondary" onclick="PayrollPage.showOpeningImport()">Import opening balances</button>
                    <button class="btn btn-primary" onclick="PayrollPage.showRunForm()">+ New Pay Run</button>
                </div>
            </div>${capNote}
            <div style="background:#fef3c7;border:1px solid #fbbf24;padding:6px 10px;margin-bottom:12px;font-size:10px;color:#92400e;">
                <strong>Disclaimer:</strong> Tax calculations are approximate. Verify with a tax professional before filing.
            </div>${PayrollPage._sCorpNote()}`;

        if (runs.length === 0) {
            html += '<div class="empty-state"><p>No payroll runs yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">Period</th><th scope="col">Pay Date</th><th scope="col">Status</th>
                <th scope="col" class="amount">Gross</th><th scope="col" class="amount">Taxes</th><th scope="col" class="amount">Net</th><th scope="col">Actions</th></tr></thead><tbody>`;
            for (const r of runs) {
                html += `<tr>
                    <td>${formatDate(r.period_start)} - ${formatDate(r.period_end)}</td>
                    <td>${formatDate(r.pay_date)}</td>
                    <td>${statusBadge(r.status)}${r.status === 'draft' && r.stale_reason
                        ? ` <span style="color:var(--text-warning);font-size:11px" title="${escapeHtml(r.stale_reason)}">figured before its year-to-date changed: process as figured, or discard it</span>` : ''}${r.warnings && r.warnings.length
                        ? ` <span style="color:var(--text-warning);font-size:11px" title="${escapeHtml(r.warnings.join(' '))}">figured with another year's tables</span>` : ''}</td>
                    <td class="amount">${formatCurrency(r.total_gross)}</td>
                    <td class="amount">${formatCurrency(r.total_taxes)}</td>
                    <td class="amount">${formatCurrency(r.total_net)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PayrollPage.view(${r.id})">View</button>
                        ${r.status === 'draft' ? `<button class="btn btn-sm btn-primary" onclick="PayrollPage.process(${r.id})">Process</button>
                        <button class="btn btn-sm btn-danger" onclick="PayrollPage.discard(${r.id})" title="Delete this draft and its stubs, and undo what creating it changed">Discard draft</button>` : ''}
                        ${r.status === 'processed' ? `<button class="btn btn-sm btn-secondary" data-destructive onclick="PayrollPage.void(${r.id})">Void</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    // ---- Opening YTD import (#433) ----------------------------------------
    // Every employee's opening balance from one CSV, the template. A dry
    // run first shows the plan (rows, notes, errors); Apply posts the same
    // file again.
    showOpeningImport() {
        openModal('Import opening balances', `
            <form onsubmit="PayrollPage.previewOpeningImport(event)">
                <p class="hint" style="margin-bottom:10px;">
                    What each employee was paid this year before the first SlowBooks pay run: the
                    <a href="/api/payroll/opening-ytd/template" download>SlowBooks template</a>
                    (one row per employee; a second state on a row with only the state columns).
                    No journal entry is made.
                </p>
                <div class="form-group"><label>File</label>
                    <input type="file" name="file" accept=".csv,.txt" required></div>
                <div class="form-group"><label>Reason (only to correct an employee whose balance is locked by a processed pay run)</label>
                    <input name="reason" maxlength="300"></div>
                <div id="opening-import-preview"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Preview</button>
                    <button type="button" class="btn btn-primary" id="opening-import-apply" hidden
                        onclick="PayrollPage.applyOpeningImport()">Apply</button>
                </div>
            </form>`);
    },

    async _postOpeningImport(form, dryRun) {
        const fd = new FormData();
        fd.append('file', form.file.files[0]);
        const q = new URLSearchParams({ dry_run: dryRun ? '1' : '0' });
        if (form.reason.value.trim()) q.append('reason', form.reason.value.trim());
        const resp = await fetch(`/api/payroll/opening-ytd/import?${q.toString()}`,
            { method: 'POST', body: fd, headers: { 'X-Slowbooks-Desktop': '1' } });
        if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
        return resp.json();
    },

    _renderOpeningPlan(plan) {
        const label = { create: 'Create', replace: 'Replace', error: 'Error' };
        const rows = plan.rows.map(r => `<tr>
            <td>${label[r.action] || escapeHtml(r.action)}</td>
            <td>${escapeHtml(r.employee || '')}</td>
            <td>${escapeHtml(r.as_of || '')}</td>
            <td class="amount">${formatCurrency(r.gross)}</td>
            <td class="amount">${formatCurrency(r.federal_tax)}</td>
            <td class="amount">${formatCurrency(r.net)}</td>
            <td style="font-size:11px;color:${r.action === 'error' ? 'var(--danger)' : 'var(--text-muted)'};">${escapeHtml(r.note || '')}</td>
        </tr>`).join('');
        const errs = [...(plan.errors || []), ...(plan.warnings || [])].map(x => `<li>${escapeHtml(x)}</li>`).join('');
        const c = plan.counts || {};
        return `
            <p style="margin:8px 0;"><strong>${c.create || 0} to create, ${c.replace || 0} to replace, ${c.error || 0} with errors.</strong>
            ${plan.dry_run ? 'Nothing has been written yet.' : `${plan.applied} written.`}</p>
            ${errs ? `<ul style="color:var(--danger);font-size:11px;margin:0 0 8px 16px;">${errs}</ul>` : ''}
            <div class="table-container" style="max-height:320px;overflow:auto;"><table>
                <thead><tr><th scope="col">Action</th><th scope="col">Employee</th><th scope="col">As of</th><th scope="col" class="amount">Gross</th><th scope="col" class="amount">Federal</th><th scope="col" class="amount">Net</th><th scope="col">Note</th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
    },

    async previewOpeningImport(e) {
        e.preventDefault();
        const form = e.target;
        PayrollPage._openingImportForm = form;
        const box = $('#opening-import-preview');
        box.innerHTML = '<p class="hint">Reading the file…</p>';
        try {
            const plan = await PayrollPage._postOpeningImport(form, true);
            box.innerHTML = PayrollPage._renderOpeningPlan(plan);
            const writes = (plan.counts.create || 0) + (plan.counts.replace || 0);
            const apply = $('#opening-import-apply');
            apply.hidden = writes === 0 || (plan.errors || []).length > 0;
            apply.textContent = `Apply ${writes} opening balance${writes === 1 ? '' : 's'}`;
        } catch (err) {
            box.innerHTML = `<p style="color:var(--danger);">${escapeHtml(err.message)}</p>`;
        }
    },

    async applyOpeningImport() {
        const form = PayrollPage._openingImportForm;
        if (!form) return;
        try {
            const plan = await PayrollPage._postOpeningImport(form, false);
            $('#opening-import-preview').innerHTML = PayrollPage._renderOpeningPlan(plan);
            $('#opening-import-apply').hidden = true;
            toast(`${plan.applied} opening balance${plan.applied === 1 ? '' : 's'} imported`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showRunForm() {
        const emps = await API.get('/employees?active_only=true');
        if (emps.length === 0) {
            toast('Add employees first', 'error');
            return;
        }

        let empRows = emps.map(e => `
            <tr data-emp-row="${e.id}">
                <td><input type="checkbox" class="pr-check" data-emp="${e.id}" checked aria-label="Pay ${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}"></td>
                <td>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</td>
                <td>${e.pay_type}</td>
                <td class="amount">${formatCurrency(e.pay_rate)}${e.pay_type==='hourly'?'/hr':'/yr'}</td>
                <td><input type="number" step="0.5" class="pr-hours" data-emp="${e.id}" value="${e.pay_type==='hourly'?'80':'0'}" style="width:60px;"></td>
                <td class="pr-te-summary" data-emp="${e.id}" style="font-size:12px;color:var(--text-muted);">—</td>
            </tr>`).join('');

        openModal('New Pay Run', `
            <form onsubmit="PayrollPage.createRun(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Period Start *</label>
                        <input name="period_start" type="date" required value="${todayISO()}" onchange="PayrollPage._refreshTimeEntryPreview()"></div>
                    <div class="form-group"><label>Period End *</label>
                        <input name="period_end" type="date" required onchange="PayrollPage._refreshTimeEntryPreview()"></div>
                    <div class="form-group"><label>Pay Date *</label>
                        <input name="pay_date" type="date" required></div>
                </div>
                <label style="display:flex;align-items:center;gap:8px;margin:8px 0 12px;font-size:13px;">
                    <input type="checkbox" id="pr-use-time-entries" onchange="PayrollPage._toggleTimeEntryMode()">
                    Use approved time entries for hourly employees (auto-fill hours from approved + unpaid entries in this period)
                </label>
                <h3 style="margin:12px 0 8px;font-size:14px;">Employee Hours</h3>
                <div class="table-container"><table>
                    <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Employee</th><th scope="col">Type</th><th scope="col" class="amount">Rate</th><th scope="col">Hours</th><th scope="col">Time Entries</th></tr></thead>
                    <tbody>${empRows}</tbody>
                </table></div>
                <div id="pr-error" role="alert" style="color:var(--danger);font-size:12px;margin-top:8px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Calculate Payroll</button>
                </div>
            </form>`);
    },

    async _refreshTimeEntryPreview() {
        const start = $('[name="period_start"]').value;
        const end = $('[name="period_end"]').value;
        if (!start || !end) return;
        try {
            const summary = await API.get(`/time-entries/summary?period_start=${start}&period_end=${end}`);
            const byEmp = {};
            for (const row of summary) byEmp[row.employee_id] = row;
            $$('.pr-te-summary').forEach(cell => {
                const empId = parseInt(cell.dataset.emp);
                const row = byEmp[empId];
                if (row) {
                    // Time still waiting for approval is not paid by this
                    // run: say so before the run is calculated.
                    const waiting = row.pending_count
                        ? ` · ${row.pending_count} not approved (${row.pending_hours.toFixed(1)} hrs, not paid)`
                        : '';
                    cell.textContent = `${row.total.toFixed(1)} hrs (${row.entry_count} entries)${waiting}`;
                    cell.style.color = row.pending_count ? 'var(--text-danger)' : 'var(--qb-navy)';
                    cell.style.fontWeight = '600';
                } else {
                    cell.textContent = '—';
                    cell.style.color = 'var(--text-muted)';
                    cell.style.fontWeight = 'normal';
                }
            });
        } catch (_) { /* preview is best-effort; ignore errors */ }
    },

    _toggleTimeEntryMode() {
        const useTE = $('#pr-use-time-entries').checked;
        $$('.pr-hours').forEach(inp => {
            inp.disabled = useTE;
            inp.style.background = useTE ? 'var(--gray-100)' : '';
        });
        if (useTE) PayrollPage._refreshTimeEntryPreview();
    },

    async createRun(e) {
        e.preventDefault();
        const form = e.target;
        const useTE = $('#pr-use-time-entries')?.checked || false;
        const stubs = [];
        $$('.pr-check:checked').forEach(cb => {
            const empId = parseInt(cb.dataset.emp);
            const hours = parseFloat($(`.pr-hours[data-emp="${empId}"]`).value) || 0;
            const stub = { employee_id: empId, hours };
            if (useTE) stub.use_time_entries = true;
            stubs.push(stub);
        });
        if (stubs.length === 0) { toast('Select employees', 'error'); return; }

        try {
            const run = await API.post('/payroll', {
                period_start: form.period_start.value,
                period_end: form.period_end.value,
                pay_date: form.pay_date.value,
                stubs,
            });
            toast('Pay run created');
            closeModal();
            App.navigate('#/payroll');
            // Time left unapproved in the period was not paid, or the pay
            // date's year has no Social Security wage base here; a toast is
            // gone before it can be read, so this waits to be closed.
            if (run.warnings && run.warnings.length) {
                openModal('Pay run created: read this before processing', `
                    <ul>${run.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul>
                    <div class="form-actions"><button class="btn btn-primary" onclick="closeModal()">OK</button></div>`);
            }
        } catch (err) {
            // A refused run names every employee it could not pay; keep that
            // on the open form, where the fix (untick, or approve time) is.
            const box = $('#pr-error');
            if (box) box.textContent = err.message;
            toast(err.message, 'error');
        }
    },

    async view(id) {
        const run = await API.get(`/payroll/${id}`);
        const benefitCell = (s) => {
            const b = s.benefits || [];
            if (!b.length) return '—';
            return b.map(x => `${escapeHtml(x.code)} ${formatCurrency(x.employee_amount)}${x.employer_amount ? ` <span style="color:var(--gray-400)">(+${formatCurrency(x.employer_amount)} ER)</span>` : ''}`).join('<br>');
        };
        // Every amount between Gross and Net has a column, so a row adds up:
        // "Other" is what is withheld besides income tax, SS and Medicare
        // (Oregon's transit tax, disability and family-leave premiums) plus
        // garnishments; reimbursements get a column when the run has any.
        const anyReimb = run.stubs.some(s => s.reimbursements);
        // Each Stub PDF names its employee, and the Employee column stays in
        // view while the table scrolls sideways: the dialog opened scrolled
        // to the first Stub PDF, with no name in sight (2.18.0 gate, macbase1
        // NEW-3).
        const who = (s) => s.employee_name || `Employee ${s.employee_id}`;
        let rows = run.stubs.map(s => `
            <tr>
                <td>${escapeHtml(who(s))}</td>
                <td class="amount">${s.hours}</td>
                <td class="amount">${formatCurrency(s.gross_pay)}</td>
                <td class="amount">${formatCurrency(s.federal_tax)}</td>
                <td class="amount">${formatCurrency(s.state_tax)}</td>
                <td class="amount">${formatCurrency(s.ss_tax)}</td>
                <td class="amount">${formatCurrency(s.medicare_tax)}</td>
                <td class="amount" title="${escapeHtml(`State taxes besides income tax ${formatCurrency(s.state_other_employee || 0)} · Garnishments ${formatCurrency(s.garnishments || 0)}`)}">${formatCurrency((s.state_other_employee || 0) + (s.garnishments || 0))}</td>
                <td style="font-size:12px;">${benefitCell(s)}</td>
                <td class="amount">${formatCurrency((s.pretax_deductions || 0) + (s.posttax_deductions || 0))}</td>
                ${anyReimb ? `<td class="amount">${formatCurrency(s.reimbursements || 0)}</td>` : ''}
                <td class="amount" style="font-weight:700;">${formatCurrency(s.net_pay)}</td>
                <td>${s.direct_deposit ? 'Direct deposit' : 'Check'}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" aria-label="${escapeHtml(`Stub PDF — ${who(s)}`)}" title="Printable pay stub (PDF)" onclick="window.open('/api/payroll/${run.id}/paystub/${s.id}','_blank')">Stub PDF — ${escapeHtml(who(s))}</button></td>
            </tr>`).join('');

        openModal(`Pay Run: ${run.period_start} to ${run.period_end}`, `
            <div class="table-container table-container--scroll"><table class="pay-run-table">
                <thead><tr><th scope="col">Employee</th><th scope="col" class="amount">Hours</th><th scope="col" class="amount">Gross</th>
                <th scope="col" class="amount">Fed</th><th scope="col" class="amount">State</th><th scope="col" class="amount">SS</th>
                <th scope="col" class="amount">Med</th><th scope="col" class="amount">Other</th><th scope="col">Benefits (EE, +ER)</th><th scope="col" class="amount">Deductions</th>${anyReimb ? '<th scope="col" class="amount">+ Reimb.</th>' : ''}<th scope="col" class="amount">Net</th><th scope="col">Paid by</th><th scope="col">Stub</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <p style="font-size:11px;color:var(--gray-500);margin:4px 0 8px;">Other: state taxes withheld besides income tax (disability, family leave, transit) and garnishments. Gross − Fed − State − SS − Med − Other − Deductions${anyReimb ? ' + Reimb.' : ''} = Net.</p>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Total Gross</span><span class="value">${formatCurrency(run.total_gross)}</span></div>
                <div class="total-row"><span class="label">Total Taxes</span><span class="value">${formatCurrency(run.total_taxes)}</span></div>
                <div class="total-row"><span class="label">Employer Taxes</span><span class="value">${formatCurrency(run.total_employer_taxes)}</span></div>
                <div class="total-row"><span class="label">Employer Benefits</span><span class="value">${formatCurrency(run.total_employer_benefits || 0)}</span></div>
                <div class="total-row grand-total"><span class="label">Total Net</span><span class="value">${formatCurrency(run.total_net)}</span></div>
                ${run.burden_job_cost_id ? `<div class="total-row"><span class="label">Labor burden distributed</span><span class="value"><a href="#/job-costs">Job cost entry #${run.burden_job_cost_id}</a></span></div>` : ''}
            </div>
            ${PayrollPage._directDepositNote(run)}
            ${run.warnings && run.warnings.length ? `<div class="card note--caution" role="note" style="padding:10px 12px;margin:8px 0">${run.warnings.map(w => `<p style="margin:0">${escapeHtml(w)}</p>`).join('')}</div>` : ''}
            <div class="form-actions">
                ${run.status === 'processed' && run.stubs.some(s => s.direct_deposit) ? `<button class="btn btn-primary" onclick="PayrollPage.downloadDirectDeposit(${run.id})" title="The ACH file to upload to the bank">Direct deposit file</button>` : ''}
                ${run.status === 'processed' ? `<button class="btn btn-secondary" data-destructive onclick="PayrollPage.void(${run.id})">Void</button>` : ''}
                ${run.status === 'draft' ? `<button class="btn btn-danger" onclick="PayrollPage.discard(${run.id})">Discard draft</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`, { wide: true });
    },

    // Who the ACH file pays and who it does not (discussion #211): the next
    // step after Process was a question nobody could answer from the page.
    _directDepositNote(run) {
        const dd = run.stubs.filter(s => s.direct_deposit).length;
        const checks = run.stubs.length - dd;
        if (run.status !== 'processed') {
            return dd ? `<p style="font-size:11px;color:var(--gray-500);margin:4px 0 8px;">Once processed, the run has a direct deposit file for the ${dd === 1 ? 'employee' : `${dd} employees`} paid by direct deposit.</p>` : '';
        }
        if (!dd) return `<p style="font-size:11px;color:var(--gray-500);margin:4px 0 8px;">No one on this run has a direct-deposit account; pay each stub by check. Accounts are entered on the employee.</p>`;
        return `<p style="font-size:11px;color:var(--gray-500);margin:4px 0 8px;">Direct deposit file: ${dd === 1 ? '1 employee' : `${dd} employees`}${checks ? `; ${checks === 1 ? '1 is' : `${checks} are`} paid by check` : ''}. Upload the file to the bank; the bank's details are under Settings → Direct Deposit (ACH).</p>`;
    },

    // Saves the run's ACH file. A refusal (the bank's details not set up,
    // nobody on the run with an account) is a sentence to read, so this
    // fetches rather than opening the address in a window.
    async downloadDirectDeposit(id) {
        try {
            const res = await fetch(`/api/payroll/${id}/nacha`, { headers: { 'X-Slowbooks-Desktop': '1' } });
            if (!res.ok) throw new Error(await API.responseError(res, 'Direct deposit file failed'));
            const disposition = res.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^";]+)"?/);
            const filename = match ? match[1] : `Direct-Deposit_${id}.ach`;
            const blob = await res.blob();
            const desktop = window.SlowbooksDesktop;
            if (!(desktop && await desktop.saveFile(blob, filename))) {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = filename;
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 30000);
                toast(`Saved ${filename}`);
            }
        } catch (err) { toast(err.message, 'error'); }
    },

    // Discard a draft (never a processed run): its stubs go, and what
    // creating it changed — benefit year-to-date, loan balances, the time
    // entries it paid — is undone.
    async discard(id, question) {
        if (!confirm(question || 'Discard this draft pay run? Its stubs are deleted, and the benefit year-to-date, loan balances and time entries it took are put back as they were.')) return;
        try {
            const res = await API.del(`/payroll/${id}`);
            const flagged = (res && res.flagged_runs) || [];
            toast(flagged.length
                ? `Draft discarded. ${flagged.length === 1 ? 'A later draft was' : `${flagged.length} later drafts were`} figured while it counted and ${flagged.length === 1 ? 'is' : 'are'} flagged.`
                : 'Draft discarded');
            closeModal();
            App.navigate('#/payroll');
        } catch (err) { toast(err.message, 'error'); }
    },

    async process(id) {
        if (!confirm('Process this pay run? This will create journal entries.')) return;
        // Net pay leaves the net pay account when the run is processed: the
        // one chosen under Settings → Payroll Accounts (#620), 1000 Checking
        // when none is. Ask first if that would overdraw it, as expenses and
        // Pay Bills do (macbase1: payroll took Checking to -$2,986.90 without
        // a word). A liability chosen there (wages paid later) is not a bank,
        // so there is nothing to ask.
        let stale = null;
        try {
            const run = await API.get(`/payroll/${id}`);
            stale = run.stale_reason;
            const chosen = (typeof App !== 'undefined' && App.settings && App.settings.payroll_net_pay_account_id) || '';
            const netPayAcct = chosen ? Number(chosen) : null;
            if (!(await Overdraft.confirm(netPayAcct, Number(run.total_net) || 0, '1000', 'Process'))) return;
        } catch (e) { /* a failed lookup never blocks processing */ }
        // A draft figured before an employee's opening YTD changed (#433):
        // say so, and process it as figured only when asked to; otherwise
        // offer to discard it, so the run can be created again.
        if (stale && !confirm(`${stale}\n\nProcess it as figured anyway?`)) {
            PayrollPage.discard(id, 'Discard this draft instead, so the run can be created again with the new figures?');
            return;
        }
        try {
            await (stale
                ? API.post(`/payroll/${id}/process?acknowledge_stale=1`)
                : API.post(`/payroll/${id}/process`));
            toastPosted('Payroll processed');
            App.navigate('#/payroll');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Void a processed run (#432): reverse the payroll journal entry, free
    // the time entries, mark the stubs void. When the server needs a second
    // look (a NACHA file was made, the run's liabilities were since paid,
    // its quarter's 941 or year's W-2s were generated) it answers 409 with
    // the warnings; they are shown and the void is sent again with
    // confirm=true only if the user says so.
    async void(id) {
        if (!confirm('Void this pay run? The payroll journal entry is reversed, the time entries go back to approved, and the stubs are marked void.')) return;
        try {
            let run;
            try {
                run = await API.post(`/payroll/${id}/void`);
            } catch (err) {
                if (!(err.status === 409 && err.detail && err.detail.code === 'confirm_void')) throw err;
                const list = (err.detail.warnings || []).map(w => '\u2022 ' + w).join('\n\n');
                if (!confirm('Before voiding this pay run:\n\n' + list + '\n\nVoid it anyway?')) return;
                run = await API.post(`/payroll/${id}/void`, undefined, { query: { confirm: true } });
            }
            toast('Pay run voided');
            for (const w of (run.warnings || [])) toast(w, 'warning');
            App.navigate('#/payroll');
        } catch (err) { toast(err.message, 'error'); }
    },
};
