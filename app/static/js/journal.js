/**
 * Manual Journal Entries — Create, view, and void journal entries
 */
const JournalPage = {
    // 'adjusting' shows the accountant's adjusting entries alone (#341)
    _show: 'all',

    async render() {
        const entries = await API.get(JournalPage._show === 'adjusting' ? '/journal?adjusting=true' : '/journal');
        // The accountant's sign-in keeps this page's own controls (#340):
        // the server lets it post journal entries and void its own.
        let html = `
            <div class="page-header">
                <h2>Journal Entries</h2>
                <button class="btn btn-primary" data-accountant-ok onclick="JournalPage.showForm()">+ New Journal Entry</button>
            </div>
            <div class="form-group" style="max-width:260px; margin-bottom:12px;">
                <label for="journal-show">Show</label>
                <select id="journal-show" data-no-search data-readonly-ok onchange="JournalPage.setShow(this.value)">
                    <option value="all" ${JournalPage._show === 'all' ? 'selected' : ''}>All entries</option>
                    <option value="adjusting" ${JournalPage._show === 'adjusting' ? 'selected' : ''}>Adjusting entries only</option>
                </select>
            </div>`;

        if (entries.length === 0) {
            html += `<div class="empty-state"><p>${JournalPage._show === 'adjusting' ? 'No adjusting entries' : 'No journal entries yet'}</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">ID</th><th scope="col">Date</th><th scope="col">Description</th><th scope="col">Reference</th><th scope="col">Posted by</th>
                <th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th><th scope="col">Actions</th></tr></thead><tbody>`;
            for (const e of entries) {
                html += `<tr>
                    <td>${e.id}</td>
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.description)}${JournalPage.adjustingMark(e)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td>${escapeHtml(e.posted_by || '')}</td>
                    <td class="amount">${formatCurrency(e.total_debit)}</td>
                    <td class="amount">${formatCurrency(e.total_credit)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="JournalPage.view(${e.id})">View</button>
                        ${e.voided ? '<span class="journal-voided" style="color:var(--danger);font-weight:700;">Voided</span>' : ''}
                        ${JournalPage.canVoid(e) ? `<button data-destructive data-accountant-ok class="btn btn-sm btn-danger" onclick="JournalPage.void(${e.id})">Void</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    setShow(value) {
        JournalPage._show = value === 'adjusting' ? 'adjusting' : 'all';
        App.navigate('#/journal');
    },

    // "Adjusting" beside an adjusting entry's description (#341)
    adjustingMark(e) {
        return e.is_adjusting ? ' <span class="badge" title="An adjusting entry" style="font-size:9px;">Adjusting</span>' : '';
    },

    // A journal entry voids here with a reversing entry: hand-typed, or an
    // import's (the lists IIF's opening balances, a Transaction Detail by
    // Account journal, a migration's), or a posting the QuickBooks Online
    // import made. A document's own posting (a bill payment, a deposit...)
    // voids from its document, so its view offers no Void. The server says
    // which is which (voidable); the page used to allow hand-typed and
    // QuickBooks Online entries only, so a wrong opening-balance import had
    // no way out. An accountant's sign-in voids only what it posted (#340):
    // the server refuses the rest, so the button is not offered.
    canVoid(e) {
        if (typeof App !== 'undefined' && App.isAccountant && App.isAccountant() && !(e.posted_by && e.posted_by === App.username)) return false;
        if (e.voidable === undefined) {
            // an entry from a server that does not say (before 2.23.5): a
            // journal of its own, by its source, and not a reversal
            const source = e.source_type || '';
            return !source.endsWith('_void') && !e.voided
                && ['manual', 'qbo_journal', 'qbo_ledger', 'iif_import', 'qb_ledger_import', 'opening_balance'].includes(source);
        }
        return !!e.voidable && !e.voided;
    },

    async view(id) {
        const entry = await API.get(`/journal/${id}`);
        let linesHtml = entry.lines.map(l =>
            `<tr><td>${l.account_number ? `${escapeHtml(l.account_number)} - ` : ''}${escapeHtml(l.account_name)}</td>
             <td>${escapeHtml(l.description || '')}</td>
             <td class="amount">${l.debit > 0 ? formatCurrency(l.debit) : ''}</td>
             <td class="amount">${l.credit > 0 ? formatCurrency(l.credit) : ''}</td></tr>`
        ).join('');

        openModal(`Journal Entry #${entry.id}`, `
            <div style="margin-bottom:12px;">
                <strong>Date:</strong> ${formatDate(entry.date)}<br>
                <strong>Description:</strong> ${escapeHtml(entry.description)}<br>
                ${entry.reference ? `<strong>Reference:</strong> ${escapeHtml(entry.reference)}<br>` : ''}
                <strong>Type:</strong> ${escapeHtml(sourceWord(entry.source_type))}${entry.is_adjusting ? ' · <strong>Adjusting entry</strong>' : ''}<br>
                ${entry.posted_by ? `<strong>Posted by:</strong> ${escapeHtml(entry.posted_by)}` : ''}
                ${entry.register_account_id ? `<div id="journal-register-note" style="margin-top:6px; color:var(--gray-500);">Posted from the bank register of <a href="#/banking/${Number(entry.register_account_id)}">${escapeHtml(entry.register_account_name)}</a>; edit it there.</div>` : ''}
                ${entry.voided ? '<div class="journal-voided" style="color:var(--danger);font-weight:700;margin-top:6px;">Voided</div>' : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}<th scope="col" class="amount">Debit</th><th scope="col" class="amount">Credit</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Total Debit</span><span class="value">${formatCurrency(entry.total_debit)}</span></div>
                <div class="total-row"><span class="label">Total Credit</span><span class="value">${formatCurrency(entry.total_credit)}</span></div>
            </div>
            <div data-accountant-ok style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
                <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments</div>
                <div id="journal-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <div style="display:flex; gap:6px; align-items:center;">
                    <input type="file" id="journal-attach-file" aria-label="File to attach" style="font-size:11px;">
                    <button class="btn btn-sm btn-secondary" onclick="JournalPage.uploadAttachment(${entry.id})">Attach</button>
                </div>
            </div>
            <div class="form-actions">
                ${JournalPage.canVoid(entry) ? `<button data-destructive data-accountant-ok class="btn btn-danger" onclick="JournalPage.void(${entry.id})">Void</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        JournalPage.loadAttachments(entry.id);
    },

    // The receipt behind a manual entry (#281): the same files, the same
    // routes, as an expense's or a bill's.
    async loadAttachments(id) {
        const el = $('#journal-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/journal_entry/${id}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="JournalPage.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(id) {
        const fileInput = $('#journal-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/journal_entry/${id}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            JournalPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachmentId}`);
            toast('Attachment deleted');
            JournalPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    _lineCount: 0,
    _accounts: [],

    async showForm() {
        const accounts = await API.get('/accounts');
        const classGroup = await classFormGroupHtml();
        const jobGroup = await jobFormGroupHtml(null);
        await CostCodes.load();
        await Nonprofit.loadFunds();
        JournalPage._accounts = accounts;
        JournalPage._lineCount = 2;

        const acctOpts = accounts.map(a =>
            `<option value="${a.id}">${accountLabel(a)}</option>`
        ).join('');

        openModal('New Journal Entry', `
            <form aria-labelledby="modal-title" data-accountant-ok data-skip-to="#je-lines" data-skip-text="Skip to the lines" onsubmit="JournalPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    ${classGroup}${jobGroup}
                    <div class="form-group full-width"><label>Description *</label>
                        <input name="description" required></div>
                    <div class="form-group full-width">
                        <label style="display:flex; align-items:center; gap:6px; font-weight:400;">
                            <input type="checkbox" name="is_adjusting" ${App.isAccountant && App.isAccountant() ? 'checked' : ''}>
                            Adjusting entry
                        </label>
                        <div class="hint">An accountant's correction to the books: marked on the entry, listed by the Adjusting Journal Entries report, and left out of a statement on request.</div>
                    </div>
                </div>
                <h3 style="margin:12px 0 8px; font-size:14px;">Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}${Nonprofit.headHtml()}<th scope="col" class="col-rate">Debit</th><th scope="col" class="col-rate">Credit</th><th scope="col" class="col-actions"></th></tr></thead>
                    <tbody id="je-lines">
                        <tr data-jeline="0">
                            <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                            <td><input class="je-desc"></td>
                            ${CostCodes.cellHtml('je-cost-code')}${Nonprofit.cellHtml('je-function')}
                            <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
                        </tr>
                        <tr data-jeline="1">
                            <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                            <td><input class="je-desc"></td>
                            ${CostCodes.cellHtml('je-cost-code')}${Nonprofit.cellHtml('je-function')}
                            <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
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
            `<option value="${a.id}">${accountLabel(a)}</option>`
        ).join('');
        $('#je-lines').insertAdjacentHTML('beforeend', `
            <tr data-jeline="${idx}">
                <td><select class="je-account"><option value="">--</option>${acctOpts}</select></td>
                <td><input class="je-desc"></td>
                            ${CostCodes.cellHtml('je-cost-code')}${Nonprofit.cellHtml('je-function')}
                <td><input class="je-debit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                <td><input class="je-credit" type="number" step="0.01" value="0" oninput="JournalPage.recalc()"></td>
                <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="this.closest('tr').remove();JournalPage.recalc()">X</button></td>
            </tr>`);
    },

    // Split support (nonprofit): the line's amount, and the expansion of
    // one row into the rule's shares — same account, the debit/credit
    // side preserved, fund and function set per share.
    lineAmount(row) {
        return (parseFloat(row.querySelector('.je-debit')?.value) || 0) || (parseFloat(row.querySelector('.je-credit')?.value) || 0);
    },
    splitApply(row, res) {
        const isDebit = (parseFloat(row.querySelector('.je-debit')?.value) || 0) > 0;
        const baseDesc = row.querySelector('.je-desc')?.value || '';
        let anchor = row;
        res.lines.forEach((ln, i) => {
            const clone = row.cloneNode(true);
            clone.dataset.jeline = JournalPage._lineCount++;
            // cloneNode drops <select> state; copy it by hand
            row.querySelectorAll('select').forEach((sel, k) => { clone.querySelectorAll('select')[k].value = sel.value; });
            clone.querySelector('.je-desc').value = `${baseDesc} (${res.rule_name}: ${Nonprofit.label(ln.function) || ln.class_name || 'share'})`;
            clone.querySelector(isDebit ? '.je-debit' : '.je-credit').value = Number(ln.amount).toFixed(2);
            clone.querySelector(isDebit ? '.je-credit' : '.je-debit').value = 0;
            const fund = clone.querySelector('.je-function-fund'); if (fund) fund.value = ln.class_id || '';
            const fn = clone.querySelector('.je-function'); if (fn) fn.value = ln.function || '';
            anchor.insertAdjacentElement('afterend', clone);
            anchor = clone;
        });
        row.remove();
        JournalPage.recalc();
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
                    cost_code_id: CostCodes.fromRow(row, 'je-cost-code'),
                    class_id: Nonprofit.fundFromRow(row, 'je-function'),
                    ...Nonprofit.linePayload(row, 'je-function'),
                });
            }
        });
        if (lines.length < 2) { toast('At least 2 lines required', 'error'); return; }
        if (!(await ClassWarn.ok(form, { rows: '#je-lines tr', cls: 'je-function' }))) return;

        try {
            await API.post('/journal', {
                date: form.date.value,
                description: form.description.value,
                reference: form.reference.value || null,
                class_id: classIdFromForm(form),
                job_id: jobIdFromForm(form),
                is_adjusting: !!(form.is_adjusting && form.is_adjusting.checked),
                lines,
            });
            toast(form.is_adjusting && form.is_adjusting.checked ? 'Adjusting entry created' : 'Journal entry created');
            closeModal();
            App.navigate('#/journal');
        } catch (err) { toast(err.message, 'error'); }
    },

    async void(id) {
        if (!confirm('Void this journal entry? A reversing entry will be created.')) return;
        try {
            await API.post(`/journal/${id}/void`);
            toast('Journal entry voided');
            closeModal();
            App.navigate('#/journal');
        } catch (err) { toast(err.message, 'error'); }
    },
};
