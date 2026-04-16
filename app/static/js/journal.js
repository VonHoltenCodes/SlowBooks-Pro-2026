const JournalPage = {
    _accounts: [],
    _lineCount: 0,

    async render() {
        const entries = await API.get('/journal');
        const canManage = App.hasPermission ? App.hasPermission('accounts.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Journal Entries</h2>
                ${canManage ? `<button class="btn btn-primary" onclick="JournalPage.showForm()">+ New Journal Entry</button>` : ''}
            </div>`;

        if (!entries.length) {
            html += `<div class="empty-state"><p>No manual journals yet</p></div>`;
            return html;
        }

        html += `<div class="table-container"><table>
            <thead><tr>
                <th>Date</th><th>Description</th><th>Reference</th><th>Status</th><th>Actions</th>
            </tr></thead><tbody>`;
        for (const entry of entries) {
            html += `<tr>
                <td>${formatDate(entry.date)}</td>
                <td>${escapeHtml(entry.description || '')}</td>
                <td>${escapeHtml(entry.reference || '')}</td>
                <td>${entry.is_voided ? '<span style="color:var(--danger);font-weight:700;">Voided</span>' : 'Open'}</td>
                <td class="actions">
                    <button class="btn btn-sm btn-secondary" onclick="JournalPage.view(${entry.id})">View</button>
                    ${canManage && !entry.is_voided ? `<button class="btn btn-sm btn-danger" onclick="JournalPage.void(${entry.id})">Void</button>` : ''}
                </td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
        return html;
    },

    async showForm() {
        JournalPage._accounts = await API.get('/accounts?active_only=true');
        JournalPage._lineCount = 2;
        const acctOpts = JournalPage._accounts.map(a =>
            `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}</option>`
        ).join('');
        openModal('New Journal Entry', `
            <form onsubmit="JournalPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    <div class="form-group full-width"><label>Description *</label>
                        <input name="description" required></div>
                </div>
                <table class="line-items-table">
                    <thead><tr><th>Account</th><th>Description</th><th class="col-amount">Debit</th><th class="col-amount">Credit</th><th></th></tr></thead>
                    <tbody id="journal-lines">
                        ${JournalPage.lineHtml(0, acctOpts)}
                        ${JournalPage.lineHtml(1, acctOpts)}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="JournalPage.addLine()">+ Add Line</button>
                <div id="journal-balance" style="margin-top:10px; font-size:11px; color:var(--text-muted);"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Journal</button>
                </div>
            </form>`);
        JournalPage.recalc();
    },

    lineHtml(idx, acctOpts) {
        return `<tr data-line="${idx}">
            <td><select class="journal-account"><option value="">Select...</option>${acctOpts}</select></td>
            <td><input class="journal-desc"></td>
            <td><input class="journal-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
            <td><input class="journal-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
            <td><button type="button" class="btn btn-sm btn-danger" onclick="JournalPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const acctOpts = JournalPage._accounts.map(a =>
            `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}</option>`
        ).join('');
        $('#journal-lines').insertAdjacentHTML('beforeend', JournalPage.lineHtml(JournalPage._lineCount++, acctOpts));
    },

    removeLine(idx) {
        const row = $(`[data-line="${idx}"]`);
        if (row) row.remove();
        JournalPage.recalc();
    },

    recalc() {
        let debits = 0;
        let credits = 0;
        $$('#journal-lines tr').forEach(row => {
            debits += parseFloat(row.querySelector('.journal-debit')?.value) || 0;
            credits += parseFloat(row.querySelector('.journal-credit')?.value) || 0;
        });
        const diff = Math.round((debits - credits) * 100) / 100;
        const el = $('#journal-balance');
        if (!el) return;
        if (Math.abs(diff) < 0.005) {
            el.innerHTML = `Debits ${formatCurrency(debits)} &middot; Credits ${formatCurrency(credits)} &middot; <strong style="color:var(--success);">Balanced</strong>`;
        } else {
            el.innerHTML = `Debits ${formatCurrency(debits)} &middot; Credits ${formatCurrency(credits)} &middot; <strong style="color:var(--danger);">Out by ${formatCurrency(Math.abs(diff))}</strong>`;
        }
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#journal-lines tr').forEach(row => {
            const accountId = row.querySelector('.journal-account')?.value;
            const debit = parseFloat(row.querySelector('.journal-debit')?.value) || 0;
            const credit = parseFloat(row.querySelector('.journal-credit')?.value) || 0;
            if (accountId && (debit > 0 || credit > 0)) {
                lines.push({
                    account_id: parseInt(accountId),
                    debit,
                    credit,
                    description: row.querySelector('.journal-desc')?.value || '',
                });
            }
        });
        try {
            await API.post('/journal', {
                date: form.date.value,
                description: form.description.value,
                reference: form.reference.value || null,
                lines,
            });
            toast('Journal entry created');
            closeModal();
            App.navigate('#/journal');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        const entry = await API.get(`/journal/${id}`);
        const rows = (entry.lines || []).map(line => `<tr>
            <td>${escapeHtml(line.account_number || '')}</td>
            <td>${escapeHtml(line.account_name || '')}</td>
            <td>${escapeHtml(line.description || '')}</td>
            <td class="amount">${line.debit ? formatCurrency(line.debit) : ''}</td>
            <td class="amount">${line.credit ? formatCurrency(line.credit) : ''}</td>
        </tr>`).join('');
        openModal(`Journal Entry #${entry.id}`, `
            <div style="margin-bottom:12px;">
                <strong>Date:</strong> ${formatDate(entry.date)}<br>
                <strong>Description:</strong> ${escapeHtml(entry.description || '')}<br>
                <strong>Reference:</strong> ${escapeHtml(entry.reference || '')}<br>
                <strong>Status:</strong> ${entry.is_voided ? 'Voided' : 'Open'}
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Account #</th><th>Account</th><th>Description</th><th class="amount">Debit</th><th class="amount">Credit</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions">
                ${!entry.is_voided ? `<button class="btn btn-danger" onclick="JournalPage.void(${entry.id})">Void</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    async void(id) {
        if (!confirm('Void this journal entry? A reversing entry will be posted.')) return;
        try {
            await API.post(`/journal/${id}/void`);
            toast('Journal entry voided');
            closeModal();
            App.navigate('#/journal');
        } catch (err) { toast(err.message, 'error'); }
    },
};
