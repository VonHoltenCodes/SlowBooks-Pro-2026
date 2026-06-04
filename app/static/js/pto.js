/**
 * PTO — manage PTO policies and requests
 * Feature 24: Paid time off tracking
 */
const PTOPage = {
    async render() {
        const [policies, requests, accruals, employees] = await Promise.all([
            API.get('/pto/policies'),
            API.get('/pto/requests'),
            API.get('/pto/accruals'),
            API.get('/employees?active_only=true'),
        ]);
        // Lookup maps so we can render employee + policy names without N+1 calls.
        const empById = Object.fromEntries(employees.map(e => [e.id, `${e.first_name} ${e.last_name}`]));
        const polById = Object.fromEntries(policies.map(p => [p.id, p.name]));

        // --- Policies section ---
        let policiesBody = '';
        if (policies.length === 0) {
            policiesBody = '<tr><td colspan="6"><em>No policies defined yet</em></td></tr>';
        } else {
            for (const p of policies) {
                policiesBody += `<tr>
                    <td><strong>${escapeHtml(p.name)}</strong></td>
                    <td>${escapeHtml(p.pto_type)}</td>
                    <td>${escapeHtml(p.accrual_method)}</td>
                    <td class="amount">${p.accrual_rate}</td>
                    <td class="amount">${p.max_balance}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PTOPage.showPolicyForm(${p.id})">Edit</button>
                    </td>
                </tr>`;
            }
        }

        // --- Requests section ---
        let requestsBody = '';
        if (requests.length === 0) {
            requestsBody = '<tr><td colspan="7"><em>No PTO requests on file</em></td></tr>';
        } else {
            for (const r of requests) {
                const isPending = r.status === 'pending';
                const approveBtn = isPending
                    ? `<button class="btn btn-sm btn-primary" onclick="PTOPage.approveRequest(${r.id})">Approve</button>`
                    : '';
                const rejectBtn = isPending
                    ? `<button class="btn btn-sm btn-secondary" onclick="PTOPage.rejectRequest(${r.id})">Reject</button>`
                    : '';
                requestsBody += `<tr>
                    <td>${escapeHtml(r.employee_name || String(r.employee_id))}</td>
                    <td>${formatDate(r.start_date)}</td>
                    <td>${formatDate(r.end_date)}</td>
                    <td class="amount">${r.hours}</td>
                    <td>${escapeHtml(r.pto_type)}</td>
                    <td>${statusBadge(r.status)}</td>
                    <td class="actions">${approveBtn}${rejectBtn}</td>
                </tr>`;
            }
        }

        return `
            <div class="page-header">
                <h2>PTO Policies</h2>
                <button class="btn btn-primary" onclick="PTOPage.showPolicyForm()">+ Add Policy</button>
            </div>
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>Name</th>
                        <th>Type</th>
                        <th>Accrual Method</th>
                        <th class="amount">Accrual Rate</th>
                        <th class="amount">Max Balance</th>
                        <th>Actions</th>
                    </tr></thead>
                    <tbody>${policiesBody}</tbody>
                </table>
            </div>

            <div class="page-header" style="margin-top:2rem">
                <h2>Employee Accruals</h2>
                <button class="btn btn-primary" onclick="PTOPage.showAccrualForm()">+ Enroll Employee</button>
            </div>
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>Employee</th>
                        <th>Policy</th>
                        <th class="amount">Balance</th>
                        <th class="amount">Accrued YTD</th>
                        <th class="amount">Used YTD</th>
                        <th>Actions</th>
                    </tr></thead>
                    <tbody>${accruals.length === 0 ? '<tr><td colspan="6"><em>No employees enrolled in any PTO policy yet</em></td></tr>' : accruals.map(a => `
                        <tr>
                            <td>${escapeHtml(empById[a.employee_id] || `Employee ${a.employee_id}`)}</td>
                            <td>${escapeHtml(polById[a.policy_id] || `Policy ${a.policy_id}`)}</td>
                            <td class="amount">${a.balance}</td>
                            <td class="amount">${a.accrued_ytd}</td>
                            <td class="amount">${a.used_ytd}</td>
                            <td class="actions">
                                <button class="btn btn-sm btn-secondary" onclick="PTOPage.runAccrual(${a.id})">Run Accrual</button>
                            </td>
                        </tr>`).join('')}</tbody>
                </table>
            </div>

            <div class="page-header" style="margin-top:2rem">
                <h2>PTO Requests</h2>
                <button class="btn btn-primary" onclick="PTOPage.showRequestForm()">+ New Request</button>
            </div>
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>Employee</th>
                        <th>Start</th>
                        <th>End</th>
                        <th class="amount">Hours</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr></thead>
                    <tbody>${requestsBody}</tbody>
                </table>
            </div>`;
    },

    async showPolicyForm(id = null) {
        let p = { name: '', pto_type: 'vacation', accrual_method: 'per_pay_period', accrual_rate: 0, max_balance: 0 };
        if (id) p = await API.get(`/pto/policies/${id}`);

        openModal(id ? 'Edit PTO Policy' : 'Add PTO Policy', `
            <form onsubmit="PTOPage.savePolicy(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Policy Name *</label>
                        <input name="name" required value="${escapeHtml(p.name)}"></div>
                    <div class="form-group"><label>PTO Type</label>
                        <select name="pto_type">
                            <option value="vacation"  ${p.pto_type === 'vacation'  ? 'selected' : ''}>Vacation</option>
                            <option value="sick"      ${p.pto_type === 'sick'      ? 'selected' : ''}>Sick</option>
                            <option value="personal"  ${p.pto_type === 'personal'  ? 'selected' : ''}>Personal</option>
                        </select></div>
                    <div class="form-group"><label>Accrual Method</label>
                        <select name="accrual_method">
                            <option value="per_pay_period"    ${p.accrual_method === 'per_pay_period'    ? 'selected' : ''}>Per Pay Period</option>
                            <option value="per_hour_worked"   ${p.accrual_method === 'per_hour_worked'   ? 'selected' : ''}>Per Hour Worked</option>
                            <option value="annual_lump_sum"   ${p.accrual_method === 'annual_lump_sum'   ? 'selected' : ''}>Annual Lump Sum</option>
                        </select></div>
                    <div class="form-group"><label>Accrual Rate</label>
                        <input name="accrual_rate" type="number" step="0.01" value="${p.accrual_rate}"></div>
                    <div class="form-group"><label>Max Balance</label>
                        <input name="max_balance" type="number" step="0.01" value="${p.max_balance}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Add'} Policy</button>
                </div>
            </form>`);
    },

    async savePolicy(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.accrual_rate = parseFloat(data.accrual_rate) || 0;
        data.max_balance  = parseFloat(data.max_balance)  || 0;
        try {
            if (id) { await API.put(`/pto/policies/${id}`, data); toast('Policy updated'); }
            else    { await API.post('/pto/policies', data);      toast('Policy added'); }
            closeModal();
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showRequestForm() {
        let emps = [];
        try {
            emps = await API.get('/employees?active_only=true');
        } catch {
            toast('Failed to load employees', 'error');
            return;
        }

        const empOptions = emps.map(emp =>
            `<option value="${emp.id}">${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</option>`
        ).join('');

        openModal('New PTO Request', `
            <form onsubmit="PTOPage.saveRequest(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>
                            <option value="">— Select Employee —</option>
                            ${empOptions}
                        </select></div>
                    <div class="form-group"><label>PTO Type</label>
                        <select name="pto_type">
                            <option value="vacation">Vacation</option>
                            <option value="sick">Sick</option>
                            <option value="personal">Personal</option>
                        </select></div>
                    <div class="form-group"><label>Start Date *</label>
                        <input name="start_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>End Date *</label>
                        <input name="end_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Hours</label>
                        <input name="hours" type="number" step="0.5" min="0" value="8"></div>
                    <div class="form-group"><label>Notes</label>
                        <input name="notes" value=""></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Submit Request</button>
                </div>
            </form>`);
    },

    async saveRequest(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.employee_id = parseInt(data.employee_id) || 0;
        data.hours       = parseFloat(data.hours) || 0;
        if (!data.notes) delete data.notes;
        try {
            await API.post('/pto/requests', data);
            toast('PTO request submitted');
            closeModal();
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },

    async approveRequest(id) {
        try {
            await API.post(`/pto/requests/${id}/approve`, {});
            toast('Request approved');
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },

    async rejectRequest(id) {
        try {
            await API.post(`/pto/requests/${id}/reject`, {});
            toast('Request rejected');
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showAccrualForm() {
        const [policies, employees] = await Promise.all([
            API.get('/pto/policies'),
            API.get('/employees?active_only=true'),
        ]);
        if (policies.length === 0) { toast('Define a PTO policy first', 'error'); return; }
        if (employees.length === 0) { toast('Add an employee first', 'error'); return; }
        const polOpts = policies.map(p => `<option value="${p.id}">${escapeHtml(p.name)} (${escapeHtml(p.pto_type)})</option>`).join('');
        const empOpts = employees.map(e => `<option value="${e.id}">${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`).join('');
        openModal('Enroll Employee in PTO Policy', `
            <form onsubmit="PTOPage.saveAccrual(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>${empOpts}</select></div>
                    <div class="form-group"><label>Policy *</label>
                        <select name="policy_id" required>${polOpts}</select></div>
                    <div class="form-group"><label>Starting Balance (hours)</label>
                        <input name="balance" type="number" step="0.01" value="0"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Enroll</button>
                </div>
            </form>`);
    },

    async saveAccrual(e) {
        e.preventDefault();
        const f = e.target;
        const body = {
            employee_id: parseInt(f.employee_id.value, 10),
            policy_id: parseInt(f.policy_id.value, 10),
            balance: parseFloat(f.balance.value) || 0,
        };
        try {
            await API.post('/pto/accruals', body);
            toast('Employee enrolled');
            closeModal();
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },

    async runAccrual(id) {
        // Bonus: prompt for hours worked, since per-hour-worked policies need it.
        const hoursStr = prompt('Hours worked this period (only used by "per_hour_worked" policies — leave blank for fixed accruals):', '');
        const body = { hours_worked: parseFloat(hoursStr) || 0 };
        try {
            await API.post(`/pto/accruals/${id}/accrue`, body);
            toast('Accrual applied');
            App.navigate('#/hr/pto');
        } catch (err) { toast(err.message, 'error'); }
    },
};
