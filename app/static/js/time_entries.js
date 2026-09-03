/**
 * Time Entries — admin view of employee time entries
 * Feature 17: Payroll basics
 */
const TimeEntriesPage = {
    _selectedEmpId: '',

    async render() {
        const [entries, emps] = await Promise.all([
            API.get('/time-entries'),
            API.get('/employees?active_only=false'),
        ]);

        const empMap = {};
        for (const e of emps) {
            empMap[e.id] = `${e.first_name} ${e.last_name}`;
        }

        const empOptions = emps.map(e =>
            `<option value="${e.id}" ${String(e.id) === String(TimeEntriesPage._selectedEmpId) ? 'selected' : ''}>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');

        let html = `
            <div class="page-header">
                <h2>Time Entries</h2>
                <button class="btn btn-primary" onclick="TimeEntriesPage.showForm()">+ Log Time</button>
            </div>
            <div class="form-group" style="max-width:280px;margin-bottom:16px;">
                <label>Filter by Employee</label>
                <select onchange="TimeEntriesPage.filterByEmployee(this.value)">
                    <option value="">All Employees</option>
                    ${empOptions}
                </select>
            </div>`;

        const filtered = TimeEntriesPage._selectedEmpId
            ? entries.filter(en => String(en.employee_id) === String(TimeEntriesPage._selectedEmpId))
            : entries;

        if (filtered.length === 0) {
            html += '<div class="empty-state"><p>No time entries found</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th>
                    <th scope="col">Employee</th>
                    <th scope="col">Job</th>
                    <th scope="col" class="amount">Regular Hrs</th>
                    <th scope="col" class="amount">OT Hrs</th>
                    <th scope="col">Description</th>
                    <th scope="col">Status</th>
                    <th scope="col">Actions</th>
                </tr></thead><tbody>`;
            for (const en of filtered) {
                const empName = escapeHtml(empMap[en.employee_id] || `Employee ${en.employee_id}`);
                const isPending = en.status === 'pending';
                html += `<tr>
                    <td>${formatDate(en.date)}</td>
                    <td>${empName}</td>
                    <td>${escapeHtml(en.job_name || '')}${en.cost_code_label ? ` <span style="font-size:10px;color:#888">${escapeHtml(en.cost_code_label)}</span>` : ''}${en.job_cost_id ? ' <span class="badge" style="font-size:9px">posted</span>' : ''}</td>
                    <td class="amount">${(en.regular_hours || 0).toFixed(2)}</td>
                    <td class="amount">${(en.overtime_hours || 0).toFixed(2)}</td>
                    <td>${escapeHtml(en.description || '')}</td>
                    <td>${statusBadge(en.status)}</td>
                    <td class="actions">
                        ${isPending ? `<button class="btn btn-sm btn-primary" onclick="TimeEntriesPage.approve(${en.id})">Approve</button>` : ''}
                        ${isPending ? `<button class="btn btn-sm btn-secondary" onclick="TimeEntriesPage.reject(${en.id})">Reject</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm() {
        const emps = await API.get('/employees?active_only=false');
        const jobGroup = await jobFormGroupHtml(null);
        await CostCodes.load();
        const codeGroup = CostCodes.any() ? `<div class="form-group"><label>Cost Code</label><select name="cost_code_id">${CostCodes.optionsHtml(null)}</select></div>` : '';
        const empOptions = emps.map(e =>
            `<option value="${e.id}">${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`
        ).join('');

        openModal('Log Time Entry', `
            <form onsubmit="TimeEntriesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Employee *</label>
                        <select name="employee_id" required>
                            <option value="">Select employee…</option>
                            ${empOptions}
                        </select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Regular Hours *</label>
                        <input name="regular_hours" type="number" step="0.01" min="0" required value="0"></div>
                    <div class="form-group"><label>Overtime Hours</label>
                        <input name="overtime_hours" type="number" step="0.01" min="0" value="0"></div>
                    <div class="form-group"><label>Double-Time Hours</label>
                        <input name="doubletime_hours" type="number" step="0.01" min="0" value="0"></div>
                    <div class="form-group"><label>Clock In (optional)</label>
                        <input name="clock_in" type="time"></div>
                    <div class="form-group"><label>Clock Out (optional)</label>
                        <input name="clock_out" type="time"></div>
                    <div class="form-group"><label>Break (minutes)</label>
                        <input name="break_minutes" type="number" min="0" value="0"></div>
                    ${jobGroup}${codeGroup}
                </div>
                <div class="form-group"><label>Description</label>
                    <textarea name="description" rows="3" style="width:100%;box-sizing:border-box;"></textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Log Time</button>
                </div>
            </form>`);
    },

    async approve(id) {
        try {
            await API.post(`/time-entries/${id}/approve?approved_by=admin`, {});
            toast('Time entry approved');
            App.navigate('#/time-entries');
        } catch (err) { toast(err.message, 'error'); }
    },

    async reject(id) {
        try {
            await API.post(`/time-entries/${id}/reject`, {});
            toast('Time entry rejected');
            App.navigate('#/time-entries');
        } catch (err) { toast(err.message, 'error'); }
    },

    filterByEmployee(empId) {
        TimeEntriesPage._selectedEmpId = empId;
        App.navigate('#/time-entries');
    },

    async save(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.employee_id = parseInt(data.employee_id) || 0;
        // The API's field names differ from the form's (hours_regular etc.)
        data.hours_regular = parseFloat(data.regular_hours) || 0;
        data.hours_overtime = parseFloat(data.overtime_hours) || 0;
        data.hours_doubletime = parseFloat(data.doubletime_hours) || 0;
        delete data.regular_hours; delete data.overtime_hours; delete data.doubletime_hours;
        data.notes = data.description || null; delete data.description;
        data.job_id = data.job_id ? parseInt(data.job_id) : null;
        data.cost_code_id = data.cost_code_id ? parseInt(data.cost_code_id) : null;
        delete data.break_minutes; delete data.clock_in; delete data.clock_out;
        try {
            await API.post('/time-entries', data);
            toast('Time entry logged');
            closeModal();
            App.navigate('#/time-entries');
        } catch (err) { toast(err.message, 'error'); }
    },

    async postToJob(id) {
        try {
            const r = await API.post(`/time-entries/${id}/post-to-job`, {});
            toast(`${r.number} posted: ${formatCurrency(r.total)}`);
            App.navigate('#/time-entries');
        } catch (err) { toast(err.message, 'error'); }
    },
};
