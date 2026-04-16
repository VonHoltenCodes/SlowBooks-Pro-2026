/**
 * Decompiled from QBW32.EXE!CVendorCenterView  Offset: 0x000DD800
 * Nearly identical to CCustomerCenterView — Intuit copy-pasted the customer
 * code and did a find-replace of "Customer" with "Vendor". We know this
 * because the Vendor center still had a "Customer:Job" label in the resource
 * table (RT_DIALOG id=0x00A7) that they forgot to rename. Classic.
 */
const VendorsPage = {
    async render() {
        const vendors = await API.get('/vendors');
        const canManageVendors = App.hasPermission ? App.hasPermission('contacts.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Vendors</h2>
                ${canManageVendors ? `<button class="btn btn-primary" onclick="VendorsPage.showForm()">+ New Vendor</button>` : ''}
            </div>`;

        if (vendors.length === 0) {
            html += `<div class="empty-state"><p>No vendors yet</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Company</th><th>Phone</th><th>Email</th>
                    <th>Default Expense</th><th class="amount">Balance</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const v of vendors) {
                html += `<tr>
                    <td><strong>${escapeHtml(v.name)}</strong></td>
                    <td>${escapeHtml(v.company) || ''}</td>
                    <td>${escapeHtml(v.phone) || ''}</td>
                    <td>${escapeHtml(v.email) || ''}</td>
                    <td>${v.default_expense_account_id ? `#${v.default_expense_account_id}` : ''}</td>
                    <td class="amount">${formatCurrency(v.balance)}</td>
                    <td class="actions">
                        ${canManageVendors ? `<button class="btn btn-sm btn-secondary" onclick="VendorsPage.showForm(${v.id})">Edit</button>` : ''}
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async showForm(id = null) {
        const [accounts, vendor] = await Promise.all([
            API.get('/accounts?active_only=true&account_type=expense'),
            id ? API.get(`/vendors/${id}`) : Promise.resolve(null),
        ]);
        const v = vendor || { name:'', company:'', email:'', phone:'', fax:'', website:'',
            address1:'', address2:'', city:'', state:'', zip:'', country:'NZ',
            terms:'Net 30', tax_id:'', account_number:'', default_expense_account_id:'', notes:'' };
        const expenseOptions = accounts.map(account => `<option value="${account.id}" ${String(v.default_expense_account_id || '') === String(account.id) ? 'selected' : ''}>${escapeHtml(account.account_number || '')} - ${escapeHtml(account.name)}</option>`).join('');

        openModal(id ? 'Edit Vendor' : 'New Vendor', `
            <form id="vendor-form" onsubmit="VendorsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(v.name)}"></div>
                    <div class="form-group"><label>Company</label>
                        <input name="company" value="${escapeHtml(v.company || '')}"></div>
                    <div class="form-group"><label>Email</label>
                        <input name="email" type="email" value="${escapeHtml(v.email || '')}"></div>
                    <div class="form-group"><label>Phone</label>
                        <input name="phone" value="${escapeHtml(v.phone || '')}"></div>
                    <div class="form-group"><label>Fax</label>
                        <input name="fax" value="${escapeHtml(v.fax || '')}"></div>
                    <div class="form-group"><label>Website</label>
                        <input name="website" value="${escapeHtml(v.website || '')}"></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Address</h3>
                <div class="form-grid">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="address1" value="${escapeHtml(v.address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="address2" value="${escapeHtml(v.address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="city" value="${escapeHtml(v.city || '')}"></div>
                    <div class="form-group"><label>Region</label>
                        <input name="state" value="${escapeHtml(v.state || '')}"></div>
                    <div class="form-group"><label>Postcode</label>
                        <input name="zip" value="${escapeHtml(v.zip || '')}"></div>
                    <input name="country" type="hidden" value="${escapeHtml(v.country || 'NZ')}">
                </div>
                <div class="form-grid" style="margin-top:16px;">
                    <div class="form-group"><label>Terms</label>
                        <select name="terms">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option ${v.terms===t?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Tax ID</label>
                        <input name="tax_id" value="${escapeHtml(v.tax_id || '')}"></div>
                    <div class="form-group"><label>Account #</label>
                        <input name="account_number" value="${escapeHtml(v.account_number || '')}"></div>
                    <div class="form-group"><label>Default Expense Account</label>
                        <select name="default_expense_account_id"><option value="">--</option>${expenseOptions}</select></div>
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes">${escapeHtml(v.notes || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Vendor</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.default_expense_account_id = data.default_expense_account_id ? parseInt(data.default_expense_account_id) : null;
        try {
            if (id) { await API.put(`/vendors/${id}`, data); toast('Vendor updated'); }
            else { await API.post('/vendors', data); toast('Vendor created'); }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },
};
