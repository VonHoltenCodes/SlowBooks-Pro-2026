/**
 * Multi-Company — list and create company files/databases
 * Feature 16: Company management UI
 *
 * Switching companies is NOT done live from this page. On desktop installs
 * each company is its own database file (like a QuickBooks company file):
 * close SlowBooks Pro and reopen it, and the launcher asks which company
 * to open. On server (PostgreSQL) installs each company is a separate
 * database configured at deploy time.
 */
const CompaniesPage = {
    async render() {
        const companies = await API.get('/companies');
        let html = `
            <div class="page-header">
                <h2>Company Files</h2>
                <button class="btn btn-primary" onclick="CompaniesPage.showCreate()">+ New Company</button>
            </div>
            <p style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
                Each company is stored in its own separate database.
                To switch companies, close SlowBooks Pro and open it again — you'll be asked which company to open.
            </p>`;

        if (companies.length === 0) {
            html += '<div class="empty-state"><p>No additional companies created</p></div>';
        } else {
            html += '<div class="card-grid">';
            for (const c of companies) {
                const fileLabel = c.file || c.database_name || '';
                html += `<div class="card">
                    <div class="card-header">${escapeHtml(c.name)}${c.is_current ? ' <span style="font-size:9px;color:var(--success,#2e7d32);">(currently open)</span>' : ''}</div>
                    <div style="font-size:10px;color:var(--text-muted);">${escapeHtml(fileLabel)}</div>
                    ${c.description ? `<div style="font-size:11px;margin-top:4px;">${escapeHtml(c.description)}</div>` : ''}
                    ${c.last_accessed ? `<div style="font-size:9px;color:var(--text-light);margin-top:4px;">Last accessed: ${new Date(c.last_accessed).toLocaleDateString()}</div>` : ''}
                </div>`;
            }
            html += '</div>';
        }
        return html;
    },

    showCreate() {
        openModal('New Company', `
            <form onsubmit="CompaniesPage.create(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Company Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Database Name</label>
                        <input name="database_name" pattern="[a-z0-9_]+" title="Lowercase letters, numbers, underscores only"
                            placeholder="Server installs only — auto-generated on desktop"></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Company</button>
                </div>
            </form>`);
    },

    async create(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        if (!data.database_name) delete data.database_name;
        try {
            await API.post('/companies', data);
            toast('Company created. Close and reopen SlowBooks Pro to open it.');
            closeModal();
            App.navigate('#/companies');
        } catch (err) { toast(err.message, 'error'); }
    },
};
