/**
 * Payroll — pay runs and pay stubs
 * Feature 17: Process payroll with withholding calculations
 */
const PayrollPage = {
    showAll() { PayrollPage._showAll = true; App.navigate(location.hash); },

    async render() {
        const { rows: runs, note: capNote } = await listRows(PayrollPage, '/payroll', 'PayrollPage.showAll()', 'pay runs', 200);
        let html = `
            <div class="page-header">
                <h2>Payroll</h2>
                <button class="btn btn-primary" onclick="PayrollPage.showRunForm()">+ New Pay Run</button>
            </div>${capNote}
            <div style="background:#fef3c7;border:1px solid #fbbf24;padding:6px 10px;margin-bottom:12px;font-size:10px;color:#92400e;">
                <strong>Disclaimer:</strong> Tax calculations are approximate. Verify with a tax professional before filing.
            </div>`;

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
                    <td>${statusBadge(r.status)}</td>
                    <td class="amount">${formatCurrency(r.total_gross)}</td>
                    <td class="amount">${formatCurrency(r.total_taxes)}</td>
                    <td class="amount">${formatCurrency(r.total_net)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PayrollPage.view(${r.id})">View</button>
                        ${r.status === 'draft' ? `<button class="btn btn-sm btn-primary" onclick="PayrollPage.process(${r.id})">Process</button>` : ''}
                        ${r.status === 'processed' ? `<button class="btn btn-sm btn-secondary" data-destructive onclick="PayrollPage.void(${r.id})">Void</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
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
            // Time left unapproved in the period was not paid; a toast is
            // gone before it can be read, so this waits to be closed.
            if (run.warnings && run.warnings.length) {
                openModal('Pay run created: some time was not paid', `
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
            <div class="form-actions">
                ${run.status === 'processed' && run.stubs.some(s => s.direct_deposit) ? `<button class="btn btn-primary" onclick="PayrollPage.downloadDirectDeposit(${run.id})" title="The ACH file to upload to the bank">Direct deposit file</button>` : ''}
                ${run.status === 'processed' ? `<button class="btn btn-secondary" data-destructive onclick="PayrollPage.void(${run.id})">Void</button>` : ''}
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

    async process(id) {
        if (!confirm('Process this pay run? This will create journal entries.')) return;
        // Net pay leaves the bank account (1000) when the run is processed;
        // ask first if that would overdraw it, as expenses and Pay Bills do
        // (macbase1: payroll took Checking to -$2,986.90 without a word).
        try {
            const run = await API.get(`/payroll/${id}`);
            if (!(await Overdraft.confirm(null, Number(run.total_net) || 0, '1000', 'Process'))) return;
        } catch (e) { /* a failed lookup never blocks processing */ }
        try {
            await API.post(`/payroll/${id}/process`);
            toast('Payroll processed');
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
