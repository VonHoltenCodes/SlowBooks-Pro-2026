/**
 * Manual Journal Entries — Create, view, and void journal entries
 */
const JournalPage = {
    async render() {
        const entries = await API.get('/journal');
        let html = `
            <div class="page-header">
                <h2>Journal Entries</h2>
                <button class="btn btn-primary" onclick="JournalPage.showForm()">+ New Journal Entry</button>
            </div>`;

        if (entries.length === 0) {
            html += '<div class="empty-state"><p>No manual journal entries yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>ID</th><th>Date</th><th>Description</th><th>Reference</th>
                <th class="amount">Debit</th><th class="amount">Credit</th><th>Actions</th></tr></thead><tbody>`;
            for (const e of entries) {
                html += `<tr>
                    <td>${e.id}</td>
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.description)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td class="amount">${formatCurrency(e.total_debit)}</td>
                    <td class="amount">${formatCurrency(e.total_credit)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="JournalPage.view(${e.id})">View</button>
                        ${!e.source_type.endsWith('_void') ? `<button class="btn btn-sm btn-danger" onclick="JournalPage.void(${e.id})">Void</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async view(id) {
        const entry = await API.get(`/journal/${id}`);
        let linesHtml = entry.lines.map(l =>
            `<tr><td>${escapeHtml(l.account_number)} - ${escapeHtml(l.account_name)}</td>
             <td>${escapeHtml(l.description || '')}</td>
             <td class="amount">${l.debit > 0 ? formatCurrency(l.debit) : ''}</td>
             <td class="amount">${l.credit > 0 ? formatCurrency(l.credit) : ''}</td></tr>`
        ).join('');

        openModal(`Journal Entry #${entry.id}`, `
            <div style="margin-bottom:12px;">
                <strong>Date:</strong> ${formatDate(entry.date)}<br>
                <strong>Description:</strong> ${escapeHtml(entry.description)}<br>
                ${entry.reference ? `<strong>Reference:</strong> ${escapeHtml(entry.reference)}<br>` : ''}
                <strong>Type:</strong> ${escapeHtml(entry.source_type)}
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Account</th><th>Description</th><th class="amount">Debit</th><th class="amount">Credit</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Total Debit</span><span class="value">${formatCurrency(entry.total_debit)}</span></div>
                <div class="total-row"><span class="label">Total Credit</span><span class="value">${formatCurrency(entry.total_credit)}</span></div>
            </div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    _lineCount: 0,
    _accounts: [],

    async showForm() {
        const accounts = await API.get('/accounts');
        JournalPage._accounts = accounts;
        JournalPage._lineCount = 2;

        const acctOpts = accounts.map(a =>
            `<option value="${a.id}">${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`
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
                <h3 style="margin:12px 0 8px; font-size:14px;">Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th>Account</th><th>Description</th><th class="col-rate">Debit</th><th class="col-rate">Credit</th><th class="col-actions"></th></tr></thead>
                    <tbody id="je-lines">
                        <tr data-jeline="0">
                            <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                            <td><input class="je-desc"></td>
                            <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><button type="button" class="btn btn-sm btn-danger" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
                        </tr>
                        <tr data-jeline="1">
                            <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                            <td><input class="je-desc"></td>
                            <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><button type="button" class="btn btn-sm btn-danger" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
                        </tr>
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="JournalPage.addLine()">+ Add Line</button>
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span id="je-totals" style="font-size:11px;">Debits: $0.00 | Credits: $0.00</span>
                        <span id="je-balance" style="font-size:11px; margin-left:12px; font-weight:700;"></span>
                    </div>
                    <div class="form-actions" style="margin:0;">
                        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                        <button type="submit" class="btn btn-primary" id="je-submit">Create Entry</button>
                    </div>
                </div>
            </form>`);
    },

    addLine() {
        const idx = JournalPage._lineCount++;
        const acctOpts = JournalPage._accounts.map(a =>
            `<option value="${a.id}">${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`
        ).join('');
        $('#je-lines').insertAdjacentHTML('beforeend', `
            <tr data-jeline="${idx}">
                <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                <td><input class="je-desc"></td>
                <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                <td><button type="button" class="btn btn-sm btn-danger" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
            </tr>`);
    },

    recalc() {
        let totalDebit = 0, totalCredit = 0;
        $$('#je-lines tr').forEach(row => {
            totalDebit += parseFloat(row.querySelector('.je-debit')?.value) || 0;
            totalCredit += parseFloat(row.querySelector('.je-credit')?.value) || 0;
        });
        const totalsEl = $('#je-totals');
        if (totalsEl) totalsEl.textContent = `Debits: ${formatCurrency(totalDebit)} | Credits: ${formatCurrency(totalCredit)}`;
        const balEl = $('#je-balance');
        const diff = Math.abs(totalDebit - totalCredit);
        if (balEl) {
            if (diff < 0.005) {
                balEl.textContent = 'BALANCED';
                balEl.style.color = 'var(--success)';
            } else {
                balEl.textContent = `Out of balance by ${formatCurrency(diff)}`;
                balEl.style.color = 'var(--danger)';
            }
        }
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#je-lines tr').forEach(row => {
            const account_id = row.querySelector('.je-account')?.value;
            const debit = parseFloat(row.querySelector('.je-debit')?.value) || 0;
            const credit = parseFloat(row.querySelector('.je-credit')?.value) || 0;
            if (account_id && (debit > 0 || credit > 0)) {
                lines.push({
                    account_id: parseInt(account_id),
                    debit, credit,
                    description: row.querySelector('.je-desc')?.value || '',
                });
            }
        });
        if (lines.length < 2) { toast('At least 2 lines required', 'error'); return; }

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

    async void(id) {
        if (!confirm('Void this journal entry? A reversing entry will be created.')) return;
        try {
            await API.post(`/journal/${id}/void`);
            toast('Journal entry voided');
            App.navigate('#/journal');
        } catch (err) { toast(err.message, 'error'); }
    },
};
