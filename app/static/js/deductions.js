/**
 * Deductions — manage deduction types, per-employee deductions, and garnishments
 * Feature 17: Payroll basics
 */
const DeductionsPage = {
    _deductionEmpId: '',
    _garnishmentEmpId: '',

    async render() {
        const [types, emps] = await Promise.all([
            API.get('/deductions/types'),
            API.get('/employees?active_only=false'),
        ]);

        const empOptions = (selected) => emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(selected) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');

        // --- Section 1: Deduction Types ---
        let typesRows = '';
        if (types.length === 0) {
            typesRows = '<tr><td colspan="5" style="text-align:center;color:#888;">No deduction types defined</td></tr>';
        } else {
            for (const t of types) {
                typesRows += `<tr>
                    <td>${escapeHtml(t.name)}</td>
                    <td>${escapeHtml(t.deduction_category)}</td>
                    <td>${t.is_pretax ? 'Pre-tax' : 'Post-tax'}</td>
                    <td class="amount">${(t.employee_portion_pct || 0).toFixed(2)}%</td>
                    <td class="amount">${(t.employer_portion_pct || 0).toFixed(2)}%</td>
                </tr>`;
            }
        }

        // --- Section 2: Employee Deductions ---
        const dedEmpId = DeductionsPage._deductionEmpId;
        let deductionsHtml = '<div class="empty-state"><p>Select an employee to view deductions</p></div>';
        if (dedEmpId) {
            deductionsHtml = await DeductionsPage._buildDeductionsTable(dedEmpId);
        }

        // --- Section 3: Garnishments ---
        const garnEmpId = DeductionsPage._garnishmentEmpId;
        let garnishmentsHtml = '<div class="empty-state"><p>Select an employee to view garnishments</p></div>';
        if (garnEmpId) {
            garnishmentsHtml = await DeductionsPage._buildGarnishmentsTable(garnEmpId);
        }

        const html = `
            <div class="page-header">
                <h2>Deductions</h2>
            </div>

            <div style="margin-bottom:32px;">
                <div class="page-header" style="margin-bottom:12px;">
                    <h3 style="margin:0;">Deduction Types</h3>
                    <button class="btn btn-primary" onclick="DeductionsPage.showTypeForm()">+ Add Type</button>
                </div>
                <div class="table-container"><table>
                    <thead><tr>
                        <th>Name</th>
                        <th>Category</th>
                        <th>Tax Treatment</th>
                        <th class="amount">Employee %</th>
                        <th class="amount">Employer %</th>
                    </tr></thead>
                    <tbody>${typesRows}</tbody>
                </table></div>
            </div>

            <div style="margin-bottom:32px;">
                <div class="page-header" style="margin-bottom:12px;">
                    <h3 style="margin:0;">Employee Deductions</h3>
                    <button class="btn btn-primary" onclick="DeductionsPage.showDeductionForm(DeductionsPage._deductionEmpId)">+ Add Deduction</button>
                </div>
                <div class="form-group" style="max-width:280px;margin-bottom:16px;">
                    <label>Employee</label>
                    <select onchange="DeductionsPage.loadDeductions(this.value)">
                        <option value="">Select employee…</option>
                        ${empOptions(dedEmpId)}
                    </select>
                </div>
                <div id="deductions-section">${deductionsHtml}</div>
            </div>

            <div>
                <div class="page-header" style="margin-bottom:12px;">
                    <h3 style="margin:0;">Garnishments</h3>
                    <button class="btn btn-primary" onclick="DeductionsPage.showGarnishmentForm(DeductionsPage._garnishmentEmpId)">+ Add Garnishment</button>
                </div>
                <div class="form-group" style="max-width:280px;margin-bottom:16px;">
                    <label>Employee</label>
                    <select onchange="DeductionsPage.loadGarnishments(this.value)">
                        <option value="">Select employee…</option>
                        ${empOptions(garnEmpId)}
                    </select>
                </div>
                <div id="garnishments-section">${garnishmentsHtml}</div>
            </div>`;

        return html;
    },

    async _buildDeductionsTable(empId) {
        const items = await API.get(`/deductions/employee/${empId}`);
        if (items.length === 0) {
            return '<div class="empty-state"><p>No deductions for this employee</p></div>';
        }
        let rows = '';
        for (const d of items) {
            rows += `<tr>
                <td>${escapeHtml(d.type_name || `Type ${d.deduction_type_id}`)}</td>
                <td class="amount">${formatCurrency(d.employee_amount)}</td>
                <td class="amount">${formatCurrency(d.employer_amount)}</td>
                <td>${formatDate(d.effective_date)}</td>
                <td>${d.end_date ? formatDate(d.end_date) : '—'}</td>
                <td>${d.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                <td class="actions">
                    <button class="btn btn-sm btn-secondary" onclick="DeductionsPage.deleteDeduction(${d.id}, ${empId})">Remove</button>
                </td>
            </tr>`;
        }
        return `<div class="table-container"><table>
            <thead><tr>
                <th>Type</th>
                <th class="amount">Employee Amt</th>
                <th class="amount">Employer Amt</th>
                <th>Effective</th>
                <th>End Date</th>
                <th>Status</th>
                <th>Actions</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;
    },

    async _buildGarnishmentsTable(empId) {
        const items = await API.get(`/deductions/garnishments?employee_id=${empId}`);
        if (items.length === 0) {
            return '<div class="empty-state"><p>No garnishments for this employee</p></div>';
        }
        let rows = '';
        for (const g of items) {
            rows += `<tr>
                <td>${escapeHtml(g.case_number)}</td>
                <td>${escapeHtml(g.order_type)}</td>
                <td class="amount">${formatCurrency(g.withholding_amount)}</td>
                <td class="amount">${(g.max_pct_disposable || 0).toFixed(1)}%</td>
                <td>${escapeHtml(g.issuing_state)}</td>
                <td>${escapeHtml(g.issuing_agency || '')}</td>
                <td>${formatDate(g.effective_date)}</td>
                <td>${g.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
            </tr>`;
        }
        return `<div class="table-container"><table>
            <thead><tr>
                <th>Case #</th>
                <th>Order Type</th>
                <th class="amount">Amount</th>
                <th class="amount">Max % Disposable</th>
                <th>State</th>
                <th>Agency</th>
                <th>Effective</th>
                <th>Status</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;
    },

    async loadDeductions(empId) {
        DeductionsPage._deductionEmpId = empId;
        const section = $('#deductions-section');
        if (!section) return;
        if (!empId) {
            section.innerHTML = '<div class="empty-state"><p>Select an employee to view deductions</p></div>';
            return;
        }
        try {
            section.innerHTML = await DeductionsPage._buildDeductionsTable(empId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadGarnishments(empId) {
        DeductionsPage._garnishmentEmpId = empId;
        const section = $('#garnishments-section');
        if (!section) return;
        if (!empId) {
            section.innerHTML = '<div class="empty-state"><p>Select an employee to view garnishments</p></div>';
            return;
        }
        try {
            section.innerHTML = await DeductionsPage._buildGarnishmentsTable(empId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTypeForm() {
        const categories = [
            'medical', 'dental', 'vision', 'life_insurance', 'retirement_401k',
            'hsa', 'fsa', 'other_pretax', 'other_posttax',
        ];
        const catOptions = categories.map(c => `<option value="${c}">${c}</option>`).join('');

        openModal('Add Deduction Type', `
            <form onsubmit="DeductionsPage.saveType(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Category *</label>
                        <select name="deduction_category" required>
                            ${catOptions}
                        </select></div>
                    <div class="form-group"><label>GL Account Number</label>
                        <input name="gl_account_number"></div>
                    <div class="form-group"><label>Employee Portion %</label>
                        <input name="employee_portion_pct" type="number" step="0.01" min="0" max="100" value="0"></div>
                    <div class="form-group"><label>Employer Portion %</label>
                        <input name="employer_portion_pct" type="number" step="0.01" min="0" max="100" value="0"></div>
                    <div class="form-group" style="display:flex;align-items:center;gap:8px;padding-top:24px;">
                        <input name="is_pretax" type="checkbox" id="chk-pretax" value="1">
                        <label for="chk-pretax" style="margin:0;">Pre-tax deduction</label>
                    </div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Add Type</button>
                </div>
            </form>`);
    },

    async showDeductionForm(empId) {
        const [types, emps] = await Promise.all([
            API.get('/deductions/types'),
            API.get('/employees?active_only=false'),
        ]);
        const empOptions = emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(empId) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');
        const typeOptions = types.map(t =>
            `<option value="${t.id}">${escapeHtml(t.name)}</option>`
        ).join('');

        openModal('Add Employee Deduction', `
            <form onsubmit="DeductionsPage.saveDeduction(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>
                            <option value="">Select employee…</option>
                            ${empOptions}
                        </select></div>
                    <div class="form-group"><label>Deduction Type *</label>
                        <select name="deduction_type_id" required>
                            <option value="">Select type…</option>
                            ${typeOptions}
                        </select></div>
                    <div class="form-group"><label>Employee Amount *</label>
                        <input name="employee_amount" type="number" step="0.01" min="0" required value="0"></div>
                    <div class="form-group"><label>Employer Amount</label>
                        <input name="employer_amount" type="number" step="0.01" min="0" value="0"></div>
                    <div class="form-group"><label>Effective Date *</label>
                        <input name="effective_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Priority</label>
                        <input name="priority" type="number" min="1" value="1"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Add Deduction</button>
                </div>
            </form>`);
    },

    async showGarnishmentForm(empId) {
        const emps = await API.get('/employees?active_only=false');
        const empOptions = emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(empId) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');
        const orderTypes = ['child_support', 'creditor_garnishment', 'tax_levy', 'student_loan', 'other'];
        const orderOptions = orderTypes.map(o => `<option value="${o}">${o}</option>`).join('');

        openModal('Add Garnishment', `
            <form onsubmit="DeductionsPage.saveGarnishment(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>
                            <option value="">Select employee…</option>
                            ${empOptions}
                        </select></div>
                    <div class="form-group"><label>Case Number *</label>
                        <input name="case_number" required></div>
                    <div class="form-group"><label>Order Type *</label>
                        <select name="order_type" required>
                            ${orderOptions}
                        </select></div>
                    <div class="form-group"><label>Withholding Amount *</label>
                        <input name="withholding_amount" type="number" step="0.01" min="0" required value="0"></div>
                    <div class="form-group"><label>Max % of Disposable Income</label>
                        <input name="max_pct_disposable" type="number" step="0.01" min="0" max="100" value="0"></div>
                    <div class="form-group"><label>Issuing State (2-char) *</label>
                        <input name="issuing_state" maxlength="2" required style="text-transform:uppercase;"></div>
                    <div class="form-group"><label>Issuing Agency</label>
                        <input name="issuing_agency"></div>
                    <div class="form-group"><label>Effective Date *</label>
                        <input name="effective_date" type="date" required value="${todayISO()}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Add Garnishment</button>
                </div>
            </form>`);
    },

    async deleteDeduction(id, empId) {
        if (!confirm('Remove this deduction?')) return;
        try {
            await API.del(`/deductions/employee/${id}`);
            toast('Deduction removed');
            await DeductionsPage.loadDeductions(empId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveType(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.is_pretax = !!data.is_pretax;
        data.employee_portion_pct = parseFloat(data.employee_portion_pct) || 0;
        data.employer_portion_pct = parseFloat(data.employer_portion_pct) || 0;
        try {
            await API.post('/deductions/types', data);
            toast('Deduction type added');
            closeModal();
            App.navigate('#/deductions');
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveDeduction(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.employee_id = parseInt(data.employee_id) || 0;
        data.deduction_type_id = parseInt(data.deduction_type_id) || 0;
        data.employee_amount = parseFloat(data.employee_amount) || 0;
        data.employer_amount = parseFloat(data.employer_amount) || 0;
        data.priority = parseInt(data.priority) || 1;
        data.is_active = true;
        try {
            await API.post('/deductions/employee', data);
            toast('Deduction added');
            closeModal();
            await DeductionsPage.loadDeductions(data.employee_id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveGarnishment(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.employee_id = parseInt(data.employee_id) || 0;
        data.withholding_amount = parseFloat(data.withholding_amount) || 0;
        data.max_pct_disposable = parseFloat(data.max_pct_disposable) || 0;
        data.issuing_state = (data.issuing_state || '').toUpperCase();
        try {
            await API.post('/deductions/garnishments', data);
            toast('Garnishment added');
            closeModal();
            await DeductionsPage.loadGarnishments(data.employee_id);
        } catch (err) { toast(err.message, 'error'); }
    },
};
