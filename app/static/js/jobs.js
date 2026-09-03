/**
 * Jobs (Projects) — QuickBooks "Customer:Job" / Online "Projects".
 *
 * One page: every job with its income, costs and margin for the period,
 * filterable by customer and status. Row click = job detail (summary +
 * every posted line attributed to it). Jobs are created here or from the
 * Customer Center; a job belongs to exactly one customer.
 *
 * The figures come from posted ledger lines (a line's own job, else its
 * transaction's), so they always agree with the Job Profitability report
 * and reconcile to the P&L.
 */
const JobsPage = {
    _jobs: [],
    _profit: {},
    _customers: [],
    _filter: { customer_id: '', status: '', q: '' },

    STATUS_LABELS: {
        pending: 'Pending', awarded: 'Awarded', in_progress: 'In progress',
        closed: 'Closed', not_awarded: 'Not awarded',
    },

    async render() {
        const [jobs, profit, customers] = await Promise.all([
            API.get('/jobs?include_inactive=true'),
            API.get('/jobs/profitability').catch(() => []),
            API.get('/customers'),
        ]);
        JobsPage._jobs = jobs;
        JobsPage._customers = customers;
        JobsPage._profit = {};
        for (const p of profit) if (p.job_id) JobsPage._profit[p.job_id] = p;

        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        const statusOpts = Object.entries(JobsPage.STATUS_LABELS)
            .map(([k, v]) => `<option value="${k}">${v}</option>`).join('');

        return `
            <div class="page-header">
                <h2>Jobs</h2>
                <button class="btn btn-primary" onclick="JobsPage.showForm()">+ New Job</button>
            </div>
            <div class="toolbar" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                <input type="text" placeholder="Search jobs..." id="job-search" oninput="JobsPage.setFilter('q', this.value)">
                <select id="job-filter-customer" onchange="JobsPage.setFilter('customer_id', this.value)">
                    <option value="">All customers</option>${custOpts}</select>
                <select id="job-filter-status" onchange="JobsPage.setFilter('status', this.value)">
                    <option value="">Active jobs</option>${statusOpts}<option value="__inactive__">Inactive</option></select>
                <span style="font-size:11px; color:var(--gray-500);">Figures are all-time, from posted lines. Period view: Reports → Job Profitability.</span>
            </div>
            <div id="jobs-table">${JobsPage._tableHtml()}</div>`;
    },

    setFilter(key, value) {
        JobsPage._filter[key] = value;
        const el = $('#jobs-table');
        if (el) el.innerHTML = JobsPage._tableHtml();
    },

    _visible() {
        const f = JobsPage._filter;
        const q = (f.q || '').toLowerCase();
        return JobsPage._jobs.filter(j => {
            if (f.status === '__inactive__') { if (j.is_active) return false; }
            else {
                if (!j.is_active) return false;
                if (f.status && j.status !== f.status) return false;
            }
            if (f.customer_id && String(j.customer_id) !== String(f.customer_id)) return false;
            if (q && !(`${j.customer_name} ${j.name} ${j.job_number || ''}`.toLowerCase().includes(q))) return false;
            return true;
        });
    },

    _tableHtml() {
        const jobs = JobsPage._visible();
        if (jobs.length === 0) {
            return `<div class="empty-state">
                <p>${JobsPage._jobs.length ? 'No jobs match.' : 'No jobs yet. A job is a customer\'s project — every invoice, bill, expense and time entry can be tagged to one.'}</p>
                ${JobsPage._jobs.length ? '' : '<button class="btn btn-primary" onclick="JobsPage.showForm()" style="margin-top:10px;">+ Create your first job</button>'}
            </div>`;
        }
        const pct = v => (v === null || v === undefined) ? '—' : `${v.toFixed(1)}%`;
        let totals = { income: 0, total_costs: 0, net_income: 0 };
        const rows = jobs.map(j => {
            const p = JobsPage._profit[j.id] || { income: 0, total_costs: 0, net_income: 0, margin_pct: null };
            totals.income += p.income; totals.total_costs += p.total_costs; totals.net_income += p.net_income;
            return `<tr class="clickable" onclick="JobsPage.showDetails(${j.id})">
                <td>${escapeHtml(j.customer_name)}</td>
                <td><strong>${escapeHtml(j.name)}</strong>${j.job_number ? ` <span style="color:var(--gray-500); font-size:11px;">#${escapeHtml(j.job_number)}</span>` : ''}</td>
                <td><span class="badge">${escapeHtml(JobsPage.STATUS_LABELS[j.status] || j.status)}</span>${j.is_active ? '' : ' <span style="color:var(--gray-500); font-size:11px;">inactive</span>'}</td>
                <td class="amount">${j.contract_amount ? formatCurrency(j.contract_amount) : ''}</td>
                <td class="amount">${formatCurrency(p.income)}</td>
                <td class="amount">${formatCurrency(p.total_costs)}</td>
                <td class="amount" style="font-weight:700; color:${p.net_income < 0 ? '#a4242b' : 'inherit'}">${formatCurrency(p.net_income)}</td>
                <td class="amount">${pct(p.margin_pct)}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); JobsPage.showForm(${j.id})">Edit</button></td>
            </tr>`;
        }).join('');
        return `<div class="table-container"><table>
            <thead><tr><th>Customer</th><th>Job</th><th>Status</th><th class="amount">Contract</th>
            <th class="amount">Income</th><th class="amount">Costs</th><th class="amount">Net</th><th class="amount">Margin</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="4">Total (${jobs.length})</td>
                <td class="amount">${formatCurrency(totals.income)}</td>
                <td class="amount">${formatCurrency(totals.total_costs)}</td>
                <td class="amount">${formatCurrency(totals.net_income)}</td>
                <td></td><td></td>
            </tr></tfoot>
        </table></div>`;
    },

    // -- Job detail: summary + job cost detail ------------------------------
    async showDetails(id) {
        let job, lines;
        try {
            [job, lines] = await Promise.all([API.get(`/jobs/${id}`), API.get(`/jobs/${id}/transactions`)]);
        } catch (err) { toast(err.message, 'error'); return; }
        const s = job.summary;
        const pct = v => (v === null || v === undefined) ? '—' : `${v.toFixed(1)}%`;
        const billed = job.contract_amount ? (s.income / parseFloat(job.contract_amount) * 100) : null;
        const sourceLabel = { invoice: 'Invoice', bill: 'Bill', expense: 'Expense', cc_charge: 'Card charge', manual: 'Journal', credit_memo: 'Credit memo', sales_receipt: 'Sales receipt', deposit: 'Deposit', check: 'Check' };
        const lineRows = lines.map(l => `<tr>
            <td>${escapeHtml(l.date)}</td>
            <td>${escapeHtml(sourceLabel[l.source_type] || l.source_type || '')}${l.reference ? ` ${escapeHtml(l.reference)}` : ''}</td>
            <td>${escapeHtml(l.account_name)}</td>
            <td>${escapeHtml(l.description || '')}</td>
            <td class="amount">${l.kind === 'income' ? formatCurrency(l.amount) : ''}</td>
            <td class="amount">${l.kind === 'cost' ? formatCurrency(l.amount) : ''}</td>
        </tr>`).join('');
        const stat = (label, value, color) => `<div style="min-width:120px;">
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">${label}</div>
            <div style="font-size:18px;font-weight:700;${color ? `color:${color}` : ''}">${value}</div></div>`;
        const html = `
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;margin-bottom:12px">
                <div>
                    <h3 style="margin:0;font-size:18px">${escapeHtml(job.full_name)}</h3>
                    <div style="margin-top:6px;font-size:12px;color:#888">
                        ${escapeHtml(JobsPage.STATUS_LABELS[job.status] || job.status)}
                        ${job.job_number ? ` · #${escapeHtml(job.job_number)}` : ''}
                        ${job.job_type ? ` · ${escapeHtml(job.job_type)}` : ''}
                        ${job.start_date ? ` · ${escapeHtml(job.start_date)}${job.projected_end_date ? ` → ${escapeHtml(job.projected_end_date)}` : ''}` : ''}
                        ${job.is_active ? '' : ' · <span style="color:#a4242b">Inactive</span>'}
                    </div>
                    ${job.site_address ? `<div style="font-size:12px;color:#666;margin-top:4px;white-space:pre-wrap">${escapeHtml(job.site_address)}</div>` : ''}
                </div>
                <div>
                    <button class="btn btn-sm btn-primary" onclick="closeModal();InvoicesPage.showForm(null,${job.customer_id})">New Invoice</button>
                    <button class="btn btn-sm btn-secondary" onclick="closeModal();App.navigate('#/bills')">Enter Bill</button>
                    <button class="btn btn-sm btn-secondary" onclick="JobsPage.showForm(${job.id})">Edit</button>
                </div>
            </div>
            <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:14px">
                ${stat('Contract', job.contract_amount ? formatCurrency(job.contract_amount) : '—')}
                ${stat('Income', formatCurrency(s.income))}
                ${stat('Costs', formatCurrency(s.total_costs))}
                ${stat('Net', formatCurrency(s.net_income), s.net_income < 0 ? '#a4242b' : '#1f7a36')}
                ${stat('Margin', pct(s.margin_pct))}
                ${stat('Billed vs contract', billed === null ? '—' : pct(billed))}
            </div>
            ${job.description ? `<p style="font-size:13px;margin:0 0 12px 0">${escapeHtml(job.description)}</p>` : ''}
            <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Job cost detail (${lines.length})</h4>
            ${lines.length === 0 ? '<p style="color:#888;font-size:13px;margin:0">Nothing posted to this job yet. Pick it in the Job field on an invoice, bill or expense.</p>' :
                `<div class="table-container"><table class="data-table" style="font-size:12px">
                    <thead><tr><th>Date</th><th>Source</th><th>Account</th><th>Memo</th><th class="amount">Income</th><th class="amount">Cost</th></tr></thead>
                    <tbody>${lineRows}</tbody>
                </table></div>`}
            ${job.notes ? `<h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:12px 0 4px 0">Notes</h4><pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(job.notes)}</pre>` : ''}`;
        openModal(`Job — ${job.full_name}`, html);
    },

    // -- Create / edit ---------------------------------------------------------
    async showForm(id = null, customerId = null) {
        let job = { customer_id: customerId || '', name: '', job_number: '', status: 'in_progress', job_type: '',
            description: '', site_address: '', start_date: '', projected_end_date: '', end_date: '',
            contract_amount: '', notes: '', is_active: true };
        if (id) { try { job = await API.get(`/jobs/${id}`); } catch (err) { toast(err.message, 'error'); return; } }
        if (!JobsPage._customers.length) { try { JobsPage._customers = await API.get('/customers'); } catch (e) { /* keep empty */ } }
        const custOpts = JobsPage._customers.map(c => `<option value="${c.id}" ${String(job.customer_id) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.name)}</option>`).join('');
        const statusOpts = Object.entries(JobsPage.STATUS_LABELS)
            .map(([k, v]) => `<option value="${k}" ${job.status === k ? 'selected' : ''}>${v}</option>`).join('');
        const html = `
            <form id="job-form" onsubmit="JobsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Job name *</label>
                        <input name="name" required maxlength="200" value="${escapeHtml(job.name)}" placeholder="Kitchen remodel"></div>
                    <div class="form-group"><label>Job #</label>
                        <input name="job_number" maxlength="50" value="${escapeHtml(job.job_number || '')}"></div>
                    <div class="form-group"><label>Status</label>
                        <select name="status">${statusOpts}</select></div>
                    <div class="form-group"><label>Job type</label>
                        <input name="job_type" maxlength="100" value="${escapeHtml(job.job_type || '')}" placeholder="Remodel, New build, Service…"></div>
                    <div class="form-group"><label>Contract amount</label>
                        <input name="contract_amount" type="number" step="0.01" min="0" value="${job.contract_amount ?? ''}"></div>
                    <div class="form-group"><label>Start date</label>
                        <input name="start_date" type="date" value="${job.start_date || ''}"></div>
                    <div class="form-group"><label>Projected end</label>
                        <input name="projected_end_date" type="date" value="${job.projected_end_date || ''}"></div>
                    <div class="form-group"><label>Actual end</label>
                        <input name="end_date" type="date" value="${job.end_date || ''}"></div>
                    ${id ? `<div class="form-group"><label>Active</label>
                        <select name="is_active"><option value="true" ${job.is_active ? 'selected' : ''}>Yes</option><option value="false" ${job.is_active ? '' : 'selected'}>No — hide from pickers</option></select></div>` : ''}
                    <div class="form-group full-width"><label>Site address</label>
                        <textarea name="site_address" rows="2">${escapeHtml(job.site_address || '')}</textarea></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description" rows="2">${escapeHtml(job.description || '')}</textarea></div>
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes" rows="2">${escapeHtml(job.notes || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    ${id ? `<button type="button" class="btn btn-secondary" onclick="JobsPage.remove(${id})" style="margin-right:auto;">Delete</button>` : ''}
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Save Job' : 'Create Job'}</button>
                </div>
            </form>`;
        openModal(id ? 'Edit Job' : 'New Job', html);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const val = n => form[n] ? (form[n].value || null) : null;
        const data = {
            customer_id: parseInt(form.customer_id.value),
            name: form.name.value.trim(),
            job_number: val('job_number'),
            status: form.status.value,
            job_type: val('job_type'),
            contract_amount: form.contract_amount.value ? parseFloat(form.contract_amount.value) : null,
            start_date: val('start_date'),
            projected_end_date: val('projected_end_date'),
            end_date: val('end_date'),
            site_address: val('site_address'),
            description: val('description'),
            notes: val('notes'),
        };
        if (form.is_active) data.is_active = form.is_active.value === 'true';
        try {
            if (id) await API.put(`/jobs/${id}`, data);
            else await API.post('/jobs', data);
            toast(id ? 'Job saved' : 'Job created');
            closeModal();
            if (location.hash.replace('#', '') === '/jobs') App.navigate('#/jobs');
        } catch (err) { toast(err.message, 'error'); }
    },

    async remove(id) {
        if (!confirm('Delete this job? Jobs with posted activity cannot be deleted — mark them inactive instead.')) return;
        try {
            await API.del(`/jobs/${id}`);
            toast('Job deleted');
            closeModal();
            App.navigate('#/jobs');
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.JobsPage = JobsPage;
