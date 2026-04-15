/**
 * Payroll — NZ draft pay runs with PAYE calculations and posting.
 */
const PayrollPage = {
    async render() {
        const canCreateRun = !App.hasPermission || App.hasPermission('payroll.create');
        const canProcessRun = !App.hasPermission || App.hasPermission('payroll.process');
        const canViewPayslips = !App.hasPermission || App.hasPermission('payroll.payslips.view');
        const canEmailPayslips = !App.hasPermission || App.hasPermission('payroll.payslips.email');
        const canExportFiling = !App.hasPermission || App.hasPermission('payroll.filing.export');
        const [runs, employees] = await Promise.all([
            API.get('/payroll'),
            API.get('/employees?active_only=true'),
        ]);
        const filingHistoryByRun = new Map();
        await Promise.all(runs.map(async (run) => {
            if (String(run.status || '').toLowerCase() !== 'processed') {
                filingHistoryByRun.set(run.id, []);
                return;
            }
            try {
                filingHistoryByRun.set(run.id, await API.get(`/payroll/${run.id}/filing/history`));
            } catch (_err) {
                filingHistoryByRun.set(run.id, []);
            }
        }));

        let html = `
            <div class="page-header">
                <h2>Payroll</h2>
                ${canCreateRun ? `<button class="btn btn-primary" onclick="PayrollPage.showForm()">New Pay Run</button>` : ''}
            </div>
            <div style="background:#e0f2fe;border:1px solid #7dd3fc;padding:10px 12px;margin-bottom:12px;font-size:12px;color:#0c4a6e;">
                <strong>NZ payroll setup is ready.</strong> PAYE calculations, KiwiSaver deductions, student loan deductions, ESCT, payslips, Employment Information export, starter/leaver filing, and posting now run through the NZ payroll workflow.
            </div>
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">
                ${employees.length} active employee${employees.length === 1 ? '' : 's'} available for payroll.
            </div>`;

        if (!runs.length) {
            html += `<div class="empty-state"><p>No pay runs yet. Create a draft run to calculate PAYE and review totals before processing.</p></div>`;
            return html;
        }

        html += `<div class="table-container"><table>
            <thead><tr><th>Pay Date</th><th>Tax Year</th><th>Status</th><th class="amount">Gross</th><th class="amount">Net</th><th class="amount">Deductions</th><th>Filing Status</th><th>Actions</th></tr></thead>
            <tbody>`;
        for (const run of runs) {
            const status = String(run.status || '').toLowerCase();
            const latestFiling = (filingHistoryByRun.get(run.id) || [])[0];
            html += `<tr>
                <td>${formatDate(run.pay_date)}</td>
                <td>${escapeHtml(run.tax_year)}</td>
                <td>${status === 'processed' ? '<span class="badge badge-paid">Processed</span>' : '<span class="badge badge-draft">Draft</span>'}</td>
                <td class="amount">${formatCurrency(run.total_gross)}</td>
                <td class="amount">${formatCurrency(run.total_net)}</td>
                <td class="amount">${formatCurrency(run.total_taxes)}</td>
                <td style="font-size:10px;">${PayrollPage.filingSummary(latestFiling)}</td>
                <td class="actions">
                    <button class="btn btn-sm btn-secondary" onclick="PayrollPage.viewRun(${run.id})">View</button>
                    ${status === 'draft'
                        ? `${canProcessRun ? `<button class="btn btn-sm btn-primary" onclick="PayrollPage.processRun(${run.id})">Process</button>` : ''}`
                        : `${canViewPayslips ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.viewRun(${run.id})">Payslips</button>` : ''}
                           ${canExportFiling ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.exportEmploymentInformation(${run.id})">Employment Information</button>` : ''}
                           ${canExportFiling && latestFiling && latestFiling.status === 'generated' ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.markFilingStatus(${run.id}, ${latestFiling.id}, 'filed')">Mark Filed</button>` : ''}
                           ${canExportFiling && latestFiling && latestFiling.status === 'filed' && latestFiling.changed_since_source ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.markFilingStatus(${run.id}, ${latestFiling.id}, 'amended')">Mark Amended</button>` : ''}`}
                </td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
        return html;
    },

    filingSummary(record) {
        if (!record) return '';
        return `<div>Employment Information ${escapeHtml(record.status)}</div>${record.changed_since_source ? '<div style="color:#9d1f1f;">Changed since filing</div>' : ''}`;
    },

    async showForm() {
        const employees = await API.get('/employees?active_only=true');
        if (!employees.length) {
            toast('Add an active employee before creating a pay run', 'error');
            return;
        }

        const today = todayISO();
        const rows = employees.map(emp => `
            <tr>
                <td>
                    <label style="display:flex; align-items:center; gap:6px;">
                        <input type="checkbox" name="include_${emp.id}" checked>
                        <span>${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</span>
                    </label>
                </td>
                <td>${escapeHtml(emp.pay_type)}</td>
                <td>${escapeHtml(emp.tax_code || '')}</td>
                <td>${escapeHtml(emp.pay_frequency || '')}</td>
                <td><input name="hours_${emp.id}" type="number" step="0.01" min="0" value="${emp.pay_type === 'hourly' ? '0' : ''}" ${emp.pay_type === 'salary' ? 'disabled' : ''}></td>
            </tr>
        `).join('');

        openModal('New Pay Run', `
            <form onsubmit="PayrollPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Period Start</label><input name="period_start" type="date" value="${today}" required></div>
                    <div class="form-group"><label>Period End</label><input name="period_end" type="date" value="${today}" required></div>
                    <div class="form-group"><label>Pay Date</label><input name="pay_date" type="date" value="${today}" required></div>
                </div>
                <div class="table-container" style="margin-top:12px;">
                    <table>
                        <thead><tr><th>Employee</th><th>Pay Type</th><th>Tax Code</th><th>Frequency</th><th>Hours</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <p style="font-size:11px; color:var(--text-muted); margin-top:10px;">Salary employees use their annual salary and pay frequency. Enter hours for hourly staff.</p>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Draft Run</button>
                </div>
            </form>`);
    },

    async save(e) {
        e.preventDefault();
        const form = new FormData(e.target);
        const employees = await API.get('/employees?active_only=true');
        const stubs = employees
            .filter(emp => form.get(`include_${emp.id}`))
            .map(emp => ({
                employee_id: emp.id,
                ...(emp.pay_type === 'hourly' ? { hours: parseFloat(form.get(`hours_${emp.id}`) || '0') || 0 } : {}),
            }));
        if (!stubs.length) {
            toast('Select at least one employee', 'error');
            return;
        }

        try {
            await API.post('/payroll', {
                period_start: form.get('period_start'),
                period_end: form.get('period_end'),
                pay_date: form.get('pay_date'),
                stubs,
            });
            closeModal();
            toast('Draft pay run created');
            App.navigate('#/payroll');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async processRun(id) {
        if (!confirm('Process this pay run and post the payroll journal?')) return;
        try {
            await API.post(`/payroll/${id}/process`);
            toast('Pay run processed');
            App.navigate('#/payroll');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async viewRun(id) {
        try {
            const canViewPayslips = !App.hasPermission || App.hasPermission('payroll.payslips.view');
            const canEmailPayslips = !App.hasPermission || App.hasPermission('payroll.payslips.email');
            const run = await API.get(`/payroll/${id}`);
            const rows = (run.stubs || []).map(stub => `
                <tr>
                    <td>${escapeHtml(stub.employee_name || '')}</td>
                    <td>${escapeHtml(stub.tax_code || '')}</td>
                    <td class="amount">${formatCurrency(stub.gross_pay)}</td>
                    <td class="amount">${formatCurrency(stub.paye)}</td>
                    <td class="amount">${formatCurrency(stub.acc_earners_levy)}</td>
                    <td class="amount">${formatCurrency(stub.student_loan_deduction)}</td>
                    <td class="amount">${formatCurrency(stub.kiwisaver_employee_deduction)}</td>
                    <td class="amount">${formatCurrency(stub.child_support_deduction)}</td>
                    <td class="amount">${formatCurrency(stub.net_pay)}</td>
                    <td>${String(run.status || '').toLowerCase() === 'processed' ? `${canViewPayslips ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.openPayslip(${run.id}, ${stub.employee_id})">Print / PDF</button>` : ''} ${canEmailPayslips ? `<button class="btn btn-sm btn-secondary" onclick="PayrollPage.emailPayslip(${run.id}, ${stub.employee_id})">Email</button>` : ''}` : ''}</td>
                </tr>
            `).join('');
            openModal(`Pay Run ${id}`, `
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:10px;">
                    Pay Date: <strong>${formatDate(run.pay_date)}</strong>
                    · Tax Year: <strong>${escapeHtml(run.tax_year)}</strong>
                    · Status: <strong>${escapeHtml(run.status)}</strong>
                </div>
                <div class="table-container">
                    <table>
                        <thead><tr><th>Employee</th><th>Tax Code</th><th class="amount">Gross</th><th class="amount">PAYE</th><th class="amount">ACC</th><th class="amount">Student Loan</th><th class="amount">KiwiSaver</th><th class="amount">Child Support</th><th class="amount">Net</th><th>Actions</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div style="margin-top:10px; font-size:11px; color:var(--text-muted);">
                    Employer KiwiSaver: <strong>${formatCurrency(run.total_employer_kiwisaver || 0)}</strong>
                    · ESCT: <strong>${formatCurrency(run.total_esct || 0)}</strong>
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Close</button>
                </div>`);
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    openPayslip(runId, employeeId) {
        API.open(`/payroll/${runId}/payslips/${employeeId}/pdf`, `payslip-${employeeId}.pdf`);
    },

    emailPayslip(runId, employeeId) {
        App.showDocumentEmailModal({
            title: `Email Payslip for Pay Run ${runId}`,
            endpoint: `/payroll/${runId}/payslips/${employeeId}/email`,
            recipient: '',
            defaultSubject: `Payslip for pay run ${runId}`,
            successMessage: 'Payslip emailed',
        });
    },

    exportEmploymentInformation(runId) {
        API.open(`/payroll/${runId}/employment-information/export`, `employment-information-${runId}.csv`);
    },

    async markFilingStatus(runId, auditId, status) {
        try {
            await API.post(`/payroll/${runId}/filing/${auditId}/status`, { status });
            toast(`Filing marked ${status}`);
            App.navigate('#/payroll');
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};
