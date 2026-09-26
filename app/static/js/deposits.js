/**
 * Make Deposits — Move funds from Undeposited Funds to a bank account
 * Classic QB deposit slip workflow. A deposit remembers the payments it
 * took; voiding it (Recent deposits) puts them back on the list, which is
 * how one payment comes out of a deposit.
 */
const DepositsPage = {
    async render() {
        const [pending, accounts, classes, recent] = await Promise.all([
            API.get('/deposits/pending'),
            API.get('/accounts'),
            API.get('/classes').catch(() => []),
            API.get('/deposits?limit=20').catch(() => []),
        ]);
        const classOpts = classes.map(c =>
            `<option value="${c.id}" ${c.is_system_default ? 'selected' : ''}>${escapeHtml(c.name)}</option>`
        ).join('');

        const bankAccts = accounts.filter(a => a.bank_kind === 'bank');
        const bankOpts = bankAccts.map(a => `<option value="${a.id}">${escapeHtml(a.name)} (${formatCurrency(a.balance)})</option>`).join('');

        let html = `
            <div class="page-header">
                <h2>Make Deposits</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Select payments to deposit from Undeposited Funds to a bank account
                </div>
            </div>
            <div class="toolbar">
                <label style="font-size:10px;font-weight:700;">Deposit To:</label>
                <select id="deposit-bank-acct">${bankOpts.length ? bankOpts : '<option>No bank accounts</option>'}</select>
                <label style="font-size:10px;font-weight:700;">Date:</label>
                <input type="date" id="deposit-date" value="${todayISO()}">
                <label style="font-size:10px;font-weight:700;">Reference:</label>
                <input type="text" id="deposit-ref" placeholder="Deposit slip #" style="width:120px;">
                ${classOpts ? `<label style="font-size:10px;font-weight:700;">${T('Class')}:</label>
                <select id="deposit-class">${classOpts}</select>` : ''}
            </div>`;

        if (pending.length === 0) {
            html += '<div class="empty-state"><p>No payments waiting to be deposited</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col" style="width:30px;"><input type="checkbox" id="dep-select-all" aria-label="Select all payments" onchange="DepositsPage.toggleAll()"></th>
                    <th scope="col">Date</th><th scope="col">Received From</th><th scope="col">Description</th>
                    <th scope="col">Check # / Ref</th><th scope="col">Method</th>
                    <th scope="col" class="amount">Amount</th>
                </tr></thead><tbody>`;
            for (const p of pending) {
                html += `<tr>
                    <td><input type="checkbox" class="dep-check" data-lineid="${p.transaction_line_id}" data-amount="${p.amount}" aria-label="Deposit ${escapeHtml(p.description)}" onchange="DepositsPage.recalc()"></td>
                    <td>${formatDate(p.date)}</td>
                    <td>${escapeHtml(p.received_from || '')}</td>
                    <td>${escapeHtml(p.description)}${DepositsPage._paid(p)}</td>
                    <td>${escapeHtml(DepositsPage._ref(p))}</td>
                    <td>${escapeHtml(p.method || '')}</td>
                    <td class="amount">${formatCurrency(p.amount)}</td>
                </tr>`;
            }
            html += `</tbody></table></div>
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div id="deposit-total" style="font-size:16px; font-weight:700; color:var(--qb-navy);">
                        Selected: $0.00 (0 items)
                    </div>
                    <button class="btn btn-primary" onclick="DepositsPage.makeDeposit()">Make Deposit</button>
                </div>`;
        }
        html += DepositsPage._recentHtml(recent);
        return html;
    },

    // "Check 4420 · REF-9": the check number and the payment's own
    // reference, else whatever the posting carried.
    _ref(p) {
        const parts = [];
        if (p.check_number) parts.push(`Check ${p.check_number}`);
        if (p.payment_reference && p.payment_reference !== p.check_number) parts.push(p.payment_reference);
        return parts.length ? parts.join(' · ') : (p.reference || '');
    },

    // What a plain payment paid ("for Invoice #1001"); a sales receipt's
    // number is already its description.
    _paid(p) {
        if (!p.document || (p.description || '').includes(p.document)) return '';
        return ` <span style="color:var(--text-muted);">for ${escapeHtml(p.document)}</span>`;
    },

    _recentHtml(recent) {
        if (!recent.length) return '';
        const rows = recent.map(d => {
            const status = d.voided ? 'Void' : (d.reconciled ? 'Reconciled' : '');
            const canVoid = !d.voided && !d.reconciled;
            return `<tr style="${d.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(d.date)}</td>
                <td>${escapeHtml(d.account_name || '')}</td>
                <td>${escapeHtml(d.reference || '')}</td>
                <td class="amount">${d.items == null ? '' : d.items}</td>
                <td class="amount">${formatCurrency(d.amount)}</td>
                <td>${escapeHtml(status)}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="DepositsPage.view(${d.id})">View</button>
                    ${canVoid ? `<button class="btn btn-sm btn-secondary" onclick="DepositsPage.voidDeposit(${d.id})">Void</button>` : ''}</td>
            </tr>`;
        }).join('');
        return `
            <h3 style="margin:20px 0 8px; font-size:14px;">Recent deposits</h3>
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:6px;">
                To take a payment out of a deposit, void the deposit: its payments go back on the list above to deposit again.
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Deposited To</th><th scope="col">Slip #</th>
                <th scope="col" class="amount">Payments</th><th scope="col" class="amount">Amount</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`;
    },

    // One deposit and the payments it took, with Void while it can still be
    // voided. The bank register links a deposit to #/deposits/{id}, which
    // said "Page not found" (explore 2.17.3).
    async view(id) {
        const d = await API.get(`/deposits/${id}`);
        const status = d.voided ? 'Void' : (d.reconciled ? 'Reconciled' : 'Deposited');
        const rows = (d.payments || []).map(p => `<tr>
                <td>${formatDate(p.date)}</td>
                <td>${escapeHtml(p.received_from || '')}</td>
                <td>${escapeHtml(p.description)}${DepositsPage._paid(p)}</td>
                <td>${escapeHtml(DepositsPage._ref(p))}</td>
                <td>${escapeHtml(p.method || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');
        const payments = rows
            ? `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Received From</th><th scope="col">Description</th>
                <th scope="col">Check # / Ref</th><th scope="col">Method</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows}</tbody></table></div>`
            : `<p style="font-size:11px; color:var(--text-muted);">${d.voided
                ? 'This deposit is void: its payments went back on the Make Deposits list.'
                : 'This deposit does not list its payments: it was recorded as an amount (imported, or made before deposits kept their list).'}</p>`;
        const canVoid = !d.voided && !d.reconciled;
        openModal(`Deposit — ${formatDate(d.date)}`, `
            <div style="margin-bottom:12px;">
                <strong>Deposited to:</strong> ${escapeHtml(d.account_name || '')}<br>
                <strong>Date:</strong> ${formatDate(d.date)}<br>
                ${d.reference ? `<strong>Slip #:</strong> ${escapeHtml(d.reference)}<br>` : ''}
                <strong>Amount:</strong> ${formatCurrency(d.amount)}<br>
                <strong>Status:</strong> ${status}${d.reconciled && !d.voided ? ' — on a reconciled bank statement, so it can\'t be voided' : ''}
            </div>
            ${payments}
            <div class="form-actions">
                ${canVoid ? `<button type="button" class="btn btn-danger" onclick="DepositsPage.voidDeposit(${d.id})">Void</button>` : ''}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    toggleAll() {
        const checked = $('#dep-select-all').checked;
        $$('.dep-check').forEach(cb => { cb.checked = checked; });
        DepositsPage.recalc();
    },

    recalc() {
        let cents = 0, count = 0;
        $$('.dep-check').forEach(cb => {
            if (cb.checked) {
                cents += Math.round((parseFloat(cb.dataset.amount) || 0) * 100);
                count++;
            }
        });
        const el = $('#deposit-total');
        if (el) el.textContent = `Selected: ${formatCurrency(cents / 100)} (${count} items)`;
    },

    async makeDeposit() {
        const lineIds = [];
        let cents = 0;
        $$('.dep-check').forEach(cb => {
            if (cb.checked) {
                lineIds.push(parseInt(cb.dataset.lineid));
                cents += Math.round((parseFloat(cb.dataset.amount) || 0) * 100);
            }
        });
        const total = cents / 100;

        if (lineIds.length === 0) { toast('Select payments to deposit', 'error'); return; }

        const bankAcctId = $('#deposit-bank-acct')?.value;
        if (!bankAcctId) { toast('Select a bank account', 'error'); return; }

        try {
            await API.post('/deposits', {
                deposit_to_account_id: parseInt(bankAcctId),
                date: $('#deposit-date').value,
                total: total,
                reference: $('#deposit-ref')?.value || null,
                class_id: $('#deposit-class')?.value ? parseInt($('#deposit-class').value) : null,
                line_ids: lineIds,
            });
            toast(`Deposited ${formatCurrency(total)}`);
            App.navigate('#/deposits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidDeposit(id) {
        if (!confirm('Void this deposit? A reversing entry is posted, and its payments go back on the list to deposit again.')) return;
        try {
            await API.post(`/deposits/${id}/void`);
            toast('Deposit voided; its payments are back on the list');
            closeModal();
            // off a deposit's own address (#/deposits/12), back to the list
            if (location.hash === '#/deposits') App.navigate('#/deposits');
            else location.hash = '#/deposits';
        } catch (err) { toast(err.message, 'error'); }
    },
};
