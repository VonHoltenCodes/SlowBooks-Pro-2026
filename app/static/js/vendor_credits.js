/**
 * Vendor Credits — the AP-side counterpart of a credit memo (issue #129).
 *
 * A supplier credits you for returned or short-shipped materials. The
 * document debits Accounts Payable and credits the expense (or Inventory,
 * for stock going back). Applying it to a bill posts nothing — it decides
 * which bill the credit settles.
 */
const VendorCreditsPage = {
    showAll() { VendorCreditsPage._showAll = true; App.navigate(location.hash); },

    async render() {
        const { rows: credits, note: capNote } = await listRows(VendorCreditsPage, '/vendor-credits', 'VendorCreditsPage.showAll()', 'vendor credits');
        return renderListPage({
            title: 'Vendor Credits',
            headerHtml: `<button class="btn btn-primary" onclick="VendorCreditsPage.showForm()">+ New Vendor Credit</button>` + capNote,
            empty: '<p>No vendor credits yet. Enter one when a supplier credits you for a return, a short shipment or an overcharge.</p>',
            columns: ['#', 'Vendor', 'Date', 'Ref', 'Status',
                { label: 'Total', cls: 'amount' }, { label: 'Remaining', cls: 'amount' }, 'Actions'],
            items: credits,
            row: c => `<tr>
                    <td><strong>${escapeHtml(c.credit_number)}</strong></td>
                    <td>${escapeHtml(c.vendor_name || '')}</td>
                    <td>${formatDate(c.date)}</td>
                    <td>${escapeHtml(c.ref_number || '')}</td>
                    <td>${statusBadge(c.status)}</td>
                    <td class="amount">${formatCurrency(c.total)}</td>
                    <td class="amount">${formatCurrency(c.balance_remaining)}</td>
                    <td class="actions">
                        ${c.status === 'issued' ? `<button class="btn btn-sm btn-primary" onclick="VendorCreditsPage.showApply(${c.id})">Apply</button>` : ''}
                        ${c.status !== 'void' ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="VendorCreditsPage.void(${c.id})">Void</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    async view(id) {
        const c = await API.get(`/vendor-credits/${id}`);
        const lines = (c.lines || []).map(l => `<tr>
                <td>${escapeHtml(l.description || '')}</td>
                <td class="amount">${l.quantity}</td>
                <td class="amount">${formatCurrency(l.rate)}</td>
                <td class="amount">${formatCurrency(l.amount)}</td>
            </tr>`).join('');
        openModal(`Vendor Credit ${escapeHtml(c.credit_number)}`, `
            <p>${escapeHtml(c.vendor_name || '')} &middot; ${formatDate(c.date)} &middot; ${statusBadge(c.status)}${c.discount_bill_payment_id ? ' (early-payment discount; void its bill payment to undo it)' : ''}</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${lines}</tbody>
            </table></div>
            <p style="margin-top:8px;">Total ${formatCurrency(c.total)} &middot; applied ${formatCurrency(c.amount_applied)} &middot; remaining <strong>${formatCurrency(c.balance_remaining)}</strong></p>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/vendor-credits/${Number(id)}`);
        return '';
    },

    async void(id) {
        if (!confirm('Void this vendor credit? Any applied credit goes back onto the bill, a reversing entry is posted, and returned stock goes back on the shelf.')) return;
        try {
            await API.post(`/vendor-credits/${id}/void`, {});
            toast('Vendor credit voided');
            App.navigate('#/vendor-credits');
        } catch (err) { toast(err.message, 'error'); }
    },

    _items: [],
    _accounts: [],
    _vendors: [],
    lineCount: 0,

    async showForm() {
        const [vendors, items, accounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/accounts?active_only=true'),
        ]);
        VendorCreditsPage._items = items;
        VendorCreditsPage._vendors = vendors;
        VendorCreditsPage._accounts = PurchaseAccounts.filter(accounts);
        VendorCreditsPage.lineCount = 1;
        const classGroup = await classFormGroupHtml();

        const vendOpts = vendors.map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');

        openModal('New Vendor Credit', `
            <form id="vc-form" onsubmit="VendorCreditsPage.save(event)">
                <p class="form-hint">Enter the credit as a positive amount. It reduces what you owe this vendor, and you choose which bill it settles afterwards.</p>
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        <select name="vendor_id" required><option value="">Select...</option>${vendOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Their credit note #</label>
                        <input name="ref_number" placeholder="optional"></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" min="0" value="0" oninput="VendorCreditsPage.recalc()"></div>
                    ${classGroup}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Credit Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Account</th><th scope="col">Description</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="vc-lines">${VendorCreditsPage.lineHtml(0)}</tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="VendorCreditsPage.addLine()">+ Add Line</button>
                <div class="invoice-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="vc-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="vc-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="vc-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes"></textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Vendor Credit</button>
                </div>
            </form>`);
        typeof LineOrder !== 'undefined' && LineOrder.attach('#vc-lines', { add: () => VendorCreditsPage.addLine(), changed: () => VendorCreditsPage.recalc() });
    },

    lineHtml(idx) {
        const acctOpts = PurchaseAccounts.options(VendorCreditsPage._accounts);
        return `<tr data-vcline="${idx}">
                <td><select class="line-item" onchange="VendorCreditsPage.itemSelected(this)">${ItemQuickAdd.options(VendorCreditsPage._items)}</select></td>
                <td>${AccountQuickAdd.picker(`<select class="line-account" title="Blank: the item's expense account, else the vendor's default"><option value="">Item / vendor default</option>${acctOpts}</select>`, 'vendor-credit')}</td>
                <td><input class="line-desc"></td>
                <td><input class="line-qty" type="number" step="0.0001" value="1" oninput="VendorCreditsPage.recalc()"></td>
                <td><input class="line-rate money" type="number" step="0.0001" value="0" oninput="VendorCreditsPage.recalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
            </tr>`;
    },

    addLine() {
        $('#vc-lines').insertAdjacentHTML('beforeend', VendorCreditsPage.lineHtml(VendorCreditsPage.lineCount++));
    },

    // Picking an item fills what it cost (the rate stayed 0 — W-M1).
    itemSelected(select) {
        if (ItemQuickAdd.selected(select, VendorCreditsPage._items, () => VendorCreditsPage.itemSelected(select), 'purchase')) return;
        const row = select.closest('tr');
        const item = VendorCreditsPage._items.find(i => i.id == select.value);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = PurchaseLines.price(item);
        }
        VendorCreditsPage.recalc();
    },

    recalc() {
        let subtotal = 0;
        $$('#vc-lines tr').forEach(row => {
            const amount = PurchaseLines.lineAmount(row);
            subtotal += amount;
            const cell = row.querySelector('.line-amount');
            if (cell) cell.textContent = formatCurrency(amount);
        });
        // to the cent, half up, as the server works it out
        const tax = SalesLines.tax(subtotal, $('#vc-form [name="tax_rate"]')?.value);
        if ($('#vc-subtotal')) $('#vc-subtotal').textContent = formatCurrency(subtotal);
        if ($('#vc-tax')) $('#vc-tax').textContent = formatCurrency(tax);
        if ($('#vc-total')) $('#vc-total').textContent = formatCurrency(subtotal + tax);
        return PurchaseLines.cents(subtotal + tax);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        if (VendorCreditsPage.recalc() <= 0) {
            toast('Enter the amount of the credit: a quantity and rate on at least one line.', 'error');
            return;
        }
        const vendor = VendorCreditsPage._vendors.find(v => v.id == form.vendor_id.value);
        const lines = [];
        let missing = 0;
        $$('#vc-lines tr').forEach((row, i) => {
            const acct = row.querySelector('.line-account')?.value;
            const itemId = ItemQuickAdd.itemId(row.querySelector('.line-item'));
            const item = itemId ? VendorCreditsPage._items.find(it => it.id == itemId) : null;
            const named = acct || (item && (item.track_inventory || item.expense_account_id)) || (vendor && vendor.default_expense_account_id);
            if (!missing && !named && PurchaseLines.lineAmount(row) > 0) missing = i + 1;
            lines.push({
                item_id: itemId,
                account_id: acct ? parseInt(acct) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                line_order: i,
            });
        });
        if (missing) {
            toast(`Choose an account for line ${missing}: its item has none and the vendor has no default expense account.`, 'error');
            return;
        }
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form))) return;
        try {
            const saved = await API.post('/vendor-credits', {
                vendor_id: parseInt(form.vendor_id.value),
                date: form.date.value,
                ref_number: form.ref_number.value || null,
                tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
                notes: form.notes.value || null,
                class_id: classIdFromForm(form),
                lines,
            });
            toastPosted('Vendor credit created');
            // stock the return took below what was on hand (W-74): posted, and said
            for (const w of (saved && saved.warnings) || []) toast(w, 'warning');
            closeModal();
            App.navigate('#/vendor-credits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showApply(vcId) {
        const vc = await API.get(`/vendor-credits/${vcId}`);
        const bills = await fetchAllPages(`/bills?vendor_id=${vc.vendor_id}&open_only=true`);
        const open = bills.filter(b => b.status !== 'void' && b.status !== 'paid' && parseFloat(b.balance_due) > 0);

        let rows = open.map(b => `
            <tr>
                <td>${escapeHtml(b.bill_number)}</td>
                <td>${formatDate(b.date)}</td>
                <td class="amount">${formatCurrency(b.balance_due)}</td>
                <td><input type="number" step="0.01" class="vc-apply-amt money" data-bill="${b.id}" value="0" style="width:90px;"></td>
            </tr>`).join('');
        if (!rows) rows = '<tr><td colspan="4">No open bills for this vendor. The credit stays on their account until there is one.</td></tr>';

        openModal(`Apply Credit ${escapeHtml(vc.credit_number)}`, `
            <p style="margin-bottom:8px;">Credit remaining: <strong>${formatCurrency(vc.balance_remaining)}</strong></p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Bill</th><th scope="col">Date</th><th scope="col" class="amount">Balance</th><th scope="col" class="amount">Apply</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="VendorCreditsPage.doApply(${vcId})">Apply Credit</button>
            </div>`);
    },

    async doApply(vcId) {
        const inputs = $$('.vc-apply-amt');
        let applied = 0;
        for (const input of inputs) {
            const amt = parseFloat(input.value) || 0;
            if (amt > 0) {
                try {
                    await API.post(`/vendor-credits/${vcId}/apply`, {
                        bill_id: parseInt(input.dataset.bill), amount: amt,
                    });
                    applied += amt;
                } catch (err) { toast(err.message, 'error'); return; }
            }
        }
        if (!applied) { toast('Enter an amount to apply', 'error'); return; }
        toast('Credit applied');
        closeModal();
        App.navigate('#/vendor-credits');
    },
};

// A credit line's Account cell adds an expense or cost of goods account
// (#485), on every line's picker and picked on its own line.
if (typeof AccountQuickAdd !== 'undefined') {
    AccountQuickAdd.register('vendor-credit', {
        types: AccountQuickAdd.PURCHASE_TYPES,
        compact: true,
        chart: () => VendorCreditsPage._accounts,
        added: () => AccountQuickAdd.refillPurchase($$('#vc-lines select.line-account'), VendorCreditsPage._accounts),
    });
}
