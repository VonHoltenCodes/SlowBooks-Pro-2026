/**
 * Employees — NZ payroll setup
 */
const EmployeesPage = {
    async render() {
        const emps = await API.get('/employees');
        let html = `
            <div class="page-header">
                <h2>Employees</h2>
                <button class="btn btn-primary" onclick="EmployeesPage.showForm()">+ Add Employee</button>
            </div>`;

        if (emps.length === 0) {
            html += '<div class="empty-state"><p>No employees added yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>Name</th><th>Tax Code</th><th>Pay Frequency</th><th class="amount">Rate</th><th>Status</th><th>Actions</th></tr></thead><tbody>`;
            for (const e of emps) {
                html += `<tr>
                    <td><strong>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</strong></td>
                    <td>${escapeHtml(e.tax_code || '')}</td>
                    <td>${escapeHtml(e.pay_frequency || '')}</td>
                    <td class="amount">${formatCurrency(e.pay_rate)}${e.pay_type==='hourly'?'/hr':'/yr'}</td>
                    <td>${e.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="EmployeesPage.showForm(${e.id})">Edit</button>
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm(id = null) {
        let emp = {
            first_name: '', last_name: '', ird_number: '', pay_type: 'hourly', pay_rate: 0,
            tax_code: 'M', kiwisaver_enrolled: false, kiwisaver_rate: '0.0300',
            student_loan: false, child_support: false, esct_rate: '0.0000',
            pay_frequency: 'fortnightly', start_date: todayISO(), end_date: ''
        };
        if (id) emp = await API.get(`/employees/${id}`);

        openModal(id ? 'Edit Employee' : 'Add Employee', `
            <form onsubmit="EmployeesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>First Name *</label>
                        <input name="first_name" required value="${escapeHtml(emp.first_name)}"></div>
                    <div class="form-group"><label>Last Name *</label>
                        <input name="last_name" required value="${escapeHtml(emp.last_name)}"></div>
                    <div class="form-group"><label>IRD Number</label>
                        <input name="ird_number" value="${escapeHtml(emp.ird_number || '')}"></div>
                    <div class="form-group"><label>Pay Type</label>
                        <select name="pay_type">
                            <option value="hourly" ${emp.pay_type==='hourly'?'selected':''}>Hourly</option>
                            <option value="salary" ${emp.pay_type==='salary'?'selected':''}>Salary</option>
                        </select></div>
                    <div class="form-group"><label>Pay Rate</label>
                        <input name="pay_rate" type="number" step="0.01" value="${emp.pay_rate}"></div>
                    <div class="form-group"><label>Tax Code</label>
                        <input name="tax_code" value="${escapeHtml(emp.tax_code || 'M')}"></div>
                    <div class="form-group"><label>KiwiSaver Enrolled</label>
                        <select name="kiwisaver_enrolled">
                            <option value="false" ${!emp.kiwisaver_enrolled?'selected':''}>No</option>
                            <option value="true" ${emp.kiwisaver_enrolled?'selected':''}>Yes</option>
                        </select></div>
                    <div class="form-group"><label>KiwiSaver Rate</label>
                        <input name="kiwisaver_rate" type="number" step="0.0001" value="${emp.kiwisaver_rate || '0.0300'}"></div>
                    <div class="form-group"><label>Student Loan</label>
                        <select name="student_loan">
                            <option value="false" ${!emp.student_loan?'selected':''}>No</option>
                            <option value="true" ${emp.student_loan?'selected':''}>Yes</option>
                        </select></div>
                    <div class="form-group"><label>Child Support</label>
                        <select name="child_support">
                            <option value="false" ${!emp.child_support?'selected':''}>No</option>
                            <option value="true" ${emp.child_support?'selected':''}>Yes</option>
                        </select></div>
                    <div class="form-group"><label>ESCT Rate</label>
                        <input name="esct_rate" type="number" step="0.0001" value="${emp.esct_rate || '0.0000'}"></div>
                    <div class="form-group"><label>Pay Frequency</label>
                        <select name="pay_frequency">
                            <option value="weekly" ${emp.pay_frequency==='weekly'?'selected':''}>Weekly</option>
                            <option value="fortnightly" ${emp.pay_frequency==='fortnightly'?'selected':''}>Fortnightly</option>
                            <option value="monthly" ${emp.pay_frequency==='monthly'?'selected':''}>Monthly</option>
                        </select></div>
                    <div class="form-group"><label>Start Date</label>
                        <input name="start_date" type="date" value="${emp.start_date || ''}"></div>
                    <div class="form-group"><label>End Date</label>
                        <input name="end_date" type="date" value="${emp.end_date || ''}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Add'} Employee</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.pay_rate = parseFloat(data.pay_rate) || 0;
        data.kiwisaver_enrolled = data.kiwisaver_enrolled === 'true';
        data.student_loan = data.student_loan === 'true';
        data.child_support = data.child_support === 'true';
        if (!data.start_date) delete data.start_date;
        if (!data.end_date) delete data.end_date;
        try {
            if (id) { await API.put(`/employees/${id}`, data); toast('Employee updated'); }
            else { await API.post('/employees', data); toast('Employee added'); }
            closeModal();
            App.navigate('#/employees');
        } catch (err) { toast(err.message, 'error'); }
    },
};
