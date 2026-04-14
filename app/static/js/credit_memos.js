/**
 * Credit Memos — issue credits against customers, apply to invoices
 * Feature 5: Credit memo UI with apply-to-invoice workflow
 */
const CreditMemosPage = {
    async render() {
        const memos = await API.get('/credit-memos');
        let html = `
            <div class="page-header">
                <h2>Credit Memos</h2>
                <button class="btn btn-primary" onclick="CreditMemosPage.showForm()">+ New Credit Memo</button>
            </div>`;

        if (memos.length === 0) {
            html += '<div class="empty-state"><p>No credit memos yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>#</th><th>Customer</th><th>Date</th><th>Status</th>
                <th class="amount">Total</th><th class="amount">Remaining</th><th>Actions</th></tr></thead><tbody>`;
            for (const m of memos) {
                html += `<tr>
                    <td><strong>${escapeHtml(m.memo_number)}</strong></td>
                    <td>${escapeHtml(m.customer_name || '')}</td>
                    <td>${formatDate(m.date)}</td>
                    <td>${statusBadge(m.status)}</td>
                    <td class="amount">${formatCurrency(m.total)}</td>
                    <td class="amount">${formatCurrency(m.balance_remaining)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="CreditMemosPage.emailCreditMemo(${m.id})">Email</button>
                        ${m.status === 'issued' ? `<button class="btn btn-sm btn-primary" onclick="CreditMemosPage.showApply(${m.id})">Apply</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async emailCreditMemo(id) {
        const memo = await API.get(`/credit-memos/${id}`);
        const customer = memo.customer_id ? await API.get(`/customers/${memo.customer_id}`) : null;
        App.showDocumentEmailModal({
            title: `Email Credit Note #${memo.memo_number}`,
            endpoint: `/credit-memos/${id}/email`,
            recipient: customer?.email || '',
            defaultSubject: `Credit Note #${memo.memo_number}`,
            successMessage: 'Credit note emailed',
        });
    },

    _items: [],
    lineCount: 0,

    async showForm() {
        const [customers, items, gstCodes] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/gst-codes'),
        ]);
        App.gstCodes = gstCodes;
        CreditMemosPage._items = items;
        CreditMemosPage.lineCount = 1;

        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        const itemOpts = items.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');

        openModal('New Credit Memo', `
            <form onsubmit="CreditMemosPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <input name="tax_rate" type="hidden" value="0">
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Credit Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th>Item</th><th>Description</th><th class="col-qty">Qty</th><th>GST</th><th class="col-rate">Rate</th></tr></thead>
                    <tbody id="cm-lines">
                        <tr data-cmline="0">
                            <td><select class="line-item"><option value="">--</option>${itemOpts}</select></td>
                            <td><input class="line-desc"></td>
                            <td><input class="line-qty" type="number" step="0.01" value="1"></td>
                            <td><select class="line-gst">${gstOptionsHtml('GST15')}</select></td>
                            <td><input class="line-rate" type="number" step="0.01" value="0"></td>
                        </tr>
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="CreditMemosPage.addLine()">+ Add Line</button>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes"></textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Credit Memo</button>
                </div>
            </form>`);
    },

    addLine() {
        const idx = CreditMemosPage.lineCount++;
        const itemOpts = CreditMemosPage._items.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');
        $('#cm-lines').insertAdjacentHTML('beforeend', `
            <tr data-cmline="${idx}">
                <td><select class="line-item"><option value="">--</option>${itemOpts}</select></td>
                <td><input class="line-desc"></td>
                <td><input class="line-qty" type="number" step="0.01" value="1"></td>
                <td><select class="line-gst">${gstOptionsHtml('GST15')}</select></td>
                <td><input class="line-rate" type="number" step="0.01" value="0"></td>
            </tr>`);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#cm-lines tr').forEach((row, i) => {
            const gst = readGstLinePayload(row);
            lines.push({
                item_id: row.querySelector('.line-item')?.value ? parseInt(row.querySelector('.line-item').value) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: gst.quantity,
                rate: gst.rate,
                gst_code: gst.gst_code,
                gst_rate: gst.gst_rate,
                line_order: i,
            });
        });
        try {
            await API.post('/credit-memos', {
                customer_id: parseInt(form.customer_id.value),
                date: form.date.value,
                tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
                notes: form.notes.value || null,
                lines,
            });
            toast('Credit memo created');
            closeModal();
            App.navigate('#/credit-memos');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showApply(cmId) {
        const cm = await API.get(`/credit-memos/${cmId}`);
        const invoices = await API.get(`/invoices?customer_id=${cm.customer_id}`);
        const openInv = invoices.filter(i => i.status !== 'void' && i.status !== 'paid' && i.balance_due > 0);

        let rows = openInv.map(inv => `
            <tr>
                <td>${escapeHtml(inv.invoice_number)}</td>
                <td class="amount">${formatCurrency(inv.balance_due)}</td>
                <td><input type="number" step="0.01" class="apply-amt" data-inv="${inv.id}" value="0" style="width:80px;"></td>
            </tr>`).join('');
        if (!rows) rows = '<tr><td colspan="3">No open invoices for this customer</td></tr>';

        openModal(`Apply Credit ${cm.memo_number}`, `
            <p style="margin-bottom:8px;">Credit remaining: <strong>${formatCurrency(cm.balance_remaining)}</strong></p>
            <div class="table-container"><table>
                <thead><tr><th>Invoice</th><th class="amount">Balance</th><th class="amount">Apply</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="CreditMemosPage.doApply(${cmId})">Apply Credit</button>
            </div>`);
    },

    async doApply(cmId) {
        const inputs = $$('.apply-amt');
        for (const input of inputs) {
            const amt = parseFloat(input.value) || 0;
            if (amt > 0) {
                try {
                    await API.post(`/credit-memos/${cmId}/apply`, {
                        invoice_id: parseInt(input.dataset.inv), amount: amt,
                    });
                } catch (err) { toast(err.message, 'error'); return; }
            }
        }
        toast('Credit applied');
        closeModal();
        App.navigate('#/credit-memos');
    },
};
