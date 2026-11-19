/**
 * In-kind gifts — a donated piano, a pallet of paint, pro bono work.
 * Each line names what the property is (an asset or expense account) and
 * its fair value; the credit is In-Kind Contributions. The acknowledgment
 * letter describes the property and never states a value — the donor
 * values the gift, not the charity.
 */
const InKindPage = {
    _customers: [],
    _accounts: [],
    _lineCount: 0,

    async render() {
        const gifts = await API.get('/in-kind-gifts');
        const rows = gifts.map(g => `<tr class="clickable${g.status === 'void' ? ' row--dim' : ''}" onclick="InKindPage.view(${g.id})">
            <td>${escapeHtml(g.number)}</td>
            <td>${escapeHtml(g.date)}</td>
            <td>${escapeHtml(g.customer_name || '')}</td>
            <td>${escapeHtml(g.lines.map(l => l.description).join('; '))}</td>
            <td>${escapeHtml(g.class_name || '')}</td>
            <td class="amount">${formatCurrency(g.total)}</td>
            <td>${g.status === 'void' ? '<span style="color:var(--text-danger)">void</span>' : 'posted'}</td>
        </tr>`).join('');
        return `
            <div class="page-header">
                <div style="display:flex; gap:4px; align-items:center;"><h2>In-Kind Gifts</h2>${hint("Donated property and services. The value you enter is the donor's estimate for the books; the acknowledgment letter describes the gift without a dollar figure.", 'In-Kind Gifts')}</div>
                <button class="btn btn-primary" onclick="InKindPage.showForm()">+ In-Kind Gift</button>
            </div>
            ${gifts.length === 0 ? `<div class="empty-state"><p>No in-kind gifts yet.</p></div>` : `
            <div class="table-container"><table>
                <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">${T('Customer')}</th><th scope="col">Property</th><th scope="col">${T('Class')}</th><th scope="col" class="amount">Book value</th><th scope="col">Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`}`;
    },

    async showForm() {
        const [customers, accounts] = await Promise.all([API.get('/customers?active_only=true&tree=1'), API.get('/accounts')]);
        InKindPage._customers = customers;
        InKindPage._accounts = accounts;
        const classGroup = await classFormGroupHtml();
        const jobGroup = await jobFormGroupHtml(null, 'ik-customer');
        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(customerLabel(c))}</option>`).join('');
        InKindPage._lineCount = 0;
        openModal('In-Kind Gift', `
            <form onsubmit="InKindPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="ik-customer" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    ${classGroup}${jobGroup}
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="memo" placeholder="e.g. donated for the youth program"></div>
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Property</h3>
                <div class="table-container"><table class="line-items-table">
                    <thead><tr><th scope="col">Description</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Fair value (each)</th><th scope="col" class="col-amount">Amount</th><th scope="col">What it is (account)</th><th scope="col"></th></tr></thead>
                    <tbody id="ik-lines">${InKindPage.lineHtml(0)}</tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="InKindPage.addLine()">+ Add Line</button>
                <div style="margin-top:8px;text-align:right;font-weight:700" id="ik-total">Total: $0.00</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Gift</button>
                </div>
            </form>`);
        InKindPage._lineCount = 1;
    },

    lineHtml(idx) {
        const accts = InKindPage._accounts.filter(a => ['asset', 'expense', 'cogs'].includes(a.account_type));
        const opts = `<option value="">Select...</option>` + accts.map(a => `<option value="${a.id}">${accountLabel(a, null, ' ')}</option>`).join('');
        return `<tr data-ikline="${idx}">
            <td><input class="ik-desc" placeholder="Yamaha U1 upright piano" required style="min-width:180px"></td>
            <td><input class="ik-qty" type="number" step="0.01" min="0.01" value="1" oninput="InKindPage.recalc()"></td>
            <td><input class="ik-fv money" type="number" step="0.01" min="0" value="0" oninput="InKindPage.recalc()"></td>
            <td class="col-amount ik-amount">$0.00</td>
            <td><select class="ik-debit" required>${opts}</select></td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="this.closest('tr').remove();InKindPage.recalc()">X</button></td>
        </tr>`;
    },

    addLine() { $('#ik-lines').insertAdjacentHTML('beforeend', InKindPage.lineHtml(InKindPage._lineCount++)); },

    recalc() {
        let total = 0;
        $$('#ik-lines tr').forEach(row => {
            const amt = (parseFloat(row.querySelector('.ik-qty')?.value) || 0) * (parseFloat(row.querySelector('.ik-fv')?.value) || 0);
            total += amt;
            const cell = row.querySelector('.ik-amount'); if (cell) cell.textContent = formatCurrency(amt);
        });
        const t = $('#ik-total'); if (t) t.textContent = `Total: ${formatCurrency(total)}`;
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#ik-lines tr').forEach(row => {
            lines.push({
                description: row.querySelector('.ik-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.ik-qty')?.value) || 1,
                fair_value: parseFloat(row.querySelector('.ik-fv')?.value) || 0,
                debit_account_id: parseInt(row.querySelector('.ik-debit')?.value) || null,
            });
        });
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form))) return;
        try {
            const g = await API.post('/in-kind-gifts', {
                customer_id: parseInt(form.customer_id.value),
                date: form.date.value,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                job_id: jobIdFromForm(form),
                lines,
            });
            toastPosted(`${g.number} posted`);
            closeModal();
            App.navigate('#/in-kind-gifts');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        let g;
        try { g = await API.get(`/in-kind-gifts/${id}`); } catch (err) { toast(err.message, 'error'); return; }
        const rows = g.lines.map(l => `<tr>
            <td>${escapeHtml(l.description)}</td><td class="amount">${l.quantity}</td><td class="amount">${formatCurrency(l.fair_value)}</td>
            <td class="amount">${formatCurrency(l.amount)}</td><td style="font-size:11px">${escapeHtml(l.debit_account_name || '')} / ${escapeHtml(l.credit_account_name || '')}</td>
        </tr>`).join('');
        openModal(`In-Kind Gift ${g.number}`, `
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <div style="font-size:13px">
                    <div><strong>${escapeHtml(g.date)}</strong> · ${escapeHtml(g.customer_name || '')}${g.class_name ? ` · ${escapeHtml(g.class_name)}` : ''}</div>
                    ${g.memo ? `<div style="color:var(--text-muted)">${escapeHtml(g.memo)}</div>` : ''}
                </div>
                <div style="text-align:right">
                    <div style="font-size:20px;font-weight:700">${formatCurrency(g.total)}</div>
                    <div>${g.status === 'void' ? '<span style="color:var(--text-danger);font-weight:600">VOID</span>' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="InKindPage.voidEntry(${g.id})">Void</button>`}</div>
                </div>
            </div>
            <div class="table-container"><table class="data-table" style="font-size:12px">
                <thead><tr><th scope="col">Property</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Fair value</th><th scope="col" class="amount">Amount</th><th scope="col">Debit / credit</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions">
                ${g.status !== 'void' ? `<button class="btn btn-secondary" onclick="window.open('/api/donors/gifts/in-kind/${g.id}/acknowledgment/pdf','_blank')">Acknowledgment (PDF)</button>
                <button class="btn btn-secondary" onclick="Donors.emailAcknowledgment('in-kind', ${g.id})">Email Acknowledgment</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    async voidEntry(id) {
        if (!confirm('Void this in-kind gift? A reversing entry is posted; the original stays in the ledger.')) return;
        try {
            await API.post(`/in-kind-gifts/${id}/void`, {});
            toast('In-kind gift voided');
            closeModal();
            App.navigate('#/in-kind-gifts');
        } catch (err) { toast(err.message, 'error'); }
    },
};
window.InKindPage = InKindPage;
