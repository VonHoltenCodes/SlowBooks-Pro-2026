/**
 * Jobs (Projects) — QuickBooks "Customer:Job" / Online "Projects".
 *
 * Two screens:
 *   #/jobs        every job with budget / committed / actual / projected /
 *                 variance, filterable; row click opens the job.
 *   #/jobs/<id>   the job page: Overview · Cost Detail (the drill-down:
 *                 cost type → division → code → sub-code → posted lines,
 *                 every level with the budget columns, each line linking
 *                 to its source document) · Budget (editor, seed from an
 *                 estimate) · Transactions · Time.
 *
 * Column vocabulary matches what contractors read weekly (Procore's
 * standard budget view, QuickBooks' Estimates vs Actuals):
 *   Budget = original + changes · Committed = open POs · Actual = posted
 *   JTD cost · Projected = actual + committed · Variance = budget −
 *   projected (positive = under) · % Used = projected / budget.
 * All figures come from posted ledger lines, so the page always agrees
 * with the Job Profitability report and reconciles to the P&L.
 */
const JobsPage = {
    _jobs: [],
    _bva: {},
    _customers: [],
    _filter: { customer_id: '', status: '', q: '' },
    // detail state
    _job: null,
    _tab: 'overview',
    _period: { start: '', end: '' },
    _tree: null,
    _open: new Set(),        // expanded node keys
    _showLines: new Set(),   // nodes with their line list open

    STATUS_LABELS: {
        pending: 'Pending', awarded: 'Awarded', in_progress: 'In progress',
        closed: 'Closed', not_awarded: 'Not awarded',
    },
    TAB_LABELS: { overview: 'Overview', costs: 'Cost Detail', budget: 'Budget', transactions: 'Transactions', time: 'Time' },

    // =====================================================================
    // List page
    // =====================================================================
    async render() {
        const [jobs, bva, customers] = await Promise.all([
            API.get('/jobs?include_inactive=true'),
            API.get('/jobs/budget-vs-actual?include_inactive=true').catch(() => []),
            API.get('/customers'),
        ]);
        JobsPage._jobs = jobs;
        JobsPage._customers = customers;
        JobsPage._bva = {};
        for (const r of bva) JobsPage._bva[r.job_id] = r;

        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        const statusOpts = Object.entries(JobsPage.STATUS_LABELS)
            .map(([k, v]) => `<option value="${k}">${v}</option>`).join('');

        return `
            <div class="page-header">
                <h2>Jobs</h2>
                <div>
                    <button class="btn btn-secondary" onclick="App.navigate('#/job-costs')">Job Cost Entries</button>
                    <button class="btn btn-primary" onclick="JobsPage.showForm()">+ New Job</button>
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                <input type="text" placeholder="Search jobs..." id="job-search" oninput="JobsPage.setFilter('q', this.value)">
                <select id="job-filter-customer" onchange="JobsPage.setFilter('customer_id', this.value)">
                    <option value="">All customers</option>${custOpts}</select>
                <select id="job-filter-status" onchange="JobsPage.setFilter('status', this.value)">
                    <option value="">Active jobs</option>${statusOpts}<option value="__inactive__">Inactive</option></select>
                <span style="font-size:11px; color:var(--gray-500);">Job-to-date figures from posted lines. Click a job to drill down.</span>
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
                <p>${JobsPage._jobs.length ? 'No jobs match.' : 'No jobs yet. A job is a customer\'s project — every invoice, bill, expense, time entry and job cost can be tagged to one.'}</p>
                ${JobsPage._jobs.length ? '' : '<button class="btn btn-primary" onclick="JobsPage.showForm()" style="margin-top:10px;">+ Create your first job</button>'}
            </div>`;
        }
        const t = { revised: 0, committed: 0, actual: 0, projected: 0, variance: 0, act_revenue: 0 };
        const rows = jobs.map(j => {
            const b = JobsPage._bva[j.id] || {};
            for (const k of Object.keys(t)) t[k] += (b[k] || 0);
            return `<tr class="clickable" onclick="App.navigate('#/jobs/${j.id}')">
                <td>${escapeHtml(j.customer_name)}</td>
                <td><strong>${escapeHtml(j.name)}</strong>${j.job_number ? ` <span style="color:var(--gray-500); font-size:11px;">#${escapeHtml(j.job_number)}</span>` : ''}</td>
                <td><span class="badge">${escapeHtml(JobsPage.STATUS_LABELS[j.status] || j.status)}</span>${j.is_active ? '' : ' <span style="color:var(--gray-500); font-size:11px;">inactive</span>'}</td>
                <td class="amount">${JobsPage.money(b.revised)}</td>
                <td class="amount">${JobsPage.money(b.committed)}</td>
                <td class="amount">${JobsPage.money(b.actual)}</td>
                <td class="amount">${JobsPage.money(b.projected)}</td>
                <td class="amount" style="font-weight:700; ${JobsPage.varColor(b)}">${JobsPage.money(b.variance)}</td>
                <td class="amount">${JobsPage.pct(b.pct_used)}</td>
                <td class="amount">${JobsPage.money(b.act_revenue)}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); JobsPage.showForm(${j.id})">Edit</button></td>
            </tr>`;
        }).join('');
        return `<div class="table-container"><table>
            <thead><tr><th scope="col">Customer</th><th scope="col">Job</th><th scope="col">Status</th>
            <th scope="col" class="amount" title="Original budget + changes">Budget</th>
            <th scope="col" class="amount" title="Open purchase orders not yet billed">Committed</th>
            <th scope="col" class="amount" title="Posted job-to-date cost">Actual</th>
            <th scope="col" class="amount" title="Actual + committed">Projected</th>
            <th scope="col" class="amount" title="Budget − projected (positive = under)">Variance</th>
            <th scope="col" class="amount">% Used</th><th scope="col" class="amount">Revenue</th><th scope="col"></th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr style="font-weight:700; background:var(--gray-50);">
                <td colspan="3">Total (${jobs.length})</td>
                <td class="amount">${JobsPage.money(t.revised)}</td>
                <td class="amount">${JobsPage.money(t.committed)}</td>
                <td class="amount">${JobsPage.money(t.actual)}</td>
                <td class="amount">${JobsPage.money(t.projected)}</td>
                <td class="amount">${JobsPage.money(t.variance)}</td>
                <td></td>
                <td class="amount">${JobsPage.money(t.act_revenue)}</td>
                <td></td>
            </tr></tfoot>
        </table></div>`;
    },

    // ---- formatting helpers -------------------------------------------------
    money(v) { return (v === null || v === undefined) ? '' : formatCurrency(v); },
    pct(v) { return (v === null || v === undefined) ? '—' : `${v.toFixed(1)}%`; },
    varColor(f) {
        if (!f || f.revised === undefined || !f.revised) return '';
        return f.variance < 0 ? 'color:#a4242b;' : 'color:#1f7a36;';
    },

    // =====================================================================
    // Job page
    // =====================================================================
    async renderDetail(id) {
        JobsPage._job = null;
        JobsPage._tree = null;
        JobsPage._open = new Set();
        JobsPage._showLines = new Set();
        try {
            JobsPage._job = await API.get(`/jobs/${id}`);
        } catch (err) {
            return `<div class="empty-state"><p>${escapeHtml(err.message)}</p><a href="#/jobs">Back to jobs</a></div>`;
        }
        const job = JobsPage._job;
        return `
            <div class="page-header">
                <div>
                    <div style="font-size:11px;"><a href="#/jobs">Jobs</a> › ${escapeHtml(job.customer_name)}</div>
                    <h2 style="margin:2px 0 0 0;">${escapeHtml(job.name)}
                        <span class="badge" style="font-size:11px; vertical-align:middle;">${escapeHtml(JobsPage.STATUS_LABELS[job.status] || job.status)}</span>
                        ${job.is_active ? '' : '<span style="font-size:11px;color:#a4242b;">inactive</span>'}
                    </h2>
                </div>
                <div>
                    <button class="btn btn-secondary" onclick="InvoicesPage.showForm(null,${job.customer_id})">New Invoice</button>
                    <button class="btn btn-secondary" onclick="JobCostsPage.showForm(null, ${job.id})">Job Cost Entry</button>
                    <button class="btn btn-secondary" onclick="JobsPage.showForm(${job.id})">Edit</button>
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                ${Object.entries(JobsPage.TAB_LABELS).map(([t, label]) => `
                    <button class="btn btn-sm ${JobsPage._tab === t ? 'btn-primary' : 'btn-secondary'}" data-jobtab="${t}" onclick="JobsPage.setTab('${t}')">${label}</button>`).join('')}
                <span style="margin-left:auto; font-size:11px; display:flex; gap:6px; align-items:center;">
                    Period <input type="date" id="job-period-start" value="${JobsPage._period.start}" onchange="JobsPage.setPeriod()">
                    – <input type="date" id="job-period-end" value="${JobsPage._period.end}" onchange="JobsPage.setPeriod()">
                    <button class="btn btn-sm btn-secondary" onclick="JobsPage.clearPeriod()" title="Job to date">JTD</button>
                </span>
            </div>
            <div id="job-tab-body">${await JobsPage.tabHtml()}</div>`;
    },

    async setTab(tab) {
        JobsPage._tab = tab;
        $$('[data-jobtab]').forEach(b => { b.className = `btn btn-sm ${b.dataset.jobtab === tab ? 'btn-primary' : 'btn-secondary'}`; });
        const body = $('#job-tab-body');
        if (body) { body.innerHTML = '<p style="color:var(--gray-500);">Loading…</p>'; body.innerHTML = await JobsPage.tabHtml(); }
    },
    async setPeriod() {
        JobsPage._period = { start: $('#job-period-start')?.value || '', end: $('#job-period-end')?.value || '' };
        JobsPage._tree = null;
        await JobsPage.setTab(JobsPage._tab);
    },
    async clearPeriod() {
        JobsPage._period = { start: '', end: '' };
        const s = $('#job-period-start'), e = $('#job-period-end');
        if (s) s.value = ''; if (e) e.value = '';
        JobsPage._tree = null;
        await JobsPage.setTab(JobsPage._tab);
    },
    _periodQs() {
        const p = JobsPage._period;
        const parts = [];
        if (p.start) parts.push(`start_date=${p.start}`);
        if (p.end) parts.push(`end_date=${p.end}`);
        return parts.length ? '?' + parts.join('&') : '';
    },

    async tabHtml() {
        try {
            switch (JobsPage._tab) {
                case 'costs': return await JobsPage.costsHtml();
                case 'budget': return await JobsPage.budgetHtml();
                case 'transactions': return await JobsPage.transactionsHtml();
                case 'time': return await JobsPage.timeHtml();
                default: return await JobsPage.overviewHtml();
            }
        } catch (err) {
            return `<div style="color:#a4242b;">${escapeHtml(err.message)}</div>`;
        }
    },

    async loadTree() {
        if (!JobsPage._tree) JobsPage._tree = await API.get(`/jobs/${JobsPage._job.id}/cost-tree${JobsPage._periodQs()}`);
        return JobsPage._tree;
    },

    // ---- Overview -------------------------------------------------------------
    async overviewHtml() {
        const job = JobsPage._job;
        const tree = await JobsPage.loadTree();
        const t = tree.totals;
        const stat = (label, value, color, title) => `<div style="min-width:130px;" title="${title || ''}">
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">${label}</div>
            <div style="font-size:18px;font-weight:700;${color ? `color:${color}` : ''}">${value}</div></div>`;
        const contract = job.contract_amount ? parseFloat(job.contract_amount) : null;
        const billedPct = contract ? (t.act_revenue / contract * 100) : null;
        const margin = t.act_revenue ? ((t.act_revenue - t.actual) / t.act_revenue * 100) : null;
        const typeRows = tree.types.map(ty => `<tr class="clickable" onclick="JobsPage.jumpTo('type:${ty.cost_type}')">
            <td>${escapeHtml(ty.name)}</td>
            <td class="amount">${JobsPage.money(ty.figures.revised)}</td>
            <td class="amount">${JobsPage.money(ty.figures.committed)}</td>
            <td class="amount">${JobsPage.money(ty.figures.actual)}</td>
            <td class="amount">${JobsPage.money(ty.figures.projected)}</td>
            <td class="amount" style="${JobsPage.varColor(ty.figures)}">${JobsPage.money(ty.figures.variance)}</td>
            <td class="amount">${JobsPage.pct(ty.figures.pct_used)}</td>
        </tr>`).join('');
        return `
            <div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:16px">
                ${stat('Contract', contract !== null ? formatCurrency(contract) : '—')}
                ${stat('Budget', JobsPage.money(t.revised), null, 'Original + changes')}
                ${stat('Committed', JobsPage.money(t.committed), null, 'Open POs not yet billed')}
                ${stat('Actual cost', JobsPage.money(t.actual))}
                ${stat('Projected', JobsPage.money(t.projected), null, 'Actual + committed')}
                ${stat('Variance', JobsPage.money(t.variance), t.revised ? (t.variance < 0 ? '#a4242b' : '#1f7a36') : null, 'Budget − projected')}
                ${stat('Revenue', JobsPage.money(t.act_revenue))}
                ${stat('Margin', JobsPage.pct(margin), margin !== null && margin < 0 ? '#a4242b' : null, '(revenue − cost) / revenue')}
                ${stat('Billed vs contract', JobsPage.pct(billedPct))}
            </div>
            <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start;">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">By cost type — click a row to drill down</h4>
                    <div class="table-container"><table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">Type</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th><th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th></tr></thead>
                        <tbody>${typeRows || '<tr><td colspan="7" style="color:#888">Nothing budgeted or posted yet.</td></tr>'}</tbody>
                    </table></div>
                </div>
                <div style="font-size:13px">
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Job</h4>
                    <div>${job.job_number ? `#${escapeHtml(job.job_number)} · ` : ''}${escapeHtml(job.job_type || '')}</div>
                    <div>${job.start_date ? escapeHtml(job.start_date) : ''}${job.projected_end_date ? ` → ${escapeHtml(job.projected_end_date)}` : ''}${job.end_date ? ` (ended ${escapeHtml(job.end_date)})` : ''}</div>
                    ${job.site_address ? `<pre style="font-family:inherit;white-space:pre-wrap;margin:6px 0">${escapeHtml(job.site_address)}</pre>` : ''}
                    ${job.description ? `<p style="margin:6px 0">${escapeHtml(job.description)}</p>` : ''}
                    ${job.notes ? `<h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:10px 0 4px 0">Notes</h4><pre style="font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(job.notes)}</pre>` : ''}
                </div>
            </div>`;
    },

    async jumpTo(key) {
        JobsPage._open.add(key);
        await JobsPage.setTab('costs');
        const el = document.querySelector(`[data-node="${key}"]`);
        if (el) el.scrollIntoView({ block: 'center' });
    },

    // ---- Cost Detail: the drill-down tree --------------------------------------
    async costsHtml() {
        const tree = await JobsPage.loadTree();
        if (!tree.types.length) {
            return `<div class="empty-state"><p>Nothing budgeted or posted to this job yet.</p>
                <p style="font-size:12px;color:#888">Tag a bill, expense, time entry or job cost entry to it, or set a budget on the Budget tab.</p></div>`;
        }
        const head = `<thead><tr>
            <th scope="col" style="min-width:260px">Cost type › code</th>
            <th scope="col" class="amount" title="Original budget">Original</th>
            <th scope="col" class="amount" title="Budget changes">Changes</th>
            <th scope="col" class="amount" title="Original + changes">Budget</th>
            <th scope="col" class="amount" title="Open POs">Committed</th>
            <th scope="col" class="amount" title="Posted cost">Actual</th>
            <th scope="col" class="amount" title="Actual + committed">Projected</th>
            <th scope="col" class="amount" title="Budget − projected">Variance</th>
            <th scope="col" class="amount">% Used</th>
            <th scope="col" class="amount" title="Estimated revenue (from the estimate / budget)">Est. Rev</th>
            <th scope="col" class="amount" title="Invoiced revenue on this code">Act. Rev</th>
        </tr></thead>`;
        let body = '';
        for (const ty of tree.types) {
            const key = `type:${ty.cost_type}`;
            const open = JobsPage._open.has(key);
            body += JobsPage.rowHtml(key, 0, `${escapeHtml(ty.name)}${ty.is_labor ? ' <span style="font-size:10px;color:#888">(burden applies)</span>' : ''}`, ty.figures, open, true, 'type');
            if (open) {
                for (const node of ty.codes) body += JobsPage.nodeHtml(node, 1);
                const un = ty.uncoded;
                if (un.lines.length || un.figures.actual || un.figures.committed) {
                    const ukey = `uncoded:${ty.cost_type}`;
                    body += JobsPage.rowHtml(ukey, 1, '<em>No cost code</em>', un.figures, JobsPage._showLines.has(ukey), false, 'uncoded', un.lines.length);
                    if (JobsPage._showLines.has(ukey)) body += JobsPage.linesHtml(un.lines, 2);
                }
            }
        }
        const jl = tree.job_level_budget;
        if (jl.original || jl.changes || jl.est_revenue) {
            body += JobsPage.rowHtml('joblevel', 0, '<em>Whole-job budget (not by code)</em>', jl, false, false, 'joblevel');
        }
        const t = tree.totals;
        const foot = `<tfoot><tr style="font-weight:700;background:var(--gray-50)">
            <td>Total</td>
            <td class="amount">${JobsPage.money(t.original)}</td><td class="amount">${JobsPage.money(t.changes)}</td>
            <td class="amount">${JobsPage.money(t.revised)}</td><td class="amount">${JobsPage.money(t.committed)}</td>
            <td class="amount">${JobsPage.money(t.actual)}</td><td class="amount">${JobsPage.money(t.projected)}</td>
            <td class="amount" style="${JobsPage.varColor(t)}">${JobsPage.money(t.variance)}</td>
            <td class="amount">${JobsPage.pct(t.pct_used)}</td>
            <td class="amount">${JobsPage.money(t.est_revenue)}</td><td class="amount">${JobsPage.money(t.act_revenue)}</td>
        </tr></tfoot>`;
        return `
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;font-size:11px;color:#888">
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.expandAll(true)">Expand all</button>
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.expandAll(false)">Collapse all</button>
                <span>Click a type or code to open it; click the line count to see the posted lines; click a line to open its document.</span>
            </div>
            <div class="table-container"><table class="data-table job-tree" style="font-size:12px">${head}<tbody>${body}</tbody>${foot}</table></div>`;
    },

    nodeHtml(node, depth) {
        const key = `code:${node.id}`;
        const open = JobsPage._open.has(key);
        const hasKids = node.children.length > 0;
        let html = JobsPage.rowHtml(key, depth, `<code>${escapeHtml(node.code)}</code> ${escapeHtml(node.name)}${node.is_active ? '' : ' <span style="font-size:10px;color:#888">(inactive)</span>'}`, node.figures, open, hasKids, 'code', node.lines.length);
        if (open && hasKids) for (const ch of node.children) html += JobsPage.nodeHtml(ch, depth + 1);
        if (JobsPage._showLines.has(key)) {
            if (hasKids && (node.own.actual || node.own.act_revenue)) {
                html += `<tr><td colspan="11" style="padding-left:${16 + depth * 18}px;font-size:11px;color:#888">Own lines on ${escapeHtml(node.code)} (children listed under their own codes):</td></tr>`;
            }
            html += JobsPage.linesHtml(node.lines, depth + 1);
        }
        return html;
    },

    rowHtml(key, depth, label, f, open, expandable, kind, lineCount) {
        const indent = 8 + depth * 18;
        const caret = expandable ? `<span style="display:inline-block;width:14px;">${open ? '▾' : '▸'}</span>` : '<span style="display:inline-block;width:14px;"></span>';
        const count = (lineCount !== undefined) ? ` <a href="#" onclick="event.preventDefault();event.stopPropagation();JobsPage.toggleLines('${key}')" style="font-size:10px;color:var(--gray-500)" title="Show the posted lines">${lineCount} line${lineCount === 1 ? '' : 's'}${JobsPage._showLines.has(key) ? ' ▴' : ' ▾'}</a>` : '';
        const weight = kind === 'type' ? 'font-weight:700;background:var(--gray-50);' : (depth === 1 ? 'font-weight:600;' : '');
        return `<tr data-node="${key}" ${expandable ? `class="clickable" onclick="JobsPage.toggle('${key}')"` : ''} style="${weight}">
            <td style="padding-left:${indent}px;white-space:nowrap">${caret}${label}${count}</td>
            <td class="amount">${JobsPage.money(f.original)}</td>
            <td class="amount">${JobsPage.money(f.changes)}</td>
            <td class="amount">${JobsPage.money(f.revised)}</td>
            <td class="amount">${JobsPage.money(f.committed)}</td>
            <td class="amount">${JobsPage.money(f.actual)}</td>
            <td class="amount">${JobsPage.money(f.projected)}</td>
            <td class="amount" style="${JobsPage.varColor(f)}">${JobsPage.money(f.variance)}</td>
            <td class="amount">${JobsPage.pct(f.pct_used)}</td>
            <td class="amount">${JobsPage.money(f.est_revenue)}</td>
            <td class="amount">${JobsPage.money(f.act_revenue)}</td>
        </tr>`;
    },

    linesHtml(lines, depth) {
        if (!lines.length) return `<tr><td colspan="11" style="padding-left:${16 + depth * 18}px;color:#888;font-size:11px">No posted lines.</td></tr>`;
        return lines.map(l => `<tr class="clickable" onclick="JobsPage.openSource('${escapeHtml(l.source_type || '')}', ${l.source_id === null ? 'null' : l.source_id}, ${l.transaction_id})" style="font-size:11px;color:var(--gray-600)">
            <td style="padding-left:${16 + depth * 18}px">${escapeHtml(l.date)} · <strong>${escapeHtml(JobsPage.sourceLabel(l.source_type))}</strong>${l.reference ? ` ${escapeHtml(l.reference)}` : ''} · ${escapeHtml(l.description || '')} <span style="color:#aaa">(${escapeHtml(l.account_name)})</span>${l.is_billable ? ' <span class="badge" style="font-size:9px">billable</span>' : ''}</td>
            <td colspan="4"></td>
            <td class="amount">${l.kind === 'cost' ? formatCurrency(l.amount) : ''}</td>
            <td colspan="3"></td>
            <td class="amount">${l.kind === 'income' ? formatCurrency(l.amount) : ''}</td>
        </tr>`).join('');
    },

    sourceLabel(t) {
        return { invoice: 'Invoice', bill: 'Bill', expense: 'Expense', cc_charge: 'Card charge', manual: 'Journal',
            credit_memo: 'Credit memo', sales_receipt: 'Sales receipt', deposit: 'Deposit', check: 'Check',
            job_cost: 'Job cost', job_cost_void: 'Void job cost', expense_void: 'Void expense', bill_void: 'Void bill' }[t] || (t || 'Entry');
    },

    async toggle(key) { JobsPage._open.has(key) ? JobsPage._open.delete(key) : JobsPage._open.add(key); await JobsPage.setTab('costs'); },
    async toggleLines(key) { JobsPage._showLines.has(key) ? JobsPage._showLines.delete(key) : JobsPage._showLines.add(key); await JobsPage.setTab('costs'); },
    async expandAll(open) {
        const tree = await JobsPage.loadTree();
        JobsPage._open = new Set();
        if (open) {
            const walk = n => { JobsPage._open.add(`code:${n.id}`); n.children.forEach(walk); };
            for (const ty of tree.types) { JobsPage._open.add(`type:${ty.cost_type}`); ty.codes.forEach(walk); }
        }
        await JobsPage.setTab('costs');
    },

    // Open the document behind a posted line. Falls back to the journal
    // entry when a page has no viewer for that source.
    openSource(sourceType, sourceId, transactionId) {
        const go = (fn) => { try { fn(); } catch (e) { toast('Could not open the document', 'error'); } };
        if (sourceType === 'invoice' && sourceId && window.InvoicesPage?.view) return go(() => InvoicesPage.view(sourceId));
        if (sourceType === 'bill' && sourceId && window.BillsPage?.view) return go(() => BillsPage.view(sourceId));
        if (sourceType === 'expense' && window.ExpensesPage?.showDetail) return go(() => ExpensesPage.showDetail(transactionId));
        if ((sourceType === 'job_cost' || sourceType === 'job_cost_void') && sourceId && window.JobCostsPage?.view) return go(() => JobCostsPage.view(sourceId));
        if (window.JournalPage?.view) return go(() => JournalPage.view(transactionId));
        toast('No viewer for this document type');
    },

    // ---- Budget editor -----------------------------------------------------------
    async budgetHtml() {
        const job = JobsPage._job;
        const [rows, codes, types, estimates] = await Promise.all([
            API.get(`/jobs/${job.id}/budgets`),
            API.get('/cost-codes'),
            API.get('/cost-types'),
            API.get(`/estimates?customer_id=${job.customer_id}`).catch(() => []),
        ]);
        const byCode = {}; const byType = {}; let whole = null;
        for (const r of rows) {
            if (r.cost_code_id) byCode[r.cost_code_id] = r;
            else if (r.cost_type) byType[r.cost_type] = r;
            else whole = r;
        }
        const codeRows = codes.map(c => {
            const r = byCode[c.id] || {};
            return `<tr data-budget-code="${c.id}">
                <td style="padding-left:${8 + (c.depth || 0) * 18}px"><code>${escapeHtml(c.code)}</code> ${escapeHtml(c.name)} <span style="font-size:10px;color:#888">${escapeHtml(c.cost_type)}</span></td>
                <td><input type="number" step="0.01" class="bud-cost" value="${r.amount ?? ''}" style="width:110px;text-align:right"></td>
                <td><input type="number" step="0.01" class="bud-rev" value="${r.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                <td style="font-size:10px;color:#888">${r.source ? escapeHtml(r.source) : ''}</td>
            </tr>`;
        }).join('');
        const typeRows = types.map(t => {
            const r = byType[t.code] || {};
            return `<tr data-budget-type="${t.code}">
                <td>${escapeHtml(t.name)} <span style="font-size:10px;color:#888">(whole type, not by code)</span></td>
                <td><input type="number" step="0.01" class="bud-cost" value="${r.amount ?? ''}" style="width:110px;text-align:right"></td>
                <td><input type="number" step="0.01" class="bud-rev" value="${r.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                <td style="font-size:10px;color:#888">${r.source ? escapeHtml(r.source) : ''}</td>
            </tr>`;
        }).join('');
        const estOpts = (estimates || []).filter(e => e.job_id === job.id || !e.job_id)
            .map(e => `<option value="${e.id}">${escapeHtml(e.estimate_number || ('#' + e.id))} · ${escapeHtml(e.date || '')} · ${formatCurrency(e.total)}${e.job_id === job.id ? ' (this job)' : ''}</option>`).join('');
        return `
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;font-size:12px">
                <span>Seed from an estimate:</span>
                <select id="budget-estimate"><option value="">Pick an estimate…</option>${estOpts}</select>
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.seedBudget()">Load estimate lines as budget</button>
                <span style="color:#888;font-size:11px">Cost = qty × unit cost (or the line amount when no cost is entered); revenue = the line amount. Rows you edit here become manual and survive re-seeding.</span>
            </div>
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Cost code</th><th scope="col">Budget cost</th><th scope="col">Est. revenue</th><th scope="col">Source</th></tr></thead>
                <tbody>
                    ${codeRows || '<tr><td colspan="4" style="color:#888">No cost codes yet — add them under Settings → Cost Codes, or budget by type below.</td></tr>'}
                    <tr><td colspan="4" style="background:var(--gray-50);font-weight:600;font-size:11px">By cost type</td></tr>
                    ${typeRows}
                    <tr data-budget-whole="1"><td><em>Whole job (not by code or type)</em></td>
                        <td><input type="number" step="0.01" class="bud-cost" value="${whole?.amount ?? ''}" style="width:110px;text-align:right"></td>
                        <td><input type="number" step="0.01" class="bud-rev" value="${whole?.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                        <td style="font-size:10px;color:#888">${whole?.source ? escapeHtml(whole.source) : ''}</td></tr>
                </tbody>
            </table></div>
            <div class="form-actions"><button class="btn btn-primary" onclick="JobsPage.saveBudget()">Save Budget</button></div>`;
    },

    async saveBudget() {
        const rows = [];
        const num = el => { const v = parseFloat(el?.value); return isNaN(v) ? 0 : v; };
        $$('[data-budget-code]').forEach(tr => rows.push({ cost_code_id: parseInt(tr.dataset.budgetCode), amount: num(tr.querySelector('.bud-cost')), revenue_amount: num(tr.querySelector('.bud-rev')) }));
        $$('[data-budget-type]').forEach(tr => rows.push({ cost_type: tr.dataset.budgetType, amount: num(tr.querySelector('.bud-cost')), revenue_amount: num(tr.querySelector('.bud-rev')) }));
        const whole = document.querySelector('[data-budget-whole]');
        if (whole) rows.push({ amount: num(whole.querySelector('.bud-cost')), revenue_amount: num(whole.querySelector('.bud-rev')) });
        try {
            await API.put(`/jobs/${JobsPage._job.id}/budgets`, { rows });
            toast('Budget saved');
            JobsPage._tree = null;
            await JobsPage.setTab('budget');
        } catch (err) { toast(err.message, 'error'); }
    },

    async seedBudget() {
        const estId = $('#budget-estimate')?.value;
        if (!estId) { toast('Pick an estimate first', 'error'); return; }
        try {
            await API.post(`/jobs/${JobsPage._job.id}/budgets/from-estimate/${estId}`, {});
            toast('Budget loaded from the estimate');
            JobsPage._tree = null;
            await JobsPage.setTab('budget');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Transactions (flat) -------------------------------------------------------
    async transactionsHtml() {
        const lines = await API.get(`/jobs/${JobsPage._job.id}/transactions${JobsPage._periodQs()}`);
        if (!lines.length) return '<div class="empty-state"><p>Nothing posted to this job in the period.</p></div>';
        const rows = lines.map(l => `<tr class="clickable" onclick="JobsPage.openSource('${escapeHtml(l.source_type || '')}', ${l.source_id === null ? 'null' : l.source_id}, ${l.transaction_id})">
            <td>${escapeHtml(l.date)}</td>
            <td>${escapeHtml(JobsPage.sourceLabel(l.source_type))}${l.reference ? ` ${escapeHtml(l.reference)}` : ''}</td>
            <td>${escapeHtml(l.account_name)}</td>
            <td>${escapeHtml(l.description || '')}</td>
            <td class="amount">${l.kind === 'income' ? formatCurrency(l.amount) : ''}</td>
            <td class="amount">${l.kind === 'cost' ? formatCurrency(l.amount) : ''}</td>
        </tr>`).join('');
        return `<div class="table-container"><table class="data-table" style="font-size:12px">
            <thead><tr><th scope="col">Date</th><th scope="col">Source</th><th scope="col">Account</th><th scope="col">Memo</th><th scope="col" class="amount">Income</th><th scope="col" class="amount">Cost</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`;
    },

    // ---- Time ----------------------------------------------------------------------
    async timeHtml() {
        const job = JobsPage._job;
        const entries = (await API.get('/time-entries')).filter(e => e.job_id === job.id);
        if (!entries.length) return '<div class="empty-state"><p>No time entries tagged to this job. Pick the job on a time entry to bring labor cost here.</p></div>';
        const unposted = entries.filter(e => !e.job_cost_id && (e.status === 'approved' || e.status === 'submitted'));
        const rows = entries.map(e => `<tr>
            <td>${escapeHtml(e.date)}</td><td>${escapeHtml(e.employee_name || '')}</td>
            <td>${escapeHtml(e.cost_code_label || '')}</td>
            <td class="amount">${e.hours_regular}</td><td class="amount">${e.hours_overtime}</td><td class="amount">${e.hours_doubletime}</td>
            <td>${escapeHtml(e.status)}</td>
            <td>${e.job_cost_id ? `<a href="#" onclick="event.preventDefault();JobCostsPage.view(${e.job_cost_id})">posted</a>` : ((e.status === 'approved' || e.status === 'submitted') ? `<button class="btn btn-sm btn-secondary" onclick="JobsPage.postTime([${e.id}])">Post to job</button>` : '<span style="color:#888">draft</span>')}</td>
        </tr>`).join('');
        return `
            ${unposted.length ? `<div style="margin-bottom:8px"><button class="btn btn-sm btn-primary" onclick="JobsPage.postTime([${unposted.map(e => e.id).join(',')}])">Post ${unposted.length} approved entr${unposted.length === 1 ? 'y' : 'ies'} to this job</button>
                <span style="font-size:11px;color:#888">Labor posts at the employee's loaded cost rate with burden as its own line.</span></div>` : ''}
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Date</th><th scope="col">Employee</th><th scope="col">Cost code</th><th scope="col" class="amount">Reg</th><th scope="col" class="amount">OT</th><th scope="col" class="amount">DT</th><th scope="col">Status</th><th scope="col">Job cost</th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
    },

    async postTime(ids) {
        try {
            const r = await API.post('/time-entries/post-to-job', { ids });
            const failed = r.results.filter(x => !x.ok);
            toast(`${r.posted} posted${failed.length ? `, ${failed.length} failed: ${failed[0].error}` : ''}`, failed.length ? 'error' : undefined);
            JobsPage._tree = null;
            await JobsPage.setTab('time');
        } catch (err) { toast(err.message, 'error'); }
    },

    // =====================================================================
    // Create / edit
    // =====================================================================
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
            const saved = id ? await API.put(`/jobs/${id}`, data) : await API.post('/jobs', data);
            toast(id ? 'Job saved' : 'Job created');
            closeModal();
            App.navigate(`#/jobs/${id || saved.id}`);
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

    // Kept for callers from other pages (Customer Center, reports).
    showDetails(id) { App.navigate(`#/jobs/${id}`); },
};
window.JobsPage = JobsPage;
