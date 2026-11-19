/**
 * Nonprofit documents — what a fund-accounting ledger posts that a
 * business never does. Release from Restriction: a restricted fund spent
 * money for its purpose, so that much moves from net assets with donor
 * restrictions to net assets without. The form suggests the fund's
 * unreleased spending for the period; the posting is DR 3400 / CR 3300
 * tagged to the fund, voidable like any other document.
 */
const ReleasesPage = {
    _funds: [],

    async render() {
        const releases = await API.get('/nonprofit/releases');
        const rows = releases.map(r => `<tr class="clickable${r.status === 'void' ? ' row--dim' : ''}" onclick="ReleasesPage.view(${r.id})">
            <td>${escapeHtml(r.number)}</td>
            <td>${escapeHtml(r.date)}</td>
            <td>${escapeHtml(r.class_name || '')}</td>
            <td>${escapeHtml(r.period_start || '')} — ${escapeHtml(r.period_end || '')}</td>
            <td>${escapeHtml(r.memo || '')}</td>
            <td class="amount">${formatCurrency(r.amount)}</td>
            <td>${r.status === 'void' ? '<span style="color:var(--text-danger)">void</span>' : 'posted'}</td>
        </tr>`).join('');
        return `
            <div class="page-header">
                <div style="display:flex; gap:4px; align-items:center;"><h2>Releases from Restriction</h2>${hint("When a restricted fund spends for its purpose, release that much to net assets without donor restrictions. The amount suggested is the fund's spending in the period less what was already released.", 'Releases from Restriction')}</div>
                <button class="btn btn-primary" onclick="ReleasesPage.showForm()">+ Release</button>
            </div>
            ${releases.length === 0 ? `<div class="empty-state"><p>No releases yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">${T('Class')}</th><th scope="col">Period</th><th scope="col">Memo</th><th scope="col" class="amount">Amount</th><th scope="col">Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`}`;
    },

    async showForm() {
        const classes = await API.get('/classes');
        ReleasesPage._funds = classes.filter(c => c.restriction && c.restriction !== 'unrestricted');
        if (!ReleasesPage._funds.length) {
            toast(`No restricted ${T('classes')} yet — mark a ${T('class')} as restricted in Settings`, 'error');
            return;
        }
        const fundOpts = ReleasesPage._funds.map(f => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join('');
        const year = new Date().getFullYear();
        openModal('Release from Restriction', `
            <form onsubmit="ReleasesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Class')} *</label>
                        <select name="class_id" required onchange="ReleasesPage.suggest()">${fundOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Period start</label>
                        <input name="period_start" type="date" value="${year}-01-01" onchange="ReleasesPage.suggest()"></div>
                    <div class="form-group"><label>Period end</label>
                        <input name="period_end" type="date" value="${todayISO()}" onchange="ReleasesPage.suggest()"></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" class="money" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group" style="display:flex;align-items:flex-end;">
                        <button type="button" class="btn btn-secondary" onclick="ReleasesPage.suggest()">Suggest</button></div>
                    <div class="form-group full-width" id="release-hint" style="font-size:11px; color:var(--gray-500);"></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="memo" placeholder="e.g. June youth program spending"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Release</button>
                </div>
            </form>`);
        ReleasesPage.suggest();
    },

    async suggest() {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const hint = $('#release-hint');
        try {
            const s = await API.get(`/nonprofit/releases/suggest?class_id=${form.class_id.value}&start_date=${form.period_start.value}&end_date=${form.period_end.value}`);
            form.amount.value = Number(s.suggested).toFixed(2);
            if (hint) hint.textContent = `${s.class_name}: spent ${formatCurrency(s.expenses)} in the period, ${formatCurrency(s.released)} already released, ${formatCurrency(s.suggested)} to release.`;
        } catch (err) {
            if (hint) hint.textContent = err.message;
        }
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        if (!(await TxnWarn.ok(form))) return;
        try {
            const rel = await API.post('/nonprofit/releases', {
                date: form.date.value,
                class_id: parseInt(form.class_id.value),
                amount: parseFloat(form.amount.value),
                period_start: form.period_start.value || null,
                period_end: form.period_end.value || null,
                memo: form.memo.value || null,
            });
            toast(`${rel.number}: ${formatCurrency(rel.amount)} released from ${rel.class_name}`);
            closeModal();
            App.navigate('#/releases');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        let r;
        try { r = await API.get(`/nonprofit/releases/${id}`); } catch (err) { toast(err.message, 'error'); return; }
        openModal(`Release ${r.number}`, `
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="font-size:13px">
                    <div><strong>${escapeHtml(r.date)}</strong> · ${escapeHtml(r.class_name || '')}</div>
                    <div style="color:var(--text-muted)">Period ${escapeHtml(r.period_start || '')} — ${escapeHtml(r.period_end || '')}</div>
                    ${r.memo ? `<div style="color:var(--text-muted)">${escapeHtml(r.memo)}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div style="font-size:20px;font-weight:700">${formatCurrency(r.amount)} ${hint(`Posted as a debit to Net Assets With Donor Restrictions and a credit to Net Assets Without, both tagged to the ${T('class')}.`, 'Amount')}</div>
                    <div>${r.status === 'void' ? '<span style="color:var(--text-danger);font-weight:600">VOID</span>' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="ReleasesPage.voidEntry(${r.id})">Void</button>`}</div>
                </div>
            </div>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async voidEntry(id) {
        if (!confirm('Void this release? A reversing entry is posted; the original stays in the ledger.')) return;
        try {
            await API.post(`/nonprofit/releases/${id}/void`, {});
            toast('Release voided');
            closeModal();
            App.navigate('#/releases');
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.ReleasesPage = ReleasesPage;


/**
 * Functional allocations — a saved rule (rent 70/20/10 by square feet, the
 * office manager's wages by hours on grants) run at period end on the
 * cost still unassigned on its source account. The posting is a
 * same-account reclass: debit each target with its function, credit the
 * source without one, so the P&L is unchanged and the Statement of
 * Functional Expenses has its columns. Running the same period twice
 * finds nothing left to move. The same rules feed the Split button on
 * entry lines.
 */
const AllocationsPage = {
    _rules: [],
    _accounts: [],
    _classes: [],
    _jobs: [],

    async render() {
        const [runs, rules] = await Promise.all([API.get('/nonprofit/allocations'), API.get('/nonprofit/allocation-rules?include_inactive=true')]);
        AllocationsPage._rules = rules;
        const rows = runs.map(a => `<tr class="clickable${a.status === 'void' ? ' row--dim' : ''}" onclick="AllocationsPage.view(${a.id})">
            <td>${escapeHtml(a.number)}</td>
            <td>${escapeHtml(a.date)}</td>
            <td>${escapeHtml(a.rule_name || '')}</td>
            <td>${escapeHtml(a.period_start)} — ${escapeHtml(a.period_end)}</td>
            <td class="amount">${formatCurrency(a.total)}</td>
            <td>${a.status === 'void' ? '<span style="color:var(--text-danger)">void</span>' : 'posted'}</td>
        </tr>`).join('');
        const ruleRows = rules.map(r => `<tr${r.is_active ? '' : ' class="row--dim"'}>
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml({ percent: 'Percent', square_feet: 'Square feet', hours: 'Hours on grants' }[r.basis] || r.basis)}</td>
            <td>${escapeHtml(r.source_account_name || 'any expense')}${r.source_class_name ? ` · ${escapeHtml(r.source_class_name)}` : ''}</td>
            <td style="font-size:11px">${r.targets.map(t => `${escapeHtml([t.class_name, Nonprofit.label(t.function)].filter(Boolean).join(' / ') || t.job_name || '')} ${Number(t.weight)}`).join(', ')}</td>
            <td class="actions">
                <button type="button" class="btn btn-sm btn-secondary" onclick="AllocationsPage.showRule(${r.id})">Edit</button>
                <button type="button" class="btn btn-sm btn-primary" onclick="AllocationsPage.showRun(${r.id})">Run</button>
            </td>
        </tr>`).join('');
        return `
            <div class="page-header">
                <div style="display:flex; gap:4px; align-items:center;"><h2>Functional Allocations</h2>${hint(`Rules split a shared cost across program, management and fundraising (and across ${T('classes')}). Run one at month end on what is still unassigned; use the same rule from the Split button on a bill or journal line.`, 'Functional Allocations')}</div>
                <div>
                    <button class="btn btn-secondary" onclick="AllocationsPage.showRule()">+ Allocation Rule</button>
                    <button class="btn btn-primary" onclick="AllocationsPage.showRun()">Run a Rule</button>
                </div>
            </div>
            <h3 style="margin:12px 0 6px; font-size:13px;">Rules</h3>
            ${rules.length === 0 ? `<div class="empty-state"><p>No allocation rules yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">Name</th><th scope="col">Basis</th><th scope="col">Source</th><th scope="col">Targets · weight</th><th scope="col">Actions</th></tr></thead>
                <tbody>${ruleRows}</tbody>
            </table></div>`}
            <h3 style="margin:16px 0 6px; font-size:13px;">Runs</h3>
            ${runs.length === 0 ? `<div class="empty-state"><p>No allocations posted yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">Rule</th><th scope="col">Period</th><th scope="col" class="amount">Moved</th><th scope="col">Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`}`;
    },

    async _loadRefs() {
        const [accounts, classes, jobs] = await Promise.all([
            API.get('/accounts'), API.get('/classes'), API.get('/jobs').catch(() => []),
        ]);
        Object.assign(AllocationsPage, { _accounts: accounts.filter(a => ['expense', 'cogs'].includes(a.account_type)), _classes: classes, _jobs: jobs });
    },

    _opt(list, valueKey, labelFn, selected, blank = '--') {
        return `<option value="">${blank}</option>` + list.map(x => `<option value="${x[valueKey]}" ${String(selected ?? '') === String(x[valueKey]) ? 'selected' : ''}>${escapeHtml(labelFn(x))}</option>`).join('');
    },

    // ---- Rule editor --------------------------------------------------------------
    async showRule(id = null) {
        await AllocationsPage._loadRefs();
        const rule = id ? AllocationsPage._rules.find(r => r.id === id) : null;
        const acctOpts = AllocationsPage._opt(AllocationsPage._accounts, 'id', a => accountWords(a, null, ' '), rule?.source_account_id, 'Any expense account');
        const classOpts = AllocationsPage._opt(AllocationsPage._classes, 'id', c => c.name, rule?.source_class_id, `Any ${T('class')}`);
        const targets = rule?.targets?.length ? rule.targets : [{ function: 'program', weight: 70 }, { function: 'management', weight: 20 }, { function: 'fundraising', weight: 10 }];
        openModal(rule ? `Rule: ${escapeHtml(rule.name)}` : 'New Allocation Rule', `
            <form onsubmit="AllocationsPage.saveRule(event, ${rule ? rule.id : 'null'})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label><input name="name" required maxlength="100" value="${escapeHtml(rule?.name || '')}" placeholder="e.g. Rent by square footage"></div>
                    <div class="form-group"><label>Basis</label>
                        <select name="basis" onchange="AllocationsPage.basisChanged(this.value)">
                            <option value="percent" ${!rule || rule.basis === 'percent' ? 'selected' : ''}>Percent / weights</option>
                            <option value="square_feet" ${rule?.basis === 'square_feet' ? 'selected' : ''}>Square feet</option>
                            <option value="hours" ${rule?.basis === 'hours' ? 'selected' : ''}>Hours on ${T('jobs')} in the period</option>
                        </select></div>
                    <div class="form-group"><label>Source account</label><select name="source_account_id">${acctOpts}</select></div>
                    <div class="form-group"><label>Source ${T('class')}</label><select name="source_class_id">${classOpts}</select></div>
                    <div class="form-group full-width"><label>Notes</label><input name="notes" value="${escapeHtml(rule?.notes || '')}"></div>
                    ${rule ? `<div class="form-group full-width"><label style="font-weight:normal"><input type="checkbox" name="is_active" ${rule.is_active ? 'checked' : ''}> Active</label></div>` : ''}
                </div>
                <div style="display:flex; gap:4px; align-items:center; margin:12px 0 8px;"><h3 style="margin:0;font-size:14px;">Targets</h3>${hint(`Each share lands in a ${T('class')}, a function, or both. Weights are relative (70/20/10, or square feet).`, 'Targets')}</div>
                <div class="table-container"><table class="line-items-table">
                    <thead><tr><th scope="col">${T('Class')}</th><th scope="col">Function</th><th scope="col" class="alloc-job-col">${T('Job')} (hours)</th><th scope="col" class="col-qty">Weight</th><th scope="col"></th></tr></thead>
                    <tbody id="rule-targets">${targets.map(t => AllocationsPage.targetHtml(t)).join('')}</tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="AllocationsPage.addTarget()">+ Add Target</button>
                <div class="form-actions">
                    ${rule ? `<button data-destructive type="button" class="btn btn-secondary" onclick="AllocationsPage.deleteRule(${rule.id})">Delete</button>` : ''}
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Rule</button>
                </div>
            </form>`);
        AllocationsPage.basisChanged(rule?.basis || 'percent');
    },

    targetHtml(t = {}) {
        const classOpts = AllocationsPage._opt(AllocationsPage._classes, 'id', c => c.name, t.class_id, '—');
        const jobOpts = AllocationsPage._opt(AllocationsPage._jobs, 'id', j => j.full_name, t.job_id, '—');
        return `<tr>
            <td><select class="tg-class">${classOpts}</select></td>
            <td><select class="tg-function">${Nonprofit.optionsHtml(t.function || null, '—')}</select></td>
            <td class="alloc-job-col"><select class="tg-job">${jobOpts}</select></td>
            <td><input class="tg-weight" type="number" step="0.01" min="0" value="${t.weight != null ? Number(t.weight) : 1}"></td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove target" onclick="this.closest('tr').remove()">X</button></td>
        </tr>`;
    },

    addTarget() { $('#rule-targets').insertAdjacentHTML('beforeend', AllocationsPage.targetHtml()); },

    basisChanged(basis) {
        $$('.alloc-job-col').forEach(el => { el.style.display = basis === 'hours' ? '' : 'none'; });
    },

    async saveRule(e, id) {
        e.preventDefault();
        const form = e.target;
        const targets = [];
        $$('#rule-targets tr').forEach(row => {
            const cls = row.querySelector('.tg-class')?.value;
            const fn = row.querySelector('.tg-function')?.value;
            const job = row.querySelector('.tg-job')?.value;
            targets.push({
                class_id: cls ? parseInt(cls) : null,
                function: fn || null,
                job_id: job ? parseInt(job) : null,
                weight: parseFloat(row.querySelector('.tg-weight')?.value) || 0,
            });
        });
        const body = {
            name: form.name.value.trim(),
            basis: form.basis.value,
            source_account_id: form.source_account_id.value ? parseInt(form.source_account_id.value) : null,
            source_class_id: form.source_class_id.value ? parseInt(form.source_class_id.value) : null,
            notes: form.notes.value || null,
            is_active: form.is_active ? form.is_active.checked : true,
            targets,
        };
        try {
            if (id) await API.put(`/nonprofit/allocation-rules/${id}`, body);
            else await API.post('/nonprofit/allocation-rules', body);
            toast('Rule saved');
            closeModal();
            App.navigate('#/functional-allocations');
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteRule(id) {
        if (!confirm('Delete this rule? Rules with posted runs cannot be deleted — deactivate them instead.')) return;
        try {
            await API.del(`/nonprofit/allocation-rules/${id}`);
            toast('Rule deleted');
            closeModal();
            App.navigate('#/functional-allocations');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ---- Run a rule ---------------------------------------------------------------
    async showRun(ruleId = null) {
        const rules = AllocationsPage._rules.filter(r => r.is_active);
        if (!rules.length) { toast('Create an allocation rule first', 'error'); return; }
        const ruleOpts = AllocationsPage._opt(rules, 'id', r => r.name, ruleId, 'Pick a rule…');
        const d = new Date();
        const first = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
        openModal('Run an Allocation Rule', `
            <form onsubmit="AllocationsPage.saveRun(event)">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Rule *</label><select name="rule_id" required onchange="AllocationsPage.preview()">${ruleOpts}</select></div>
                    <div class="form-group"><label>Period start *</label><input name="period_start" type="date" required value="${first}" onchange="AllocationsPage.preview()"></div>
                    <div class="form-group"><label>Period end *</label><input name="period_end" type="date" required value="${todayISO()}" onchange="AllocationsPage.preview()"></div>
                    <div class="form-group"><label>Posting date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo"></div>
                </div>
                <div id="alloc-preview" style="margin-top:10px;font-size:12px;color:var(--gray-500)">Pick a rule to preview what would move.</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Allocation</button>
                </div>
            </form>`);
        if (ruleId) AllocationsPage.preview();
    },

    async preview() {
        const form = document.querySelector('#modal-body form');
        const box = $('#alloc-preview');
        if (!form || !box || !form.rule_id.value) return;
        try {
            const p = await API.get(`/nonprofit/allocation-rules/${form.rule_id.value}/preview?start_date=${form.period_start.value}&end_date=${form.period_end.value}`);
            if (!p.pool.length) { box.innerHTML = '<em>Nothing unassigned on the source for that period.</em>'; return; }
            box.innerHTML = `<div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Unassigned on</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${p.pool.map(r => `<tr><td>${accountLabel(r, r.account_name, ' ')}</td><td class="amount">${formatCurrency(r.amount)}</td></tr>`).join('')}
                <tr style="font-weight:600"><td>Total</td><td class="amount">${formatCurrency(p.total)}</td></tr></tbody></table></div>
                <div style="margin-top:6px">Would move to: ${p.lines.map(l => `${escapeHtml([l.class_name, Nonprofit.label(l.function)].filter(Boolean).join(' / '))} ${formatCurrency(l.amount)}`).join(' · ')}</div>`;
        } catch (err) { box.textContent = err.message; }
    },

    async saveRun(e) {
        e.preventDefault();
        const form = e.target;
        if (!(await TxnWarn.ok(form))) return;
        try {
            const fa = await API.post('/nonprofit/allocations', {
                date: form.date.value,
                rule_id: parseInt(form.rule_id.value),
                period_start: form.period_start.value,
                period_end: form.period_end.value,
                memo: form.memo.value || null,
            });
            toast(`${fa.number}: ${formatCurrency(fa.total)} allocated`);
            closeModal();
            App.navigate('#/functional-allocations');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        let a;
        try { a = await API.get(`/nonprofit/allocations/${id}`); } catch (err) { toast(err.message, 'error'); return; }
        const rows = a.lines.map(l => `<tr>
            <td>${escapeHtml(l.account_name || '')}</td>
            <td>${escapeHtml(l.class_name || '')}</td>
            <td>${escapeHtml(Nonprofit.label(l.function) || '')}</td>
            <td class="amount">${l.weight != null ? Number(l.weight) : ''}</td>
            <td class="amount">${formatCurrency(l.amount)}</td>
        </tr>`).join('');
        openModal(`Allocation ${a.number}`, `
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="font-size:13px">
                    <div><strong>${escapeHtml(a.date)}</strong> · ${escapeHtml(a.rule_name || '')} · ${escapeHtml(a.period_start)} — ${escapeHtml(a.period_end)}</div>
                    ${a.memo ? `<div style="color:var(--text-muted)">${escapeHtml(a.memo)}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div style="font-size:20px;font-weight:700">${formatCurrency(a.total)}</div>
                    <div>${a.status === 'void' ? '<span style="color:var(--text-danger);font-weight:600">VOID</span>' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="AllocationsPage.voidEntry(${a.id})">Void</button>`}</div>
                </div>
            </div>
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Account</th><th scope="col">${T('Class')}</th><th scope="col">Function</th><th scope="col" class="amount">Weight</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async voidEntry(id) {
        if (!confirm('Void this allocation? A reversing entry is posted; the cost returns to unassigned.')) return;
        try {
            await API.post(`/nonprofit/allocations/${id}/void`, {});
            toast('Allocation voided');
            closeModal();
            App.navigate('#/functional-allocations');
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.AllocationsPage = AllocationsPage;


/**
 * Donor documents: the acknowledgment letter lives on the receipt and
 * payment views; this is the shared "email it" action.
 */
const Donors = {
    async emailAcknowledgment(kind, id) {
        const to = prompt('Send the acknowledgment letter to (leave as-is to use the donor\'s email):', '');
        if (to === null) return;
        try {
            const r = await API.post(`/donors/gifts/${kind}/${id}/acknowledgment/email`, { recipient: to.trim() || null });
            toast(`Acknowledgment sent to ${r.recipient}`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.Donors = Donors;
