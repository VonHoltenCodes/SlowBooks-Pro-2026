/**
 * Bank Rules — auto-categorize imported bank transactions by payee pattern
 * Phase 10: Quick Wins + Medium Effort Features
 */
const BankRulesPage = {
    async render() {
        const rules = await API.get('/bank-rules');
        let html = `
            <div class="page-header">
                <h2>Bank Rules</h2>
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="BankRulesPage.showForm()">+ New Rule</button>
                    <button class="btn btn-secondary" onclick="BankRulesPage.applyAll()">Apply Rules Now</button>
                </div>
            </div>
            <p style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">
                Rules auto-match imported bank transactions by payee name. Higher priority rules are checked first.
            </p>`;

        if (rules.length === 0) {
            html += '<div class="empty-state"><p>No bank rules yet. Create one to auto-categorize transactions.</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Pattern</th><th>Match Type</th>
                    <th>Category Account</th><th>Priority</th><th>Active</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const r of rules) {
                html += `<tr>
                    <td><strong>${escapeHtml(r.name)}</strong></td>
                    <td><code>${escapeHtml(r.pattern)}</code></td>
                    <td>${escapeHtml(r.rule_type)}</td>
                    <td>${r.account_id || '—'}</td>
                    <td>${r.priority}</td>
                    <td>${r.is_active ? 'Yes' : 'No'}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="BankRulesPage.showForm(${r.id})">Edit</button>
                        <button class="btn btn-sm btn-danger" onclick="BankRulesPage.deleteRule(${r.id})">Delete</button>
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm(id = null) {
        let rule = { name: '', pattern: '', account_id: '', vendor_id: '', rule_type: 'contains', priority: 0, is_active: true };
        if (id) rule = await API.get(`/bank-rules/${id}`);

        const accounts = await API.get('/accounts');
        const acctOpts = accounts
            .filter(a => ['expense','income','asset','liability','cogs'].includes(a.account_type))
            .map(a => `<option value="${a.id}" ${rule.account_id==a.id?'selected':''}>${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`).join('');

        openModal(id ? 'Edit Bank Rule' : 'New Bank Rule', `
            <form onsubmit="BankRulesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Rule Name *</label>
                        <input name="name" required value="${escapeHtml(rule.name)}"></div>
                    <div class="form-group"><label>Payee Pattern *</label>
                        <input name="pattern" required value="${escapeHtml(rule.pattern)}" placeholder="e.g. AMZN or Amazon"></div>
                    <div class="form-group"><label>Match Type</label>
                        <select name="rule_type">
                            <option value="contains" ${rule.rule_type==='contains'?'selected':''}>Contains</option>
                            <option value="starts_with" ${rule.rule_type==='starts_with'?'selected':''}>Starts With</option>
                            <option value="exact" ${rule.rule_type==='exact'?'selected':''}>Exact Match</option>
                        </select></div>
                    <div class="form-group"><label>Category Account</label>
                        <select name="account_id"><option value="">-- None --</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Priority</label>
                        <input name="priority" type="number" value="${rule.priority}" title="Higher = checked first"></div>
                    <div class="form-group"><label>Active</label>
                        <select name="is_active">
                            <option value="true" ${rule.is_active !== false ? 'selected' : ''}>Yes</option>
                            <option value="false" ${rule.is_active === false ? 'selected' : ''}>No</option>
                        </select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Rule</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.account_id = data.account_id ? parseInt(data.account_id) : null;
        data.vendor_id = data.vendor_id ? parseInt(data.vendor_id) : null;
        data.priority = parseInt(data.priority) || 0;
        data.is_active = data.is_active === 'true';
        try {
            if (id) { await API.put(`/bank-rules/${id}`, data); toast('Rule updated'); }
            else { await API.post('/bank-rules', data); toast('Rule created'); }
            closeModal();
            App.navigate('#/bank-rules');
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteRule(id) {
        if (!confirm('Delete this rule?')) return;
        try {
            await API.del(`/bank-rules/${id}`);
            toast('Rule deleted');
            App.navigate('#/bank-rules');
        } catch (err) { toast(err.message, 'error'); }
    },

    async applyAll() {
        try {
            const result = await API.post('/bank-rules/apply');
            toast(`Matched ${result.matched} of ${result.total_unmatched} unmatched transactions`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
