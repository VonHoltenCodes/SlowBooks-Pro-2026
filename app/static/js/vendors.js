/**
 * Vendor Center — a near-mirror of the Customer Center; the two views
 * share their layout on purpose.
 */
const VendorsPage = {
    async render() {
        const vendors = await API.get(`/vendors${ActiveLists.query('vendors')}`);
        ActiveLists.remember('vendors', vendors);
        let html = `
            <div class="page-header">
                <h2>Vendors</h2>
                <button class="btn btn-primary" onclick="VendorsPage.showForm()">+ New Vendor</button>
            </div>
            <div class="toolbar">${ActiveLists.pickerHtml('vendors')}</div>`;

        if (vendors.length === 0 && ActiveLists.emptyText('vendors')) {
            html += `<div class="empty-state"><p>${ActiveLists.emptyText('vendors')}</p></div>`;
        } else if (vendors.length === 0) {
            html += `<div class="empty-state">
                <p>No vendors yet.</p>
                <button class="btn btn-primary" onclick="VendorsPage.showForm()" style="margin-top:10px;">+ Create your first vendor</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Name</th><th scope="col">Company</th><th scope="col">Phone</th><th scope="col">Email</th>
                    <th scope="col" class="amount">Balance</th><th scope="col">Actions</th>
                </tr></thead><tbody>`;
            for (const v of vendors) {
                const inactive = v.is_active === false;
                // a row opens the vendor's page, as a customer's does (#223);
                // the buttons on it keep to themselves
                html += `<tr class="clickable vendor-row${inactive ? ' row--dim' : ''}" onclick="VendorsPage.showDetails(${v.id})">
                    <td><strong>${escapeHtml(v.name)}</strong>${inactive ? ' <span class="badge badge-draft">inactive</span>' : ''}</td>
                    <td>${escapeHtml(v.company) || ''}</td>
                    <td>${escapeHtml(v.phone) || ''}</td>
                    <td>${escapeHtml(v.email) || ''}</td>
                    <td class="amount">${formatCurrency(v.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); VendorsPage.showForm(${v.id})">Edit</button>
                        ${ActiveLists.buttonHtml('vendors', v)}
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    // -- Vendor details modal (#223) ----------------------------------------
    // Row-click destination, the mirror of CustomersPage.showDetails: what
    // we know about the vendor on one screen — contact, address, terms, the
    // default account, 1099, NOTES (editable inline), what's owed, the last
    // 10 bills, the last 10 payments, and credits not applied yet.
    async showDetails(id) {
        let vendor, bills, payments, credits, accounts;
        try {
            [vendor, bills, payments, credits, accounts] = await Promise.all([
                API.get(`/vendors/${id}`),
                fetchAllPages(`/bills?vendor_id=${id}`).catch(() => []),
                API.get(`/bill-payments?vendor_id=${id}`).catch(() => []),
                API.get(`/vendor-credits?vendor_id=${id}`).catch(() => []),
                API.get('/accounts').catch(() => []),
            ]);
        } catch (err) {
            toast(err.message, 'error');
            return;
        }

        const address = [
            [vendor.address1, vendor.address2].filter(Boolean).join(', '),
            [vendor.city, vendor.state, vendor.zip].filter(Boolean).join(' '),
            vendor.country && vendor.country !== 'US' ? vendor.country : '',
        ].filter(Boolean).join('\n');
        const acct = accounts.find(a => a.id === vendor.default_expense_account_id);
        const acctLabel = acct ? `${acct.account_number ? acct.account_number + ' - ' : ''}${acct.name}` : '';

        // -- Bills + payments — last 10 each, newest first, click-through --
        // a row opens the document at its own address, the page's address
        // kept behind it so Back returns here with focus on the link
        // (NEW-38's rule, the vendor's side; NEW-23 review); the row is
        // passed, so the link noted is the row clicked, not the focused one
        const hop = (href) => `ReportsPage._leaveFrom(this);closeModal({ keepAddress: true });App.navigate('${href}')`;
        const rowLink = (href, key, text) => `<a href="${href}" data-row-key="${key}" style="color:var(--text-link); text-decoration:none;" onclick="event.preventDefault()">${escapeHtml(text)}</a>`;
        const billRows = bills.slice(0, 10).map(b =>
            `<tr style="cursor:pointer" onclick="${hop(`#/bills/${b.id}`)}">
                <td>${rowLink(`#/bills/${b.id}`, `bill:${b.id}`, b.bill_number || `#${b.id}`)}</td>
                <td>${formatDate(b.date)}</td>
                <td>${b.due_date ? formatDate(b.due_date) : ''}</td>
                <td>${statusBadge(b.status)}</td>
                <td class="amount">${formatCurrency(b.total)}</td>
                <td class="amount">${formatCurrency(b.balance_due)}</td>
            </tr>`).join('');
        const payRows = payments.slice(0, 10).map(p =>
            `<tr style="cursor:pointer" onclick="${hop(`#/bill-payments/${p.id}`)}">
                <td>${rowLink(`#/bill-payments/${p.id}`, `bill-payment:${p.id}`, formatDate(p.date))}</td>
                <td>${escapeHtml(p.method || '')}${p.is_voided ? ' <span style="color:var(--text-danger)">(void)</span>' : ''}</td>
                <td>${escapeHtml(p.check_number || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');

        // -- Credits the vendor owes us, not yet applied to a bill --
        const open = credits.filter(c => c.status !== 'void' && parseFloat(c.balance_remaining) > 0);
        const creditsHtml = open.length === 0 ? '' : `
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Credits not applied yet (${formatCurrency(open.reduce((t, c) => t + parseFloat(c.balance_remaining), 0))})</h4>
                <ul style="margin:0;padding-left:18px;font-size:13px">${open.map(c => `<li style="margin:2px 0">
                    <a href="javascript:void(0)" onclick="VendorCreditsPage.view(${c.id})">${escapeHtml(c.credit_number)}</a> of ${formatDate(c.date)}: <strong>${formatCurrency(c.balance_remaining)}</strong> left of ${formatCurrency(c.total)}
                </li>`).join('')}</ul>
            </div>`;
        const balance = parseFloat(vendor.balance) || 0;

        const html = `
            <!-- header: name + what's owed + quick actions -->
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:12px">
                <div>
                    <h3 style="margin:0;font-size:18px">${escapeHtml(vendor.name)}</h3>
                    ${vendor.company && vendor.company !== vendor.name ? `<div style="color:var(--text-muted);font-size:13px">${escapeHtml(vendor.company)}</div>` : ''}
                    <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
                        ${vendor.is_active === false ? '<span style="color:var(--text-danger)">Inactive</span>' : '<span style="color:var(--text-success)">Active</span>'}
                        &nbsp;·&nbsp; Terms: ${escapeHtml(vendor.terms || 'Net 30')}
                        ${vendor.is_1099_vendor ? ` · <strong>1099${vendor.vendor_1099_type ? ' ' + escapeHtml(vendor.vendor_1099_type) : ''}</strong>` : ''}
                        ${vendor.tax_id ? ` · Tax ID: <code>${escapeHtml(vendor.tax_id)}</code>` : ''}
                        ${vendor.account_number ? ` · Account #: <code>${escapeHtml(vendor.account_number)}</code>` : ''}
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${balance < 0 ? 'Credit' : 'Balance owed'}</div>
                    <div style="font-size:22px;font-weight:700;color:${balance > 0 ? 'var(--text-danger)' : (balance < 0 ? 'var(--text-success)' : 'var(--text-primary)')}">
                        ${formatCurrency(Math.abs(balance))}
                    </div>
                    <div style="margin-top:8px">
                        <button class="btn btn-sm btn-primary" data-write onclick="closeModal({ keepAddress: true });BillsPage._startVendor=${id};BillsPage.showForm()">Enter Bill</button>
                        <button class="btn btn-sm btn-secondary" data-write onclick="closeModal({ keepAddress: true });BillsPage.showPayForm(${id})">Pay Bills</button>
                        <button class="btn btn-sm btn-secondary" onclick="VendorsPage.showForm(${id})">Edit</button>
                    </div>
                    ${VendorsPage._reportsRow(vendor, acct)}
                </div>
            </div>

            <!-- contact + address + account in 3 columns -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Contact</h4>
                    <div style="font-size:13px">
                        ${vendor.email ? `<div>📧 ${escapeHtml(vendor.email)}</div>` : ''}
                        ${vendor.phone ? `<div>☎ ${escapeHtml(vendor.phone)}</div>` : ''}
                        ${vendor.fax ? `<div>📠 ${escapeHtml(vendor.fax)}</div>` : ''}
                        ${vendor.website ? `<div>🌐 ${escapeHtml(vendor.website)}</div>` : ''}
                        ${!vendor.email && !vendor.phone && !vendor.fax && !vendor.website ? '<span style="color:var(--text-muted)">No contact info</span>' : ''}
                    </div>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Address</h4>
                    <pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(address || '—')}</pre>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Default expense or COGS account</h4>
                    <div style="font-size:13px">${acctLabel ? escapeHtml(acctLabel) : '<span style="color:var(--text-muted)">None — each bill line picks its own</span>'}</div>
                </div>
            </div>

            <!-- notes (inline editable) -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0;display:flex;justify-content:space-between">
                    <span>Notes</span>
                    <span id="vend-note-status-${id}" style="font-size:10px;color:var(--text-muted);text-transform:none;letter-spacing:0;font-weight:normal"></span>
                </h4>
                <textarea id="vend-notes-${id}" rows="3" data-write aria-label="Notes" style="width:100%;font-size:13px;font-family:inherit"
                    placeholder="Internal notes about this vendor — visible to everyone with admin access."
                    onblur="VendorsPage._saveNotes(${id}, this.value)">${escapeHtml(vendor.notes || '')}</textarea>
            </div>

            ${creditsHtml}

            <!-- recent bills -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Recent bills (${bills.length})</h4>
                ${bills.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;margin:0">No bills yet</p>' :
                    `<table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col">Due</th><th scope="col">Status</th><th scope="col" class="amount">Total</th><th scope="col" class="amount">Balance</th></tr></thead>
                        <tbody>${billRows}</tbody>
                    </table>`}
            </div>

            <!-- recent payments -->
            <div>
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Recent payments (${payments.length})</h4>
                ${payments.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;margin:0">No payments yet</p>' :
                    `<table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">Date</th><th scope="col">Method</th><th scope="col">Check #</th><th scope="col" class="amount">Amount</th></tr></thead>
                        <tbody>${payRows}</tbody>
                    </table>`}
            </div>`;

        // The page's own address (#/vendors/3, App.routes): Back from a
        // report opened below returns here, a reload keeps the page.
        openModal(`Vendor — ${vendor.name}`, html);
        App.documentAddress(`#/vendors/${vendor.id}`);  // after the dialog: it marks it as its own
    },

    // Reports for this vendor (#238), each through the report's own address
    // (docs/dev/report-views.md): A/P Aging as of today with the vendor's
    // row highlighted; the 1099 Summary for this year, likewise, when the
    // vendor is a 1099 vendor; the default expense or COGS account's
    // register (the drill-down, this year to date). Reads: a read-only
    // sign-in keeps them. Each is a real link whose click notes it on this
    // page's entry, the link itself, so Back from the report returns here
    // with focus on it, after a mouse click too (CustomersPage._reportsRow).
    _reportsRow(vendor, acct) {
        const link = (view, params, text) =>
            `<a class="btn btn-sm btn-secondary" href="${ReportsPage.viewUrl(view, params)}" data-row-key="report:${view}" onclick="ReportsPage._leaveFrom(this)">${escapeHtml(text)}</a>`;
        return `<div role="group" aria-label="Reports for this vendor" style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;justify-content:flex-end">
            <span style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;align-self:center">Reports</span>
            ${link('ap-aging', { vendor_id: vendor.id, period: 'this_year_to_date' }, 'A/P Aging')}
            ${vendor.is_1099_vendor ? link('1099-summary', { year: new Date().getFullYear(), vendor_id: vendor.id }, '1099 Summary') : ''}
            ${acct ? link('account-transactions', { account_id: acct.id, period: 'this_year_to_date' }, `${acct.name} register`) : ''}
        </div>`;
    },

    // Only a change is saved, once, as the customer page's notes are
    // (CustomersPage._saveNotes): leaving the box as it was loaded, or as a
    // save still on its way is writing it, writes nothing (macOS gate
    // NEW-44); words are compared trimmed and saved as typed, a note of
    // spaces as empty; one save at a time.
    _notesSaving: {},

    // The word beside the box, one at a time: showing one stops the
    // clearing an earlier "✓ saved" set going, which wiped a "⚠ save
    // failed" shown within its 1.5s (review).
    _noteTimers: {},
    _noteStatus(id, text, clearAfter = 0) {
        clearTimeout(VendorsPage._noteTimers[id]);
        delete VendorsPage._noteTimers[id];
        const status = document.getElementById(`vend-note-status-${id}`);
        if (!status) return;
        status.textContent = text;
        if (clearAfter) VendorsPage._noteTimers[id] = setTimeout(() => {
            delete VendorsPage._noteTimers[id];
            status.textContent = '';
        }, clearAfter);
    },

    async _saveNotes(id, value) {
        const box = document.getElementById(`vend-notes-${id}`);
        const pending = VendorsPage._notesSaving[id];
        const onFile = pending ? pending.value : box ? box.defaultValue : null;
        if (onFile !== null && value.trim() === onFile.trim()) return;
        if (!value.trim()) value = '';
        VendorsPage._noteStatus(id, 'saving…');
        const save = { value };
        save.done = (async () => {
            if (pending) await pending.done.catch(() => {});
            await API.put(`/vendors/${id}`, { notes: value });
        })();
        VendorsPage._notesSaving[id] = save;
        try {
            await save.done;
            if (box) box.defaultValue = value;
            VendorsPage._noteStatus(id, '✓ saved', 1500);
        } catch (err) {
            VendorsPage._noteStatus(id, '⚠ save failed');
            toast(err.message, 'error');
        } finally {
            if (VendorsPage._notesSaving[id] === save) delete VendorsPage._notesSaving[id];
        }
    },

    async showForm(id = null) {
        let v = { name:'', company:'', email:'', phone:'', fax:'', website:'',
            address1:'', address2:'', city:'', state:'', zip:'', country:'US',
            terms:'Net 30', tax_id:'', account_number:'', default_expense_account_id:'',
            is_1099_vendor:false, vendor_1099_type:'', notes:'' };
        if (id) v = await API.get(`/vendors/${id}`);

        // Cost of goods too: a flour mill or a panel supplier is bought
        // against 5100 Materials Cost, not an expense (W-L18, F8).
        const accounts = await API.get('/accounts');
        const acctOpts = PurchaseAccounts.options(
            PurchaseAccounts.filter(accounts, v.default_expense_account_id), v.default_expense_account_id);

        openModal(id ? 'Edit Vendor' : 'New Vendor', `
            <form id="vendor-form" onsubmit="VendorsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required maxlength="200" value="${escapeHtml(v.name)}"></div>
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
                    <div class="form-group"><label>Default Expense or COGS Account</label>
                        <select name="default_expense_account_id"><option value="">-- None --</option>${acctOpts}</select></div>
                    ${id ? `<div class="form-group"><label>Status</label>
                        <select name="is_active" title="An inactive vendor keeps its history but leaves the pickers on bills, expenses and orders">
                            <option value="true" ${v.is_active !== false ? 'selected' : ''}>Active</option>
                            <option value="false" ${v.is_active === false ? 'selected' : ''}>Inactive</option>
                        </select></div>` : ''}
                    <div class="form-group"><label>1099 Vendor</label>
                        <select name="is_1099_vendor" onchange="VendorsPage.toggle1099Type(this)">
                            <option value="false" ${!v.is_1099_vendor ? 'selected' : ''}>No</option>
                            <option value="true" ${v.is_1099_vendor ? 'selected' : ''}>Yes</option>
                        </select></div>
                    <div class="form-group"><label>1099 Type</label>
                        <select name="vendor_1099_type" ${v.is_1099_vendor ? '' : 'disabled'} title="Set 1099 Vendor to Yes to choose a type">
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

    // A 1099 type describes a 1099 vendor only: the server clears it on any
    // other vendor, and the select stayed live under "1099 Vendor: No" as
    // if the type were kept. It is off, and empty, until the answer is Yes.
    toggle1099Type(select) {
        const type = select.form && select.form.elements.namedItem('vendor_1099_type');
        if (!type) return;
        const on = select.value === 'true';
        type.disabled = !on;
        if (!on) type.value = '';
    },

    async save(e, id, force) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.default_expense_account_id = data.default_expense_account_id ? parseInt(data.default_expense_account_id) : null;
        data.is_1099_vendor = data.is_1099_vendor === 'true';
        data.vendor_1099_type = data.vendor_1099_type || null;
        if ('is_active' in data) data.is_active = data.is_active === 'true';
        try {
            if (id) {
                await API.put(`/vendors/${id}`, data);
                toast('Vendor updated');
            } else {
                await API.post('/vendors', data, force ? { query: { force: true } } : undefined);
                toast('Vendor created');
            }
            App.refresh();
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
 * The accounts a purchase posts to — expenses and cost of goods sold, by
 * number — for the Account pickers on bills, purchase orders, vendor
 * credits, expenses, card charges and the vendor's default. They listed
 * expense accounts only, so materials could not be pointed at 5100
 * Materials Cost (W-L18, F8). `keepId` keeps an account already chosen on
 * the record even when it is of another type or inactive.
 */
const PurchaseAccounts = {
    filter(accounts, ...keepIds) {
        const keep = new Set(keepIds.filter(Boolean).map(Number));
        return accounts.filter(a =>
            ((a.account_type === 'expense' || a.account_type === 'cogs') && a.is_active !== false)
            || keep.has(Number(a.id)));
    },
    // Grouped, cost of goods first, so the COGS accounts read as a set and
    // not as a few odd expenses (#214); anything else the record already
    // uses comes last.
    options(accounts, selectedId) {
        const opt = a => `<option value="${a.id}" ${selectedId && a.id == selectedId ? 'selected' : ''}>`
            + `${a.account_number ? escapeHtml(a.account_number) + ' - ' : ''}${escapeHtml(a.name)}</option>`;
        const groups = [['cogs', 'Cost of Goods Sold'], ['expense', 'Expenses']];
        let html = '';
        for (const [type, label] of groups) {
            const these = accounts.filter(a => a.account_type === type);
            if (these.length) html += `<optgroup label="${label}">${these.map(opt).join('')}</optgroup>`;
        }
        const rest = accounts.filter(a => !groups.some(([type]) => type === a.account_type));
        if (rest.length) html += `<optgroup label="Other">${rest.map(opt).join('')}</optgroup>`;
        return html;
    },
};

/** Line arithmetic for the purchase forms, the way the server stores it. */
const PurchaseLines = {
    // Half up to the cent, as the server rounds and as SalesLines.cents does:
    // shift by exponent, not by multiplying. 2.5 x 13.37 is 33.42499999...
    // in floating point, Number.EPSILON is far below its last digit, and
    // x 100 rounded to $33.42 while the server stored $33.43 (2.22.1 gate,
    // NEW-48: bills, purchase orders and vendor credits).
    cents(x) {
        const n = Number(x) || 0;
        const a = Math.abs(n);
        let r = Number(`${a}e2`);
        r = Number.isFinite(r) ? Math.round(r) : Math.round(a * 100);
        const back = Number(`${r}e-2`); // NaN past 1e21, where the string has an exponent
        return (n < 0 ? -1 : 1) * (Number.isFinite(back) ? back : r / 100);
    },
    // Each line is rounded to the cent before it is added up.
    lineAmount(row) {
        const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
        const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
        return PurchaseLines.cents(qty * rate);
    },
    // What a picked item fills in: what it costs, else its price.
    price(item) { return Number(item.cost) ? item.cost : item.rate; },
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
                <input id="${id}-new-name" placeholder="Name *" aria-label="Vendor name" aria-required="true" style="width:100%; margin-bottom:4px;">
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
