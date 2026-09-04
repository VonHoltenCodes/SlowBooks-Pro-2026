/**
 * Benefits — benefit codes (rules with dated rates), employee groups,
 * per-employee enrollments, YTD accumulators and remittance.
 * A benefit is a code, not a feature: payroll evaluates whatever is attached.
 */
const BenefitsPage = {
    _tab: 'codes',
    _enrollEmpId: '',
    _accounts: null,
    _vendors: null,

    async render() {
        const tab = BenefitsPage._tab;
        const tabs = [
            ['codes', 'Benefit Codes'],
            ['groups', 'Employee Groups'],
            ['enrollments', 'Enrollments'],
            ['remittance', 'Remittance & YTD'],
        ].map(([id, label]) =>
            `<button class="btn ${tab === id ? 'btn-primary' : 'btn-secondary'}" onclick="BenefitsPage.switchTab('${id}')" aria-pressed="${tab === id}">${label}</button>`
        ).join(' ');
        let body = '';
        try {
            if (tab === 'codes') body = await BenefitsPage._codesTab();
            else if (tab === 'groups') body = await BenefitsPage._groupsTab();
            else if (tab === 'enrollments') body = await BenefitsPage._enrollmentsTab();
            else body = await BenefitsPage._remittanceTab();
        } catch (err) { body = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`; }
        return `
            <div class="page-header">
                <h2>Benefits & Deductions</h2>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">${tabs}</div>
            </div>
            <p style="font-size:12px;color:var(--gray-400);margin:0 0 12px;">
                A benefit is a code with a rule. Pre-tax codes apply in sequence order and each one changes the taxable base for the next.
                Rates are dated and resolve against the pay-period end date; processed pay runs keep the rules they used.
                Garnishments live on the <a href="#/hr/deductions">Garnishments</a> page.
            </p>
            <div id="benefits-body">${body}</div>`;
    },

    switchTab(tab) { BenefitsPage._tab = tab; App.navigate('#/hr/benefits'); },

    async _lookups() {
        if (!BenefitsPage._accounts) {
            const [accounts, vendors] = await Promise.all([API.get('/accounts'), API.get('/vendors')]);
            BenefitsPage._accounts = accounts;
            BenefitsPage._vendors = vendors;
        }
        return { accounts: BenefitsPage._accounts, vendors: BenefitsPage._vendors };
    },

    _acctOptions(accounts, selected, types) {
        const list = types ? accounts.filter(a => types.includes(a.account_type)) : accounts;
        return '<option value="">— none —</option>' + list.map(a =>
            `<option value="${a.id}" ${String(a.id) === String(selected || '') ? 'selected' : ''}>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`).join('');
    },

    // ------------------------------------------------------------------ codes
    _fmtRate(code) {
        const r = code.current_rate;
        if (!r) return '<span class="badge badge-draft">no rate in force</span>';
        const pct = code.calc_method.startsWith('percent') || code.calc_method === 'tiered';
        const fmt = v => pct ? `${(+v).toFixed(2)}%` : (code.calc_method === 'amount_per_hour' ? `${formatCurrency(v)}/hr` : formatCurrency(v));
        const er = code.employer_calc_method === 'match_percent'
            ? (r.tiers_json ? 'tiered match' : `${(+r.employer_rate).toFixed(0)}% match${r.employer_match_limit_pct ? ` to ${r.employer_match_limit_pct}%` : ''}`)
            : fmt(r.employer_rate);
        return `EE ${fmt(r.employee_rate)} · ER ${er}`;
    },

    async _codesTab() {
        const codes = await API.get('/benefits/codes?include_inactive=true');
        const rows = codes.map(c => `<tr ${c.is_active ? '' : 'style="opacity:.55"'}>
            <td class="amount">${c.sequence}</td>
            <td><strong>${escapeHtml(c.code)}</strong><br><span style="font-size:12px;">${escapeHtml(c.name)}</span></td>
            <td>${escapeHtml(c.kind)} · ${c.category === 'pretax' ? 'pre-tax' : 'post-tax'}</td>
            <td>${escapeHtml(c.calc_method)}${c.employer_calc_method ? ` / ${escapeHtml(c.employer_calc_method)}` : ''}</td>
            <td style="font-size:12px;">${c.category === 'pretax' ? ['reduces_federal', 'reduces_state', 'reduces_fica'].filter(k => c[k]).map(k => k.replace('reduces_', '')).join(', ') || 'none' : '—'}</td>
            <td>${BenefitsPage._fmtRate(c)}</td>
            <td>${c.burden_routing === 'job_burden' ? 'job burden' : 'fringe pool'}${c.tracks_balance ? ' · balance' : ''}</td>
            <td class="actions">
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.showCodeForm(${c.id})">Edit</button>
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.showRates(${c.id})">Rates</button>
                ${c.is_active ? `<button class="btn btn-sm btn-secondary" onclick="BenefitsPage.retireCode(${c.id})">Retire</button>` : ''}
            </td>
        </tr>`).join('');
        return `
            <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
                <button class="btn btn-primary" onclick="BenefitsPage.showCodeForm()">+ Add Code</button>
                <button class="btn btn-secondary" onclick="BenefitsPage.seedStandard()">Seed standard codes</button>
                <button class="btn btn-secondary" onclick="BenefitsPage.setupAccounts()">Create default accounts</button>
            </div>
            <div class="table-container"><table>
                <thead><tr>
                    <th scope="col" class="amount">Seq</th><th scope="col">Code</th><th scope="col">Kind</th>
                    <th scope="col">Method (EE / ER)</th><th scope="col">Reduces</th><th scope="col">Rate in force</th>
                    <th scope="col">Routing</th><th scope="col">Actions</th>
                </tr></thead>
                <tbody>${rows || '<tr><td colspan="8" style="text-align:center;color:var(--gray-400);">No benefit codes yet — seed the standard set or add one.</td></tr>'}</tbody>
            </table></div>`;
    },

    async showCodeForm(id = null) {
        const { accounts, vendors } = await BenefitsPage._lookups();
        let c = { code: '', name: '', kind: 'deduction', category: 'pretax', calc_method: 'fixed_amount', employer_calc_method: '',
                  reduces_federal: true, reduces_state: true, reduces_fica: false, employer_taxable: false, sequence: 100,
                  expense_account_id: '', liability_account_id: '', remittance_vendor_id: '', burden_routing: 'fringe_pool',
                  tracks_balance: false, effective_from: '', effective_to: '', notes: '' };
        if (id) c = await API.get(`/benefits/codes/${id}`);
        const sel = (name, options, value) => `<select name="${name}">${options.map(([v, l]) => `<option value="${v}" ${String(value) === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
        const methods = [['fixed_amount', 'Fixed amount per period'], ['percent_of_gross', 'Percent of gross'], ['percent_of_taxable', 'Percent of taxable (after earlier codes)'], ['amount_per_hour', 'Amount per hour'], ['tiered', 'Tiered bands']];
        const rateBlock = id ? '' : `
            <h3 style="margin:16px 0 8px;font-size:14px;">Initial rate (effective today; add dated changes later under Rates)</h3>
            <div class="form-grid">
                <div class="form-group"><label>Employee rate</label><input name="rate_employee_rate" type="number" step="0.0001" value="0"></div>
                <div class="form-group"><label>Employer rate</label><input name="rate_employer_rate" type="number" step="0.0001" value="0"></div>
                <div class="form-group"><label>Per-period cap ($)</label><input name="rate_per_period_cap" type="number" step="0.01"></div>
                <div class="form-group"><label>Annual cap ($)</label><input name="rate_annual_cap" type="number" step="0.01"></div>
                <div class="form-group"><label>Wage-base ceiling ($ YTD)</label><input name="rate_wage_base_ceiling" type="number" step="0.01"></div>
                <div class="form-group"><label>Employer annual cap ($)</label><input name="rate_employer_annual_cap" type="number" step="0.01"></div>
                <div class="form-group"><label>Match limit (% of gross)</label><input name="rate_employer_match_limit_pct" type="number" step="0.01"></div>
                <div class="form-group"><label>Tiers JSON</label><input name="rate_tiers_json" placeholder='[{"up_to_pct":3,"match_pct":100}]'></div>
            </div>`;
        openModal(id ? `Edit ${escapeHtml(c.code)}` : 'Add Benefit Code', `
            <form onsubmit="BenefitsPage.saveCode(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Code *</label><input name="code" required value="${escapeHtml(c.code)}" style="text-transform:uppercase;"></div>
                    <div class="form-group"><label>Name *</label><input name="name" required value="${escapeHtml(c.name)}"></div>
                    <div class="form-group"><label>Kind</label>${sel('kind', [['deduction', 'Deduction (employee pays)'], ['benefit', 'Benefit (employer pays)'], ['both', 'Both']], c.kind)}</div>
                    <div class="form-group"><label>Tax category</label>${sel('category', [['pretax', 'Pre-tax'], ['posttax', 'Post-tax']], c.category)}</div>
                    <div class="form-group"><label>Employee calc method</label>${sel('calc_method', methods, c.calc_method)}</div>
                    <div class="form-group"><label>Employer calc method</label>${sel('employer_calc_method', [['', 'Same as employee'], ...methods, ['match_percent', 'Match % of employee contribution']], c.employer_calc_method || '')}</div>
                    <div class="form-group"><label>Sequence (pre-tax order)</label><input name="sequence" type="number" value="${c.sequence}"></div>
                    <div class="form-group"><label>Burden routing (job costing)</label>${sel('burden_routing', [['fringe_pool', 'Fringe pool (stay in expense)'], ['job_burden', 'Distribute to jobs as labor burden']], c.burden_routing)}</div>
                    <div class="form-group"><label>Expense account (employer side)</label><select name="expense_account_id">${BenefitsPage._acctOptions(accounts, c.expense_account_id, ['expense', 'cogs'])}</select></div>
                    <div class="form-group"><label>Liability account</label><select name="liability_account_id">${BenefitsPage._acctOptions(accounts, c.liability_account_id, ['liability'])}</select></div>
                    <div class="form-group"><label>Remittance vendor</label><select name="remittance_vendor_id"><option value="">— none —</option>${vendors.map(v => `<option value="${v.id}" ${String(v.id) === String(c.remittance_vendor_id || '') ? 'selected' : ''}>${escapeHtml(v.name)}</option>`).join('')}</select></div>
                    <div class="form-group"><label>Effective from</label><input name="effective_from" type="date" value="${c.effective_from || ''}"></div>
                    <div class="form-group"><label>Effective to</label><input name="effective_to" type="date" value="${c.effective_to || ''}"></div>
                </div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;margin:8px 0;">
                    <label><input type="checkbox" name="reduces_federal" ${c.reduces_federal ? 'checked' : ''}> Reduces federal wages</label>
                    <label><input type="checkbox" name="reduces_state" ${c.reduces_state ? 'checked' : ''}> Reduces state wages</label>
                    <label><input type="checkbox" name="reduces_fica" ${c.reduces_fica ? 'checked' : ''}> Reduces FICA wages</label>
                    <label><input type="checkbox" name="employer_taxable" ${c.employer_taxable ? 'checked' : ''}> Employer side is taxable to employee</label>
                    <label><input type="checkbox" name="tracks_balance" ${c.tracks_balance ? 'checked' : ''}> Tracks a balance (loan)</label>
                </div>
                <div class="form-group"><label>Notes</label><input name="notes" value="${escapeHtml(c.notes || '')}"></div>
                ${rateBlock}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Save' : 'Add Code'}</button>
                </div>
            </form>`);
    },

    _num(v) { return v === '' || v === undefined || v === null ? null : parseFloat(v); },

    async saveCode(e, id) {
        e.preventDefault();
        const f = e.target;
        const raw = Object.fromEntries(new FormData(f).entries());
        const data = {
            code: raw.code.toUpperCase(), name: raw.name, kind: raw.kind, category: raw.category,
            calc_method: raw.calc_method, employer_calc_method: raw.employer_calc_method || null,
            sequence: parseInt(raw.sequence) || 100, burden_routing: raw.burden_routing,
            expense_account_id: raw.expense_account_id ? parseInt(raw.expense_account_id) : null,
            liability_account_id: raw.liability_account_id ? parseInt(raw.liability_account_id) : null,
            remittance_vendor_id: raw.remittance_vendor_id ? parseInt(raw.remittance_vendor_id) : null,
            effective_from: raw.effective_from || null, effective_to: raw.effective_to || null,
            notes: raw.notes || null,
        };
        for (const k of ['reduces_federal', 'reduces_state', 'reduces_fica', 'employer_taxable', 'tracks_balance']) data[k] = f[k].checked;
        if (!id) {
            data.rate = {
                effective_from: todayISO(),
                employee_rate: parseFloat(raw.rate_employee_rate) || 0,
                employer_rate: parseFloat(raw.rate_employer_rate) || 0,
                per_period_cap: BenefitsPage._num(raw.rate_per_period_cap),
                annual_cap: BenefitsPage._num(raw.rate_annual_cap),
                wage_base_ceiling: BenefitsPage._num(raw.rate_wage_base_ceiling),
                employer_annual_cap: BenefitsPage._num(raw.rate_employer_annual_cap),
                employer_match_limit_pct: BenefitsPage._num(raw.rate_employer_match_limit_pct),
                tiers_json: raw.rate_tiers_json || null,
            };
        }
        try {
            if (id) await API.put(`/benefits/codes/${id}`, data);
            else await API.post('/benefits/codes', data);
            toast(id ? 'Code saved' : 'Code added');
            closeModal();
            App.navigate('#/hr/benefits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async retireCode(id) {
        if (!confirm('Retire this code? Posted pay runs keep it; it just stops applying.')) return;
        try { await API.del(`/benefits/codes/${id}`); toast('Code retired'); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },

    async seedStandard() {
        try { const codes = await API.post('/benefits/codes/seed-standard', {}); toast(`${codes.length} codes on file`); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },

    async setupAccounts() {
        try { const r = await API.post('/benefits/setup-accounts', {}); toast(r.created.length ? `Created ${r.created.join(', ')}` : 'Accounts already present'); BenefitsPage._accounts = null; }
        catch (err) { toast(err.message, 'error'); }
    },

    async showRates(codeId) {
        const code = await API.get(`/benefits/codes/${codeId}`);
        const rates = await API.get(`/benefits/codes/${codeId}/rates`);
        const rows = rates.map(r => `<tr>
            <td>${formatDate(r.effective_from)}</td><td>${r.effective_to ? formatDate(r.effective_to) : 'open'}</td>
            <td class="amount">${r.employee_rate}</td><td class="amount">${r.employer_rate}</td>
            <td class="amount">${r.per_period_cap ?? '—'}</td><td class="amount">${r.annual_cap ?? '—'}</td>
            <td class="amount">${r.wage_base_ceiling ?? '—'}</td><td class="amount">${r.employer_annual_cap ?? '—'}</td>
            <td style="font-size:11px;">${r.employer_match_limit_pct ? `limit ${r.employer_match_limit_pct}% ` : ''}${escapeHtml(r.tiers_json || '')}</td>
            <td class="actions">${rates.length > 1 ? `<button class="btn btn-sm btn-secondary" onclick="BenefitsPage.deleteRate(${codeId}, ${r.id})" aria-label="Delete rate">Delete</button>` : ''}</td>
        </tr>`).join('');
        openModal(`Rates — ${escapeHtml(code.code)}`, `
            <div class="table-container"><table>
                <thead><tr><th scope="col">From</th><th scope="col">To</th><th scope="col" class="amount">EE rate</th><th scope="col" class="amount">ER rate</th>
                <th scope="col" class="amount">Period cap</th><th scope="col" class="amount">Annual cap</th><th scope="col" class="amount">Wage base</th>
                <th scope="col" class="amount">ER annual cap</th><th scope="col">Match / tiers</th><th scope="col">Actions</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <h3 style="margin:16px 0 8px;font-size:14px;">Add a dated change</h3>
            <form onsubmit="BenefitsPage.saveRate(event, ${codeId})">
                <div class="form-grid">
                    <div class="form-group"><label>Effective from *</label><input name="effective_from" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Employee rate</label><input name="employee_rate" type="number" step="0.0001" value="${code.current_rate ? code.current_rate.employee_rate : 0}"></div>
                    <div class="form-group"><label>Employer rate</label><input name="employer_rate" type="number" step="0.0001" value="${code.current_rate ? code.current_rate.employer_rate : 0}"></div>
                    <div class="form-group"><label>Per-period cap</label><input name="per_period_cap" type="number" step="0.01" value="${code.current_rate?.per_period_cap ?? ''}"></div>
                    <div class="form-group"><label>Annual cap</label><input name="annual_cap" type="number" step="0.01" value="${code.current_rate?.annual_cap ?? ''}"></div>
                    <div class="form-group"><label>Wage-base ceiling</label><input name="wage_base_ceiling" type="number" step="0.01" value="${code.current_rate?.wage_base_ceiling ?? ''}"></div>
                    <div class="form-group"><label>Employer annual cap</label><input name="employer_annual_cap" type="number" step="0.01" value="${code.current_rate?.employer_annual_cap ?? ''}"></div>
                    <div class="form-group"><label>Match limit (% of gross)</label><input name="employer_match_limit_pct" type="number" step="0.01" value="${code.current_rate?.employer_match_limit_pct ?? ''}"></div>
                    <div class="form-group"><label>Tiers JSON</label><input name="tiers_json" value="${escapeHtml(code.current_rate?.tiers_json || '')}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
                    <button type="submit" class="btn btn-primary">Add Rate</button>
                </div>
            </form>`);
    },

    async saveRate(e, codeId) {
        e.preventDefault();
        const raw = Object.fromEntries(new FormData(e.target).entries());
        const data = { effective_from: raw.effective_from, employee_rate: parseFloat(raw.employee_rate) || 0, employer_rate: parseFloat(raw.employer_rate) || 0, tiers_json: raw.tiers_json || null };
        for (const k of ['per_period_cap', 'annual_cap', 'wage_base_ceiling', 'employer_annual_cap', 'employer_match_limit_pct']) data[k] = BenefitsPage._num(raw[k]);
        try { await API.post(`/benefits/codes/${codeId}/rates`, data); toast('Rate added'); closeModal(); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },

    async deleteRate(codeId, rateId) {
        if (!confirm('Delete this dated rate?')) return;
        try { await API.del(`/benefits/codes/${codeId}/rates/${rateId}`); toast('Rate deleted'); BenefitsPage.showRates(codeId); }
        catch (err) { toast(err.message, 'error'); }
    },

    // ----------------------------------------------------------------- groups
    async _groupsTab() {
        const groups = await API.get('/benefits/groups');
        const rows = groups.map(g => `<tr>
            <td><strong>${escapeHtml(g.name)}</strong><br><span style="font-size:12px;">${escapeHtml(g.description || '')}</span></td>
            <td>${g.codes.map(c => escapeHtml(c.code)).join(', ') || '—'}</td>
            <td class="amount">${g.member_count}</td>
            <td class="actions">
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.showGroupForm(${g.id})">Codes</button>
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.showMembers(${g.id})">Members</button>
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.deleteGroup(${g.id})">Delete</button>
            </td>
        </tr>`).join('');
        return `
            <div style="margin-bottom:12px;"><button class="btn btn-primary" onclick="BenefitsPage.showGroupForm()">+ Add Group</button></div>
            <p style="font-size:12px;color:var(--gray-400);">A group is a template: every employee in it gets the group's codes. An enrollment on the employee overrides the group for that code.</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Group</th><th scope="col">Codes</th><th scope="col" class="amount">Members</th><th scope="col">Actions</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="4" style="text-align:center;color:var(--gray-400);">No groups yet</td></tr>'}</tbody>
            </table></div>`;
    },

    async showGroupForm(id = null) {
        const codes = await API.get('/benefits/codes');
        let g = { name: '', description: '', codes: [] };
        if (id) g = (await API.get('/benefits/groups')).find(x => x.id === id) || g;
        const chosen = Object.fromEntries(g.codes.map(c => [c.benefit_code_id, c]));
        const rows = codes.map(c => { const cur = chosen[c.id]; return `<tr>
            <td><label><input type="checkbox" name="code_${c.id}" ${cur ? 'checked' : ''}> ${escapeHtml(c.code)} — ${escapeHtml(c.name)}</label></td>
            <td><input name="ee_${c.id}" type="number" step="0.0001" placeholder="code rate" value="${cur?.employee_rate ?? ''}" style="width:100px;" aria-label="Employee rate override for ${escapeHtml(c.code)}"></td>
            <td><input name="er_${c.id}" type="number" step="0.0001" placeholder="code rate" value="${cur?.employer_rate ?? ''}" style="width:100px;" aria-label="Employer rate override for ${escapeHtml(c.code)}"></td>
        </tr>`; }).join('');
        openModal(id ? `Group codes — ${escapeHtml(g.name)}` : 'Add Employee Group', `
            <form onsubmit="BenefitsPage.saveGroup(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label><input name="name" required value="${escapeHtml(g.name)}"></div>
                    <div class="form-group"><label>Description</label><input name="description" value="${escapeHtml(g.description || '')}"></div>
                </div>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Code</th><th scope="col">EE rate override</th><th scope="col">ER rate override</th></tr></thead>
                    <tbody>${rows || '<tr><td colspan="3">Add benefit codes first</td></tr>'}</tbody>
                </table></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save</button>
                </div>
            </form>`);
    },

    async saveGroup(e, id) {
        e.preventDefault();
        const f = e.target;
        const codes = [];
        for (const el of f.querySelectorAll('input[type="checkbox"][name^="code_"]')) {
            if (!el.checked) continue;
            const cid = parseInt(el.name.slice(5));
            codes.push({ benefit_code_id: cid, employee_rate: BenefitsPage._num(f[`ee_${cid}`].value), employer_rate: BenefitsPage._num(f[`er_${cid}`].value) });
        }
        try {
            if (id) {
                await API.put(`/benefits/groups/${id}`, { name: f.name.value, description: f.description.value || null });
                await API.put(`/benefits/groups/${id}/codes`, codes);
            } else {
                await API.post('/benefits/groups', { name: f.name.value, description: f.description.value || null, codes });
            }
            toast('Group saved'); closeModal(); App.navigate('#/hr/benefits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showMembers(id) {
        const [emps, groups] = await Promise.all([API.get('/employees?active_only=false'), API.get('/benefits/groups')]);
        const g = groups.find(x => x.id === id);
        const rows = emps.map(e => `<label style="display:block;padding:4px 0;"><input type="checkbox" name="emp_${e.id}" ${e.employee_group_id === id ? 'checked' : ''} ${e.employee_group_id && e.employee_group_id !== id ? 'title="Currently in another group"' : ''}> ${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}${e.employee_group_id && e.employee_group_id !== id ? ' <span style="font-size:11px;color:var(--gray-400);">(in another group)</span>' : ''}</label>`).join('');
        openModal(`Members — ${escapeHtml(g ? g.name : '')}`, `
            <form onsubmit="BenefitsPage.saveMembers(event, ${id})">
                ${rows || '<p>No employees</p>'}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Members</button>
                </div>
            </form>`);
    },

    async saveMembers(e, id) {
        e.preventDefault();
        const ids = [...e.target.querySelectorAll('input[type="checkbox"]:checked')].map(el => parseInt(el.name.slice(4)));
        try { await API.put(`/benefits/groups/${id}/members`, { employee_ids: ids }); toast('Members saved'); closeModal(); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },

    async deleteGroup(id) {
        if (!confirm('Delete this group? Members keep their own enrollments but lose the group codes.')) return;
        try { await API.del(`/benefits/groups/${id}`); toast('Group deleted'); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------ enrollments
    async _enrollmentsTab() {
        const emps = await API.get('/employees?active_only=false');
        const empId = BenefitsPage._enrollEmpId;
        const opts = emps.map(e => `<option value="${e.id}" ${String(e.id) === String(empId) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`).join('');
        let body = '<div class="empty-state"><p>Select an employee</p></div>';
        if (empId) body = await BenefitsPage._enrollmentTable(empId);
        return `
            <div style="display:flex;gap:12px;align-items:end;margin-bottom:12px;flex-wrap:wrap;">
                <div class="form-group" style="max-width:280px;margin:0;"><label>Employee</label>
                    <select onchange="BenefitsPage.loadEnrollments(this.value)"><option value="">Select employee…</option>${opts}</select></div>
                <button class="btn btn-primary" onclick="BenefitsPage.showEnrollForm(BenefitsPage._enrollEmpId)">+ Enroll</button>
            </div>
            <div id="enrollments-section">${body}</div>`;
    },

    async loadEnrollments(empId) {
        BenefitsPage._enrollEmpId = empId;
        const section = $('#enrollments-section');
        if (!section) return;
        section.innerHTML = empId ? await BenefitsPage._enrollmentTable(empId) : '<div class="empty-state"><p>Select an employee</p></div>';
    },

    async _enrollmentTable(empId) {
        const [rows, resolved] = await Promise.all([
            API.get(`/benefits/enrollments?employee_id=${empId}&include_inactive=true`),
            API.get(`/benefits/employee/${empId}/resolved`),
        ]);
        const enrolled = rows.map(r => `<tr ${r.is_active ? '' : 'style="opacity:.55"'}>
            <td><strong>${escapeHtml(r.code || '')}</strong> ${escapeHtml(r.name || '')}</td>
            <td class="amount">${r.employee_rate ?? '<span style="color:var(--gray-400)">code</span>'}</td>
            <td class="amount">${r.employer_rate ?? '<span style="color:var(--gray-400)">code</span>'}</td>
            <td class="amount">${r.per_period_cap ?? '—'} / ${r.annual_cap ?? '—'}</td>
            <td class="amount">${r.balance_remaining != null ? formatCurrency(r.balance_remaining) : '—'}</td>
            <td>${r.start_date ? formatDate(r.start_date) : '—'} → ${r.end_date ? formatDate(r.end_date) : 'open'}</td>
            <td class="actions">
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.showEnrollForm(${empId}, ${r.id})">Edit</button>
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.endEnrollment(${r.id}, ${empId})">End</button>
            </td>
        </tr>`).join('');
        const eff = resolved.map(r => `<tr>
            <td class="amount">${r.sequence}</td>
            <td><strong>${escapeHtml(r.code)}</strong> ${escapeHtml(r.name)}</td>
            <td>${r.source === 'group' ? '<span class="badge badge-draft">group</span>' : '<span class="badge badge-paid">enrollment</span>'}</td>
            <td>${escapeHtml(r.calc_method)}</td>
            <td class="amount">${r.employee_rate}</td><td class="amount">${r.employer_rate}</td>
            <td class="amount">${formatCurrency(r.ytd_employee)} / ${formatCurrency(r.ytd_employer)}</td>
        </tr>`).join('');
        return `
            <h3 style="font-size:14px;margin:0 0 8px;">Enrollments</h3>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Code</th><th scope="col" class="amount">EE rate</th><th scope="col" class="amount">ER rate</th>
                <th scope="col" class="amount">Caps (period / annual)</th><th scope="col" class="amount">Balance</th><th scope="col">Dates</th><th scope="col">Actions</th></tr></thead>
                <tbody>${enrolled || '<tr><td colspan="7" style="text-align:center;color:var(--gray-400);">No enrollments — the group\'s codes still apply</td></tr>'}</tbody>
            </table></div>
            <h3 style="font-size:14px;margin:16px 0 8px;">What the next pay run applies (in sequence)</h3>
            <div class="table-container"><table>
                <thead><tr><th scope="col" class="amount">Seq</th><th scope="col">Code</th><th scope="col">Source</th><th scope="col">Method</th>
                <th scope="col" class="amount">EE rate</th><th scope="col" class="amount">ER rate</th><th scope="col" class="amount">YTD EE / ER</th></tr></thead>
                <tbody>${eff || '<tr><td colspan="7" style="text-align:center;color:var(--gray-400);">Nothing applies</td></tr>'}</tbody>
            </table></div>`;
    },

    async showEnrollForm(empId, id = null) {
        const [codes, emps] = await Promise.all([API.get('/benefits/codes'), API.get('/employees?active_only=false')]);
        let r = { employee_id: empId || '', benefit_code_id: '', employee_rate: '', employer_rate: '', per_period_cap: '', annual_cap: '', balance_remaining: '', start_date: '', end_date: '', notes: '' };
        if (id) r = (await API.get(`/benefits/enrollments?employee_id=${empId}&include_inactive=true`)).find(x => x.id === id) || r;
        openModal(id ? 'Edit Enrollment' : 'Enroll Employee', `
            <form onsubmit="BenefitsPage.saveEnrollment(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label><select name="employee_id" required ${id ? 'disabled' : ''}><option value="">Select…</option>${emps.map(e => `<option value="${e.id}" ${String(e.id) === String(r.employee_id) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`).join('')}</select></div>
                    <div class="form-group"><label>Benefit code *</label><select name="benefit_code_id" required ${id ? 'disabled' : ''}><option value="">Select…</option>${codes.map(c => `<option value="${c.id}" ${String(c.id) === String(r.benefit_code_id) ? 'selected' : ''}>${escapeHtml(c.code)} — ${escapeHtml(c.name)}</option>`).join('')}</select></div>
                    <div class="form-group"><label>Employee rate (blank = code / group)</label><input name="employee_rate" type="number" step="0.0001" value="${r.employee_rate ?? ''}"></div>
                    <div class="form-group"><label>Employer rate (blank = code / group)</label><input name="employer_rate" type="number" step="0.0001" value="${r.employer_rate ?? ''}"></div>
                    <div class="form-group"><label>Per-period cap</label><input name="per_period_cap" type="number" step="0.01" value="${r.per_period_cap ?? ''}"></div>
                    <div class="form-group"><label>Annual cap</label><input name="annual_cap" type="number" step="0.01" value="${r.annual_cap ?? ''}"></div>
                    <div class="form-group"><label>Balance remaining (loans)</label><input name="balance_remaining" type="number" step="0.01" value="${r.balance_remaining ?? ''}"></div>
                    <div class="form-group"><label>Start date</label><input name="start_date" type="date" value="${r.start_date || ''}"></div>
                    <div class="form-group"><label>End date</label><input name="end_date" type="date" value="${r.end_date || ''}"></div>
                    <div class="form-group"><label>Notes</label><input name="notes" value="${escapeHtml(r.notes || '')}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Save' : 'Enroll'}</button>
                </div>
            </form>`);
    },

    async saveEnrollment(e, id) {
        e.preventDefault();
        const f = e.target;
        const data = {};
        for (const k of ['employee_rate', 'employer_rate', 'per_period_cap', 'annual_cap', 'balance_remaining']) data[k] = BenefitsPage._num(f[k].value);
        data.start_date = f.start_date.value || null; data.end_date = f.end_date.value || null; data.notes = f.notes.value || null;
        const empId = parseInt(f.employee_id.value);
        try {
            if (id) await API.put(`/benefits/enrollments/${id}`, data);
            else await API.post('/benefits/enrollments', { ...data, employee_id: empId, benefit_code_id: parseInt(f.benefit_code_id.value) });
            toast('Enrollment saved'); closeModal();
            BenefitsPage._enrollEmpId = String(empId); App.navigate('#/hr/benefits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async endEnrollment(id, empId) {
        const when = prompt('End date (YYYY-MM-DD) to keep it on file, or leave blank to remove it:', todayISO());
        if (when === null) return;
        try {
            await API.del(`/benefits/enrollments/${id}${when ? `?end_date=${when}` : ''}`);
            toast(when ? 'Enrollment ended' : 'Enrollment removed');
            await BenefitsPage.loadEnrollments(empId);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------- remittance
    _remStart: null, _remEnd: null,

    async _remittanceTab() {
        const today = new Date();
        const start = BenefitsPage._remStart || `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-01`;
        const end = BenefitsPage._remEnd || todayISO();
        const [rep, ytd] = await Promise.all([
            API.get(`/benefits/remittance?start_date=${start}&end_date=${end}`),
            API.get(`/benefits/ytd?year=${today.getFullYear()}`),
        ]);
        const byVendor = {};
        for (const r of rep.rows) (byVendor[r.vendor_name || '(no vendor set)'] ||= { id: r.vendor_id, rows: [] }).rows.push(r);
        const sections = Object.entries(byVendor).map(([name, v]) => `
            <h3 style="font-size:14px;margin:12px 0 6px;display:flex;justify-content:space-between;align-items:center;">
                <span>${escapeHtml(name)}</span>
                ${v.id ? `<button class="btn btn-sm btn-primary" onclick="BenefitsPage.createRemittanceBill(${v.id}, '${start}', '${end}')">Create bill</button>` : '<span style="font-size:11px;color:var(--gray-400);">set a remittance vendor on the code to bill it</span>'}
            </h3>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Code</th><th scope="col" class="amount">Withheld (EE)</th><th scope="col" class="amount">Employer</th><th scope="col" class="amount">Total</th><th scope="col" class="amount">Stubs</th></tr></thead>
                <tbody>${v.rows.map(r => `<tr><td>${escapeHtml(r.code)} — ${escapeHtml(r.name)}</td><td class="amount">${formatCurrency(r.employee_amount)}</td><td class="amount">${formatCurrency(r.employer_amount)}</td><td class="amount">${formatCurrency(r.total)}</td><td class="amount">${r.stub_count}</td></tr>`).join('')}</tbody>
            </table></div>`).join('');
        const ytdRows = ytd.map(r => `<tr><td>${r.employee_id}</td><td>${escapeHtml(r.code || '')} — ${escapeHtml(r.name || '')}</td><td class="amount">${formatCurrency(r.employee_amount)}</td><td class="amount">${formatCurrency(r.employer_amount)}</td></tr>`).join('');
        return `
            <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:8px;">
                <div class="form-group" style="margin:0;"><label>Pay dates from</label><input type="date" id="rem-start" value="${start}"></div>
                <div class="form-group" style="margin:0;"><label>to</label><input type="date" id="rem-end" value="${end}"></div>
                <button class="btn btn-secondary" onclick="BenefitsPage.reloadRemittance()">Refresh</button>
                <span style="font-size:12px;color:var(--gray-400);">Processed pay runs only. Totals: withheld ${formatCurrency(rep.total_employee)} · employer ${formatCurrency(rep.total_employer)}</span>
            </div>
            ${sections || '<div class="empty-state"><p>No processed payroll with benefit codes in this range</p></div>'}
            <h3 style="font-size:14px;margin:20px 0 6px;display:flex;justify-content:space-between;align-items:center;">
                <span>Year-to-date accumulators (${today.getFullYear()})</span>
                <button class="btn btn-sm btn-secondary" onclick="BenefitsPage.rebuildYTD(${today.getFullYear()})">Rebuild from stubs</button>
            </h3>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Employee #</th><th scope="col">Code</th><th scope="col" class="amount">EE YTD</th><th scope="col" class="amount">ER YTD</th></tr></thead>
                <tbody>${ytdRows || '<tr><td colspan="4" style="text-align:center;color:var(--gray-400);">Nothing accumulated this year</td></tr>'}</tbody>
            </table></div>`;
    },

    reloadRemittance() {
        BenefitsPage._remStart = $('#rem-start').value; BenefitsPage._remEnd = $('#rem-end').value;
        App.navigate('#/hr/benefits');
    },

    async createRemittanceBill(vendorId, start, end) {
        if (!confirm('Create a vendor bill for this period\'s withholdings and employer contributions?')) return;
        try { const r = await API.post('/benefits/remittance/bill', { vendor_id: vendorId, start_date: start, end_date: end }); toast(`Bill ${r.bill_number} created for ${formatCurrency(r.total)}`); }
        catch (err) { toast(err.message, 'error'); }
    },

    async rebuildYTD(year) {
        if (!confirm(`Rebuild ${year} accumulators from the pay-stub snapshots?`)) return;
        try { const r = await API.post(`/benefits/ytd/rebuild?year=${year}`, {}); toast(`${r.rows} rows rebuilt`); App.navigate('#/hr/benefits'); }
        catch (err) { toast(err.message, 'error'); }
    },
};
