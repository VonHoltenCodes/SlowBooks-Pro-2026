/**
 * Purchase Orders — vendor-facing non-posting documents
 * Feature 6: CRUD + convert to bill
 */
const PurchaseOrdersPage = {
    async render() {
        const pos = await API.get('/purchase-orders');
        return renderListPage({
            title: 'Purchase Orders',
            headerHtml: `<button class="btn btn-primary" onclick="PurchaseOrdersPage.showForm()">+ New PO</button>`,
            empty: '<p>No purchase orders yet</p>',
            columns: ['#', 'Vendor', 'Date', 'Status', { label: 'Total', cls: 'amount' }, 'Actions'],
            items: pos,
            row: po => `<tr>
                    <td><strong>${escapeHtml(po.po_number)}</strong></td>
                    <td>${escapeHtml(po.vendor_name || '')}</td>
                    <td>${formatDate(po.date)}</td>
                    <td>${statusBadge(po.status)}</td>
                    <td class="amount">${formatCurrency(po.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.view(${po.id})">View</button>
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.showForm(${po.id})">Edit</button>
                        ${po.status !== 'closed' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    // A purchase order is for sending: View shows it, Save PDF and Print
    // make the copy the vendor gets (it could be made and billed, but never
    // seen or sent — W-L19).
    async view(id) {
        const po = await API.get(`/purchase-orders/${id}`);
        const linesHtml = po.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');
        openModal(`Purchase Order ${po.po_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Vendor:</strong> ${escapeHtml(po.vendor_name || '')}<br>
                <strong>Date:</strong> ${formatDate(po.date)}<br>
                ${po.expected_date ? `<strong>Expected:</strong> ${formatDate(po.expected_date)}<br>` : ''}
                <strong>Status:</strong> ${statusBadge(po.status)}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(po.subtotal)}</span></div>
                ${Number(po.tax_amount) ? `<div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(po.tax_amount)}</span></div>` : ''}
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(po.total)}</span></div>
            </div>
            ${po.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(po.notes)}</p>` : ''}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/purchase-orders/${po.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/purchase-orders/${po.id}/print-preview','_blank')">Print</button>
                ${po.status !== 'closed' ? `<button class="btn btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    _items: [],
    lineCount: 0,

    async showForm(id = null) {
        const [vendors, items] = await Promise.all([
            API.get('/vendors'),
            API.get('/items?active_only=true'),
        ]);
        PurchaseOrdersPage._items = items;

        // A supplier's tax is not the company's selling rate: a new PO
        // starts at 0% (it pre-filled 8.25%, and the tax then landed on
        // Sales Tax Payable — W-H5, F9).
        let po = {
            vendor_id: '',
            date: todayISO(),
            expected_date: '',
            ship_to: '',
            tax_rate: 0,
            notes: '',
            lines: [],
        };
        if (id) po = await API.get(`/purchase-orders/${id}`);
        if (po.lines.length === 0) po.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];
        PurchaseOrdersPage.lineCount = po.lines.length;

        const jobGroup = await jobFormGroupHtml(po.job_id || null);
        await CostCodes.load();
        // Inactive vendors stay off the picker, except the one this PO names.
        const vendorOpts = vendors
            .filter(v => v.is_active !== false || v.id == po.vendor_id)
            .map(v => `<option value="${v.id}" ${po.vendor_id==v.id?'selected':''}>${escapeHtml(v.name)}</option>`).join('');

        openModal(id ? 'Edit Purchase Order' : 'New Purchase Order', `
            <form id="po-form" onsubmit="PurchaseOrdersPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        <select name="vendor_id" required><option value="">Select...</option>${vendorOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${po.date}"></div>
                    <div class="form-group"><label>Expected Date</label>
                        <input name="expected_date" type="date" value="${po.expected_date || ''}"></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.01" min="0" value="${(po.tax_rate * 100) || 0}"
                            oninput="PurchaseOrdersPage.recalc()"
                            title="Sales tax the vendor charges, if any. It becomes part of what the goods cost."></div>
                    ${jobGroup}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Description</th>${CostCodes.headHtml()}<th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="po-lines">
                        ${po.lines.map((l, i) => PurchaseOrdersPage.lineHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="PurchaseOrdersPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="po-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="po-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="po-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="po-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(po.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} PO</button>
                </div>
            </form>`);
        PurchaseOrdersPage.recalc();
    },

    lineHtml(idx, line, items) {
        const opts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-poline="${idx}">
            <td><select class="line-item" onchange="PurchaseOrdersPage.itemSel(${idx})"><option value="">--</option>${opts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            ${CostCodes.cellHtml('line-cost-code', line.cost_code_id || null)}
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="PurchaseOrdersPage.recalc()"></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="PurchaseOrdersPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1)*(line.rate||0))}</td>
        </tr>`;
    },

    addLine() {
        const idx = PurchaseOrdersPage.lineCount++;
        $('#po-lines').insertAdjacentHTML('beforeend',
            PurchaseOrdersPage.lineHtml(idx, {}, PurchaseOrdersPage._items));
        PurchaseOrdersPage.recalc();
    },

    itemSel(idx) {
        const row = $(`[data-poline="${idx}"]`);
        const item = PurchaseOrdersPage._items.find(i => i.id == row.querySelector('.line-item').value);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = PurchaseLines.price(item);
        }
        PurchaseOrdersPage.recalc();
    },

    // Line amounts and totals as the server will store them: each line
    // rounded to the cent, tax on the subtotal (it showed $0.00 on every
    // line and no total at all — F7).
    recalc() {
        let subtotal = 0;
        $$('#po-lines tr').forEach(row => {
            const amount = PurchaseLines.lineAmount(row);
            subtotal += amount;
            const cell = row.querySelector('.line-amount');
            if (cell) cell.textContent = formatCurrency(amount);
        });
        const pct = parseFloat($('#po-form [name="tax_rate"]')?.value) || 0;
        const tax = PurchaseLines.cents(subtotal * pct / 100);
        if ($('#po-subtotal')) $('#po-subtotal').textContent = formatCurrency(subtotal);
        if ($('#po-tax')) $('#po-tax').textContent = formatCurrency(tax);
        if ($('#po-total')) $('#po-total').textContent = formatCurrency(subtotal + tax);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#po-lines tr').forEach((row, i) => {
            lines.push({
                item_id: row.querySelector('.line-item')?.value ? parseInt(row.querySelector('.line-item').value) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                line_order: i,
            });
        });
        const data = {
            vendor_id: parseInt(form.vendor_id.value),
            job_id: jobIdFromForm(form),
            date: form.date.value,
            expected_date: form.expected_date.value || null,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };
        try {
            if (id) { await API.put(`/purchase-orders/${id}`, data); toast('PO updated'); }
            else { await API.post('/purchase-orders', data); toast('PO created'); }
            closeModal();
            App.navigate('#/purchase-orders');
        } catch (err) { toast(err.message, 'error'); }
    },

    // To Bill asks where each line goes before anything posts: an item's
    // expense account or the vendor's default fills the choice in, and a
    // line neither names waits for one — it used to land on account 6000,
    // Advertising & Marketing (W-H5, F8). Stock items post to Inventory.
    async convertToBill(id) {
        let po, vendor, items, accounts;
        try {
            po = await API.get(`/purchase-orders/${id}`);
            [vendor, items, accounts] = await Promise.all([
                API.get(`/vendors/${po.vendor_id}`),
                // every item, inactive ones too: a line on the order may use one
                API.get('/items?active_only=false'),
                API.get('/accounts?active_only=true'),
            ]);
        } catch (err) { toast(err.message, 'error'); return; }
        const itemById = Object.fromEntries(items.map(i => [i.id, i]));
        const posting = PurchaseAccounts.filter(accounts);
        const rows = po.lines.map((l, n) => {
            const item = l.item_id ? itemById[l.item_id] : null;
            let cell;
            if (item && item.track_inventory) {
                cell = '<span style="color:var(--text-muted);">Inventory (stock item)</span>';
            } else if (!Number(l.amount)) {
                cell = '<span style="color:var(--text-muted);">Nothing to record</span>';
            } else {
                const pick = (item && item.expense_account_id) || vendor.default_expense_account_id || '';
                cell = `<select class="po-conv-account" data-line="${l.id}" aria-label="Account for line ${n + 1}">
                        <option value="">Choose an account...</option>${PurchaseAccounts.options(posting, pick)}</select>`;
            }
            return `<tr><td>${escapeHtml(l.description || (item ? item.name : ''))}</td>
                <td class="amount">${formatCurrency(l.amount)}</td><td>${cell}</td></tr>`;
        }).join('');
        const taxNote = Number(po.tax_amount)
            ? `<p class="form-hint">The ${formatCurrency(po.tax_amount)} of tax on this order is part of what these goods cost: it is added to each line's account, not to Sales Tax Payable.</p>`
            : '';
        openModal(`Turn ${po.po_number} into a bill`, `
            <p style="margin-bottom:8px;">Choose where each line is recorded. The bill is dated ${formatDate(po.date)} and takes ${escapeHtml(vendor.name)}'s terms (${escapeHtml(vendor.terms || 'Net 30')}).</p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Line</th><th scope="col" class="amount">Amount</th><th scope="col">Account</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            ${taxNote}
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="button" class="btn btn-primary" onclick="PurchaseOrdersPage.doConvert(${po.id})">Create Bill</button>
            </div>`);
    },

    async doConvert(id) {
        const lines = [];
        for (const sel of $$('.po-conv-account')) {
            if (!sel.value) {
                toast('Choose an account for every line, then create the bill.', 'error');
                sel.focus();
                return;
            }
            lines.push({ line_id: parseInt(sel.dataset.line), account_id: parseInt(sel.value) });
        }
        try {
            const result = await API.post(`/purchase-orders/${id}/convert-to-bill`, { lines });
            toast(result.message);
            closeModal();
            // Through the hash, so the address (and a refresh) shows Bills.
            if (location.hash === '#/bills') App.navigate('#/bills');
            else location.hash = '#/bills';
        } catch (err) { toast(err.message, 'error'); }
    },
};
