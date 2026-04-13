/**
 * Decompiled from QBW32.EXE!CCustomerCenterView  Offset: 0x000D9200
 * Original was a CFormView with a CListCtrl (report mode) and a tabbed
 * detail panel on the right. The "Customer:Job" hierarchy was stored as
 * a colon-delimited string in CUST.DAT field 0x02 — e.g. "Smith:Kitchen Remodel".
 * We flattened this because nobody actually liked that feature.
 */
const CustomersPage = {
    async render() {
        const customers = await API.get('/customers');
        let html = `
            <div class="page-header">
                <h2>Customers</h2>
                <button class="btn btn-primary" onclick="CustomersPage.showForm()">+ New Customer</button>
            </div>
            <div class="toolbar">
                <input type="text" placeholder="Search customers..." id="customer-search"
                    oninput="CustomersPage.filter(this.value)">
            </div>`;

        if (customers.length === 0) {
            html += `<div class="empty-state"><p>No customers yet</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Company</th><th>Phone</th><th>Email</th>
                    <th class="amount">Balance</th><th>Actions</th>
                </tr></thead>
                <tbody id="customer-tbody">`;
            for (const c of customers) {
                html += `<tr class="clickable customer-row" data-name="${escapeHtml(c.name).toLowerCase()}">
                    <td><strong>${escapeHtml(c.name)}</strong></td>
                    <td>${escapeHtml(c.company) || ''}</td>
                    <td>${escapeHtml(c.phone) || ''}</td>
                    <td>${escapeHtml(c.email) || ''}</td>
                    <td class="amount">${formatCurrency(c.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); CustomersPage.showForm(${c.id})">Edit</button>
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    filter(query) {
        const q = query.toLowerCase();
        $$('.customer-row').forEach(row => {
            row.style.display = row.dataset.name.includes(q) ? '' : 'none';
        });
    },

    async showForm(id = null) {
        let c = { name: '', company: '', email: '', phone: '', mobile: '', fax: '', website: '',
            bill_address1: '', bill_address2: '', bill_city: '', bill_state: '', bill_zip: '', bill_country: 'NZ',
            ship_address1: '', ship_address2: '', ship_city: '', ship_state: '', ship_zip: '', ship_country: 'NZ',
            terms: 'Net 30', credit_limit: '', tax_id: '', is_taxable: true, notes: '' };
        if (id) c = await API.get(`/customers/${id}`);

        const title = id ? 'Edit Customer' : 'New Customer';
        openModal(title, `
            <form id="customer-form" onsubmit="CustomersPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(c.name)}"></div>
                    <div class="form-group"><label>Company</label>
                        <input name="company" value="${escapeHtml(c.company || '')}"></div>
                    <div class="form-group"><label>Email</label>
                        <input name="email" type="email" value="${escapeHtml(c.email || '')}"></div>
                    <div class="form-group"><label>Phone</label>
                        <input name="phone" value="${escapeHtml(c.phone || '')}"></div>
                    <div class="form-group"><label>Mobile</label>
                        <input name="mobile" value="${escapeHtml(c.mobile || '')}"></div>
                    <div class="form-group"><label>Fax</label>
                        <input name="fax" value="${escapeHtml(c.fax || '')}"></div>
                    <div class="form-group"><label>Website</label>
                        <input name="website" value="${escapeHtml(c.website || '')}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option ${c.terms===t?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Billing Address</h3>
                <div class="form-grid">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="bill_address1" value="${escapeHtml(c.bill_address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="bill_address2" value="${escapeHtml(c.bill_address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="bill_city" value="${escapeHtml(c.bill_city || '')}"></div>
                    <div class="form-group"><label>Region</label>
                        <input name="bill_state" value="${escapeHtml(c.bill_state || '')}"></div>
                    <div class="form-group"><label>Postcode</label>
                        <input name="bill_zip" value="${escapeHtml(c.bill_zip || '')}"></div>
                    <input name="bill_country" type="hidden" value="${escapeHtml(c.bill_country || 'NZ')}">
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Shipping Address</h3>
                <div class="form-grid">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="ship_address1" value="${escapeHtml(c.ship_address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="ship_address2" value="${escapeHtml(c.ship_address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="ship_city" value="${escapeHtml(c.ship_city || '')}"></div>
                    <div class="form-group"><label>Region</label>
                        <input name="ship_state" value="${escapeHtml(c.ship_state || '')}"></div>
                    <div class="form-group"><label>Postcode</label>
                        <input name="ship_zip" value="${escapeHtml(c.ship_zip || '')}"></div>
                    <input name="ship_country" type="hidden" value="${escapeHtml(c.ship_country || 'NZ')}">
                </div>
                <div class="form-grid" style="margin-top:16px;">
                    <div class="form-group"><label>Tax ID</label>
                        <input name="tax_id" value="${escapeHtml(c.tax_id || '')}"></div>
                    <div class="form-group"><label>Credit Limit</label>
                        <input name="credit_limit" type="number" step="0.01" value="${c.credit_limit || ''}"></div>
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes">${escapeHtml(c.notes || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Customer</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const form = new FormData(e.target);
        const data = Object.fromEntries(form.entries());
        if (data.credit_limit) data.credit_limit = parseFloat(data.credit_limit);
        else delete data.credit_limit;

        try {
            if (id) {
                await API.put(`/customers/${id}`, data);
                toast('Customer updated');
            } else {
                await API.post('/customers', data);
                toast('Customer created');
            }
            closeModal();
            App.navigate(location.hash);
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};
