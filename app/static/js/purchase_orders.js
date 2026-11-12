/**
 * Purchase Orders — vendor-facing non-posting documents
 * Feature 6: CRUD + convert to bill
 */
const PurchaseOrdersPage = {
    // The status badge. A commitment that is sent reads "Issued" in
    // construction mode, as the form and the Issue button say.
    statusBadge(status) {
        const label = Terms.isConstruction() && status === 'sent' ? 'Issued' : status;
        return `<span class="badge badge-${status}">${label}</span>`;
    },

    showAll() { PurchaseOrdersPage._showAll = true; App.navigate(location.hash); },

    async render() {
        if (Terms.isConstruction()) await TradePackages.load();
        const { rows: pos, note: capNote } = await listRows(PurchaseOrdersPage, '/purchase-orders', 'PurchaseOrdersPage.showAll()', 'purchase orders');
        // Construction shows a commitment's type and its package; a business
        // company keeps today's columns.
        const con = Terms.isConstruction();
        const columns = con
            ? ['#', 'Vendor', 'Type', 'Package', 'Date', 'Status', { label: 'Total', cls: 'amount' }, 'Actions']
            : ['#', 'Vendor', 'Date', 'Status', { label: 'Total', cls: 'amount' }, 'Actions'];
        return renderListPage({
            title: 'Purchase Orders',
            headerHtml: `<button class="btn btn-primary" onclick="PurchaseOrdersPage.showForm()">+ New PO</button>` + capNote,
            empty: '<p>No purchase orders yet</p>',
            columns,
            items: pos,
            row: po => `<tr>
                    <td><strong>${escapeHtml(po.po_number)}</strong></td>
                    <td>${escapeHtml(po.vendor_name || '')}</td>
                    ${con ? `<td>${po.commitment_type === 'subcontract' ? 'Subcontract' : 'Purchase order'}</td>` : ''}
                    ${con ? `<td>${escapeHtml(po.trade_package_name || '—')}</td>` : ''}
                    <td>${formatDate(po.date)}</td>
                    <td>${PurchaseOrdersPage.statusBadge(po.status)}</td>
                    <td class="amount">${formatCurrency(po.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.view(${po.id})">View</button>
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.showForm(${po.id})">Edit</button>
                        ${con && po.status === 'draft' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.issue(${po.id})" title="Issue it to the vendor; it then counts as bought and committed">Issue</button>` : ''}
                        ${con && po.status !== 'closed' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.subPayApp(${po.id})">Sub pay app</button>` : (po.status !== 'closed' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : '')}
                        ${con && (po.retainage_pct || Number(po.retainage_released)) ? `<button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.releaseRetainage(${po.id})">Release retainage</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    // A purchase order is for sending: View shows it, Save PDF and Print
    // make the copy the vendor gets (it could be made and billed, but never
    // seen or sent — W-L19).
    async view(id) {
        const po = await API.get(`/purchase-orders/${id}`);
        // In construction a commitment's lines are its schedule of values —
        // scheduled value, billed to date and what remains.
        const con = Terms.isConstruction();
        const head = con
            ? `<tr><th scope="col">Description</th><th scope="col" class="amount">Scheduled value</th><th scope="col" class="amount">Billed to date</th><th scope="col" class="amount">Remaining</th></tr>`
            : `<tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr>`;
        const linesHtml = po.lines.map(l => {
            if (con) {
                const rem = (Number(l.amount) || 0) - (Number(l.billed_amount) || 0);
                return `<tr><td>${escapeHtml(l.description || '')}</td>
                    <td class="amount">${formatCurrency(l.amount)}</td>
                    <td class="amount">${formatCurrency(l.billed_amount)}</td>
                    <td class="amount">${formatCurrency(rem)}</td></tr>`;
            }
            return `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`;
        }).join('');
        const conMeta = con ? `<br><strong>Type:</strong> ${po.commitment_type === 'subcontract' ? 'Subcontract' : 'Purchase order'}${po.trade_package_name ? ` · <strong>Package:</strong> ${escapeHtml(po.trade_package_name)}` : ''}${(po.retainage_pct !== null && po.retainage_pct !== undefined) ? ` · <strong>Retainage:</strong> ${po.retainage_pct}%` : ''}` : '';
        openModal(`Purchase Order ${po.po_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Vendor:</strong> ${escapeHtml(po.vendor_name || '')}<br>
                <strong>Date:</strong> ${formatDate(po.date)}<br>
                ${po.expected_date ? `<strong>Expected:</strong> ${formatDate(po.expected_date)}<br>` : ''}
                <strong>Status:</strong> ${PurchaseOrdersPage.statusBadge(po.status)}${conMeta}
            </div>
            <div class="table-container"><table>
                <thead>${head}</thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(po.subtotal)}</span></div>
                ${Number(po.tax_amount) ? `<div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(po.tax_amount)}</span></div>` : ''}
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(po.total)}</span></div>
            </div>
            ${po.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(po.notes)}</p>` : ''}
            ${InvoicesPage.attachmentsPanelHtml('purchase_order', po.id)}
            ${typeof activityPanelHtml === 'undefined' ? '' : activityPanelHtml('purchase_order', po.id)}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/purchase-orders/${po.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/purchase-orders/${po.id}/print-preview','_blank')">Print</button>
                ${con && po.status === 'draft' ? `<button class="btn btn-primary" onclick="PurchaseOrdersPage.issue(${po.id})">Issue</button>` : ''}
                ${con && po.status !== 'closed' ? `<button class="btn btn-primary" onclick="PurchaseOrdersPage.subPayApp(${po.id})">Sub pay app</button>` : (po.status !== 'closed' ? `<button class="btn btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : '')}
                ${con && (po.retainage_pct || Number(po.retainage_released)) ? `<button class="btn btn-secondary" onclick="PurchaseOrdersPage.releaseRetainage(${po.id})">Release retainage</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        InvoicesPage.loadAttachments('purchase_order', po.id);
    },

    // Issue a draft commitment (draft -> sent): until it is issued it is in no
    // Buyout, committed or commitment-status figure.
    async issue(id) {
        try {
            await API.put(`/purchase-orders/${id}`, { status: 'sent' });
            toast('Commitment issued');
            closeModal();
            App.navigate('#/purchase-orders');
        } catch (err) { toast(err.message, 'error'); }
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
            commitment_type: 'po',
            trade_package_id: null,
            retainage_pct: null,
            scope_notes: '',
            lines: [],
        };
        if (id) po = await API.get(`/purchase-orders/${id}`);
        if (po.lines.length === 0) po.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];
        PurchaseOrdersPage.lineCount = po.lines.length;
        PurchaseOrdersPage._vendors = vendors;

        if (Terms.isConstruction()) await TradePackages.load();
        const jobGroup = await jobFormGroupHtml(po.job_id || null);
        await CostCodes.load();
        await CostTypes.load();
        // Inactive vendors stay off the picker, except the one this PO names.
        const vendorOpts = vendors
            .filter(v => v.is_active !== false || v.id == po.vendor_id)
            .map(v => `<option value="${v.id}" ${po.vendor_id==v.id?'selected':''}>${escapeHtml(v.name)}</option>`).join('');

        PendingFiles.reset();  // files attached before the first save (#881)
        openModal(id ? 'Edit Purchase Order' : 'New Purchase Order', `
            <form id="po-form" onsubmit="PurchaseOrdersPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        <select name="vendor_id" required onchange="PurchaseOrdersPage.refreshVendorNote()"><option value="">Select...</option>${vendorOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${po.date}"></div>
                    <div class="form-group"><label>Expected Date</label>
                        <input name="expected_date" type="date" value="${po.expected_date || ''}"></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" min="0" value="${+((po.tax_rate || 0) * 100).toFixed(4)}"
                            oninput="PurchaseOrdersPage.recalc()"
                            title="Sales tax the vendor charges, if any. It becomes part of what the goods cost."></div>
                    ${jobGroup}
                    ${PurchaseOrdersPage.commitmentFieldsHtml(po)}
                </div>
                ${PurchaseOrdersPage.vendorNoteHtml(po)}
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Description</th>${CostCodes.headHtml()}${CostTypes.headHtml()}<th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
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
                ${id ? '' : PendingFiles.stripHtml()}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} PO</button>
                </div>
            </form>`, { onClose: () => PendingFiles.discard() });
        typeof LineOrder !== 'undefined' && LineOrder.attach('#po-lines', { add: () => PurchaseOrdersPage.addLine(), changed: () => PurchaseOrdersPage.recalc() });
        PurchaseOrdersPage.recalc();
    },

    lineHtml(idx, line, items) {
        return `<tr data-poline="${idx}" data-line-id="${line.id || ''}">
            <td><select class="line-item" onchange="PurchaseOrdersPage.itemSel(${idx})">${ItemQuickAdd.options(items, line.item_id)}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            ${CostCodes.cellHtml('line-cost-code', line.cost_code_id || null)}${CostTypes.cellHtml('line-cost-type', line.cost_type || null)}
            <td><input class="line-qty" type="number" step="0.0001" value="${line.quantity || 1}" oninput="PurchaseOrdersPage.recalc()"></td>
            <td><input class="line-rate money" type="number" step="0.0001" value="${line.rate || 0}" oninput="PurchaseOrdersPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1)*(line.rate||0))}</td>
        </tr>`;
    },

    // Construction commitment fields (#553): type, package, retainage and
    // scope. A business company sees none of this — a PO is just a PO.
    commitmentFieldsHtml(po) {
        if (!Terms.isConstruction()) return '';
        const sel = (v, val) => v === val ? 'selected' : '';
        const t = po.commitment_type || 'po';
        const pkgOpts = ['<option value="">Unassigned</option>'].concat(
            (TradePackages._list || []).map(p =>
                `<option value="${p.id}" ${String(po.trade_package_id) === String(p.id) ? 'selected' : ''}>${escapeHtml(p.name)}</option>`)).join('');
        const ret = (po.retainage_pct === null || po.retainage_pct === undefined) ? '' : po.retainage_pct;
        // A saved commitment's status: Issued (sent) is what puts it in the Buyout.
        const st = po.status || 'draft';
        const statusGroup = po.id ? `<div class="form-group"><label>Status</label>
                <select name="status" title="A draft counts nowhere; issue it to count it as bought and committed.">
                    ${[['draft', 'Draft'], ['sent', 'Issued'], ['partial', 'Partially billed'], ['received', 'Received'], ['closed', 'Closed']]
                        .map(([v, label]) => `<option value="${v}" ${sel(st, v)}>${label}</option>`).join('')}
                </select></div>` : '';
        return statusGroup + `
            <div class="form-group"><label>Type</label>
                <select name="commitment_type" onchange="PurchaseOrdersPage.refreshVendorNote()">
                    <option value="po" ${sel(t, 'po')}>Purchase order</option>
                    <option value="subcontract" ${sel(t, 'subcontract')}>Subcontract</option>
                </select></div>
            <div class="form-group"><label>Package</label>
                <select name="trade_package_id">${pkgOpts}</select></div>
            <div class="form-group"><label>Retainage %</label>
                <input name="retainage_pct" type="number" step="0.01" min="0" max="100" value="${ret}"
                    title="Held back on every bill from this commitment, posted to Retainage Payable."></div>
            <div class="form-group" style="grid-column:1/-1"><label>Scope notes</label>
                <textarea name="scope_notes">${escapeHtml(po.scope_notes || '')}</textarea></div>`;
    },

    // A subcontract to a vendor not marked a Sub is allowed, but its license
    // and insurance aren't tracked — say so, don't block.
    vendorNoteHtml(po) {
        return `<div id="po-vendor-note" style="margin:4px 0 8px;font-size:12px;color:var(--text-muted)">${PurchaseOrdersPage._vendorNote(po.vendor_id, po.commitment_type)}</div>`;
    },

    _vendorNote(vendorId, type) {
        if (!Terms.isConstruction() || type !== 'subcontract' || !vendorId) return '';
        const v = (PurchaseOrdersPage._vendors || []).find(x => String(x.id) === String(vendorId));
        if (v && v.vendor_type !== 'sub') {
            return "Vendor is not marked a Sub; its license and insurance aren't tracked.";
        }
        return '';
    },

    refreshVendorNote() {
        const el = $('#po-vendor-note');
        const form = $('#po-form');
        if (!el || !form) return;
        el.textContent = PurchaseOrdersPage._vendorNote(form.vendor_id.value, form.commitment_type ? form.commitment_type.value : 'po');
    },

    addLine() {
        const idx = PurchaseOrdersPage.lineCount++;
        $('#po-lines').insertAdjacentHTML('beforeend',
            PurchaseOrdersPage.lineHtml(idx, {}, PurchaseOrdersPage._items));
        PurchaseOrdersPage.recalc();
    },

    itemSel(idx) {
        const row = $(`[data-poline="${idx}"]`);
        if (row && ItemQuickAdd.selected(row.querySelector('.line-item'), PurchaseOrdersPage._items, () => PurchaseOrdersPage.itemSel(idx), 'purchase')) return;
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
        // to the cent, half up, as the server works it out
        const tax = SalesLines.tax(subtotal, $('#po-form [name="tax_rate"]')?.value);
        if ($('#po-subtotal')) $('#po-subtotal').textContent = formatCurrency(subtotal);
        if ($('#po-tax')) $('#po-tax').textContent = formatCurrency(tax);
        if ($('#po-total')) $('#po-total').textContent = formatCurrency(subtotal + tax);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#po-lines tr').forEach((row, i) => {
            // Send an existing line's id so its billed-to-date survives the
            // edit (#553); a new line has none.
            const lineId = row.dataset.lineId ? parseInt(row.dataset.lineId) : null;
            const line = {
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                cost_type: CostTypes.fromRow(row, 'line-cost-type'),
                line_order: i,
            };
            if (lineId) line.id = lineId;
            lines.push(line);
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
        if (Terms.isConstruction()) {
            data.commitment_type = form.commitment_type ? form.commitment_type.value : 'po';
            data.trade_package_id = form.trade_package_id && form.trade_package_id.value ? parseInt(form.trade_package_id.value) : null;
            data.retainage_pct = form.retainage_pct && form.retainage_pct.value !== '' ? parseFloat(form.retainage_pct.value) : null;
            data.scope_notes = form.scope_notes ? (form.scope_notes.value || null) : null;
            if (id && form.status) data.status = form.status.value;
        }
        if (!(await TxnWarn.ok(form, { editing: !!id }))) return;
        try {
            if (id) { await API.put(`/purchase-orders/${id}`, data); toast('PO updated'); }
            else {
                await API.post('/purchase-orders', { ...data, pending_attachments: PendingFiles.ids() });
                PendingFiles.saved();
                toast('PO created');
            }
            closeModal();
            App.navigate('#/purchase-orders');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Sub pay app (#555): the commitment's schedule — previous / this period /
    // remaining / retainage — billed a period at a time through the existing
    // convert. Reads the pay-app schedule, then opens the To Bill dialog
    // (which carries the amount-this-period column and the retainage note).
    async subPayApp(id) {
        try {
            await API.get(`/purchase-orders/${id}/pay-app`);  // the schedule read
        } catch (err) { toast(err.message, 'error'); return; }
        return PurchaseOrdersPage.convertToBill(id);
    },

    // Release retainage (#555): a prompt for the amount (default the whole
    // outstanding) → a bill DR Retainage Payable / CR A/P, payable in Pay Bills.
    async releaseRetainage(id) {
        let sched;
        try { sched = await API.get(`/purchase-orders/${id}/pay-app`); }
        catch (err) { toast(err.message, 'error'); return; }
        const outstanding = sched.retainage_outstanding || 0;
        if (outstanding <= 0) { toast('No retainage outstanding to release.'); return; }
        const raw = prompt(`Retainage outstanding is ${formatCurrency(outstanding)}. Amount to release:`, outstanding.toFixed(2));
        if (raw === null) return;
        const amount = parseFloat(raw);
        if (isNaN(amount) || amount <= 0) { toast('Enter an amount to release.', 'error'); return; }
        try {
            const r = await API.post(`/purchase-orders/${id}/release-retainage`, { amount });
            toast(r.message);
            if (location.hash === '#/bills') App.navigate('#/bills'); else location.hash = '#/bills';
        } catch (err) { toast(err.message, 'error'); }
    },

    // To Bill asks where each line goes before anything posts: an item's
    // expense account, a construction line's cost code or cost type account
    // (material 5100, subcontract 5300 …), or the vendor's default fills the
    // choice in, and a
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
            // a construction line's cost code or type names its account
            if (Terms.isConstruction()) await Promise.all([CostCodes.load(), CostTypes.load()]);
        } catch (err) { toast(err.message, 'error'); return; }
        const itemById = Object.fromEntries(items.map(i => [i.id, i]));
        const posting = PurchaseAccounts.filter(accounts);
        PurchaseOrdersPage._convAccounts = posting;
        // Construction bills in parts: an amount-this-period column, prefilled
        // with the line's remaining. A business company keeps today's dialog.
        const partial = Terms.isConstruction();
        // Delivery tickets (field tickets): an order being received shows what
        // has arrived per line and warns when a line bills past it.
        const receiving = partial && po.lines.some(l => Number(l.received_qty) > 0);
        PurchaseOrdersPage._convReceived = Object.fromEntries(po.lines.map(l => [l.id, {
            value: (Number(l.received_qty) || 0) * (Number(l.rate) || 0), billed: Number(l.billed_amount) || 0,
        }]));
        PurchaseOrdersPage._convReceiving = receiving;
        const rows = po.lines.map((l, n) => {
            const item = l.item_id ? itemById[l.item_id] : null;
            const remaining = (Number(l.amount) || 0) - (Number(l.billed_amount) || 0);
            let cell;
            if (item && item.track_inventory) {
                cell = '<span style="color:var(--text-muted);">Inventory (stock item)</span>';
            } else if (!Number(l.amount)) {
                cell = '<span style="color:var(--text-muted);">Nothing to record</span>';
            } else {
                const pick = (item && item.expense_account_id)
                    || CostTypes.accountFor(l.cost_code_id, l.cost_type)
                    || vendor.default_expense_account_id || '';
                cell = AccountQuickAdd.picker(`<select class="po-conv-account" data-line="${l.id}" aria-label="Account for line ${n + 1}">
                        <option value="">Choose an account...</option>${PurchaseAccounts.options(posting, pick)}</select>`, 'po-convert');
            }
            // The sub pay-app schedule (#555): previous (billed) / this period /
            // remaining / the retainage this period holds back.
            const retThis = remaining * (Number(po.retainage_pct) || 0) / 100;
            const amtCols = partial
                ? `<td class="amount">${formatCurrency(remaining)}</td>`
                + `<td class="amount"><input type="number" step="0.01" min="0" class="po-conv-amount" data-line="${l.id}" value="${remaining.toFixed(2)}" style="width:90px" aria-label="Amount this period for line ${n + 1}" oninput="PurchaseOrdersPage.convReceivedWarn()">`
                + `<div class="po-conv-warn form-hint" data-line="${l.id}" style="color:var(--text-danger)"></div></td>`
                + `<td class="amount">${formatCurrency(retThis)}</td>`
                : '';
            const qtyCols = receiving ? `<td class="amount">${Number(l.quantity) || 0}</td><td class="amount">${Number(l.received_qty) || 0}</td>` : '';
            return `<tr><td>${escapeHtml(l.description || (item ? item.name : ''))}</td>${qtyCols}
                <td class="amount">${formatCurrency(l.amount)}</td>${partial ? `<td class="amount">${formatCurrency(l.billed_amount)}</td>` : ''}${amtCols}<td>${cell}</td></tr>`;
        }).join('');
        const taxNote = Number(po.tax_amount)
            ? `<p class="form-hint">The ${formatCurrency(po.tax_amount)} of tax on this order is part of what these goods cost: it is added to each line's account, not to Sales Tax Payable.</p>`
            : '';
        const retNote = partial && Number(po.retainage_pct)
            ? `<p class="form-hint">${po.retainage_pct}% retainage is held back on this bill and posted to Retainage Payable.</p>`
            : '';
        openModal(`Turn ${po.po_number} into a bill`, `
            <p style="margin-bottom:8px;">Choose where each line is recorded. The bill takes ${escapeHtml(vendor.name)}'s terms (${escapeHtml(vendor.terms || 'Net 30')}) from the date below.</p>
            <div class="form-group" style="max-width:200px;"><label for="po-conv-date">Bill date</label>
                <input type="date" id="po-conv-date" value="${todayISO()}"></div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Line</th>${receiving ? '<th scope="col" class="amount">Ordered</th><th scope="col" class="amount">Received</th>' : ''}<th scope="col" class="amount">Scheduled</th>${partial ? '<th scope="col" class="amount">Previous</th><th scope="col" class="amount">Remaining</th><th scope="col" class="amount">This period</th><th scope="col" class="amount">Retainage</th>' : ''}<th scope="col">Account</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            ${taxNote}
            ${retNote}
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="button" class="btn btn-primary" onclick="PurchaseOrdersPage.doConvert(${po.id})">Create Bill</button>
            </div>`);
        PurchaseOrdersPage.convReceivedWarn();
    },

    // Per line: previous billing plus this period past the value received on
    // the order's delivery tickets is a warning, never a refusal. Returns how
    // many lines it warned on.
    convReceivedWarn() {
        if (!PurchaseOrdersPage._convReceiving) return 0;
        let n = 0;
        for (const inp of $$('.po-conv-amount')) {
            const r = PurchaseOrdersPage._convReceived[inp.dataset.line] || { value: 0, billed: 0 };
            const total = r.billed + (parseFloat(inp.value) || 0);
            const over = (parseFloat(inp.value) || 0) > 0 && total > r.value + 0.005;
            const el = $(`.po-conv-warn[data-line="${inp.dataset.line}"]`);
            if (el) el.textContent = over ? `Bills ${formatCurrency(total)} against ${formatCurrency(r.value)} received` : '';
            if (over) n += 1;
        }
        return n;
    },

    async doConvert(id) {
        let lines;
        const amtInputs = $$('.po-conv-amount');
        if (amtInputs.length) {
            // Construction partial billing: send {line_id, amount} for every
            // line, zeros included, so the server knows this is a partial
            // request and bills only the lines with an amount. Require an
            // account only on a line actually being billed.
            const byLine = {};
            for (const inp of amtInputs) {
                const lid = parseInt(inp.dataset.line);
                byLine[lid] = { line_id: lid, amount: parseFloat(inp.value) || 0 };
            }
            for (const sel of $$('.po-conv-account')) {
                const lid = parseInt(sel.dataset.line);
                const row = byLine[lid];
                if (row && row.amount > 0 && !sel.value) {
                    toast('Choose an account for every line you are billing, then create the bill.', 'error');
                    sel.focus();
                    return;
                }
                if (row && sel.value) row.account_id = parseInt(sel.value);
            }
            lines = Object.values(byLine);
            const over = PurchaseOrdersPage.convReceivedWarn();
            if (over && !confirm(`${over} line${over > 1 ? 's bill' : ' bills'} more than the delivery tickets received. Create the bill anyway?`)) return;
        } else {
            // Business dialog: an account per line, billed in full as today.
            lines = [];
            for (const sel of $$('.po-conv-account')) {
                if (!sel.value) {
                    toast('Choose an account for every line, then create the bill.', 'error');
                    sel.focus();
                    return;
                }
                lines.push({ line_id: parseInt(sel.dataset.line), account_id: parseInt(sel.value) });
            }
        }
        try {
            const date = $('#po-conv-date')?.value || null;
            const result = await API.post(`/purchase-orders/${id}/convert-to-bill`, { lines, date });
            toast(result.message);
            for (const w of result.warnings || []) toast(w, 'warning');
            closeModal();
            // Through the hash, so the address (and a refresh) shows Bills.
            if (location.hash === '#/bills') App.navigate('#/bills');
            else location.hash = '#/bills';
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Turning an order into a bill: each line's Account adds an expense or cost
// of goods account (#485), offered on every line and picked on its own.
if (typeof AccountQuickAdd !== 'undefined') {
    AccountQuickAdd.register('po-convert', {
        types: AccountQuickAdd.PURCHASE_TYPES,
        compact: true,
        chart: () => (PurchaseOrdersPage._convAccounts = PurchaseOrdersPage._convAccounts || []),
        added: () => AccountQuickAdd.refillPurchase($$('.po-conv-account'), PurchaseOrdersPage._convAccounts),
    });
}
