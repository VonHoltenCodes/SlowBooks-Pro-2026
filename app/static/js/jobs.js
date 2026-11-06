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

    // The detail tabs, with the Contract tab folded in (after Budget) only in
    // construction mode (#552); other companies see today's five tabs exactly.
    tabLabels() {
        if (!Terms.isConstruction()) return JobsPage.TAB_LABELS;
        const { overview, costs, budget, ...rest } = JobsPage.TAB_LABELS;
        // Contract (the owner's side), Buyout (the sub/supplier side, #553),
        // then Change orders (#554).
        return { overview, costs, budget, contract: 'Contract', buyout: 'Buyout', changeorders: 'Change orders', payapps: 'Pay apps', ...rest };
    },

    // =====================================================================
    // List page
    // =====================================================================
    async render() {
        const [jobs, bva] = await Promise.all([
            API.get('/jobs?include_inactive=true'),
            API.get('/jobs/budget-vs-actual?include_inactive=true').catch(() => []),
        ]);
        JobsPage._jobs = jobs;
        ActiveLists.remember('jobs', jobs);
        JobsPage._bva = {};
        for (const r of bva) JobsPage._bva[r.job_id] = r;

        // The filter offers the customers these jobs belong to — every one,
        // an inactive customer's too — rather than every customer on file.
        const jobCustomers = new Map();
        for (const j of jobs) if (j.customer_id) jobCustomers.set(j.customer_id, j.customer_name || '');
        const custOpts = [...jobCustomers].sort((a, b) => a[1].localeCompare(b[1]))
            .map(([cid, name]) => `<option value="${cid}">${escapeHtml(name)}</option>`).join('');
        const statusOpts = Object.entries(JobsPage.STATUS_LABELS)
            .map(([k, v]) => `<option value="${k}">${v}</option>`).join('');

        return `
            <div class="page-header">
                <h2>${T('Jobs')}</h2>
                <div>
                    <button class="btn btn-secondary" onclick="App.navigate('#/job-costs')">${T('Job Cost Entries')}</button>
                    <button class="btn btn-primary" onclick="JobsPage.showForm()">+ New ${T('Job')}</button>
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                <input type="text" placeholder="${Terms.text('Search jobs...')}" id="job-search" oninput="JobsPage.setFilter('q', this.value)">
                <select id="job-filter-customer" aria-label="${Terms.text('Customer')}" onchange="JobsPage.setFilter('customer_id', this.value)">
                    <option value="">${Terms.text('All customers')}</option>${custOpts}</select>
                <select id="job-filter-status" aria-label="Status" onchange="JobsPage.setFilter('status', this.value)">
                    <option value="">All statuses</option>${statusOpts}</select>
                ${ActiveLists.pickerHtml('jobs')}
                <span style="font-size:11px; color:var(--gray-500);">${Terms.text('Job-to-date figures from posted lines. Click a job to drill down.')}</span>
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
            // Show: Active / Inactive / All (#210); Status is the job's own
            if (!ActiveLists.shows('jobs', j)) return false;
            if (f.status && j.status !== f.status) return false;
            if (f.customer_id && String(j.customer_id) !== String(f.customer_id)) return false;
            if (q && !(`${j.customer_name} ${j.name} ${j.job_number || ''}`.toLowerCase().includes(q))) return false;
            return true;
        });
    },

    _tableHtml() {
        const jobs = JobsPage._visible();
        if (jobs.length === 0) {
            return `<div class="empty-state">
                <p>${Terms.text(JobsPage._jobs.length ? 'No jobs match.' : 'No jobs yet. A job is a customer\'s project — every invoice, bill, expense, time entry and job cost can be tagged to one.')}</p>
                ${JobsPage._jobs.length ? '' : '<button class="btn btn-primary" onclick="JobsPage.showForm()" style="margin-top:10px;">+ ' + Terms.text('Create your first job') + '</button>'}
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
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); JobsPage.showForm(${j.id})">Edit</button>
                    ${ActiveLists.buttonHtml('jobs', j)}</td>
            </tr>`;
        }).join('');
        return `<div class="table-container"><table>
            <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">${T('Job')}</th><th scope="col">Status</th>
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
        return f.variance < 0 ? 'color:var(--text-danger);' : 'color:var(--text-success);';
    },

    // =====================================================================
    // Job page
    // =====================================================================
    // The address can carry the period (#/jobs/12?start_date=…&end_date=…)
    // and the report it was opened from (&from=profit-loss-by-job), so a
    // report's heading lands on the job for the report's dates with a way
    // back (#242); a period set on the page is written back to the address
    // (replaced, as a change inside a view is).
    async renderDetail(id, query) {
        JobsPage._job = null;
        JobsPage._tree = null;
        JobsPage._open = new Set();
        JobsPage._showLines = new Set();
        const q = query || {};
        // a date that is not one, or From after To, is said so and ignored
        const dates = datesFromQuery(q);
        // The period is the address's, every time; none is Job to date.
        // (Set only when the address carried one, the last job's period
        // stayed on the next job opened from the list — its boxes and its
        // figures, with an address that said nothing of it — and a refused
        // range named that period as "kept": round-3 review.)
        JobsPage._period = { start: dates.start_date, end: dates.end_date };
        JobsPage._from =(typeof ReportsPage !== 'undefined' && ReportsPage._view(q.from)) ? q.from : null;
        try {
            JobsPage._job = await API.get(`/jobs/${id}`);
        } catch (err) {
            return `<div class="empty-state"><p>${escapeHtml(err.message)}</p><a href="#/jobs">Back to jobs</a></div>`;
        }
        const job = JobsPage._job;
        // a date the address carried that was refused is not kept on the
        // bar (NEW-35 review): the address reads the period in use
        if ((q.start_date || '') !== (dates.start_date || '') || (q.end_date || '') !== (dates.end_date || '')) JobsPage._syncAddress();
        const back = JobsPage._from
            ? ReportsPage._backButton(JobsPage._from, { start_date: JobsPage._period.start, end_date: JobsPage._period.end }, '', 'btn btn-sm btn-secondary')
            : '';
        return `
            <div class="page-header">
                <div>
                    <div style="font-size:11px;"><a href="#/jobs">${T('Jobs')}</a> › ${escapeHtml(job.customer_name)}${back ? ` <span style="margin-left:8px;">${back}</span>` : ''}</div>
                    <h2 style="margin:2px 0 0 0;">${escapeHtml(job.name)}
                        <span class="badge" style="font-size:11px; vertical-align:middle;">${escapeHtml(JobsPage.STATUS_LABELS[job.status] || job.status)}</span>
                        ${job.is_active ? '' : '<span style="font-size:11px;color:var(--text-danger);">inactive</span>'}
                    </h2>
                </div>
                <div>
                    <button class="btn btn-secondary" data-write onclick="InvoicesPage.showForm(null,${job.customer_id})">${T('New Invoice')}</button>
                    <button class="btn btn-secondary" data-write onclick="JobCostsPage.showForm(null, ${job.id})">${T('Job')} Cost Entry</button>
                    <button class="btn btn-secondary" onclick="JobsPage.showForm(${job.id})">Edit</button>
                </div>
            </div>
            <div class="toolbar" style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                ${Object.entries(JobsPage.tabLabels()).map(([t, label]) => `
                    <button class="btn btn-sm ${JobsPage._tab === t ? 'btn-primary' : 'btn-secondary'}" data-jobtab="${t}" onclick="JobsPage.setTab('${t}')">${label}</button>`).join('')}
                <span style="margin-left:auto; font-size:11px; display:flex; gap:6px; align-items:center;">
                    Period <input type="date" id="job-period-start" aria-label="Period start" value="${JobsPage._period.start}" onchange="JobsPage.setPeriod()">
                    – <input type="date" id="job-period-end" aria-label="Period end" value="${JobsPage._period.end}" onchange="JobsPage.setPeriod()">
                    <button class="btn btn-sm btn-secondary" onclick="JobsPage.clearPeriod()" title="${T('Job')} to date">JTD</button>
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
    // The period typed on the page. From after To is refused as the
    // reports, the Classes list and a register refuse it (W-3, W-4): said,
    // the boxes put back to the period in use, and the page, and its
    // address, left on it.
    async setPeriod() {
        const startEl = $('#job-period-start');
        const endEl = $('#job-period-end');
        const start = startEl?.value || '';
        const end = endEl?.value || '';
        if (reversedRangeRefused(start, end, JobsPage._period)) {
            if (startEl) startEl.value = JobsPage._period.start;
            if (endEl) endEl.value = JobsPage._period.end;
            return;
        }
        JobsPage._period = { start, end };
        JobsPage._tree = null;
        JobsPage._syncAddress();
        await JobsPage.setTab(JobsPage._tab);
    },
    async clearPeriod() {
        JobsPage._period = { start: '', end: '' };
        const s = $('#job-period-start'), e = $('#job-period-end');
        if (s) s.value = ''; if (e) e.value = '';
        JobsPage._tree = null;
        JobsPage._syncAddress();
        await JobsPage.setTab(JobsPage._tab);
    },
    // The page's period on its address, replacing the entry (R7's rule 2).
    _syncAddress() {
        if (!JobsPage._job) return;
        const qs = new URLSearchParams();
        if (JobsPage._period.start) qs.set('start_date', JobsPage._period.start);
        if (JobsPage._period.end) qs.set('end_date', JobsPage._period.end);
        if (JobsPage._from) qs.set('from', JobsPage._from);
        const q = qs.toString();
        const url = `#/jobs/${JobsPage._job.id}${q ? `?${q}` : ''}`;
        if ((location.hash || '') !== url) { history.replaceState(history.state, '', url); if (window.App && App.addressShown) App.addressShown(); }
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
                case 'contract': return await JobsPage.contractHtml();
                case 'buyout': return await JobsPage.buyoutHtml();
                case 'changeorders': return await JobsPage.changeOrdersHtml();
                case 'payapps': return await JobsPage.payAppsHtml();
                case 'transactions': return await JobsPage.transactionsHtml();
                case 'time': return await JobsPage.timeHtml();
                default: return await JobsPage.overviewHtml();
            }
        } catch (err) {
            return `<div style="color:var(--text-danger);">${escapeHtml(err.message)}</div>`;
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
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${label}</div>
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
                ${stat('Variance', JobsPage.money(t.variance), t.revised ? (t.variance < 0 ? 'var(--text-danger)' : 'var(--text-success)') : null, 'Budget − projected')}
                ${stat('Revenue', JobsPage.money(t.act_revenue))}
                ${stat('Margin', JobsPage.pct(margin), margin !== null && margin < 0 ? 'var(--text-danger)' : null, '(revenue − cost) / revenue')}
                ${stat('Billed vs contract', JobsPage.pct(billedPct))}
                ${Terms.isConstruction() ? stat('Committed total', JobsPage.money(t.committed_total), null, 'Every subcontract and PO issued on this job (not period-scoped)') : ''}
                ${Terms.isConstruction() ? stat('Billed to date (subs)', JobsPage.money(t.billed_to_date), null, 'Billed against commitments so far — the sub/supplier side, not the owner billing on the Contract tab') : ''}
            </div>
            <div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start;">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">By cost type — click a row to drill down</h4>
                    <div class="table-container"><table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">Type</th><th scope="col" class="amount">Budget</th><th scope="col" class="amount">Committed</th><th scope="col" class="amount">Actual</th><th scope="col" class="amount">Projected</th><th scope="col" class="amount">Variance</th><th scope="col" class="amount">% Used</th></tr></thead>
                        <tbody>${typeRows || '<tr><td colspan="7" style="color:var(--text-muted)">Nothing budgeted or posted yet.</td></tr>'}</tbody>
                    </table></div>
                </div>
                <div style="font-size:13px">
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">${T('Job')}</h4>
                    <div>${job.job_number ? `#${escapeHtml(job.job_number)} · ` : ''}${escapeHtml(job.job_type || '')}</div>
                    <div>${job.start_date ? escapeHtml(job.start_date) : ''}${job.projected_end_date ? ` → ${escapeHtml(job.projected_end_date)}` : ''}${job.end_date ? ` (ended ${escapeHtml(job.end_date)})` : ''}</div>
                    ${job.site_address ? `<pre style="font-family:inherit;white-space:pre-wrap;margin:6px 0">${escapeHtml(job.site_address)}</pre>` : ''}
                    ${job.description ? `<p style="margin:6px 0">${escapeHtml(job.description)}</p>` : ''}
                    ${job.notes ? `<h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:10px 0 4px 0">Notes</h4><pre style="font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(job.notes)}</pre>` : ''}
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
                <p style="font-size:12px;color:var(--text-muted)">Tag a bill, expense, time entry or job cost entry to it, or set a budget on the Budget tab.</p></div>`;
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
            body += JobsPage.rowHtml(key, 0, `${escapeHtml(ty.name)}${ty.is_labor ? ' <span style="font-size:10px;color:var(--text-muted)">(burden applies)</span>' : ''}`, ty.figures, open, true, 'type');
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
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;font-size:11px;color:var(--text-muted)">
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.expandAll(true)">Expand all</button>
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.expandAll(false)">Collapse all</button>
                <span>Click a type or code to open it; click the line count to see the posted lines; click a line to open its document.</span>
                <span style="margin-left:auto;display:flex;gap:6px">
                    <button class="btn btn-sm btn-secondary" onclick="JobsPage.saveCostTree('pdf')" title="The tree as shown, for this period">Save PDF</button>
                    <button class="btn btn-sm btn-secondary" onclick="JobsPage.saveCostTree('csv')" title="Every type and cost code, for this period">Save CSV</button>
                </span>
            </div>
            <div class="table-container"><table class="data-table job-tree" style="font-size:12px">${head}<tbody>${body}</tbody>${foot}</table></div>`;
    },

    nodeHtml(node, depth) {
        const key = `code:${node.id}`;
        const open = JobsPage._open.has(key);
        const hasKids = node.children.length > 0;
        let html = JobsPage.rowHtml(key, depth, `<code>${escapeHtml(node.code)}</code> ${escapeHtml(node.name)}${node.is_active ? '' : ' <span style="font-size:10px;color:var(--text-muted)">(inactive)</span>'}`, node.figures, open, hasKids, 'code', node.lines.length);
        if (open && hasKids) for (const ch of node.children) html += JobsPage.nodeHtml(ch, depth + 1);
        if (JobsPage._showLines.has(key)) {
            if (hasKids && (node.own.actual || node.own.act_revenue)) {
                html += `<tr><td colspan="11" style="padding-left:${16 + depth * 18}px;font-size:11px;color:var(--text-muted)">Own lines on ${escapeHtml(node.code)} (children listed under their own codes):</td></tr>`;
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
        if (!lines.length) return `<tr><td colspan="11" style="padding-left:${16 + depth * 18}px;color:var(--text-muted);font-size:11px">No posted lines.</td></tr>`;
        return lines.map(l => `<tr class="clickable" onclick="JobsPage.openSource('${escapeHtml(l.source_type || '')}', ${l.source_id === null ? 'null' : l.source_id}, ${l.transaction_id})" style="font-size:11px;color:var(--gray-600)">
            <td style="padding-left:${16 + depth * 18}px">${escapeHtml(l.date)} · <strong>${escapeHtml(JobsPage.sourceLabel(l.source_type))}</strong>${l.reference ? ` ${escapeHtml(l.reference)}` : ''} · ${escapeHtml(l.description || '')} <span style="color:var(--text-muted)">(${escapeHtml(l.account_name)})</span>${l.is_billable ? ' <span class="badge" style="font-size:9px">billable</span>' : ''}</td>
            <td colspan="4"></td>
            <td class="amount">${l.kind === 'cost' ? formatCurrency(l.amount) : ''}</td>
            <td colspan="3"></td>
            <td class="amount">${l.kind === 'income' ? formatCurrency(l.amount) : ''}</td>
        </tr>`).join('');
    },

    sourceLabel(t) {
        return { invoice: T('Invoice'), bill: 'Bill', expense: 'Expense', cc_charge: 'Card charge', manual: 'Journal',
            credit_memo: 'Credit memo', vendor_credit: 'Vendor credit', vendor_credit_void: 'Void vendor credit',
            sales_receipt: 'Sales receipt', deposit: 'Deposit', check: 'Check',
            job_cost: `${T('Job')} cost`, job_cost_void: `Void ${T('Job').toLowerCase()} cost`, expense_void: 'Void expense', bill_void: 'Void bill', payroll: 'Payroll', payroll_void: 'Void payroll' }[t] || (t || 'Entry');
    },

    async toggle(key) { JobsPage._open.has(key) ? JobsPage._open.delete(key) : JobsPage._open.add(key); await JobsPage.setTab('costs'); },
    async toggleLines(key) { JobsPage._showLines.has(key) ? JobsPage._showLines.delete(key) : JobsPage._showLines.add(key); await JobsPage.setTab('costs'); },
    // Save PDF / Save CSV of the Cost Detail tab (#691): the period on screen;
    // the PDF prints the rows that are open, the CSV every row.
    saveCostTree(kind) {
        const id = JobsPage._job.id;
        const qs = new URLSearchParams(JobsPage._periodQs().replace(/^\?/, ''));
        let url = `/api/jobs/${id}/cost-tree/csv`;
        if (kind === 'pdf') {
            url = `/api/jobs/${id}/cost-tree/pdf`;
            qs.set('expanded', [...JobsPage._open].join(','));
        }
        const q = qs.toString();
        window.open(q ? `${url}?${q}` : url, '_blank');
    },

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
                <td style="padding-left:${8 + (c.depth || 0) * 18}px"><code>${escapeHtml(c.code)}</code> ${escapeHtml(c.name)} <span style="font-size:10px;color:var(--text-muted)">${escapeHtml(c.cost_type)}</span></td>
                <td><input type="number" step="0.01" class="bud-cost" data-write value="${r.amount ?? ''}" style="width:110px;text-align:right"></td>
                <td><input type="number" step="0.01" class="bud-rev" data-write value="${r.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                <td style="font-size:10px;color:var(--text-muted)">${r.source ? escapeHtml(r.source) : ''}</td>
            </tr>`;
        }).join('');
        const typeRows = types.map(t => {
            const r = byType[t.code] || {};
            return `<tr data-budget-type="${t.code}">
                <td>${escapeHtml(t.name)} <span style="font-size:10px;color:var(--text-muted)">(whole type, not by code)</span></td>
                <td><input type="number" step="0.01" class="bud-cost" data-write value="${r.amount ?? ''}" style="width:110px;text-align:right"></td>
                <td><input type="number" step="0.01" class="bud-rev" data-write value="${r.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                <td style="font-size:10px;color:var(--text-muted)">${r.source ? escapeHtml(r.source) : ''}</td>
            </tr>`;
        }).join('');
        const estOpts = (estimates || []).filter(e => e.job_id === job.id || !e.job_id)
            .map(e => `<option value="${e.id}">${escapeHtml(e.estimate_number || ('#' + e.id))} · ${escapeHtml(e.date || '')} · ${formatCurrency(e.total)}${e.job_id === job.id ? ' (this job)' : ''}</option>`).join('');
        return `
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;font-size:12px" data-write>
                <span>Seed from an estimate:</span>
                <select id="budget-estimate"><option value="">Pick an estimate…</option>${estOpts}</select>
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.seedBudget()">Load estimate lines as budget</button>
                <span style="color:var(--text-muted);font-size:11px">Cost = qty × unit cost (or the line amount when no cost is entered); revenue = the line amount. Rows you edit here become manual and survive re-seeding.</span>
            </div>
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Cost code</th><th scope="col">Budget cost</th><th scope="col">Est. revenue</th><th scope="col">Source</th></tr></thead>
                <tbody>
                    ${codeRows || '<tr><td colspan="4" style="color:var(--text-muted)">No cost codes yet — add them under Settings → Cost Codes, or budget by type below.</td></tr>'}
                    <tr><td colspan="4" style="background:var(--gray-50);font-weight:600;font-size:11px">By cost type</td></tr>
                    ${typeRows}
                    <tr data-budget-whole="1"><td><em>Whole job (not by code or type)</em></td>
                        <td><input type="number" step="0.01" class="bud-cost" data-write value="${whole?.amount ?? ''}" style="width:110px;text-align:right"></td>
                        <td><input type="number" step="0.01" class="bud-rev" data-write value="${whole?.revenue_amount ?? ''}" style="width:110px;text-align:right"></td>
                        <td style="font-size:10px;color:var(--text-muted)">${whole?.source ? escapeHtml(whole.source) : ''}</td></tr>
                </tbody>
            </table></div>
            <div class="form-actions" data-write><button class="btn btn-primary" onclick="JobsPage.saveBudget()">Save Budget</button></div>`;
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

    // ---- Contract (construction mode only, #552) -------------------------------
    // The owner's side of the job: the contract terms, the summary tiles and
    // the schedule of values. The SOV must balance to the contract sum — the
    // balance line stays highlighted until it does.
    SOV_KINDS: { work: 'Work', allowance: 'Allowance', contingency: 'Contingency', general_conditions: 'General conditions', insurance: 'Insurance', fee: 'Fee', bond: 'Bond' },
    CONTRACT_TYPES: { lump_sum: 'Lump sum', gmp: 'GMP (guaranteed max)', cost_plus: 'Cost-plus' },

    async contractHtml() {
        const job = JobsPage._job;
        if (!TradePackages._list) await TradePackages.load();
        const [data, estimates, codes] = await Promise.all([
            API.get(`/jobs/${job.id}/contract`),
            API.get(`/estimates?customer_id=${job.customer_id}`).catch(() => []),
            API.get('/cost-codes').catch(() => []),
        ]);
        JobsPage._contract = data;
        JobsPage._sovCodes = codes;
        // the accepted estimates on this job, kept so Build reuses them
        JobsPage._acceptedEstimates = (estimates || []).filter(e => e.job_id === job.id && e.status === 'accepted');
        const packages = (TradePackages._list || []);
        return JobsPage.contractTermsHtml(data.contract)
            + JobsPage.contractTilesHtml(data.contract)
            + JobsPage.sovGridHtml(data.sov, data.contract, packages, codes, JobsPage._acceptedEstimates.length > 0);
    },

    // The terms block: type, sum, date, fee, cap, contingency, retainage.
    contractTermsHtml(c) {
        c = c || {};
        const typeOpts = Object.entries(JobsPage.CONTRACT_TYPES)
            .map(([k, v]) => `<option value="${k}" ${c.contract_type === k ? 'selected' : ''}>${v}</option>`).join('');
        const v = x => (x === null || x === undefined) ? '' : x;
        const src = c.contract_estimate_id ? `<div class="form-group"><label>Source estimate</label><div style="padding-top:6px;font-size:12px;color:var(--text-muted)">#${c.contract_estimate_id}</div></div>` : '';
        return `<form id="contract-terms" data-write onsubmit="JobsPage.saveContract(event)">
            <div class="form-grid">
                <div class="form-group"><label for="c-type">Contract type</label>
                    <select id="c-type" name="contract_type"><option value="">— none —</option>${typeOpts}</select></div>
                <div class="form-group"><label for="c-amount">Contract sum</label>
                    <input id="c-amount" name="contract_amount" type="number" step="0.01" min="0" value="${v(c.contract_amount)}"></div>
                <div class="form-group"><label for="c-date">Contract date</label>
                    <input id="c-date" name="contract_date" type="date" value="${v(c.contract_date)}"></div>
                <div class="form-group"><label for="c-feepct">Fee %</label>
                    <input id="c-feepct" name="fee_pct" type="number" step="0.0001" min="0" value="${v(c.fee_pct)}"></div>
                <div class="form-group"><label for="c-feeamt">Fixed fee</label>
                    <input id="c-feeamt" name="fee_amount" type="number" step="0.01" min="0" value="${v(c.fee_amount)}" placeholder="wins over fee %"></div>
                <div class="form-group"><label for="c-cap">Cap / GMP max</label>
                    <input id="c-cap" name="cap_amount" type="number" step="0.01" min="0" value="${v(c.cap_amount)}"></div>
                <div class="form-group"><label for="c-cont">Contingency</label>
                    <input id="c-cont" name="contingency_amount" type="number" step="0.01" min="0" value="${v(c.contingency_amount)}"></div>
                <div class="form-group"><label for="c-ret">Retainage %</label>
                    <input id="c-ret" name="retainage_pct" type="number" step="0.01" min="0" max="100" value="${v(c.retainage_pct)}"></div>
                <div class="form-group"><label for="c-comarkup">Markup on change orders (%)</label>
                    <input id="c-comarkup" name="co_markup_pct" type="number" step="0.0001" min="-99.9999" max="1000" value="${v(c.co_markup_pct)}" placeholder="default markup on owner COs"></div>
                ${src}
            </div>
            <div class="form-actions"><button type="submit" class="btn btn-primary">Save contract terms</button></div>
        </form>`;
    },

    contractTilesHtml(c) {
        c = c || {};
        const m = JobsPage.money;
        const tile = (label, value, title) => `<div style="min-width:120px;" title="${title || ''}">
            <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${label}</div>
            <div style="font-size:16px;font-weight:700">${value}</div></div>`;
        return `<div style="display:flex;gap:16px;flex-wrap:wrap;margin:12px 0 18px 0">
            ${tile('Contract sum', m(c.contract_amount))}
            ${tile('Changes', m(c.changes), 'Revised − original (approved owner change orders)')}
            ${tile('Fee', m(c.fee))}
            ${tile('Cost to date', m(c.cost_to_date))}
            ${tile('Cap remaining', c.cap_remaining === null || c.cap_remaining === undefined ? '—' : m(c.cap_remaining))}
            ${tile('Contingency left', c.contingency_remaining === null || c.contingency_remaining === undefined ? '—' : m(c.contingency_remaining))}
            ${tile('Billed to date', m(c.billed_to_date))}
            ${tile('Billed %', JobsPage.pct(c.billed_pct))}
        </div>`;
    },

    // The schedule-of-values grid. Pure: it takes the lines and figures and
    // the pickers, so a node probe can drive it without the network.
    sovGridHtml(sov, contract, packages, codes, hasAccepted) {
        sov = sov || [];
        contract = contract || {};
        const usage = {};
        for (const a of (contract.allowances || [])) usage[a.sov_line_id] = a;
        const rows = sov.map((line, i) => JobsPage.sovRowHtml(line, i, packages, codes, usage[line.id])).join('');
        const total = sov.reduce((s, l) => s + (parseFloat(l.scheduled_value) || 0), 0);
        const balance = (contract.contract_amount || 0) - total;
        const balColor = Math.abs(balance) > 0.005 ? 'var(--text-danger)' : 'var(--text-success)';
        return `<h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:4px 0 6px 0">Schedule of values</h4>
            <div class="table-container"><table class="data-table" id="sov-grid" style="font-size:12px">
                <thead><tr>
                    <th scope="col" style="width:52px">Item #</th>
                    <th scope="col">Description</th>
                    <th scope="col">Package</th>
                    <th scope="col">Section</th>
                    <th scope="col">Kind</th>
                    <th scope="col" class="amount">Scheduled value</th>
                    <th scope="col" style="width:70px"></th>
                </tr></thead>
                <tbody id="sov-body">${rows || '<tr><td colspan="7" style="color:var(--text-muted)">No schedule of values yet. Add rows, or build one from an accepted estimate.</td></tr>'}</tbody>
                <tfoot>
                    <tr><td colspan="5" style="text-align:right;font-weight:600">Total</td><td class="amount" id="sov-total" style="font-weight:600">${formatCurrency(total)}</td><td></td></tr>
                    <tr><td colspan="5" style="text-align:right">Balance vs contract sum</td><td class="amount" id="sov-balance" style="color:${balColor};font-weight:600">${formatCurrency(balance)}</td><td></td></tr>
                </tfoot>
            </table></div>
            <div class="form-actions" data-write style="justify-content:flex-start;gap:8px">
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.addSovRow()">+ Add row</button>
                <button class="btn btn-primary" onclick="JobsPage.saveSov()">Save schedule of values</button>
                <button class="btn btn-secondary" ${hasAccepted ? '' : 'disabled title="Accept an estimate on this job first"'} onclick="JobsPage.buildSov()">Build from accepted estimate</button>
            </div>`;
    },

    sovRowHtml(line, i, packages, codes, usage) {
        line = line || {};
        // A change_order-kind line is owned by its change order (#554): it
        // shows read-only, with no inputs and no remove, so its value can
        // only move through the CO.
        if (line.kind === 'change_order') {
            return `<tr class="sov-row sov-co-row" data-co="1">
                <td>${escapeHtml(line.item_no || '')}</td>
                <td>${escapeHtml(line.description || '')}</td>
                <td colspan="2" style="color:var(--text-muted)">Change order</td>
                <td>Change order</td>
                <td class="amount sov-covalue" data-value="${line.scheduled_value ?? 0}">${formatCurrency(parseFloat(line.scheduled_value) || 0)}</td>
                <td style="color:var(--text-muted);font-size:11px">read-only</td></tr>`;
        }
        const pkgOpts = ['<option value="">—</option>'].concat((packages || []).map(p =>
            `<option value="${p.id}" ${String(line.trade_package_id) === String(p.id) ? 'selected' : ''}>${escapeHtml(p.name)}</option>`)).join('');
        const codeOpts = ['<option value="">—</option>'].concat((codes || []).map(c =>
            `<option value="${c.id}" ${String(line.cost_code_id) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.code)}</option>`)).join('');
        const kindOpts = Object.entries(JobsPage.SOV_KINDS).filter(([k]) => k !== 'change_order').map(([k, v]) =>
            `<option value="${k}" ${(line.kind || 'work') === k ? 'selected' : ''}>${v}</option>`).join('');
        // An allowance line shows its change-order draw-down (used / remaining).
        const allowanceNote = (line.kind === 'allowance' && usage)
            ? `<div style="font-size:11px;color:var(--text-muted)">Used ${formatCurrency(usage.used)} · ${formatCurrency(usage.remaining)} left</div>` : '';
        return `<tr class="sov-row" data-id="${line.id || ''}">
            <td><input class="sov-item" data-write maxlength="10" value="${escapeHtml(line.item_no || '')}" style="width:46px"></td>
            <td><input class="sov-desc" data-write maxlength="200" value="${escapeHtml(line.description || '')}" style="width:100%">${allowanceNote}</td>
            <td><select class="sov-pkg" data-write>${pkgOpts}</select></td>
            <td><select class="sov-code" data-write>${codeOpts}</select></td>
            <td><select class="sov-kind" data-write>${kindOpts}</select></td>
            <td><input class="sov-value money" data-write type="number" step="0.01" value="${line.scheduled_value ?? ''}" style="width:110px;text-align:right" oninput="JobsPage.retotalSov()" title="A deduct alternate may be negative"></td>
            <td style="white-space:nowrap">
                <button class="btn btn-sm btn-secondary" data-write title="Move up" onclick="JobsPage.moveSovRow(this,-1)">↑</button>
                <button class="btn btn-sm btn-secondary" data-write title="Move down" onclick="JobsPage.moveSovRow(this,1)">↓</button>
                <button class="btn btn-sm btn-secondary" data-write title="Remove" onclick="JobsPage.removeSovRow(this)">✕</button>
            </td></tr>`;
    },

    _readSov() {
        // Skip change_order rows — their value is owned by the CO, not the grid.
        return $$('#sov-body .sov-row').filter(tr => !tr.dataset.co).map(tr => ({
            id: tr.dataset.id ? parseInt(tr.dataset.id) : null,
            item_no: tr.querySelector('.sov-item').value.trim() || null,
            description: tr.querySelector('.sov-desc').value.trim() || null,
            trade_package_id: tr.querySelector('.sov-pkg').value ? parseInt(tr.querySelector('.sov-pkg').value) : null,
            cost_code_id: tr.querySelector('.sov-code').value ? parseInt(tr.querySelector('.sov-code').value) : null,
            kind: tr.querySelector('.sov-kind').value,
            scheduled_value: parseFloat(tr.querySelector('.sov-value').value) || 0,
        }));
    },

    retotalSov() {
        const lines = JobsPage._readSov();
        let total = lines.reduce((s, l) => s + (l.scheduled_value || 0), 0);
        // the read-only change_order rows still count toward the contract sum
        $$('#sov-body .sov-covalue').forEach(td => { total += parseFloat(td.dataset.value) || 0; });
        const contract = (JobsPage._contract && JobsPage._contract.contract) || {};
        // the live contract-sum input wins over the saved figure, so the
        // balance tracks an unsaved edit to the sum
        const amtEl = $('#c-amount');
        const sum = amtEl && amtEl.value !== '' ? parseFloat(amtEl.value) : (contract.contract_amount || 0);
        const balance = (sum || 0) - total;
        const tEl = $('#sov-total'); if (tEl) tEl.textContent = formatCurrency(total);
        const bEl = $('#sov-balance');
        if (bEl) { bEl.textContent = formatCurrency(balance); bEl.style.color = Math.abs(balance) > 0.005 ? 'var(--text-danger)' : 'var(--text-success)'; }
    },

    addSovRow() {
        const body = $('#sov-body');
        if (!body) return;
        const empty = body.querySelector('td[colspan]');
        if (empty) body.innerHTML = '';
        const codes = JobsPage._sovCodes || [];
        const packages = TradePackages._list || [];
        body.insertAdjacentHTML('beforeend', JobsPage.sovRowHtml({}, body.children.length, packages, codes));
        JobsPage.retotalSov();
    },

    removeSovRow(btn) {
        const tr = btn.closest('tr');
        if (tr) tr.remove();
        JobsPage.retotalSov();
    },

    moveSovRow(btn, dir) {
        const tr = btn.closest('tr');
        if (!tr) return;
        if (dir < 0 && tr.previousElementSibling) tr.parentNode.insertBefore(tr, tr.previousElementSibling);
        if (dir > 0 && tr.nextElementSibling) tr.parentNode.insertBefore(tr.nextElementSibling, tr);
    },

    async saveContract(e) {
        if (e) e.preventDefault();
        const form = $('#contract-terms');
        const num = n => { const el = form[n]; const v = el && el.value !== '' ? parseFloat(el.value) : null; return (v === null || isNaN(v)) ? null : v; };
        const data = {
            contract_type: form.contract_type.value || null,
            contract_amount: num('contract_amount'),
            contract_date: form.contract_date.value || null,
            fee_pct: num('fee_pct'),
            fee_amount: num('fee_amount'),
            cap_amount: num('cap_amount'),
            contingency_amount: num('contingency_amount'),
            retainage_pct: num('retainage_pct'),
            co_markup_pct: num('co_markup_pct'),
        };
        try {
            await API.put(`/jobs/${JobsPage._job.id}/contract`, data);
            toast('Contract terms saved');
            await JobsPage.setTab('contract');
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveSov() {
        try {
            await API.put(`/jobs/${JobsPage._job.id}/sov`, { lines: JobsPage._readSov() });
            toast('Schedule of values saved');
            await JobsPage.setTab('contract');
        } catch (err) { toast(err.message, 'error'); }
    },

    async buildSov() {
        const accepted = JobsPage._acceptedEstimates || [];
        if (!accepted.length) { toast('No accepted estimate on this job', 'error'); return; }
        if (accepted.length === 1) {
            if (!confirm('This replaces the schedule of values.')) return;
            return JobsPage.buildSovFrom(accepted[0].id);
        }
        // more than one accepted estimate — let the operator pick which
        const opts = accepted.map(e => `<option value="${e.id}">${escapeHtml(e.estimate_number || ('#' + e.id))} · ${escapeHtml(e.date || '')} · ${formatCurrency(e.total)}</option>`).join('');
        openModal('Build schedule of values', `
            <p>This replaces the schedule of values. Build it from:</p>
            <div class="form-group"><label for="sov-build-est">Accepted estimate</label>
                <select id="sov-build-est">${opts}</select></div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="JobsPage.buildSovFrom(parseInt($('#sov-build-est').value), true)">Build</button>
            </div>`);
    },

    async buildSovFrom(estId, fromModal) {
        if (fromModal) closeModal();
        try {
            await API.post(`/jobs/${JobsPage._job.id}/contract/from-estimate/${estId}`, {});
            toast('Schedule of values built from the estimate');
            await JobsPage.setTab('contract');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Buyout (construction mode only, #553) ---------------------------------
    // The sub/supplier side of the job: bid vs buyout per trade package, with
    // the commitments behind each and their compliance flags. Read-only here;
    // commitments are created and billed on the Purchase Orders page.
    async buyoutHtml() {
        const data = await API.get(`/jobs/${JobsPage._job.id}/buyout`);
        return JobsPage.buyoutTableHtml(data);
    },

    // Pure: takes the buyout payload and returns the table, so a node probe
    // can drive it without the network.
    buyoutTableHtml(data) {
        data = data || {};
        const rows = data.rows || [];
        const m = JobsPage.money;
        if (!rows.length) {
            return `<div class="empty-state"><p>No commitments or bid yet. Create a purchase order or subcontract on the Purchase Orders page, tag it to this job and a package.</p></div>`;
        }
        const flag = (c) => {
            if (!c || !c.is_sub) return '';
            const bits = [];
            if (!c.w9_on_file) bits.push('W-9 missing');
            if (c.coi_expired) bits.push(c.coi_expires ? 'COI expired' : 'COI missing');
            return bits.length ? ` <span style="color:var(--text-danger);font-size:11px">(${bits.join(', ')})</span>` : '';
        };
        const body = rows.map(r => {
            const commits = (r.commitments || []).map(c =>
                `<div style="font-size:11px;padding-left:8px">
                    <a href="#/purchase-orders" onclick="PurchaseOrdersPage.view(${c.id});return false;">${escapeHtml(c.number)}</a>
                    · ${escapeHtml(c.vendor_name)} · ${c.type === 'subcontract' ? 'Subcontract' : 'PO'}
                    · ${m(c.subtotal)} (${m(c.billed)} billed) · ${escapeHtml(c.status)}${flag(c.compliance)}
                </div>`).join('');
            return `<tr>
                <td>${escapeHtml(r.package_name)}${commits ? `<div style="margin-top:4px">${commits}</div>` : ''}</td>
                <td class="amount">${m(r.bid_cost)}</td>
                <td class="amount">${m(r.bought)}</td>
                <td class="amount" style="${r.variance < 0 ? 'color:var(--text-danger)' : ''}">${m(r.variance)}</td>
                <td class="amount">${m(r.committed_remaining)}</td>
                <td class="amount">${m(r.billed_to_date)}</td>
            </tr>`;
        }).join('');
        const t = data.totals || {};
        return `<div class="table-container"><table class="data-table" id="buyout-table" style="font-size:12px">
            <thead><tr>
                <th scope="col">Package</th>
                <th scope="col" class="amount">Bid</th>
                <th scope="col" class="amount">Bought</th>
                <th scope="col" class="amount">Variance</th>
                <th scope="col" class="amount">Committed left</th>
                <th scope="col" class="amount">Billed to date</th>
            </tr></thead>
            <tbody>${body}</tbody>
            <tfoot><tr style="font-weight:700">
                <td>Total</td>
                <td class="amount">${m(t.bid_cost)}</td>
                <td class="amount">${m(t.bought)}</td>
                <td class="amount" style="${t.variance < 0 ? 'color:var(--text-danger)' : ''}">${m(t.variance)}</td>
                <td class="amount">${m(t.committed_remaining)}</td>
                <td class="amount">${m(t.billed_to_date)}</td>
            </tr></tfoot>
        </table></div>`;
    },

    // ---- Change orders (construction mode only, #554) --------------------------
    // How a locked budget, its contract and its commitments move. Three kinds
    // (potential / owner / sub); material and labor priced separately with
    // markup, none when an allowance pays. Every effect lands on approve and
    // reverses on void.
    CO_KINDS: { pco: 'Potential', owner: 'Owner', sub: 'Sub' },
    CO_FUNDING: { contract: 'Contract', allowance: 'Allowance', contingency: 'Contingency' },
    CO_STATUS_LABELS: { draft: 'Draft', pending: 'Pending', approved: 'Approved', rejected: 'Rejected', void: 'Void', promoted: 'Promoted', superseded: 'Superseded' },

    async changeOrdersHtml() {
        const job = JobsPage._job;
        await Promise.all([CostTypes.load(), TradePackages.load()]);
        const [list, contract, codes] = await Promise.all([
            API.get(`/change-orders?job_id=${job.id}`),
            API.get(`/jobs/${job.id}/contract`),
            API.get('/cost-codes').catch(() => []),
        ]);
        JobsPage._coList = list;
        JobsPage._coContract = contract.contract;
        JobsPage._coAllowances = (contract.sov || []).filter(l => l.kind === 'allowance');
        JobsPage._coCodes = codes;
        // Commitments to attach a sub CO to come from the buyout table.
        const buyout = await API.get(`/jobs/${job.id}/buyout`).catch(() => ({ rows: [] }));
        JobsPage._coCommitments = (buyout.rows || []).flatMap(r => r.commitments || []);
        return JobsPage.coLogHtml(list) + `<div id="co-form-area" style="margin-top:16px"></div>`;
    },

    coLogHtml(list) {
        list = list || [];
        const m = JobsPage.money;
        const body = list.map(co => `<tr>
            <td>${co.kind === 'pco' && (co.status === 'draft' || co.status === 'pending') ? `<input type="checkbox" class="co-pick" value="${co.id}" aria-label="Select ${escapeHtml(co.number)} to roll up"> ` : ''}${escapeHtml(co.number)}${co.revision > 1 ? ` <span style="color:var(--text-muted)">rev ${co.revision}</span>` : ''}</td>
            <td>${escapeHtml(JobsPage.CO_KINDS[co.kind] || co.kind)}</td>
            <td><span class="badge">${escapeHtml(JobsPage.CO_STATUS_LABELS[co.status] || co.status)}</span></td>
            <td>${escapeHtml(co.title || '')}</td>
            <td>${escapeHtml(JobsPage.CO_FUNDING[co.funding] || co.funding)}${co.funding !== 'contract' && co.apply_markup ? ' <span style="color:var(--text-muted)">+ markup</span>' : ''}</td>
            <td class="amount">${m(co.cost_total)}</td>
            <td class="amount">${m(co.markup_total)}</td>
            <td class="amount">${m(co.sell_total)}</td>
            <td>${escapeHtml(co.date || '')}</td>
            <td style="white-space:nowrap">${JobsPage.coRowActions(co)}</td>
        </tr>`).join('');
        return `<div class="form-actions" data-write style="justify-content:flex-start;margin-bottom:8px">
                <button class="btn btn-primary" onclick="JobsPage.coNew()">+ New change order</button>
                <button class="btn btn-secondary" onclick="JobsPage.coPromoteSelected()" title="Tick two or more potential change orders to roll them into one owner change order">Promote selected PCOs</button>
            </div>
            <div class="table-container"><table class="data-table" id="co-log" style="font-size:12px">
                <thead><tr>
                    <th scope="col">Number</th><th scope="col">Kind</th><th scope="col">Status</th>
                    <th scope="col">Title</th><th scope="col">Funding</th>
                    <th scope="col" class="amount">Cost</th><th scope="col" class="amount">Markup</th>
                    <th scope="col" class="amount">Total</th><th scope="col">Date</th><th scope="col"></th>
                </tr></thead>
                <tbody>${body || '<tr><td colspan="10" style="color:var(--text-muted)">No change orders yet.</td></tr>'}</tbody>
            </table></div>`;
    },

    coRowActions(co) {
        const id = co.id;
        const draftOrPending = co.status === 'draft' || co.status === 'pending';
        const parts = [`<button class="btn btn-sm btn-secondary" onclick="JobsPage.coEdit(${id})">${draftOrPending ? 'Edit' : 'View'}</button>`];
        if (co.status === 'draft') parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coSubmit(${id})">Submit</button>`);
        if (co.status === 'pending' && co.kind !== 'pco') parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coApprove(${id})">Approve</button>`);
        if (draftOrPending) parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coReject(${id})">Reject</button>`);
        if (co.kind === 'pco' && draftOrPending) parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coPromote(${id})">Promote</button>`);
        if (co.kind === 'pco' && draftOrPending) parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coRevise(${id})">Revise</button>`);
        if (co.status === 'approved') parts.push(`<button data-destructive class="btn btn-sm btn-danger" onclick="JobsPage.coVoid(${id})">Void</button>`);
        if (co.kind === 'owner' && co.status === 'approved') parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coMakeSub(${id})">Make sub order</button>`);
        if (co.kind !== 'sub') parts.push(`<button class="btn btn-sm btn-secondary" onclick="window.open('/api/change-orders/${id}/pdf','_blank')">PDF</button>`);
        return parts.join(' ');
    },

    coNew() { JobsPage._coEditing = null; JobsPage.coShowForm(null); },
    async coEdit(id) {
        const co = await API.get(`/change-orders/${id}`);
        JobsPage._coEditing = co;
        JobsPage.coShowForm(co);
    },

    coShowForm(co) {
        const area = $('#co-form-area');
        if (area) { area.innerHTML = JobsPage.coFormHtml(co); JobsPage.coRecalc(); }
    },

    coFormHtml(co) {
        co = co || { kind: 'owner', funding: 'contract', lines: [] };
        const ro = co.id && !(co.status === 'draft' || co.status === 'pending');
        const d = ro ? 'disabled' : '';
        const kindOpts = Object.entries(JobsPage.CO_KINDS).map(([k, v]) => `<option value="${k}" ${co.kind === k ? 'selected' : ''}>${v}</option>`).join('');
        const fundOpts = Object.entries(JobsPage.CO_FUNDING).map(([k, v]) => `<option value="${k}" ${co.funding === k ? 'selected' : ''}>${v}</option>`).join('');
        const allowOpts = ['<option value="">— allowance line —</option>'].concat((JobsPage._coAllowances || []).map(a =>
            `<option value="${a.id}" ${String(co.allowance_sov_line_id) === String(a.id) ? 'selected' : ''}>${escapeHtml(a.item_no || '')} ${escapeHtml(a.description || '')}</option>`)).join('');
        const commitOpts = ['<option value="">— commitment —</option>'].concat((JobsPage._coCommitments || []).map(c =>
            `<option value="${c.id}" ${String(co.commitment_id) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.number)} · ${escapeHtml(c.vendor_name || '')}</option>`)).join('');
        const lineRows = (co.lines || []).map((l, i) => JobsPage.coLineRowHtml(l, i)).join('');
        return `<form id="co-form" data-write onsubmit="JobsPage.coSave(event, ${co.id || 'null'})">
            <h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:4px 0 6px 0">${co.id ? `Change order ${escapeHtml(co.number || '')}` : 'New change order'}</h4>
            <div class="form-grid">
                <div class="form-group"><label>Title</label><input id="co-title" ${d} maxlength="200" value="${escapeHtml(co.title || '')}"></div>
                <div class="form-group"><label>Kind</label><select id="co-kind" ${d || (co.id ? 'disabled' : '')}>${kindOpts}</select></div>
                <div class="form-group"><label>Funding</label><select id="co-funding" ${d} onchange="JobsPage.coFundingChanged()">${fundOpts}</select></div>
                <div class="form-group"><label>Allowance line</label><select id="co-allowance" ${d}>${allowOpts}</select></div>
                <div class="form-group"><label>Commitment (sub)</label><select id="co-commitment" ${d}>${commitOpts}</select></div>
                <div class="form-group"><label>Apply markup</label><label style="font-weight:400"><input id="co-apply-markup" ${d} ${co.funding === 'contract' ? 'disabled' : ''} type="checkbox" ${(co.apply_markup ?? (co.funding === 'contract')) ? 'checked' : ''} onchange="JobsPage.coRecalc()"> ${co.funding === 'contract' ? 'Markup applied' : 'Add markup to this allowance/contingency work'}</label></div>
                <div class="form-group"><label>Markup %</label><input id="co-markup" ${d} type="number" step="0.0001" min="-99.9999" max="1000" value="${co.markup_pct ?? ''}" placeholder="default from contract" oninput="JobsPage.coRecalc()"></div>
                <div class="form-group"><label>Date</label><input id="co-date" ${d} type="date" value="${co.date || ''}"></div>
                <div class="form-group full-width"><label>Description</label><textarea id="co-desc" ${d} rows="2">${escapeHtml(co.description || '')}</textarea></div>
            </div>
            <div class="table-container"><table class="data-table" id="co-lines" style="font-size:12px">
                <thead><tr>
                    <th scope="col">Description</th><th scope="col">Cost type</th><th scope="col">Section</th><th scope="col">Package</th>
                    <th scope="col" class="amount">Qty</th><th scope="col">Unit</th><th scope="col" class="amount">Unit cost</th>
                    <th scope="col" class="amount">Markup %</th><th scope="col" class="amount">Cost</th><th scope="col" class="amount">Sell</th><th scope="col"></th>
                </tr></thead>
                <tbody id="co-line-body">${lineRows}</tbody>
                <tfoot id="co-line-foot"></tfoot>
            </table></div>
            ${ro ? '' : `<div class="form-actions" data-write style="justify-content:flex-start;gap:8px">
                <button type="button" class="btn btn-sm btn-secondary" onclick="JobsPage.coAddLine()">+ Add line</button>
                <button type="submit" class="btn btn-primary">${co.id ? 'Save change order' : 'Create change order'}</button>
                <button type="button" class="btn btn-secondary" onclick="JobsPage.setTab('changeorders')">Cancel</button>
            </div>`}
        </form>`;
    },

    coLineRowHtml(line, i) {
        line = line || {};
        const codes = JobsPage._coCodes || [];
        const packages = TradePackages._list || [];
        const types = CostTypes._list || [];
        const typeOpts = types.map(t => `<option value="${escapeHtml(t.code)}" ${line.cost_type === t.code ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('');
        const codeOpts = ['<option value="">—</option>'].concat(codes.map(c => `<option value="${c.id}" ${String(line.cost_code_id) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.code)}</option>`)).join('');
        const pkgOpts = ['<option value="">—</option>'].concat(packages.map(p => `<option value="${p.id}" ${String(line.trade_package_id) === String(p.id) ? 'selected' : ''}>${escapeHtml(p.name)}</option>`)).join('');
        return `<tr class="co-line">
            <td><input class="co-l-desc" data-write maxlength="200" value="${escapeHtml(line.description || '')}" style="width:100%"></td>
            <td><select class="co-l-type" data-write>${typeOpts}</select></td>
            <td><select class="co-l-code" data-write>${codeOpts}</select></td>
            <td><select class="co-l-pkg" data-write>${pkgOpts}</select></td>
            <td><input class="co-l-qty" data-write type="number" step="0.0001" value="${line.quantity ?? 1}" style="width:64px;text-align:right" oninput="JobsPage.coRecalc()"></td>
            <td><input class="co-l-unit" data-write maxlength="20" value="${escapeHtml(line.unit || '')}" style="width:52px"></td>
            <td><input class="co-l-cost" data-write type="number" step="0.0001" value="${line.unit_cost ?? 0}" style="width:84px;text-align:right" oninput="JobsPage.coRecalc()"></td>
            <td><input class="co-l-markup" data-write type="number" step="0.0001" min="-99.9999" max="1000" value="${line.markup_pct ?? ''}" style="width:64px;text-align:right" placeholder="dflt" oninput="JobsPage.coRecalc()"></td>
            <td class="amount co-l-amt">0.00</td>
            <td class="amount co-l-sell">0.00</td>
            <td><button type="button" class="btn btn-sm btn-secondary" data-write onclick="JobsPage.coRemoveLine(this)">✕</button></td>
        </tr>`;
    },

    // Pure line math (#554): cost = qty × unit cost; effective markup is the
    // line's, else the CO's, else the job's; zero for allowance-/contingency-
    // funded work unless markup is applied; a sub CO carries none.
    coLineMath(qty, unitCost, lineMarkup, coMarkup, jobMarkup, funding, kind, applyMarkup) {
        const cost = Math.round((Number(qty || 0) * Number(unitCost || 0)) * 100) / 100;
        let markup = 0;
        if ((funding === 'contract' || applyMarkup) && kind !== 'sub') {
            const pick = [lineMarkup, coMarkup, jobMarkup].find(v => v !== null && v !== undefined && v !== '');
            markup = Number(pick || 0);
        }
        const sell = Math.round(cost * (1 + markup / 100) * 100) / 100;
        return { cost, sell };
    },

    // Changing what pays for the change resets markup to that funding's
    // default: on for contract, off for allowance and contingency.
    coFundingChanged() {
        const box = $('#co-apply-markup');
        const contract = $('#co-funding').value === 'contract';
        if (box) { box.checked = contract; box.disabled = contract; }  // contract work is always marked up
        JobsPage.coRecalc();
    },

    coAddLine() {
        const body = $('#co-line-body');
        if (!body) return;
        body.insertAdjacentHTML('beforeend', JobsPage.coLineRowHtml({}, body.children.length));
        JobsPage.coRecalc();
    },
    coRemoveLine(btn) { const tr = btn.closest('tr'); if (tr) tr.remove(); JobsPage.coRecalc(); },

    coRecalc() {
        const funding = $('#co-funding') ? $('#co-funding').value : 'contract';
        const kind = $('#co-kind') ? $('#co-kind').value : 'owner';
        const coMarkup = $('#co-markup') ? $('#co-markup').value : '';
        const applyMarkup = $('#co-apply-markup') ? $('#co-apply-markup').checked : true;
        const jobMarkup = (JobsPage._coContract && JobsPage._coContract.co_markup_pct) || null;
        const byType = {};
        let cost = 0, sell = 0;
        $$('#co-line-body .co-line').forEach(tr => {
            const q = tr.querySelector('.co-l-qty').value;
            const uc = tr.querySelector('.co-l-cost').value;
            const lm = tr.querySelector('.co-l-markup').value;
            const t = tr.querySelector('.co-l-type').value;
            const r = JobsPage.coLineMath(q, uc, lm, coMarkup, jobMarkup, funding, kind, applyMarkup);
            tr.querySelector('.co-l-amt').textContent = formatCurrency(r.cost);
            tr.querySelector('.co-l-sell').textContent = formatCurrency(r.sell);
            cost += r.cost; sell += r.sell;
            byType[t] = byType[t] || { cost: 0, sell: 0 };
            byType[t].cost += r.cost; byType[t].sell += r.sell;
        });
        const foot = $('#co-line-foot');
        if (foot) {
            const typeRows = Object.entries(byType).map(([t, v]) =>
                `<tr><td colspan="8" style="text-align:right;color:var(--text-muted)">${t.charAt(0).toUpperCase() + t.slice(1)}</td><td class="amount">${formatCurrency(v.cost)}</td><td class="amount">${formatCurrency(v.sell)}</td><td></td></tr>`).join('');
            foot.innerHTML = typeRows +
                `<tr style="font-weight:700"><td colspan="8" style="text-align:right">Total</td><td class="amount">${formatCurrency(cost)}</td><td class="amount">${formatCurrency(sell)}</td><td></td></tr>`;
        }
    },

    coReadLines() {
        return $$('#co-line-body .co-line').map(tr => ({
            description: tr.querySelector('.co-l-desc').value.trim() || null,
            cost_type: tr.querySelector('.co-l-type').value,
            cost_code_id: tr.querySelector('.co-l-code').value ? parseInt(tr.querySelector('.co-l-code').value) : null,
            trade_package_id: tr.querySelector('.co-l-pkg').value ? parseInt(tr.querySelector('.co-l-pkg').value) : null,
            quantity: parseFloat(tr.querySelector('.co-l-qty').value) || 0,
            unit: tr.querySelector('.co-l-unit').value.trim() || null,
            unit_cost: parseFloat(tr.querySelector('.co-l-cost').value) || 0,
            markup_pct: tr.querySelector('.co-l-markup').value !== '' ? parseFloat(tr.querySelector('.co-l-markup').value) : null,
        }));
    },

    coReadForm() {
        const num = id => { const el = $(id); const v = el && el.value !== '' ? parseFloat(el.value) : null; return (v === null || isNaN(v)) ? null : v; };
        return {
            job_id: JobsPage._job.id,
            kind: $('#co-kind').value,
            title: $('#co-title').value.trim() || null,
            funding: $('#co-funding').value,
            allowance_sov_line_id: $('#co-allowance').value ? parseInt($('#co-allowance').value) : null,
            commitment_id: $('#co-commitment').value ? parseInt($('#co-commitment').value) : null,
            markup_pct: num('#co-markup'),
            apply_markup: $('#co-apply-markup').checked,
            date: $('#co-date').value || null,
            description: $('#co-desc').value.trim() || null,
            lines: JobsPage.coReadLines(),
        };
    },

    async coSave(e, id) {
        if (e) e.preventDefault();
        const data = JobsPage.coReadForm();
        try {
            if (id) { const { kind, job_id, ...body } = data; await API.put(`/change-orders/${id}`, body); }
            else { await API.post('/change-orders', data); }
            toast('Change order saved');
            await JobsPage.setTab('changeorders');
        } catch (err) { toast(err.message, 'error'); }
    },

    CO_VERB_PAST: { submit: 'submitted', approve: 'approved', reject: 'rejected', void: 'voided', promote: 'promoted', revise: 'revised' },

    async _coAction(id, verb, confirmMsg) {
        if (confirmMsg && !confirm(confirmMsg)) return;
        try {
            await API.post(`/change-orders/${id}/${verb}`, {});
            toast(`Change order ${JobsPage.CO_VERB_PAST[verb] || verb}`);
            await JobsPage.setTab('changeorders');
        } catch (err) { toast(err.message, 'error'); }
    },
    coRevise(id) { JobsPage._coAction(id, 'revise', 'Start a new revision of this potential change order? The current one is kept as history.'); },
    // Roll the ticked PCOs into one owner change order.
    async coPromoteSelected() {
        const ids = $$('.co-pick:checked').map(el => parseInt(el.value));
        if (ids.length < 2) { toast('Tick two or more potential change orders to roll up', 'error'); return; }
        if (!confirm(`Roll ${ids.length} potential change orders into one owner change order?`)) return;
        try {
            await API.post(`/change-orders/${ids[0]}/promote`, { pco_ids: ids.slice(1) });
            toast('Change order promoted');
            await JobsPage.setTab('changeorders');
        } catch (err) { toast(err.message, 'error'); }
    },
    coSubmit(id) { JobsPage._coAction(id, 'submit'); },
    coApprove(id) { JobsPage._coAction(id, 'approve', 'Approve this change order? It updates the contract, budget and commitment.'); },
    coReject(id) { JobsPage._coAction(id, 'reject', 'Reject this change order?'); },
    coVoid(id) { JobsPage._coAction(id, 'void', 'Void this change order? Every effect is reversed.'); },
    coPromote(id) { JobsPage._coAction(id, 'promote', 'Promote this potential change order to an owner change order?'); },

    // Make sub order: pick a commitment (number · vendor) and the owner-CO
    // lines to carry, rather than typing an id.
    async coMakeSub(id) {
        const co = await API.get(`/change-orders/${id}`);
        const commits = JobsPage._coCommitments || [];
        if (!commits.length) { toast('No commitments on this job to attach to', 'error'); return; }
        const commitOpts = commits.map(c => `<option value="${c.id}">${escapeHtml(c.number)} · ${escapeHtml(c.vendor_name || '')}</option>`).join('');
        const lineRows = (co.lines || []).map(l =>
            `<label style="display:block;font-size:12px"><input type="checkbox" class="co-sub-line" value="${l.id}" ${l.cost_type === 'subcontract' ? 'checked' : ''}> ${escapeHtml(l.description || '')} (${escapeHtml(l.cost_type)}) — ${formatCurrency(parseFloat(l.amount) || 0)}</label>`).join('');
        const html = `<form id="co-sub-form" onsubmit="JobsPage.coSubmitSubOrder(event, ${id})">
            <div class="form-group"><label>Commitment</label><select id="co-sub-commitment" required>${commitOpts}</select></div>
            <div class="form-group"><label>Lines to carry (unchecked = subcontract lines only)</label>${lineRows || '<div style="color:var(--text-muted)">No lines.</div>'}</div>
            <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="submit" class="btn btn-primary">Create sub order</button></div>
        </form>`;
        openModal('Make sub order', html);
    },

    async coSubmitSubOrder(e, id) {
        if (e) e.preventDefault();
        const commitment_id = parseInt($('#co-sub-commitment').value);
        const line_ids = $$('.co-sub-line').filter(c => c.checked).map(c => parseInt(c.value));
        const body = { commitment_id };
        if (line_ids.length) body.line_ids = line_ids;
        try {
            await API.post(`/change-orders/${id}/sub-order`, body);
            toast('Sub change order created');
            closeModal();
            await JobsPage.setTab('changeorders');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Pay apps (construction mode only, #555) -------------------------------
    // Owner progress billing from the schedule of values: previous / this
    // period / stored / retainage, with a release and a deposit applied.
    // Approving an application creates the invoice; voiding reverses it.
    PA_STATUS_LABELS: { draft: 'Draft', submitted: 'Submitted', approved: 'Approved', void: 'Void' },
    WAIVER_TYPES: {
        conditional_progress: 'Conditional progress',
        unconditional_progress: 'Unconditional progress',
        conditional_final: 'Conditional final',
        unconditional_final: 'Unconditional final',
    },
    LIEN_WAIVERS: { not_required: 'Not required', pending: 'Pending', conditional: 'Conditional', unconditional: 'Unconditional' },

    async payAppsHtml() {
        const job = JobsPage._job;
        const [list, deposits, contract] = await Promise.all([
            API.get(`/pay-applications?job_id=${job.id}`),
            API.get(`/jobs/${job.id}/deposits`).catch(() => ({ deposits: [], received: 0, applied: 0, unapplied: 0 })),
            API.get(`/jobs/${job.id}/contract`).catch(() => null),
        ]);
        JobsPage._paList = list;
        JobsPage._paDeposits = deposits;
        // an application bills the schedule of values line by line (#714)
        const hasSov = !contract || (contract.sov || []).length > 0;
        return JobsPage.paLogHtml(list, hasSov)
            + `<div id="pa-form-area" style="margin-top:16px"></div>`
            + JobsPage.depositsBlockHtml(deposits);
    },

    // The Pay apps tab's sentence for a job with no schedule of values
    // (#714), the one POST /pay-applications refuses with: New pay
    // application made an application with no lines, an empty grid with
    // nothing to type an amount into, and a contractor took it for a broken
    // form. It stands where the New button would, and links to Contract.
    paNoSovHtml() {
        return `<p id="pa-no-sov" role="note" style="margin:0 0 8px">This job has no schedule of values yet: set the contract on the job's
            <a href="#" onclick="event.preventDefault(); JobsPage.setTab('contract')">Contract tab</a>
            (Build from accepted estimate, or add the lines by hand), then bill against it here.</p>`;
    },

    paLogHtml(list, hasSov = true) {
        list = list || [];
        const m = JobsPage.money;
        const body = list.map(a => {
            const t = a.totals || {};
            const inv = a.invoice_id
                ? `<a href="#/invoices/${a.invoice_id}">${escapeHtml(a.invoice_number || '#' + a.invoice_id)}</a>` : '';
            return `<tr>
                <td>${escapeHtml(a.number)}${a.job_number ? `<br><span style="font-size:11px;color:var(--text-muted)">${escapeHtml(a.job_number)}</span>` : ''}</td>
                <td>${escapeHtml(a.period_to || '')}</td>
                <td><span class="badge">${escapeHtml(JobsPage.PA_STATUS_LABELS[a.status] || a.status)}</span></td>
                <td class="amount">${m(t.this_period_total)}</td>
                <td class="amount">${m(t.retainage_this_period)}</td>
                <td class="amount">${m(t.current_due)}</td>
                <td class="amount">${m(t.net_due)}</td>
                <td>${inv}</td>
                <td style="white-space:nowrap">${JobsPage.paRowActions(a)}</td>
            </tr>`;
        }).join('');
        return `${hasSov ? `<div class="form-actions" data-write style="justify-content:flex-start;margin-bottom:8px">
                <button class="btn btn-primary" onclick="JobsPage.paNew()">+ New pay application</button>
            </div>` : JobsPage.paNoSovHtml()}
            <div class="table-container"><table class="data-table" id="pa-log" style="font-size:12px">
                <thead><tr>
                    <th scope="col">No</th><th scope="col">Period</th><th scope="col">Status</th>
                    <th scope="col" class="amount">This period</th><th scope="col" class="amount">Retainage</th>
                    <th scope="col" class="amount">Current due</th><th scope="col" class="amount">Net due</th>
                    <th scope="col">${T('Invoice')}</th><th scope="col"></th>
                </tr></thead>
                <tbody>${body || '<tr><td colspan="9" style="color:var(--text-muted)">No pay applications yet.</td></tr>'}</tbody>
            </table></div>`;
    },

    paRowActions(a) {
        const id = a.id;
        const open = a.status === 'draft' || a.status === 'submitted';
        const parts = [`<button class="btn btn-sm btn-secondary" onclick="JobsPage.paEdit(${id})">${open ? 'Edit' : 'View'}</button>`];
        // An open application with no lines (made before the job had a
        // schedule of values) has nothing to submit or approve: it is
        // deleted, so the job can be billed again (#723).
        const empty = open && !(a.lines || []).length;
        if (empty) parts.push(`<button class="btn btn-sm btn-danger" data-destructive onclick="JobsPage.paDelete(${id})">Delete</button>`);
        if (a.status === 'draft' && !empty) parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.paSubmit(${id})">Submit</button>`);
        if (open && !empty) parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.paApprove(${id})">Approve</button>`);
        if (a.status === 'approved') parts.push(`<button class="btn btn-sm btn-danger" data-destructive onclick="JobsPage.paVoid(${id})">Void</button>`);
        parts.push(`<button class="btn btn-sm btn-secondary" onclick="window.open('/api/pay-applications/${id}/pdf','_blank')">PDF</button>`);
        if (a.status === 'approved' && a.invoice_id) parts.push(`<button class="btn btn-sm btn-secondary" onclick="JobsPage.paWaivers(${id})">Waivers</button>`);
        return parts.join(' ');
    },

    async paNew() {
        try {
            const a = await API.post('/pay-applications', { job_id: JobsPage._job.id, date: todayISO() });
            await JobsPage.setTab('payapps');
            JobsPage.paEdit(a.id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async paEdit(id) {
        const a = await API.get(`/pay-applications/${id}`);
        JobsPage._paEditing = a;
        const area = $('#pa-form-area');
        if (area) { area.innerHTML = JobsPage.paFormHtml(a); JobsPage.paRecalc(); }
    },

    // Pure line math (#555): completed = previous + this period + stored;
    // retainage = completed × pct/100; balance = scheduled − completed.
    paLineMath(scheduled, previous, thisPeriod, stored, pct) {
        const s = Number(scheduled || 0), p = Number(previous || 0);
        const tp = Number(thisPeriod || 0), st = Number(stored || 0);
        const completed = Math.round((p + tp + st) * 100) / 100;
        const retainage = Math.round(completed * Number(pct || 0) / 100 * 100) / 100;
        return {
            completed,
            pct_complete: s ? Math.round(completed / s * 1000) / 10 : 0,
            balance: Math.round((s - completed) * 100) / 100,
            retainage,
        };
    },

    // The sentence an open application with no lines shows in place of its
    // grid (#723), with the one thing that can be done with it.
    paEmptyHtml(a) {
        return `<div id="pa-empty" style="margin:4px 0 8px">
            <h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:4px 0 6px 0">Pay application ${escapeHtml(a.number)}</h4>
            <p role="note" style="margin:0 0 8px">This pay application has no lines because it was made before the job had a schedule of values, so there is nothing on it to bill. Delete it, then make a new pay application from the schedule of values.</p>
            <div class="form-actions" data-write style="justify-content:flex-start;gap:8px">
                <button type="button" class="btn btn-danger" data-destructive onclick="JobsPage.paDelete(${a.id})">Delete this pay application</button>
                <button type="button" class="btn btn-secondary" onclick="JobsPage.setTab('payapps')">Cancel</button>
            </div>
        </div>`;
    },

    async paDelete(id) {
        if (!confirm('Delete this pay application? It has no lines, so nothing has been billed on it.')) return;
        try {
            await API.del(`/pay-applications/${id}`);
            toast('Pay application deleted');
            await JobsPage.setTab('payapps');
        } catch (err) { toast(err.message, 'error'); }
    },

    paFormHtml(a) {
        const ro = !(a.status === 'draft' || a.status === 'submitted');
        if (!ro && !(a.lines || []).length) return JobsPage.paEmptyHtml(a);
        const d = ro ? 'disabled' : '';
        return `<form id="pa-form" data-write onsubmit="JobsPage.paSave(event, ${a.id})">
            <h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:4px 0 6px 0">Pay application ${escapeHtml(a.number)}</h4>
            ${JobsPage.paGridHtml(a.lines, a.totals, { pct: a.retainage_pct, disabled: ro })}
            <div class="form-grid" style="margin-top:10px">
                <div class="form-group"><label>Period to</label><input id="pa-period" ${d} type="date" value="${a.period_to || ''}"></div>
                <div class="form-group"><label>Retainage %</label><input id="pa-pct" ${d} type="number" step="0.01" min="0" max="100" value="${a.retainage_pct ?? ''}" oninput="JobsPage.paRecalc()"></div>
                <div class="form-group"><label>Sales tax</label>
                    <label style="font-weight:normal"><input id="pa-tax-on" ${d} type="checkbox" ${a.charge_tax !== false ? 'checked' : ''} ${a.tax_exempt ? 'disabled' : ''} onchange="JobsPage.paRecalc()"> Charge tax${a.tax_exempt ? ' (owner is tax-exempt)' : ''}</label></div>
                <div class="form-group"><label>Tax rate %</label><input id="pa-tax-rate" ${d} type="number" step="0.0001" min="0" value="${Math.round(Number(a.tax_rate || 0) * 1000000) / 10000}" oninput="JobsPage.paRecalc()"></div>
                <div class="form-group"><label>Release retainage</label><input id="pa-release" ${d} type="number" step="0.01" min="0" value="${a.release_retainage ?? 0}" oninput="JobsPage.paRecalc()"></div>
                <div class="form-group"><label>Deposit applied <span style="color:var(--text-muted)">(${formatCurrency(a.unapplied_deposits)} unapplied)</span></label>
                    <input id="pa-deposit" ${d} type="number" step="0.01" min="0" value="${a.deposit_applied ?? 0}" oninput="JobsPage.paRecalc()"></div>
                <div class="form-group"><label>Lien waiver</label><select id="pa-lien" ${d}>
                    <option value="">—</option>
                    ${Object.entries(JobsPage.LIEN_WAIVERS).map(([k, v]) => `<option value="${k}" ${a.lien_waiver_status === k ? 'selected' : ''}>${v}</option>`).join('')}
                </select></div>
            </div>
            ${JobsPage.paWaiverWarning(a.sub_waivers)}
            ${ro ? '' : `<div class="form-actions" data-write style="justify-content:flex-start;gap:8px">
                <button type="submit" class="btn btn-primary">Save</button>
                <button type="button" class="btn btn-secondary" onclick="JobsPage.paSubmit(${a.id})">Submit</button>
                <button type="button" class="btn btn-secondary" onclick="JobsPage.paApprove(${a.id})">Approve</button>
                <button type="button" class="btn btn-secondary" onclick="JobsPage.setTab('payapps')">Cancel</button>
            </div>`}
        </form>`;
    },

    // The grid: one row per SOV line, the totals block mirroring the pay
    // application header. Pure, so a node probe can drive it.
    paGridHtml(lines, totals, opts) {
        lines = lines || [];
        totals = totals || {};
        opts = opts || {};
        const d = opts.disabled ? 'disabled' : '';
        const pct = opts.pct || 0;
        const rows = lines.map(l => {
            const r = JobsPage.paLineMath(l.scheduled_value, l.previous, l.this_period, l.stored, pct);
            return `<tr class="pa-line" data-sov="${l.sov_line_id}" data-scheduled="${l.scheduled_value}" data-previous="${l.previous}">
                <td>${escapeHtml(l.item_no || '')}</td>
                <td>${escapeHtml(l.description || '')}</td>
                <td class="amount">${formatCurrency(l.scheduled_value)}</td>
                <td class="amount">${formatCurrency(l.previous)}</td>
                <td><input class="pa-tp" ${d} type="number" step="0.01" min="0" value="${l.this_period ?? 0}" style="width:96px;text-align:right" oninput="JobsPage.paRecalc()"></td>
                <td><input class="pa-st" ${d} type="number" step="0.01" min="0" value="${l.stored ?? 0}" style="width:90px;text-align:right" oninput="JobsPage.paRecalc()"></td>
                <td class="amount pa-completed">${formatCurrency(r.completed)}</td>
                <td class="amount pa-pct">${r.pct_complete}%</td>
                <td class="amount pa-balance">${formatCurrency(r.balance)}</td>
                <td class="amount pa-retainage">${formatCurrency(r.retainage)}</td>
            </tr>`;
        }).join('');
        return `<div class="table-container"><table class="data-table" id="pa-grid" style="font-size:12px">
            <thead><tr>
                <th scope="col">Item</th><th scope="col">Description</th>
                <th scope="col" class="amount">Scheduled</th><th scope="col" class="amount">Previous</th>
                <th scope="col" class="amount">This period</th><th scope="col" class="amount">Stored</th>
                <th scope="col" class="amount">Completed</th><th scope="col" class="amount">%</th>
                <th scope="col" class="amount">Balance</th><th scope="col" class="amount">Retainage</th>
            </tr></thead>
            <tbody id="pa-body">${rows}</tbody>
        </table></div>
        <div id="pa-totals" style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0">
            ${JobsPage.paTotalsHtml(totals)}
        </div>`;
    },

    paTotalsHtml(t) {
        t = t || {};
        const m = JobsPage.money;
        const tile = (label, v) => `<div style="min-width:130px"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase">${label}</div><div style="font-size:15px;font-weight:700">${m(v)}</div></div>`;
        return tile('Completed to date', t.completed_to_date)
            + tile('Retainage to date', t.retainage_to_date)
            + tile('Total earned less retainage', t.total_earned_less_retainage)
            + tile('Less previous payment applications', t.previous_applications)
            + (Number(t.tax_amount) ? tile('Sales tax', t.tax_amount) : '')
            + tile('Current payment due', t.current_due)
            + tile('Net due', t.net_due);
    },

    paWaiverWarning(sw) {
        if (!sw || !sw.missing) return '';
        const names = (sw.bills || []).map(b => `${escapeHtml(b.bill_number)} (${escapeHtml(b.vendor_name || '')})`).join(', ');
        return `<div class="form-hint" style="color:var(--text-warning,#8a6d3b)">${sw.missing} sub bill(s) without a lien waiver for this period: ${names}. Collect them before submitting.</div>`;
    },

    paRecalc() {
        const pct = Number(($('#pa-pct') ? $('#pa-pct').value : 0) || 0);
        let completed = 0, retainage = 0;
        $$('#pa-body .pa-line').forEach(tr => {
            const scheduled = tr.dataset.scheduled, previous = tr.dataset.previous;
            const tp = tr.querySelector('.pa-tp').value, st = tr.querySelector('.pa-st').value;
            const r = JobsPage.paLineMath(scheduled, previous, tp, st, pct);
            tr.querySelector('.pa-completed').textContent = formatCurrency(r.completed);
            tr.querySelector('.pa-pct').textContent = r.pct_complete + '%';
            tr.querySelector('.pa-balance').textContent = formatCurrency(r.balance);
            tr.querySelector('.pa-retainage').textContent = formatCurrency(r.retainage);
            completed += r.completed; retainage += r.retainage;
        });
        const prevRet = (JobsPage._paEditing && JobsPage._paEditing.totals && JobsPage._paEditing.totals.previous_retainage) || 0;
        const prevCert = (JobsPage._paEditing && JobsPage._paEditing.totals && JobsPage._paEditing.totals.previous_applications) || 0;
        const release = Number(($('#pa-release') ? $('#pa-release').value : 0) || 0);
        const deposit = Number(($('#pa-deposit') ? $('#pa-deposit').value : 0) || 0);
        // Retainage to date is net of every release, this one included.
        const prevReleased = (JobsPage._paEditing && JobsPage._paEditing.totals && JobsPage._paEditing.totals.previous_released) || 0;
        retainage = Math.round((retainage - prevReleased - release) * 100) / 100;
        const earnedLess = completed - retainage;
        // Tax on what this application bills (completed less the prior
        // application's completed), when tax is on and the owner is taxable.
        const prior = (JobsPage._paEditing && JobsPage._paEditing.totals) || {};
        const priorBilled = (prior.completed_to_date || 0) - (prior.billed_this_period || 0);
        const taxOn = $('#pa-tax-on') ? $('#pa-tax-on').checked : false;
        const taxRate = Number(($('#pa-tax-rate') ? $('#pa-tax-rate').value : 0) || 0) / 100;
        const exempt = !!(JobsPage._paEditing && JobsPage._paEditing.tax_exempt);
        const tax = (taxOn && !exempt) ? Math.round((completed - priorBilled) * taxRate * 100) / 100 : 0;
        const currentDue = Math.round((earnedLess - prevCert + tax) * 100) / 100;
        const totals = {
            tax_amount: tax,
            completed_to_date: completed,
            retainage_to_date: retainage,
            total_earned_less_retainage: earnedLess,
            previous_applications: prevCert,
            current_due: currentDue,
            net_due: Math.round((currentDue - deposit) * 100) / 100,
        };
        const el = $('#pa-totals');
        if (el) el.innerHTML = JobsPage.paTotalsHtml(totals);
    },

    paReadForm() {
        const num = sel => { const el = $(sel); const v = el && el.value !== '' ? parseFloat(el.value) : null; return (v === null || isNaN(v)) ? null : v; };
        return {
            period_to: ($('#pa-period') && $('#pa-period').value) || null,
            retainage_pct: num('#pa-pct'),
            release_retainage: num('#pa-release') || 0,
            deposit_applied: num('#pa-deposit') || 0,
            charge_tax: $('#pa-tax-on') ? $('#pa-tax-on').checked : true,
            tax_rate: (num('#pa-tax-rate') || 0) / 100,
            lien_waiver_status: ($('#pa-lien') && $('#pa-lien').value) || null,
            lines: $$('#pa-body .pa-line').map(tr => ({
                sov_line_id: parseInt(tr.dataset.sov),
                this_period: parseFloat(tr.querySelector('.pa-tp').value) || 0,
                stored: parseFloat(tr.querySelector('.pa-st').value) || 0,
            })),
        };
    },

    async paSave(e, id) {
        if (e) e.preventDefault();
        try {
            await API.put(`/pay-applications/${id}`, JobsPage.paReadForm());
            toast('Pay application saved');
            await JobsPage.setTab('payapps');
            JobsPage.paEdit(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async paSubmit(id) {
        try {
            if ($('#pa-form')) await API.put(`/pay-applications/${id}`, JobsPage.paReadForm());
            await API.post(`/pay-applications/${id}/submit`, {});
            toast('Pay application submitted');
            await JobsPage.setTab('payapps');
        } catch (err) { toast(err.message, 'error'); }
    },

    async paApprove(id) {
        if (!confirm('Approve this pay application? Creates the invoice for the job\'s customer.')) return;
        try {
            if ($('#pa-form')) await API.put(`/pay-applications/${id}`, JobsPage.paReadForm());
            await API.post(`/pay-applications/${id}/approve`, {});
            toast('Pay application approved — invoice created');
            await JobsPage.setTab('payapps');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Lien waivers (#555): one per payment, job and type, made here and printed.
    paWaiversHtml(payments) {
        const m = JobsPage.money;
        const rows = (payments || []).map(p => {
            const cells = Object.entries(JobsPage.WAIVER_TYPES).map(([type, label]) => {
                const w = (p.waivers || []).find(x => x.waiver_type === type);
                return w
                    ? `<td><button class="btn btn-sm btn-secondary" onclick="window.open('/api/lien-waivers/${w.id}/pdf','_blank')">${label} PDF</button></td>`
                    : `<td><button class="btn btn-sm btn-secondary" data-write onclick="JobsPage.paMakeWaiver(${p.id}, '${type}')">Make ${label.toLowerCase()}</button></td>`;
            }).join('');
            return `<tr><td>${escapeHtml(p.date || '')}</td><td class="amount">${m(p.amount)}</td>${cells}</tr>`;
        }).join('');
        return `<table class="data-table" style="font-size:12px"><thead><tr>
                <th scope="col">Payment</th><th scope="col" class="amount">Amount</th>
                ${Object.values(JobsPage.WAIVER_TYPES).map(l => `<th scope="col">${l}</th>`).join('')}
            </tr></thead><tbody>${rows || '<tr><td colspan="6" style="color:var(--text-muted)">No payments on this application\'s invoice yet. Record the payment, then make its waivers here.</td></tr>'}</tbody></table>`;
    },

    async paWaivers(id) {
        try {
            const payments = await API.get(`/pay-applications/${id}/payments`);
            JobsPage._paWaiverApp = id;
            openModal('Lien waivers', JobsPage.paWaiversHtml(payments));
        } catch (err) { toast(err.message, 'error'); }
    },

    async paMakeWaiver(paymentId, type) {
        try {
            const w = await API.post('/lien-waivers', { payment_id: paymentId, job_id: JobsPage._job.id, waiver_type: type });
            toast('Lien waiver made');
            await JobsPage.paWaivers(JobsPage._paWaiverApp);
            window.open(`/api/lien-waivers/${w.id}/pdf`, '_blank');
        } catch (err) { toast(err.message, 'error'); }
    },

    async paVoid(id) {
        if (!confirm('Void this pay application? The invoice is voided and any deposit restored.')) return;
        try {
            await API.post(`/pay-applications/${id}/void`, {});
            toast('Pay application voided');
            await JobsPage.setTab('payapps');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Owner deposits (same tab) --------------------------------------------
    depositsBlockHtml(data) {
        data = data || { deposits: [], received: 0, applied: 0, unapplied: 0 };
        const m = JobsPage.money;
        const rows = (data.deposits || []).map(dp => `<tr>
            <td>${escapeHtml(dp.date || '')}</td>
            <td class="amount">${m(dp.amount)}</td>
            <td>${escapeHtml(dp.memo || '')}</td>
            <td>${dp.voided_at ? '<span class="badge">Void</span>' : `<button class="btn btn-sm btn-danger" data-destructive onclick="JobsPage.paVoidDeposit(${dp.id})">Void</button>`}</td>
        </tr>`).join('');
        return `<h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:22px 0 6px 0">Owner deposits</h4>
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px">
                <div><div style="font-size:11px;color:var(--text-muted)">Received</div><div style="font-weight:700">${m(data.received)}</div></div>
                <div><div style="font-size:11px;color:var(--text-muted)">Applied</div><div style="font-weight:700">${m(data.applied)}</div></div>
                <div><div style="font-size:11px;color:var(--text-muted)">Unapplied</div><div style="font-weight:700">${m(data.unapplied)}</div></div>
            </div>
            <div class="form-actions" data-write style="justify-content:flex-start;margin-bottom:8px">
                <button class="btn btn-sm btn-secondary" onclick="JobsPage.paAddDeposit()">+ Record deposit</button>
            </div>
            <div class="table-container"><table class="data-table" id="pa-deposits" style="font-size:12px">
                <thead><tr><th scope="col">Date</th><th scope="col" class="amount">Amount</th><th scope="col">Memo</th><th scope="col"></th></tr></thead>
                <tbody>${rows || '<tr><td colspan="4" style="color:var(--text-muted)">No deposits recorded.</td></tr>'}</tbody>
            </table></div>`;
    },

    async paAddDeposit() {
        const accounts = await API.get('/accounts?active_only=true').catch(() => []);
        const banks = accounts.filter(a => (a.account_type === 'asset') && (a.bank_kind || /bank|checking|cash/i.test(a.name || '')));
        const bankOpts = (banks.length ? banks : accounts.filter(a => a.account_type === 'asset'))
            .map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`).join('');
        openModal('Record deposit', `
            <form id="pa-dep-form" data-write onsubmit="JobsPage.paSaveDeposit(event)">
                <div class="form-group"><label>Date</label><input name="date" type="date" value="${todayISO()}" required></div>
                <div class="form-group"><label>Amount</label><input name="amount" type="number" step="0.01" min="0.01" required></div>
                <div class="form-group"><label>Deposit to</label><select name="account" required>${bankOpts}</select></div>
                <div class="form-group"><label>Memo</label><input name="memo" maxlength="200"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record</button>
                </div>
            </form>`);
    },

    async paSaveDeposit(e) {
        if (e) e.preventDefault();
        const f = e.target;
        try {
            await API.post(`/jobs/${JobsPage._job.id}/deposits`, {
                date: f.date.value,
                amount: parseFloat(f.amount.value),
                deposit_to_account_id: parseInt(f.account.value),
                memo: f.memo.value || null,
            });
            toast('Deposit recorded');
            closeModal();
            await JobsPage.setTab('payapps');
        } catch (err) { toast(err.message, 'error'); }
    },

    async paVoidDeposit(id) {
        if (!confirm('Void this deposit?')) return;
        try {
            await API.post(`/jobs/${JobsPage._job.id}/deposits/${id}/void`, {});
            toast('Deposit voided');
            await JobsPage.setTab('payapps');
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
            <thead><tr><th scope="col">Date</th><th scope="col">Source</th><th scope="col">Account</th><th scope="col">Memo</th><th scope="col" class="amount">${T('Income')}</th><th scope="col" class="amount">Cost</th></tr></thead>
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
            <td>${e.job_cost_id ? `<a href="#" onclick="event.preventDefault();JobCostsPage.view(${e.job_cost_id})">posted</a>` : ((e.status === 'approved' || e.status === 'submitted') ? `<button class="btn btn-sm btn-secondary" onclick="JobsPage.postTime([${e.id}])">Post to job</button>` : '<span style="color:var(--text-muted)">draft</span>')}</td>
        </tr>`).join('');
        return `
            ${unposted.length ? `<div style="margin-bottom:8px"><button class="btn btn-sm btn-primary" onclick="JobsPage.postTime([${unposted.map(e => e.id).join(',')}])">Post ${unposted.length} approved entr${unposted.length === 1 ? 'y' : 'ies'} to this job</button>
                <span style="font-size:11px;color:var(--text-muted)">Labor posts at the employee's loaded cost rate with burden as its own line.</span></div>` : ''}
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Date</th><th scope="col">Employee</th><th scope="col">Cost code</th><th scope="col" class="amount">Reg</th><th scope="col" class="amount">OT</th><th scope="col" class="amount">DT</th><th scope="col">Status</th><th scope="col">${T('Job')} cost</th></tr></thead>
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
        const customers = await JobsPage._pickerCustomers(job);
        const custOpts = customers.map(c => `<option value="${c.id}" ${String(job.customer_id) === String(c.id) ? 'selected' : ''}>${escapeHtml(customerLabel(c))}</option>`).join('');
        const statusOpts = Object.entries(JobsPage.STATUS_LABELS)
            .map(([k, v]) => `<option value="${k}" ${job.status === k ? 'selected' : ''}>${v}</option>`).join('');
        const html = `
            <form id="job-form" onsubmit="JobsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>${T('Job')} name *</label>
                        <input name="name" required maxlength="200" value="${escapeHtml(job.name)}" placeholder="Kitchen remodel"></div>
                    <div class="form-group"><label>${T('Job')} #</label>
                        <input name="job_number" maxlength="50" value="${escapeHtml(job.job_number || '')}"></div>
                    <div class="form-group"><label>Status</label>
                        <select name="status">${statusOpts}</select></div>
                    <div class="form-group"><label>${T('Job')} type</label>
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
                    ${id ? `<button data-destructive type="button" class="btn btn-secondary" onclick="JobsPage.remove(${id})" style="margin-right:auto;">Delete</button>` : ''}
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? `Save ${T('Job')}` : `Create ${T('Job')}`}</button>
                </div>
            </form>`;
        openModal(id ? `Edit ${T('Job')}` : `New ${T('Job')}`, html);
    },

    // Active customers only — the picker listed inactive ones too — plus the
    // job's own customer if it has gone inactive since (or the Customer
    // Center's, for a new job), so an edit never asks for a customer again.
    async _pickerCustomers(job) {
        let customers = [];
        try { customers = await API.get('/customers?active_only=true&tree=1'); } catch (e) { /* keep empty */ }
        const own = job.customer_id ? String(job.customer_id) : '';
        if (own && !customers.some(c => String(c.id) === own)) {
            let name = job.customer_name;
            if (!name) { try { name = (await API.get(`/customers/${own}`)).name; } catch (e) { name = ''; } }
            customers.push({ id: job.customer_id, name: `${name || `${T('Customer')} ${own}`} (inactive)` });
        }
        return customers;
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
            toast(id ? `${T('Job')} saved` : `${T('Job')} created`);
            closeModal();
            App.navigate(`#/jobs/${id || saved.id}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async remove(id) {
        if (!confirm(Terms.text('Delete this job? Jobs with posted activity cannot be deleted — mark them inactive instead.'))) return;
        try {
            await API.del(`/jobs/${id}`);
            toast(`${T('Job')} deleted`);
            closeModal();
            App.navigate('#/jobs');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Kept for callers from other pages (Customer Center, reports).
    showDetails(id) { App.navigate(`#/jobs/${id}`); },
};
window.JobsPage = JobsPage;
