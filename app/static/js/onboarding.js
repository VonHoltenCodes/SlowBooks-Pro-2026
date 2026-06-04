/**
 * Onboarding — admin view of every employee's onboarding checklist
 * Feature 23: New-hire onboarding workflow
 */
const OnboardingPage = {
    _taskLabels: {
        w4:                    'Form W-4',
        i9_section1:           'I-9 Section 1 (Employee)',
        i9_section2:           'I-9 Section 2 (Employer)',
        everify:               'E-Verify',
        direct_deposit:        'Direct Deposit Auth',
        state_new_hire_report: 'State New-Hire Report',
        policy_acknowledgment: 'Policy Acknowledgment',
        emergency_contact:     'Emergency Contact',
    },

    _signableTasks: new Set(['w4', 'i9_section1', 'direct_deposit', 'policy_acknowledgment']),

    async render() {
        const emps = await API.get('/employees?active_only=false');
        let html = `
            <div class="page-header">
                <h2>Onboarding Checklists</h2>
            </div>`;

        if (emps.length === 0) {
            html += '<div class="empty-state"><p>No employees found</p></div>';
            return html;
        }

        // Fetch all checklists in parallel
        const checklists = await Promise.all(
            emps.map(emp => API.get(`/onboarding/${emp.id}`).catch(() => null))
        );

        html += `<div class="table-container"><table>
            <thead><tr>
                <th>Name</th>
                <th class="amount">Complete</th>
                <th class="amount">Total</th>
                <th class="amount">%</th>
                <th>Actions</th>
            </tr></thead><tbody>`;

        for (let i = 0; i < emps.length; i++) {
            const emp = emps[i];
            const cl  = checklists[i];
            const complete = cl ? cl.complete        : 0;
            const total    = cl ? cl.total           : 0;
            const pct      = cl ? cl.percent_complete : 0;
            html += `<tr>
                <td><strong>${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}</strong></td>
                <td class="amount">${complete}</td>
                <td class="amount">${total}</td>
                <td class="amount">${pct}%</td>
                <td class="actions">
                    <button class="btn btn-sm btn-secondary" onclick="OnboardingPage.viewChecklist(${emp.id})">View Checklist</button>
                </td>
            </tr>`;
        }

        html += '</tbody></table></div>';
        return html;
    },

    async viewChecklist(empId) {
        let cl;
        try {
            cl = await API.get(`/onboarding/${empId}`);
        } catch {
            toast('Failed to load checklist', 'error');
            return;
        }

        // Seed default tasks if none exist yet
        if (!cl.tasks || cl.tasks.length === 0) {
            try {
                cl = await API.post(`/onboarding/${empId}/seed`, {});
            } catch {
                toast('Failed to seed tasks', 'error');
                return;
            }
        }

        const tasks = cl.tasks || [];
        let rows = '';
        for (const t of tasks) {
            const label    = escapeHtml(this._taskLabels[t.task_type] || t.task_type);
            const isDone   = t.status === 'complete';
            const signable = this._signableTasks.has(t.task_type);
            const signedChk = signable
                ? `<label style="font-size:.85em;white-space:nowrap">
                       <input type="checkbox" ${t.signed ? 'checked' : ''}
                           onchange="OnboardingPage.signTask(${t.id}, this.checked, ${empId})">
                       Signed
                   </label>`
                : '';
            rows += `<tr>
                <td>${label}</td>
                <td>${statusBadge(t.status)}</td>
                <td>${signedChk}</td>
                <td class="actions">
                    <button class="btn btn-sm btn-primary" ${isDone ? 'disabled' : ''}
                        onclick="OnboardingPage.completeTask(${t.id}, ${empId})">
                        ${isDone ? 'Done' : 'Complete'}
                    </button>
                </td>
            </tr>`;
        }

        const html = `
            <div style="margin-bottom:.5rem">
                <strong>${escapeHtml(cl.employee_name || '')}</strong>
                &nbsp;—&nbsp;${cl.complete} / ${cl.total} tasks complete (${cl.percent_complete}%)
            </div>
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>Task</th><th>Status</th><th>Signed</th><th>Actions</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div class="form-actions" style="margin-top:1rem">
                <button class="btn btn-secondary" onclick="OnboardingPage.viewReport(${empId})">New-Hire Report JSON</button>
                <button class="btn btn-secondary" onclick="OnboardingPage.downloadReport(${empId})">Download PDF Report</button>
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`;

        openModal('Onboarding Checklist', html);
    },

    async completeTask(taskId, empId) {
        try {
            await API.post(`/onboarding/tasks/${taskId}/complete?completed_by=admin`, {});
            toast('Task marked complete');
            closeModal();
            await this.viewChecklist(empId);
        } catch (err) {
            toast(err.message || 'Failed to complete task', 'error');
        }
    },

    async signTask(taskId, signed, empId) {
        try {
            await API.put(`/onboarding/tasks/${taskId}`, { signed });
            toast(signed ? 'Task marked signed' : 'Signature removed');
            closeModal();
            await this.viewChecklist(empId);
        } catch (err) {
            toast(err.message || 'Failed to update task', 'error');
        }
    },

    async viewReport(empId) {
        try {
            const report = await API.get(`/onboarding/${empId}/new-hire-report`);
            openModal('New-Hire Report', `<pre style="white-space:pre-wrap;word-break:break-all;max-height:60vh;overflow:auto">${escapeHtml(JSON.stringify(report, null, 2))}</pre>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Close</button>
                </div>`);
        } catch (err) {
            toast(err.message || 'Failed to load report', 'error');
        }
    },

    async downloadReport(empId) {
        async function _openPDF(url, method = 'POST') {
            const res = await fetch(url, { method, credentials: 'same-origin' });
            if (!res.ok) { toast('PDF generation failed', 'error'); return; }
            const blob = await res.blob();
            const u = URL.createObjectURL(blob);
            window.open(u, '_blank');
            setTimeout(() => URL.revokeObjectURL(u), 15000);
        }
        await _openPDF(`/api/onboarding/${empId}/new-hire-report/pdf`, 'GET');
    },
};
