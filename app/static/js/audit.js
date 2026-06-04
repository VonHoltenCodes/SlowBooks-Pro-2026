/**
 * Audit Log Viewer — read-only audit trail browser
 * Feature 9: View all create/update/delete operations
 */
const AuditPage = {
    async render() {
        const tables = await API.get('/audit/tables');
        const tableOpts = tables.map(t => `<option value="${t}">${t}</option>`).join('');

        // Populate the results table immediately after the shell is in the
        // DOM — otherwise the page sits empty until the user pokes a filter.
        setTimeout(() => AuditPage.load(), 0);

        return `
            <div class="page-header">
                <h2>Audit Log</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    All create, update, and delete operations are logged here
                </div>
            </div>
            <div class="toolbar">
                <select id="audit-table" onchange="AuditPage.load()">
                    <option value="">All Tables</option>${tableOpts}
                </select>
                <select id="audit-action" onchange="AuditPage.load()">
                    <option value="">All Actions</option>
                    <option value="INSERT">INSERT</option>
                    <option value="UPDATE">UPDATE</option>
                    <option value="DELETE">DELETE</option>
                </select>
                <label style="font-size:10px;font-weight:700;color:var(--text-secondary);">From:</label>
                <input type="date" id="audit-start" onchange="AuditPage.load()">
                <label style="font-size:10px;font-weight:700;color:var(--text-secondary);">To:</label>
                <input type="date" id="audit-end" onchange="AuditPage.load()">
            </div>
            <div id="audit-results"></div>`;
    },

    async load() {
        const table = $('#audit-table')?.value || '';
        const action = $('#audit-action')?.value || '';
        const start = $('#audit-start')?.value || '';
        const end = $('#audit-end')?.value || '';

        let url = '/audit?limit=100';
        if (table) url += `&table_name=${table}`;
        if (action) url += `&action=${action}`;
        if (start) url += `&start_date=${start}`;
        if (end) url += `&end_date=${end}`;

        const logs = await API.get(url);
        const container = $('#audit-results');
        if (!logs.length) {
            container.innerHTML = '<div class="empty-state"><p>No audit entries found</p></div>';
            return;
        }

        let html = `<div class="table-container"><table>
            <thead><tr>
                <th>Time</th><th>Table</th><th>ID</th><th>Action</th><th>Changes</th>
            </tr></thead><tbody>`;

        for (const log of logs) {
            const actionClass = log.action === 'INSERT' ? 'badge-paid' :
                                log.action === 'DELETE' ? 'badge-void' : 'badge-sent';
            const ts = log.timestamp ? new Date(log.timestamp).toLocaleString() : '';
            let changes = '';
            if (log.action === 'UPDATE' && log.changed_fields) {
                changes = log.changed_fields.map(f => {
                    const oldV = log.old_values?.[f] ?? '';
                    const newV = log.new_values?.[f] ?? '';
                    return `<strong>${escapeHtml(f)}</strong>: ${escapeHtml(String(oldV))} → ${escapeHtml(String(newV))}`;
                }).join('<br>');
            } else if (log.action === 'INSERT' && log.new_values) {
                const keys = Object.keys(log.new_values).filter(k => k !== 'id' && log.new_values[k] != null).slice(0, 3);
                changes = keys.map(k => `<strong>${escapeHtml(k)}</strong>: ${escapeHtml(String(log.new_values[k]))}`).join('<br>');
                if (Object.keys(log.new_values).length > 3) changes += '<br>...';
            } else if (log.action === 'DELETE' && log.old_values) {
                changes = '<em style="color:var(--qb-red);">Record deleted</em>';
            }

            html += `<tr>
                <td style="white-space:nowrap;font-size:10px;">${ts}</td>
                <td><strong>${escapeHtml(log.table_name)}</strong></td>
                <td style="font-family:var(--font-mono);">${log.record_id}</td>
                <td><span class="badge ${actionClass}">${log.action}</span></td>
                <td style="font-size:10px;">${changes}</td>
            </tr>`;
        }
        html += '</tbody></table></div>';
        container.innerHTML = html;
    },
};
