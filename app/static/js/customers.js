/**
 * Customer Center — list + tabbed detail panel. QuickBooks' old
 * "Customer:Job" hierarchy was a colon-delimited name
 * ("Smith:Kitchen Remodel"); we flattened it because nobody
 * actually liked that feature.
 */
const CustomersPage = {
    async render() {
        const customers = await API.get(`/customers${ActiveLists.query('customers')}`);
        ActiveLists.remember('customers', customers);
        let html = `
            <div class="page-header">
                <h2>${T('Customers')}</h2>
                <button class="btn btn-primary" onclick="CustomersPage.showForm()">+ ${T('New Customer')}</button>
            </div>
            <div class="toolbar">
                <input type="text" placeholder="${Terms.text('Search customers...')}" id="customer-search"
                    oninput="CustomersPage.filter(this.value)">
                ${ActiveLists.pickerHtml('customers')}
                <!-- #436: the Past Customers report, opened unfiltered; the
                     report is the one view, a link here is the honest tab. -->
                <button class="btn btn-secondary" onclick="App.navigate('#/reports/past-customers')">${Terms.text('Past customers')}</button>
            </div>`;

        if (customers.length === 0 && ActiveLists.emptyText('customers')) {
            html += `<div class="empty-state"><p>${ActiveLists.emptyText('customers')}</p></div>`;
        } else if (customers.length === 0) {
            html += `<div class="empty-state">
                <p>${Terms.text('No customers yet.')}</p>
                <button class="btn btn-primary" onclick="CustomersPage.showForm()" style="margin-top:10px;">+ ${Terms.text('Create your first customer')}</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Name</th><th scope="col">Company</th><th scope="col">Phone</th><th scope="col">Email</th>
                    <th scope="col" class="amount">Balance</th><th scope="col">Actions</th>
                </tr></thead>
                <tbody id="customer-tbody">`;
            for (const c of customers) {
                // in the All view an inactive customer is dimmed and marked,
                // as on the other lists (text-muted keeps AA contrast)
                const inactive = c.is_active === false;
                html += `<tr class="clickable customer-row${inactive ? ' row--dim' : ''}" data-name="${escapeHtml(c.name).toLowerCase()}" onclick="CustomersPage.showDetails(${c.id})">
                    <td><strong>${escapeHtml(c.name)}</strong>${inactive ? ' <span class="badge badge-draft">inactive</span>' : ''}</td>
                    <td>${escapeHtml(c.company) || ''}</td>
                    <td>${escapeHtml(c.phone) || ''}</td>
                    <td>${escapeHtml(c.email) || ''}</td>
                    <td class="amount">${formatCurrency(c.balance)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); CustomersPage.showForm(${c.id})">Edit</button>
                        ${ActiveLists.buttonHtml('customers', c)}
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
        let customer, invoices, payments, permits, jobs, credits;
        try {
            [customer, invoices, payments, permits, jobs, credits] = await Promise.all([
                API.get(`/customers/${id}`),
                fetchAllPages(`/invoices?customer_id=${id}`).catch(() => []),
                fetchAllPages(`/payments?customer_id=${id}`).catch(() => []),
                API.get(`/reseller-permits?entity_type=customer&entity_id=${id}`).catch(() => []),
                API.get(`/jobs?customer_id=${id}&include_inactive=true`).catch(() => []),
                API.get(`/customers/${id}/credits`).catch(() => null),
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
            ? '<p style="color:var(--text-muted);font-size:13px;margin:0">None on file. <a href="#/reseller-permits" onclick="closeModal()">Add one</a>.</p>'
            : '<ul style="margin:0;padding-left:18px;font-size:13px">' + permits.map(p => {
                let badge = '<span style="color:var(--text-success)">Active</span>';
                if (p.is_expired) badge = '<span style="color:var(--text-danger);font-weight:600">EXPIRED</span>';
                else if (p.days_to_expire !== null && p.days_to_expire <= 30) badge = `<span style="color:var(--text-warning);font-weight:600">Expires in ${p.days_to_expire}d</span>`;
                else if (!p.is_active) badge = '<span style="color:var(--text-muted)">Inactive</span>';
                const link = p.verification_url ? ` · <a href="${escapeHtml(p.verification_url)}" target="_blank" rel="noopener">Open ${escapeHtml(p.jurisdiction)} lookup</a>` : '';
                return `<li>${escapeHtml(p.jurisdiction)} · <code>${escapeHtml(p.permit_number)}</code> · ${badge}${p.expires_at ? ` · exp ${escapeHtml(p.expires_at)}` : ''}${link}</li>`;
            }).join('') + '</ul>';

        // -- Invoices + payments — last 10 each, click-through to detail --
        // An invoice row opens the invoice at its own address (#/invoices/12,
        // over the list), not the Invoices list (#238), and a payment row the
        // payment at its (#/payments/31). The rows were a click alone, so
        // neither the keyboard nor a screen reader could open one (macOS gate
        // NEW-38): the number, and a payment's date, is a real link to the
        // address now, named by its words. The row's click does the opening,
        // for the link too (its own navigation is left to it, so the hop is
        // the one history entry App.navigate pushes); the link left is noted
        // on this page's entry (ReportsPage._leaveFrom, given the row: its
        // link is the one noted, after a mouse click too, which in WebKit
        // puts focus nowhere), so Back returns here with the focus on it
        // (App.withDocument).
        const rowLink = (href, key, text) => `<a href="${href}" data-row-key="${key}" style="color:var(--text-link); text-decoration:none;" onclick="event.preventDefault()">${escapeHtml(text)}</a>`;
        const hop = (href) => `ReportsPage._leaveFrom(this);closeModal({ keepAddress: true });App.navigate('${href}')`;
        const invRows = invoices.slice(0, 10).map(i =>
            `<tr style="cursor:pointer" onclick="${hop(`#/invoices/${i.id}`)}">
                <td>${rowLink(`#/invoices/${i.id}`, `invoice:${i.id}`, i.invoice_number || `#${i.id}`)}</td>
                <td>${escapeHtml(i.date || '')}</td>
                <td class="amount">${formatCurrency(i.total)}</td>
                <td>${escapeHtml(i.status || '')}</td>
            </tr>`).join('');
        const payRows = payments.slice(0, 10).map(p =>
            `<tr style="cursor:pointer" onclick="${hop(`#/payments/${p.id}`)}">
                <td>${rowLink(`#/payments/${p.id}`, `payment:${p.id}`, p.date || `#${p.id}`)}</td>
                <td>${escapeHtml(p.method || '')}${p.is_voided ? ' <span style="color:var(--text-danger)">(void)</span>' : ''}</td>
                <td>${escapeHtml(p.reference || p.check_number || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');

        // -- Credits the customer holds (unapplied payments, credit memos),
        // each one applicable to open invoices from here --
        const creditList = (credits && credits.credits) || [];
        const creditsHtml = creditList.length === 0 ? '' : `
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Credits not applied yet (${formatCurrency(credits.total)})</h4>
                <ul style="margin:0;padding-left:18px;font-size:13px">${creditList.map(c => `<li style="margin:2px 0">
                    ${escapeHtml(PaymentsPage._creditLabel(c))}: <strong>${formatCurrency(c.available)}</strong>
                    <button class="btn btn-sm btn-secondary" style="margin-left:6px" onclick="PaymentsPage.showApplyCredit('${c.kind}', ${c.id}, ${id})">Apply</button>
                </li>`).join('')}</ul>
            </div>`;
        const balance = parseFloat(customer.balance) || 0;

        const html = `
            <!-- header: name + balance + quick actions -->
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;flex-wrap:wrap;gap:12px">
                <div>
                    <h3 style="margin:0;font-size:18px">${escapeHtml(customer.name)}</h3>
                    ${customer.company ? `<div style="color:var(--text-muted);font-size:13px">${escapeHtml(customer.company)}</div>` : ''}
                    <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
                        ${customer.is_active === false ? '<span style="color:var(--text-danger)">Inactive</span>' : '<span style="color:var(--text-success)">Active</span>'}
                        &nbsp;·&nbsp; Terms: ${escapeHtml(customer.terms || 'Net 30')}
                        ${customer.is_taxable === false ? ' · <strong>Tax exempt</strong>' : ''}
                        ${customer.credit_limit ? ` · Credit limit: ${formatCurrency(customer.credit_limit)}` : ''}
                        ${customer.tax_id ? ` · Tax ID: <code>${escapeHtml(customer.tax_id)}</code>` : ''}
                    </div>
                </div>
                <div style="text-align:right">
                    <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em">${balance < 0 ? 'Credit' : 'Balance'}</div>
                    <div style="font-size:22px;font-weight:700;color:${balance > 0 ? 'var(--text-danger)' : (balance < 0 ? 'var(--text-success)' : 'var(--text-primary)')}">
                        ${formatCurrency(Math.abs(balance))}
                    </div>
                    <div style="margin-top:8px">
                        <button class="btn btn-sm btn-primary" data-write onclick="closeModal({ keepAddress: true });InvoicesPage.showForm(null,${id})">${T('New Invoice')}</button>
                        <button class="btn btn-sm btn-secondary" data-write onclick="closeModal({ keepAddress: true });PaymentsPage.showForm(null,${id})">Receive Payment</button>
                        <button class="btn btn-sm btn-secondary" data-write onclick="closeModal({ keepAddress: true });JobsPage.showForm(null,${id})">New ${T('Job')}</button>
                        <button class="btn btn-sm btn-secondary" onclick="CustomersPage.showForm(${id})">Edit</button>
                        ${Terms.isNonprofit() ? `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/donors/${id}/giving-statement/pdf?year=' + (new Date().getFullYear() - 1), '_blank')">Giving Statement (last year)</button>` : ''}
                    </div>
                    ${CustomersPage._reportsRow(id)}
                </div>
            </div>

            <!-- contact + addresses + permits in 3 columns -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Contact</h4>
                    <div style="font-size:13px">
                        ${customer.email ? `<div>📧 ${escapeHtml(customer.email)}</div>` : ''}
                        ${customer.phone ? `<div>☎ ${escapeHtml(customer.phone)}</div>` : ''}
                        ${customer.mobile ? `<div>📱 ${escapeHtml(customer.mobile)}</div>` : ''}
                        ${customer.website ? `<div>🌐 ${escapeHtml(customer.website)}</div>` : ''}
                        ${!customer.email && !customer.phone && !customer.mobile ? '<span style="color:var(--text-muted)">No contact info</span>' : ''}
                    </div>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Billing</h4>
                    <pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(bill || '—')}</pre>
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Shipping</h4>
                    <pre style="font-size:13px;font-family:inherit;white-space:pre-wrap;margin:0">${escapeHtml(ship || (bill ? '(same as billing)' : '—'))}</pre>
                </div>
            </div>

            <!-- notes (inline editable) -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0;display:flex;justify-content:space-between">
                    <span>Notes</span>
                    <span id="cust-note-status-${id}" style="font-size:10px;color:var(--text-muted);text-transform:none;letter-spacing:0;font-weight:normal"></span>
                </h4>
                <textarea id="cust-notes-${id}" rows="3" data-write aria-label="Notes" style="width:100%;font-size:13px;font-family:inherit"
                    placeholder="Internal notes about this customer — visible to everyone with admin access."
                    onblur="CustomersPage._saveNotes(${id}, this.value)">${escapeHtml(customer.notes || '')}</textarea>
            </div>

            ${creditsHtml}

            <!-- reseller permits -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Reseller permits</h4>
                ${permitsBody}
            </div>

            <!-- jobs (projects) -->
            <div style="margin-bottom:14px">
                <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Jobs (${jobs.length})</h4>
                ${jobs.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;margin:0">No jobs. A job is a project for this customer — invoices, bills, expenses and time can be tagged to it.</p>' :
                    `<table class="data-table" style="font-size:12px">
                        <thead><tr><th scope="col">${T('Job')}</th><th scope="col">Status</th><th scope="col" class="amount">Contract</th><th scope="col"></th></tr></thead>
                        <tbody>${jobs.map(j => `<tr style="cursor:pointer" onclick="JobsPage.showDetails(${j.id})">
                            <td>${escapeHtml(j.name)}${j.job_number ? ` <span style="color:var(--text-muted)">#${escapeHtml(j.job_number)}</span>` : ''}</td>
                            <td>${escapeHtml((window.JobsPage && JobsPage.STATUS_LABELS[j.status]) || j.status)}${j.is_active ? '' : ' <span style="color:var(--text-muted)">(inactive)</span>'}</td>
                            <td class="amount">${j.contract_amount ? formatCurrency(j.contract_amount) : ''}</td>
                            <td><a href="#" onclick="event.preventDefault();event.stopPropagation();closeModal({ keepAddress: true });JobsPage.showForm(${j.id})">Edit</a></td>
                        </tr>`).join('')}</tbody>
                    </table>`}
            </div>

            <!-- recent invoices + payments side by side -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Recent invoices (${invoices.length})</h4>
                    ${invoices.length === 0 ? Terms.text('<p style="color:var(--text-muted);font-size:13px;margin:0">No invoices yet</p>') :
                        `<table class="data-table" style="font-size:12px">
                            <thead><tr><th scope="col">#</th><th scope="col">Date</th><th scope="col" class="amount">Total</th><th scope="col">Status</th></tr></thead>
                            <tbody>${invRows}</tbody>
                        </table>`}
                </div>
                <div>
                    <h4 style="font-size:11px;text-transform:uppercase;color:var(--text-muted);margin:0 0 4px 0">Recent payments (${payments.length})</h4>
                    ${payments.length === 0 ? '<p style="color:var(--text-muted);font-size:13px;margin:0">No payments yet</p>' :
                        `<table class="data-table" style="font-size:12px">
                            <thead><tr><th scope="col">Date</th><th scope="col">Method</th><th scope="col">Ref</th><th scope="col" class="amount">Amount</th></tr></thead>
                            <tbody>${payRows}</tbody>
                        </table>`}
                </div>
            </div>`;

        // The page's own address (#/customers/12, App.routes): Back from a
        // report opened below returns here, a reload keeps the page.
        openModal(`${T('Customer')} — ${customer.name}`, html);
        App.documentAddress(`#/customers/${customer.id}`);  // after the dialog: it marks it as its own
    },

    // Reports for this customer (#238): each opens through the report's
    // own address (docs/dev/report-views.md), on this year to date — as of
    // today for the aging — so Back returns here and a reload keeps it. A
    // report that takes no customer filter highlights the customer's row;
    // Job Profitability is filtered to the customer's jobs and says so. The
    // statement PDF is the picker's, as of today; a nonprofit's is the
    // giving statement above. Reads: a read-only sign-in keeps them.
    // Each report is a real link to its address (the keyboard and a screen
    // reader find it as one); its click notes it on this page's entry
    // (ReportsPage._leaveFrom, the link itself: after a mouse click too,
    // which in WebKit focuses nothing), so Back from the report returns here
    // with focus on it (App.withDocument); they noted nothing, so Back put
    // focus where the page puts it on opening. The statement is a download,
    // not a view: there is nothing to come back to, and it notes nothing.
    _reportsRow(id) {
        const link = (view, params, text) =>
            `<a class="btn btn-sm btn-secondary" href="${ReportsPage.viewUrl(view, params)}" data-row-key="report:${view}" onclick="ReportsPage._leaveFrom(this)">${text}</a>`;
        const period = { customer_id: id, period: 'this_year_to_date' };
        return `<div role="group" aria-label="Reports for this ${T('customer')}" style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;justify-content:flex-end">
            <span style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;align-self:center">Reports</span>
            ${Terms.isNonprofit() ? '' : `<button type="button" class="btn btn-sm btn-secondary" onclick="window.open('/api/reports/customer-statement/${id}/pdf?as_of_date=${todayISO()}', '_blank')">Statement (PDF)</button>`}
            ${link('income-by-customer', period, T('Income by Customer'))}
            ${link('ar-aging', period, T('A/R Aging'))}
            ${link('job-profitability', period, T('Job Profitability'))}
        </div>`;
    },

    // The box saves when it is left — but only a change, and once. It saved
    // on every blur, so a Tab through the page wrote the company file and an
    // audit entry each time, and turned a note never written (null) into ""
    // (macOS gate NEW-44). What is on file is the save on its way, if one is
    // (_notesSaving: leaving the box again before it is back sends nothing
    // more), else the box's defaultValue: what it was loaded with, none and
    // empty alike, and then what was saved. Words are compared trimmed — a
    // space added at the end is no change, and a note of spaces is no note,
    // saved as empty — and a note is saved as typed. One save at a time: a
    // change made while one is on its way goes after it.
    _notesSaving: {},

    // The word beside the box, one at a time: showing one stops the
    // clearing an earlier "✓ saved" set going, which wiped a "⚠ save
    // failed" shown within its 1.5s (review).
    _noteTimers: {},
    _noteStatus(id, text, clearAfter = 0) {
        clearTimeout(CustomersPage._noteTimers[id]);
        delete CustomersPage._noteTimers[id];
        const status = document.getElementById(`cust-note-status-${id}`);
        if (!status) return;
        status.textContent = text;
        if (clearAfter) CustomersPage._noteTimers[id] = setTimeout(() => {
            delete CustomersPage._noteTimers[id];
            status.textContent = '';
        }, clearAfter);
    },

    async _saveNotes(id, value) {
        const box = document.getElementById(`cust-notes-${id}`);
        const pending = CustomersPage._notesSaving[id];
        const onFile = pending ? pending.value : box ? box.defaultValue : null;
        if (onFile !== null && value.trim() === onFile.trim()) return;
        if (!value.trim()) value = '';
        CustomersPage._noteStatus(id, 'saving…');
        const save = { value };
        save.done = (async () => {
            if (pending) await pending.done.catch(() => {});
            await API.put(`/customers/${id}`, { notes: value });
        })();
        CustomersPage._notesSaving[id] = save;
        try {
            await save.done;
            if (box) box.defaultValue = value;
            CustomersPage._noteStatus(id, '✓ saved', 1500);
        } catch (err) {
            CustomersPage._noteStatus(id, '⚠ save failed');
            toast(err.message, 'error');
        } finally {
            if (CustomersPage._notesSaving[id] === save) delete CustomersPage._notesSaving[id];
        }
    },

    async showForm(id = null) {
        let c = { name: '', company: '', email: '', phone: '', mobile: '', fax: '', website: '',
            bill_address1: '', bill_address2: '', bill_city: '', bill_state: '', bill_zip: '', bill_country: 'US',
            ship_address1: '', ship_address2: '', ship_city: '', ship_state: '', ship_zip: '', ship_country: 'US',
            terms: 'Net 30', credit_limit: '', tax_id: '', is_taxable: true, is_active: true, notes: '' };
        if (id) {
            c = await API.get(`/customers/${id}`);
        } else {
            // A new customer starts on the company's default terms
            // (Settings), not a hard-coded Net 30.
            const settings = await API.get('/settings').catch(() => ({}));
            if (settings.default_terms) c.terms = settings.default_terms;
        }
        const termChoices = ['Net 15', 'Net 30', 'Net 45', 'Net 60', 'Due on Receipt'];
        if (c.terms && !termChoices.includes(c.terms)) termChoices.unshift(c.terms);

        const title = id ? `Edit ${T('Customer')}` : T('New Customer');
        openModal(title, `
            <form id="customer-form" onsubmit="CustomersPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required maxlength="200" value="${escapeHtml(c.name)}"></div>
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
                            ${termChoices.map(t =>
                                `<option ${c.terms===t?'selected':''}>${escapeHtml(t)}</option>`).join('')}
                        </select></div>
                </div>
                <h3 id="cust-bill-heading" style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Billing Address</h3>
                <div class="form-grid" role="group" aria-labelledby="cust-bill-heading">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="bill_address1" value="${escapeHtml(c.bill_address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="bill_address2" value="${escapeHtml(c.bill_address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="bill_city" value="${escapeHtml(c.bill_city || '')}"></div>
                    <div class="form-group"><label>State / County</label>
                        <input name="bill_state" value="${escapeHtml(c.bill_state || '')}"></div>
                    <div class="form-group"><label>ZIP / Postcode</label>
                        <input name="bill_zip" value="${escapeHtml(c.bill_zip || '')}"></div>
                    <div class="form-group"><label>Country</label>
                        <select name="bill_country">${countryOptions(c.bill_country || 'US')}</select></div>
                </div>
                <h3 id="cust-ship-heading" style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Shipping Address</h3>
                <div class="form-grid" role="group" aria-labelledby="cust-ship-heading">
                    <div class="form-group full-width"><label>Address 1</label>
                        <input name="ship_address1" value="${escapeHtml(c.ship_address1 || '')}"></div>
                    <div class="form-group full-width"><label>Address 2</label>
                        <input name="ship_address2" value="${escapeHtml(c.ship_address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="ship_city" value="${escapeHtml(c.ship_city || '')}"></div>
                    <div class="form-group"><label>State / County</label>
                        <input name="ship_state" value="${escapeHtml(c.ship_state || '')}"></div>
                    <div class="form-group"><label>ZIP / Postcode</label>
                        <input name="ship_zip" value="${escapeHtml(c.ship_zip || '')}"></div>
                    <div class="form-group"><label>Country</label>
                        <select name="ship_country">${countryOptions(c.ship_country || 'US')}</select></div>
                </div>
                <div class="form-grid" style="margin-top:16px;">
                    <div class="form-group"><label>Tax ID</label>
                        <input name="tax_id" value="${escapeHtml(c.tax_id || '')}"></div>
                    <div class="form-group"><label>Credit Limit</label>
                        <input name="credit_limit" type="number" step="0.01" min="0" placeholder="No limit" value="${c.credit_limit || ''}"></div>
                    <div class="form-group"><label>Sales Tax</label>
                        <label style="font-weight:normal;"><input type="checkbox" name="tax_exempt" ${c.is_taxable === false ? 'checked' : ''}>
                            Tax exempt: no sales tax on anything sold to them (a reseller, a church, a school)</label></div>
                    ${id ? `<div class="form-group"><label>Status</label>
                        <label style="font-weight:normal;"><input type="checkbox" name="is_active" ${c.is_active === false ? '' : 'checked'}>
                            Active (an inactive one is left out of the lists you pick from)</label></div>` : ''}
                    ${Terms.isNonprofit() ? `
                    <div class="form-group"><label>${T('Customer')} type</label>
                        <select name="donor_type">
                            <option value="" ${!c.donor_type ? 'selected' : ''}>—</option>
                            <option value="individual" ${c.donor_type === 'individual' ? 'selected' : ''}>Individual</option>
                            <option value="organization" ${c.donor_type === 'organization' ? 'selected' : ''}>Organization / foundation</option>
                        </select></div>
                    <div class="form-group"><label>Salutation (letters)</label>
                        <input name="salutation" maxlength="100" placeholder="e.g. Dear Maria" value="${escapeHtml(c.salutation || '')}"></div>
                    <div class="form-group full-width"><label style="font-weight:normal;"><input type="checkbox" name="send_year_end_statement" ${c.send_year_end_statement === false ? '' : 'checked'}> Send the year-end giving statement</label></div>` : ''}
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes">${escapeHtml(c.notes || '')}</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} ${T('Customer')}</button>
                </div>
            </form>`);
    },

    async save(e, id, force) {
        e.preventDefault();
        const form = new FormData(e.target);
        const data = Object.fromEntries(form.entries());
        if (data.credit_limit) data.credit_limit = parseFloat(data.credit_limit);
        else if (id) data.credit_limit = null;  // cleared: no limit
        else delete data.credit_limit;
        if (e.target.send_year_end_statement) data.send_year_end_statement = e.target.send_year_end_statement.checked;
        if ('donor_type' in data && !data.donor_type) data.donor_type = null;
        // Checkboxes: "Tax exempt" is the customer's is_taxable, inverted;
        // "Active" is on the edit form only.
        delete data.tax_exempt;
        data.is_taxable = !(e.target.tax_exempt && e.target.tax_exempt.checked);
        if (e.target.is_active) data.is_active = e.target.is_active.checked;

        try {
            if (id) {
                await API.put(`/customers/${id}`, data);
                toast(`${T('Customer')} updated`);
            } else {
                await API.post('/customers', data, force ? { query: { force: true } } : undefined);
                toast(`${T('Customer')} created`);
            }
            App.refresh();
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
        openModal(Terms.text('Possible Duplicate Customer'), `
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

// Top-level const creates no window property — the topbar's
// data-action dispatch (bootstrap.js callByPath) needs this export.
window.CustomersPage = CustomersPage;
