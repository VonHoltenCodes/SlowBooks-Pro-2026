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
            html += `<div class="empty-state">
                <p>No customers yet.</p>
                <button class="btn btn-primary" onclick="CustomersPage.showForm()" style="margin-top:10px;">+ Create your first customer</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Company</th><th>Phone</th><th>Email</th>
                    <th class="amount">Balance</th><th>Actions</th>
                </tr></thead>
                <tbody id="customer-tbody">`;
            for (const c of customers) {
                html += `<tr class="clickable customer-row" data-name="${escapeHtml(c.name).toLowerCase()}" onclick="CustomersPage.showDetails(${c.id})">
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

    // -- Customer details modal --------------------------------------------
    // Row-click destination. Shows everything we know about the customer
    // on one screen: contact, addresses, terms, NOTES (editable inline),
    // any reseller permits attached, the last 10 invoices, the last 10
    // payments. Avoid the "click here to see notes, click here to see
    // invoices" gated-screen pattern.
    async showDetails(id) {
        let customer, invoices, payments, permits;
        try {
            [customer, invoices, payments, permits] = await Promise.all([
                API.get(`/customers/${id}`),
                API.get(`/invoices?customer_id=${id}`).catch(() => []),
                API.get(`/payments?customer_id=${id}`).catch(() => []),
                API.get(`/reseller-permits?entity_type=customer&entity_id=${id}`).catch(() => []),
            ]);
        } catch (err) {
            toast(err.message, 'error');
            return;
        }

        const fullAddr = (a1, a2, city, state, zip) => {
            const parts = [a1, a2].filter(Boolean).join(', ');
            const line2 = [city, state, zip].filter(Boolean).join(' ');
            return [parts, line2].filter(Boolean).join('\n');
        };
        const bill = fullAddr(customer.bill_address1, customer.bill_address2, customer.bill_city, customer.bill_state, customer.bill_zip);
        const ship = fullAddr(customer.ship_address1, customer.ship_address2, customer.ship_city, customer.ship_state, customer.ship_zip);

        // -- Permits sub-section: status badge + state + verify link --
        const permitsBody = permits.length === 0
            ? '<p style="color:#888;font-size:13px;margin:0">None on file. <a href="#/reseller-permits" onclick="closeModal()">Add one</a>.</p>'
            : '<ul style="margin:0;padding-left:18px;font-size:13px">' + permits.map(p => {
                let badge = '<span style="color:#1f7a36">Active</span>';
                if (p.is_expired) badge = '<span style="color:#a4242b;font-weight:600">EXPIRED</span>';
                else if (p.days_to_expire !== null && p.days_to_expire <= 30) badge = `<span style="color:#a8761f;font-weight:600">Expires in ${p.days_to_expire}d</span>`;
                else if (!p.is_active) badge = '<span style="color:#888">Inactive</span>';
                const link = p.verification_url ? ` · <a href="${escapeHtml(p.verification_url)}" target="_blank" rel="noopener">Open ${escapeHtml(p.jurisdiction)} lookup</a>` : '';
                return `<li>${escapeHtml(p.jurisdiction)} · <code>${escapeHtml(p.permit_number)}</code> · ${badge}${p.expires_at ? ` · exp ${escapeHtml(p.expires_at)}` : ''}${link}</li>`;
            }).join('') + '</ul>';

        // -- Invoices + payments — last 10 each, click-through to detail --
        const invRows = invoices.slice(0, 10).map(i =>
            `<tr style="cursor:pointer" onclick="closeModal();App.navigate('#/invoices')">
                <td>${escapeHtml(i.invoice_number || '')}</td>
                <td>${escapeHtml(i.date || '')}</td>
                <td class="amount">${formatCurrency(i.total)}</td>
                <td>${escapeHtml(i.status || '')}</td>
            </tr>`).join('');
        const payRows = payments.slice(0, 10).map(p =>
            `<tr>
                <td>${escapeHtml(p.date || '')}</td>
                <td>${escapeHtml(p.payment_method || '')}</td>
                <td>${escapeHtml(p.reference || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');

        const html = `
            <!-- header: name + balance + quick actions -->
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:12px">
                <div>
                    <h3 style="margin:0;font-size:18px">${escapeHtml(customer.name)}</h3>
                    ${customer.company ? `<div style="color:#666;font-size:13px">${escapeHtml(customer.company)}</div>` : ''}
                    <div style="margin-top:6px;font-size:12px;color:#888">
                        ${customer.is_active === false ? '<span style="color:#a4242b">Inactive</span>' : '<span style="color:#1f7a36">Active</span>'}
                        &nbsp;·&nbsp; Terms: ${escapeHtml(customer.terms || 'Net 30')}
                        ${customer.credit_limit ? ` · Credit limit: ${formatCurrency(customer.credit_limit)}` : ''}
                        ${customer.tax_id ? ` · Tax ID: <code>${escapeHtml(customer.tax_id)}</code>` : ''}
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">Balance</div>
                    <div style="font-size:22px;font-weight:700;color:${parseFloat(customer.balance) > 0 ? '#a4242b' : '#1a1a2e'}">
                        ${formatCurrency(customer.balance)}
                    </div>
                    <div style="margin-top:8px">
                        <button class="btn btn-sm btn-primary" onclick="closeModal();InvoicesPage.showForm(null,${id})">New Invoice</button>
                        <button class="btn btn-sm btn-secondary" onclick="closeModal();PaymentsPage.showForm(null,${id})">Receive Payment</button>
                        <button class="btn btn-sm btn-secondary" onclick="CustomersPage.showForm(${id})">Edit</button>
                    </div>
                </div>
            </div>

            <!-- contact + addresses + permits in 3 columns -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Contact</h4>
                    <div style="font-size:13px">
                        ${customer.email ? `<div>📧 ${escapeHtml(customer.email)}</div>` : ''}
                        ${customer.phone ? `<div>☎ ${escapeHtml(customer.phone)}</div>` : ''}
                        ${customer.mobile ? `<div>📱 ${escapeHtml(customer.mobile)}</div>` : ''}
                        ${customer.website ? `<div>🌐 ${escapeHtml(customer.website)}</div>` : ''}
                        ${!customer.email && !customer.phone && !customer.mobile ? '<span style="color:#888">No contact info</span>' : ''}
                    </div>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Billing</h4>
                    <pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(bill || '—')}</pre>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Shipping</h4>
                    <pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(ship || (bill ? '(same as billing)' : '—'))}</pre>
                </div>
            </div>

            <!-- notes (inline editable) -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0;display:flex;justify-content:space-between">
                    <span>Notes</span>
                    <span id="cust-note-status-${id}" style="font-size:10px;color:#888;text-transform:none;letter-spacing:0;font-weight:normal"></span>
                </h4>
                <textarea id="cust-notes-${id}" rows="3" style="width:100%;font-size:13px;font-family:inherit"
                    placeholder="Internal notes about this customer — visible to everyone with admin access."
                    onblur="CustomersPage._saveNotes(${id}, this.value)">${escapeHtml(customer.notes || '')}</textarea>
            </div>

            <!-- reseller permits -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Reseller permits</h4>
                ${permitsBody}
            </div>

            <!-- recent invoices + payments side by side -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Recent invoices (${invoices.length})</h4>
                    ${invoices.length === 0 ? '<p style="color:#888;font-size:13px;margin:0">No invoices yet</p>' :
                        `<table class="data-table" style="font-size:12px">
                            <thead><tr><th>#</th><th>Date</th><th class="amount">Total</th><th>Status</th></tr></thead>
                            <tbody>${invRows}</tbody>
                        </table>`}
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:#888;margin:0 0 4px 0">Recent payments (${payments.length})</h4>
                    ${payments.length === 0 ? '<p style="color:#888;font-size:13px;margin:0">No payments yet</p>' :
                        `<table class="data-table" style="font-size:12px">
                            <thead><tr><th>Date</th><th>Method</th><th>Ref</th><th class="amount">Amount</th></tr></thead>
                            <tbody>${payRows}</tbody>
                        </table>`}
                </div>
            </div>`;

        openModal(`Customer — ${customer.name}`, html);
    },

    async _saveNotes(id, value) {
        const status = document.getElementById(`cust-note-status-${id}`);
        if (status) status.textContent = 'saving…';
        try {
            await API.put(`/customers/${id}`, { notes: value });
            if (status) {
                status.textContent = '✓ saved';
                setTimeout(() => { if (status) status.textContent = ''; }, 1500);
            }
        } catch (err) {
            if (status) status.textContent = '⚠ save failed';
            toast(err.message, 'error');
        }
    },

    async showForm(id = null) {
        let c = { name: '', company: '', email: '', phone: '', mobile: '', fax: '', website: '',
            bill_address1: '', bill_address2: '', bill_city: '', bill_state: '', bill_zip: '',
            ship_address1: '', ship_address2: '', ship_city: '', ship_state: '', ship_zip: '',
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
                    <div class="form-group"><label>State</label>
                        <input name="bill_state" value="${escapeHtml(c.bill_state || '')}"></div>
                    <div class="form-group"><label>ZIP</label>
                        <input name="bill_zip" value="${escapeHtml(c.bill_zip || '')}"></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Shipping Address</h3>
                <div class="form-grid">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="ship_address1" value="${escapeHtml(c.ship_address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="ship_address2" value="${escapeHtml(c.ship_address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="ship_city" value="${escapeHtml(c.ship_city || '')}"></div>
                    <div class="form-group"><label>State</label>
                        <input name="ship_state" value="${escapeHtml(c.ship_state || '')}"></div>
                    <div class="form-group"><label>ZIP</label>
                        <input name="ship_zip" value="${escapeHtml(c.ship_zip || '')}"></div>
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

    async save(e, id, force) {
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
                await API.post('/customers', data, force ? { query: { force: true } } : undefined);
                toast('Customer created');
            }
            closeModal();
            App.navigate(location.hash);
        } catch (err) {
            // Phase 11: backend returns 409 with {duplicates:[...]} when a
            // similarly-named active customer already exists.
            if (err.status === 409 && err.detail && err.detail.duplicates) {
                CustomersPage._confirmDuplicate(e.target, id, data, err.detail.duplicates);
                return;
            }
            toast(err.message, 'error');
        }
    },

    _confirmDuplicate(formEl, id, data, duplicates) {
        const list = duplicates.map(d =>
            `<li><strong>${escapeHtml(d.name)}</strong>
              <span style="color:var(--text-muted);font-size:11px">
              (${Math.round(d.similarity * 100)}% match)</span></li>`
        ).join('');
        openModal('Possible Duplicate Customer', `
            <div style="font-size:13px; line-height:1.5;">
              <p>A similar customer name already exists:</p>
              <ul style="margin:8px 0 12px 20px;">${list}</ul>
              <p>Create <strong>${escapeHtml(data.name)}</strong> anyway, or cancel and reuse the existing one?</p>
            </div>
            <div class="form-actions">
              <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
              <button type="button" class="btn btn-primary"
                onclick="CustomersPage._forceCreate(${id ? id : 'null'})">
                Create Anyway
              </button>
            </div>
        `);
        CustomersPage._pendingForm = formEl;
    },

    async _forceCreate(id) {
        const formEl = CustomersPage._pendingForm;
        if (!formEl) { closeModal(); return; }
        const fakeEvt = { preventDefault: () => {}, target: formEl };
        closeModal();
        await CustomersPage.save(fakeEvt, id, true);
        CustomersPage._pendingForm = null;
    },
};
