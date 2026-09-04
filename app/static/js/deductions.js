/**
 * Garnishments — court-ordered withholding with CCPA limits and priority.
 * Voluntary deductions and benefits moved to the Benefits page (benefits.js).
 */
const DeductionsPage = {
    _garnishmentEmpId: '',

    async render() {
        const emps = await API.get('/employees?active_only=false');
        const empOptions = (selected) => emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(selected) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');
        const garnEmpId = DeductionsPage._garnishmentEmpId;
        let garnishmentsHtml = '<div class="empty-state"><p>Select an employee to view garnishments</p></div>';
        if (garnEmpId) garnishmentsHtml = await DeductionsPage._buildGarnishmentsTable(garnEmpId);

        return `
            <div class="page-header">
                <h2>Garnishments</h2>
                <button class="btn btn-primary" onclick="DeductionsPage.showGarnishmentForm(DeductionsPage._garnishmentEmpId)">+ Add Garnishment</button>
            </div>
            <p style="font-size:12px;color:var(--gray-400);margin:0 0 12px;">
                Orders apply to disposable earnings under CCPA limits, in priority order. Voluntary deductions, employer-paid benefits and 401(k) matches are on the <a href="#/hr/benefits">Benefits</a> page.
            </p>
            <div class="form-group" style="max-width:280px;margin-bottom:16px;">
                <label>Employee</label>
                <select onchange="DeductionsPage.loadGarnishments(this.value)">
                    <option value="">Select employee…</option>
                    ${empOptions(garnEmpId)}
                </select>
            </div>
            <div id="garnishments-section">${garnishmentsHtml}</div>`;
    },

    async _buildGarnishmentsTable(empId) {
        const items = await API.get(`/deductions/garnishments?employee_id=${empId}`);
        if (items.length === 0) {
            return '<div class="empty-state"><p>No garnishments for this employee</p></div>';
        }
        let rows = '';
        for (const g of items) {
            rows += `<tr>
                <td class="amount">${g.priority}</td>
                <td>${escapeHtml(g.case_number || '')}</td>
                <td>${escapeHtml(g.garnishment_type.replace(/_/g, ' '))}</td>
                <td>${g.calc_method === 'percent_disposable' ? '% of disposable' : 'fixed'}</td>
                <td class="amount">${g.calc_method === 'percent_disposable' ? `${(+g.amount).toFixed(2)}%` : formatCurrency(g.amount)}</td>
                <td style="font-size:12px;">${g.garnishment_type === 'child_support' ? [g.supports_secondary_family ? 'second family' : '', g.in_arrears_12_weeks ? '12+ wks arrears' : ''].filter(Boolean).join(', ') || '—' : '—'}</td>
                <td>${g.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                <td class="actions">
                    <button class="btn btn-sm btn-secondary" onclick="DeductionsPage.deleteGarnishment(${g.id}, ${empId})">Remove</button>
                </td>
            </tr>`;
        }
        return `<div class="table-container"><table>
            <thead><tr>
                <th scope="col" class="amount">Priority</th>
                <th scope="col">Case #</th>
                <th scope="col">Order Type</th>
                <th scope="col">Method</th>
                <th scope="col" class="amount">Amount</th>
                <th scope="col">CCPA modifiers</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;
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

    async showGarnishmentForm(empId) {
        const emps = await API.get('/employees?active_only=false');
        const empOptions = emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(empId) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');
        const orderTypes = [
            ['child_support', 'Child support'], ['federal_levy', 'Federal tax levy'], ['state_tax_levy', 'State tax levy'],
            ['student_loan', 'Student loan'], ['bankruptcy', 'Bankruptcy'], ['creditor', 'Creditor garnishment'],
        ];
        const orderOptions = orderTypes.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');

        openModal('Add Garnishment', `
            <form onsubmit="DeductionsPage.saveGarnishment(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>
                            <option value="">Select employee…</option>
                            ${empOptions}
                        </select></div>
                    <div class="form-group"><label>Case Number</label>
                        <input name="case_number"></div>
                    <div class="form-group"><label>Order Type *</label>
                        <select name="garnishment_type" required>
                            ${orderOptions}
                        </select></div>
                    <div class="form-group"><label>Method</label>
                        <select name="calc_method">
                            <option value="fixed">Fixed amount per period</option>
                            <option value="percent_disposable">Percent of disposable earnings</option>
                        </select></div>
                    <div class="form-group"><label>Amount ($ or %) *</label>
                        <input name="amount" type="number" step="0.01" min="0" required value="0"></div>
                    <div class="form-group"><label>Priority (1 = first)</label>
                        <input name="priority" type="number" min="0" value="1"></div>
                </div>
                <div style="display:flex;gap:16px;margin:8px 0;">
                    <label><input type="checkbox" name="supports_secondary_family"> Supports a second family (child support)</label>
                    <label><input type="checkbox" name="in_arrears_12_weeks"> 12+ weeks in arrears</label>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Add Garnishment</button>
                </div>
            </form>`);
    },

    async saveGarnishment(e) {
        e.preventDefault();
        const f = e.target;
        const data = {
            employee_id: parseInt(f.employee_id.value) || 0,
            garnishment_type: f.garnishment_type.value,
            calc_method: f.calc_method.value,
            amount: parseFloat(f.amount.value) || 0,
            priority: parseInt(f.priority.value) || 0,
            case_number: f.case_number.value || null,
            supports_secondary_family: f.supports_secondary_family.checked,
            in_arrears_12_weeks: f.in_arrears_12_weeks.checked,
        };
        try {
            await API.post('/deductions/garnishments', data);
            toast('Garnishment added');
            closeModal();
            await DeductionsPage.loadGarnishments(data.employee_id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteGarnishment(id, empId) {
        if (!confirm('Remove this garnishment order?')) return;
        try {
            await API.del(`/deductions/garnishments/${id}`);
            toast('Garnishment removed');
            await DeductionsPage.loadGarnishments(empId);
        } catch (err) { toast(err.message, 'error'); }
    },
};
