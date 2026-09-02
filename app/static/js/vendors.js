/**
 * Vendor Center — a near-mirror of the Customer Center; the two views
 * share their layout on purpose.
 */
const VendorsPage = {
    async render() {
        const vendors = await API.get('/vendors');
        let html = `
            <div class="page-header">
                <h2>Vendors</h2>
                <button class="btn btn-primary" onclick="VendorsPage.showForm()">+ New Vendor</button>
            </div>`;

        if (vendors.length === 0) {
            html += `<div class="empty-state">
                <p>No vendors yet.</p>
                <button class="btn btn-primary" onclick="VendorsPage.showForm()" style="margin-top:10px;">+ Create your first vendor</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Company</th><th>Phone</th><th>Email</th>
                    <th class="amount">Balance</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const v of vendors) {
                html += `<tr>
                    <td><strong>${escapeHtml(v.name)}</strong></td>
                    <td>${escapeHtml(v.company) || ''}</td>
                    <td>${escapeHtml(v.phone) || ''}</td>
                    <td>${escapeHtml(v.email) || ''}</td>
                    <td class="amount">${formatCurrency(v.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="VendorsPage.showForm(${v.id})">Edit</button>
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async showForm(id = null) {
        let v = { name:'', company:'', email:'', phone:'', fax:'', website:'',
            address1:'', address2:'', city:'', state:'', zip:'', country:'US',
            terms:'Net 30', tax_id:'', account_number:'', default_expense_account_id:'',
            is_1099_vendor:false, vendor_1099_type:'', notes:'' };
        if (id) v = await API.get(`/vendors/${id}`);

        const accounts = await API.get('/accounts?account_type=expense');
        const acctOpts = accounts.map(a => `<option value="${a.id}" ${v.default_expense_account_id==a.id?'selected':''}>${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`).join('');

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
                    <div class="form-group"><label>State / County</label>
                        <input name="state" value="${escapeHtml(v.state || '')}"></div>
                    <div class="form-group"><label>ZIP / Postcode</label>
                        <input name="zip" value="${escapeHtml(v.zip || '')}"></div>
                    <div class="form-group"><label>Country</label>
                        <select name="country">${countryOptions(v.country || 'US')}</select></div>
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
                        <select name="default_expense_account_id"><option value="">-- None --</option>${acctOpts}</select></div>
                    <div class="form-group"><label>1099 Vendor</label>
                        <select name="is_1099_vendor">
                            <option value="false" ${!v.is_1099_vendor ? 'selected' : ''}>No</option>
                            <option value="true" ${v.is_1099_vendor ? 'selected' : ''}>Yes</option>
                        </select></div>
                    <div class="form-group"><label>1099 Type</label>
                        <select name="vendor_1099_type">
                            <option value="" ${!v.vendor_1099_type ? 'selected' : ''}>-- None --</option>
                            <option value="NEC" ${v.vendor_1099_type==='NEC' ? 'selected' : ''}>NEC (Non-Employee Comp)</option>
                            <option value="MISC" ${v.vendor_1099_type==='MISC' ? 'selected' : ''}>MISC</option>
                            <option value="INT" ${v.vendor_1099_type==='INT' ? 'selected' : ''}>INT (Interest)</option>
                            <option value="DIV" ${v.vendor_1099_type==='DIV' ? 'selected' : ''}>DIV (Dividends)</option>
                        </select></div>
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes">${escapeHtml(v.notes || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Vendor</button>
                </div>
            </form>`);
    },

    async save(e, id, force) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.default_expense_account_id = data.default_expense_account_id ? parseInt(data.default_expense_account_id) : null;
        data.is_1099_vendor = data.is_1099_vendor === 'true';
        data.vendor_1099_type = data.vendor_1099_type || null;
        try {
            if (id) {
                await API.put(`/vendors/${id}`, data);
                toast('Vendor updated');
            } else {
                await API.post('/vendors', data, force ? { query: { force: true } } : undefined);
                toast('Vendor created');
            }
            closeModal();
            App.navigate(location.hash);
        } catch (err) {
            // Phase 11: backend returns 409 with {duplicates:[...]} when a
            // similarly-named active vendor already exists. Show the matches
            // and let the user confirm-and-create-anyway.
            if (err.status === 409 && err.detail && err.detail.duplicates) {
                VendorsPage._confirmDuplicate(e.target, id, data, err.detail.duplicates);
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
        openModal('Possible Duplicate Vendor', `
            <div style="font-size:13px; line-height:1.5;">
              <p>A similar vendor name already exists:</p>
              <ul style="margin:8px 0 12px 20px;">${list}</ul>
              <p>Create <strong>${escapeHtml(data.name)}</strong> anyway, or cancel and reuse the existing one?</p>
            </div>
            <div class="form-actions">
              <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
              <button type="button" class="btn btn-primary"
                onclick="VendorsPage._forceCreate(${id ? id : 'null'})">
                Create Anyway
              </button>
            </div>
        `);
        // Stash the form so _forceCreate can resubmit it without re-rendering
        VendorsPage._pendingForm = formEl;
    },

    async _forceCreate(id) {
        const formEl = VendorsPage._pendingForm;
        if (!formEl) { closeModal(); return; }
        // Synthesize a submit-like event and replay save() with force=true
        const fakeEvt = { preventDefault: () => {}, target: formEl };
        closeModal();
        await VendorsPage.save(fakeEvt, id, true);
        VendorsPage._pendingForm = null;
    },
};

/**
 * Vendor picker with inline quick-add, shared by the Bill and Expense forms.
 * A scanned receipt names a merchant the books may not know yet; the
 * operator shouldn't have to leave the form to add them (SkyTech build-42
 * lap: "you need to select a vendor or you can't complete").
 *
 * Markup: <select id=ID name="vendor_id"> with a "+ New Vendor" option that
 * reveals #ID-new (name input + Save/Cancel). save() creates the vendor and
 * selects it; ensure() is the form-submit path — it creates the vendor from
 * the name box if the operator picked "+ New Vendor" but never hit Save.
 */
const VendorQuickAdd = {
    NEW: '__new__',

    html(vendors, { id = 'vendor-select', onchange = '', required = true } = {}) {
        const opts = vendors.map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');
        const change = `VendorQuickAdd.selected('${id}');${onchange}`;
        return `
            <select name="vendor_id" id="${id}" ${required ? 'required' : ''} onchange="${change}">
                <option value="">Select...</option><option value="${this.NEW}">+ New Vendor</option>${opts}</select>
            <div id="${id}-new" style="display:none; margin-top:8px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light);">
                <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add Vendor</div>
                <input id="${id}-new-name" placeholder="Name *" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                <div style="display:flex; gap:6px;">
                    <button type="button" class="btn btn-sm btn-primary" onclick="VendorQuickAdd.save('${id}')">Save</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="VendorQuickAdd.cancel('${id}')">Cancel</button>
                </div>
            </div>`;
    },

    selected(id) {
        const sel = $(`#${id}`), box = $(`#${id}-new`);
        if (!sel || !box) return;
        box.style.display = sel.value === this.NEW ? 'block' : 'none';
        if (sel.value === this.NEW) { const n = $(`#${id}-new-name`); if (n && !n.value) n.focus(); }
    },

    // The quick-add name input when it's showing — the scan canvas targets
    // it for the Merchant field.
    nameInput(id) {
        const sel = $(`#${id}`), n = $(`#${id}-new-name`);
        return sel && sel.value === this.NEW ? n : null;
    },

    cancel(id) {
        const sel = $(`#${id}`), box = $(`#${id}-new`);
        if (sel) sel.value = '';
        if (box) box.style.display = 'none';
    },

    // Pick the vendor by name (case-insensitive); otherwise open quick-add
    // with the name filled in. Returns the matched vendor or null.
    prefill(id, name, vendors) {
        const sel = $(`#${id}`);
        if (!sel || !name) return null;
        const match = (vendors || []).find(v => v.name && v.name.toLowerCase() === name.toLowerCase());
        if (match) { sel.value = match.id; this.selected(id); return match; }
        sel.value = this.NEW;
        this.selected(id);
        const n = $(`#${id}-new-name`);
        if (n) n.value = name;
        return null;
    },

    async _create(id, name) {
        let vendor;
        try {
            vendor = await API.post('/vendors', { name });
        } catch (err) {
            // Near-duplicate (OCR spelling of a vendor the books already
            // have): use the existing record rather than minting a twin.
            const dupes = err.status === 409 && err.detail && err.detail.duplicates;
            if (!dupes || !dupes.length) throw err;
            vendor = dupes[0];
            toast(`Using existing vendor "${vendor.name}"`);
        }
        const sel = $(`#${id}`);
        if (sel) {
            if (!sel.querySelector(`option[value="${vendor.id}"]`)) {
                sel.insertAdjacentHTML('beforeend', `<option value="${vendor.id}">${escapeHtml(vendor.name)}</option>`);
            }
            sel.value = vendor.id;
        }
        const box = $(`#${id}-new`);
        if (box) box.style.display = 'none';
        return vendor;
    },

    async save(id) {
        const n = $(`#${id}-new-name`);
        const name = n ? n.value.trim() : '';
        if (!name) { toast('Vendor name is required', 'error'); return null; }
        try {
            const vendor = await this._create(id, name);
            toast(`Vendor "${vendor.name}" added`);
            const sel = $(`#${id}`);
            if (sel) sel.dispatchEvent(new Event('change'));
            return vendor;
        } catch (err) { toast(err.message, 'error'); return null; }
    },

    // Resolve the picker to a vendor id at submit time, creating the
    // quick-add vendor if it's still pending. Throws when nothing usable
    // was chosen so the caller's catch shows the reason.
    async ensure(id, { required = true } = {}) {
        const sel = $(`#${id}`);
        const value = sel ? sel.value : '';
        if (value === this.NEW) {
            const n = $(`#${id}-new-name`);
            const name = n ? n.value.trim() : '';
            if (!name) throw new Error('Enter a name for the new vendor');
            const vendor = await this._create(id, name);
            return vendor.id;
        }
        if (!value) {
            if (required) throw new Error('Select a vendor');
            return null;
        }
        return parseInt(value);
    },
};
