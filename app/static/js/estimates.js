/**
 * Estimates — same form as invoices (see invoices.js), green tint
 * instead of yellow. "Create Invoice" deep-copies every field and line
 * item, then marks the estimate CONVERTED.
 */
const EstimatesPage = {
    showAll() { EstimatesPage._showAll = true; App.navigate(location.hash); },

    async render() {
        const { rows: estimates, note: capNote } = await listRows(EstimatesPage, '/estimates', 'EstimatesPage.showAll()', 'estimates');
        return renderListPage({
            title: 'Estimates',
            headerHtml: `<button class="btn btn-primary" onclick="EstimatesPage.showForm()">+ New Estimate</button>` + capNote,
            empty: `<p>No estimates yet.</p>
                <button class="btn btn-primary" onclick="EstimatesPage.showForm()" style="margin-top:10px;">+ Create your first estimate</button>`,
            columns: ['#', T('Customer'), 'Date', 'Expires', 'Status',
                { label: 'Total', cls: 'amount' }, 'Actions'],
            items: estimates,
            row: est => `<tr>
                    <td><strong>${escapeHtml(est.estimate_number)}</strong></td>
                    <td>${escapeHtml(est.customer_name || '')}</td>
                    <td>${formatDate(est.date)}</td>
                    <td>${formatDate(est.expiration_date)}</td>
                    <td>${statusBadge(est.status)}</td>
                    <td class="amount">${formatCurrency(est.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="EstimatesPage.view(${est.id})">View</button>
                        <button class="btn btn-sm btn-secondary" onclick="EstimatesPage.showForm(${est.id})">Edit</button>
                        ${est.status !== 'converted' ? `<button class="btn btn-sm btn-primary" onclick="EstimatesPage.convert(${est.id})">Convert</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    async view(id) {
        const est = await API.get(`/estimates/${id}`);
        const construction = Terms.isConstruction();

        let bodyHtml;
        if (construction && est.totals) {
            bodyHtml = EstimatesPage.constructionViewHtml(est);
        } else {
            const linesHtml = est.lines.map(l =>
                `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
                 <td class="amount">${SalesLines.rate(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
            ).join('');
            bodyHtml = `<div class="table-container"><table>
                    <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>${linesHtml}</tbody>
                </table></div>
                <div class="invoice-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(est.subtotal)}</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(est.tax_amount)}</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(est.total)}</span></div>
                </div>`;
        }

        openModal(`Estimate #${est.estimate_number}`, `
            <div style="margin-bottom:12px;">
                <strong>${T('Customer')}:</strong> ${escapeHtml(est.customer_name || '')}<br>
                ${construction && est.job_name ? `<strong>Project:</strong> ${escapeHtml(est.job_name)}${est.job_site_address ? ', ' + escapeHtml(est.job_site_address) : ''}<br>` : ''}
                <strong>Date:</strong> ${formatDate(est.date)}<br>
                ${est.expiration_date ? `<strong>Expires:</strong> ${formatDate(est.expiration_date)}<br>` : ''}
                <strong>Status:</strong> ${statusBadge(est.status)}
            </div>
            ${bodyHtml}
            ${construction && est.terms ? `<p style="margin-top:12px;white-space:pre-wrap;"><strong>Terms, inclusions and exclusions</strong>\n${escapeHtml(est.terms)}</p>` : ''}
            ${est.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(est.notes)}</p>` : ''}
            <div class="form-actions">
                ${EstimatesPage.acceptButtonHtml(est)}
                <button class="btn btn-secondary" onclick="window.open('/api/estimates/${est.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="EstimatesPage.emailEstimate(${est.id})">Email</button>
                <button class="btn btn-secondary" onclick="window.open('/api/estimates/${est.id}/print-preview','_blank')">Print</button>
                ${est.status !== 'converted' ? `<button class="btn btn-primary" onclick="EstimatesPage.convert(${est.id})">Convert to ${T('Invoice')}</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    // The bid, grouped by package with its allowances, alternates and unit
    // prices broken out and the marked-up bid total (#551).
    constructionViewHtml(est) {
        const t = est.totals;
        // group the bought-scope lines (minus allowances) by package ID, in
        // the totals' package order; a null ID is the Unassigned bucket, so a
        // package literally named "Unassigned" never collides with it
        const scopeLine = l => {
            const k = l.line_kind || 'base';
            if (k === 'allowance' || k === 'unit_price') return false;
            if (k === 'alternate' && !l.alternate_accepted) return false;
            return true;
        };
        const groups = t.by_package.map(p => ({
            name: p.package_name,
            lines: est.lines.filter(l => scopeLine(l) && (l.trade_package_id ?? null) === (p.package_id ?? null)),
        })).filter(g => g.lines.length);
        // an accepted alternate sits in its package at cost, tagged so it reads
        // as the add or deduct it was (the owner's PDF tags it the same way)
        const altTag = l => ((l.line_kind || 'base') === 'alternate'
            ? ` <em style="color:var(--gray-500);">(${Number(l.amount) < 0 ? 'deduct' : 'add'} alternate, accepted)</em>` : '');
        const lineRow = l => `<tr><td>${escapeHtml(l.description || '')}${altTag(l)}</td>
            <td class="amount">${l.quantity}${l.unit ? ' ' + escapeHtml(l.unit) : ''}</td>
            <td class="amount">${SalesLines.rate(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`;
        const groupsHtml = groups.map(g =>
            `<tr><td colspan="4" style="background:var(--gray-100); font-weight:700;">${escapeHtml(g.name)}</td></tr>${g.lines.map(lineRow).join('')}`).join('');
        const allowances = est.lines.filter(l => (l.line_kind || 'base') === 'allowance');
        const allowBlock = allowances.length
            ? `<div class="table-container" style="margin-top:12px;"><table>
                <thead><tr><th scope="col">Allowances</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${allowances.map(l => `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`).join('')}</tbody>
            </table></div>` : '';
        // an accepted alternate is in the scope above; the block offers the rest
        const pendingAlts = t.alternates.filter(a => !a.accepted);
        const altBlock = pendingAlts.length
            ? `<div style="margin-top:12px;"><strong>Alternates</strong> <span style="color:var(--gray-500);">(add / deduct)</span>${pendingAlts.map(a =>
                `<div>${escapeHtml(a.description || '(alternate)')} — ${formatCurrency(a.amount)}</div>`).join('')}</div>` : '';
        const upBlock = t.unit_prices.length
            ? `<div style="margin-top:12px;"><strong>Unit prices</strong>${t.unit_prices.map(u =>
                `<div>${escapeHtml(u.description || '(unit price)')} — ${SalesLines.rate(u.rate)}${u.unit ? ' / ' + escapeHtml(u.unit) : ''}</div>`).join('')}</div>` : '';
        // Lines at cost, then the four markups as their own rows under the
        // subtotal, the pre-tax Bid total, Tax (when charged) and Total (#551).
        const pctLabel = v => String(+Number(v || 0));
        const mRow = (label, pct, amt) => (Number(amt) ? `<div class="total-row"><span class="label">${label} (${pctLabel(pct)}%)</span><span class="value">${formatCurrency(amt)}</span></div>` : '');
        const taxRow = Number(est.tax_amount) ? `<div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(est.tax_amount)}</span></div>` : '';
        // the markup rows below are the GC's view; the owner's PDF can spread
        // them into the package prices instead (the form's "Show markup rows")
        const hiddenNote = est.show_markup_rows === false
            ? '<div style="margin-top:8px;color:var(--gray-500);font-size:12px;">On the owner\'s PDF the markup rows are hidden: each package\'s price carries its share and one subtotal is printed.</div>' : '';
        return `<div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${groupsHtml}</tbody>
            </table></div>
            ${allowBlock}${altBlock}${upBlock}${hiddenNote}
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(t.subtotal)}</span></div>
                ${mRow('General conditions', t.gc_pct, t.gc)}
                ${mRow('Insurance', t.insurance_pct, t.insurance)}
                ${mRow('Fee', t.fee_pct, t.fee)}
                ${mRow('Bond', t.bond_pct, t.bond)}
                ${allowances.length || t.markup_allowances ? `<div class="total-row"><span class="label">Markup on allowances</span><span class="value">${t.markup_allowances ? 'On' : 'Off'}</span></div>` : ''}
                <div class="total-row"><span class="label">Bid total</span><span class="value">${formatCurrency(t.bid_total)}</span></div>
                ${taxRow}
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(est.total)}</span></div>
            </div>`;
    },

    async convert(id) {
        if (!confirm('Convert this estimate to an invoice?')) return;
        try {
            // the new invoice may take the customer past their credit limit
            const est = await API.get(`/estimates/${id}`);
            const customer = await API.get(`/customers/${est.customer_id}`);
            if (!(await InvoicesPage.creditLimitOk(customer, parseFloat(est.total) || 0))) return;
        } catch (err) { /* the server still decides the conversion */ }
        try {
            // an estimate for $0.00 asks before it becomes a $0.00 invoice
            const inv = await SalesLines.sendAllowingZero(flags =>
                API.post(`/estimates/${id}/convert`, Object.keys(flags).length ? flags : undefined),
                InvoicesPage._overLimitOk ? { allow_over_credit_limit: true } : {});
            if (!inv) return;
            toastPosted(`Created ${T('Invoice')} #${inv.invoice_number}`);
            // another customer's job stored on the estimate was left off
            if (inv.jobs_left_off && inv.jobs_left_off.length) toast(JobPicker.leftOffNote(inv.jobs_left_off), 'info');
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Email the estimate PDF by the invoice's path (same address checks and
    // Recent sends log); the recipient starts as the customer's email.
    async emailEstimate(id) {
        const est = await API.get(`/estimates/${id}`);
        let email = '';
        try { email = (await API.get(`/customers/${est.customer_id}`)).email || ''; }
        catch (e) { /* the user types it */ }
        openModal('Email Estimate', `
            <form onsubmit="EstimatesPage.sendEmail(event, ${id})">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Recipient Email *</label>
                        <input name="recipient" type="email" required value="${escapeHtml(email)}"></div>
                    <div class="form-group"><label>CC <span class="form-hint">Optional. Separate addresses with commas.</span></label>
                        <input name="cc" placeholder="office@example.com"></div>
                    <div class="form-group"><label>BCC <span class="form-hint">Optional. Hidden from the recipient.</span></label>
                        <input name="bcc" placeholder="books@example.com"></div>
                    <div class="form-group full-width"><label>Subject</label>
                        <input name="subject" placeholder="Estimate #${escapeHtml(est.estimate_number)}"></div>
                    <div class="form-group full-width"><label>Message <span class="form-hint">Appears at the top of the email, above the attached PDF.</span></label>
                        <textarea name="message"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Send Email</button>
                </div>
            </form>`);
    },

    async sendEmail(e, id) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post(`/estimates/${id}/email`, {
                recipient: form.recipient.value,
                cc: form.cc.value.trim() || null,
                bcc: form.bcc.value.trim() || null,
                subject: form.subject.value.trim() || null,
                message: form.message.value,
            });
            toast('Estimate emailed');
            closeModal();
        } catch (err) { toast(err.message, 'error'); }
    },

    lineCount: 0,
    _items: [],
    _customers: [],

    customerSelected(customerId) {
        setTimeout(() => EstimatesPage.recalc(), 0);
        if (customerId === '__new__') {
            const form = $('#est-new-customer-form');
            if (form) form.style.display = 'block';
            return;
        }
        const ncf = $('#est-new-customer-form');
        if (ncf) ncf.style.display = 'none';
    },

    async saveNewCustomer() {
        const name = $('#est-new-cust-name').value.trim();
        if (!name) { toast(`${T('Customer')} name is required`, 'error'); return; }
        try {
            const cust = await API.post('/customers', {
                name, email: $('#est-new-cust-email').value.trim() || null,
                phone: $('#est-new-cust-phone').value.trim() || null,
            });
            EstimatesPage._customers.push(cust);
            const sel = $('#est-customer-select');
            const opt = document.createElement('option');
            opt.value = cust.id; opt.textContent = cust.name; opt.selected = true;
            sel.appendChild(opt);
            $('#est-new-customer-form').style.display = 'none';
            toast(`${T('Customer')} "${cust.name}" created`);
        } catch (err) { toast(err.message, 'error'); }
    },

    cancelNewCustomer() {
        $('#est-new-customer-form').style.display = 'none';
        $('#est-customer-select').value = '';
    },

    async showForm(id = null) {
        const [customers, items, settings] = await Promise.all([
            API.get('/customers?active_only=true&tree=1'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
        ]);

        let est = {
            customer_id: '',
            date: todayISO(),
            expiration_date: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: '',
            lines: [],
        };
        if (id) est = await API.get(`/estimates/${id}`);
        // a new construction estimate starts with the company's default terms
        else est.terms = settings.estimate_terms || '';
        const classGroup = await classFormGroupHtml(est.class_id);
        const jobGroup = await jobFormGroupHtml(est.job_id, 'est-customer-select');
        await CostCodes.load();
        await CostTypes.load();
        await TradePackages.load();
        if (est.lines.length === 0) est.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];

        EstimatesPage.lineCount = est.lines.length;
        EstimatesPage._items = items;

        EstimatesPage._customers = customers;
        const custOpts = customers.map(c => `<option value="${c.id}" ${est.customer_id==c.id?'selected':''}>${escapeHtml(customerLabel(c))}</option>`).join('');

        openModal(id ? 'Edit Estimate' : 'New Estimate', `
            <form id="est-form" onsubmit="EstimatesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="est-customer-select" required onchange="EstimatesPage.customerSelected(this.value)"><option value="">Select...</option><option value="__new__">+ ${T('New Customer')}</option>${custOpts}</select>
                        <div id="est-new-customer-form" style="display:none; margin-top:8px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light);">
                            <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add ${T('Customer')}</div>
                            <input id="est-new-cust-name" placeholder="Name *" aria-label="${T('Customer')} name" aria-required="true" style="width:100%; margin-bottom:4px;">
                            <input id="est-new-cust-email" placeholder="Email" style="width:100%; margin-bottom:4px;">
                            <input id="est-new-cust-phone" placeholder="Phone" style="width:100%; margin-bottom:4px;">
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-sm btn-primary" onclick="EstimatesPage.saveNewCustomer()">Save</button>
                                <button type="button" class="btn btn-sm btn-secondary" onclick="EstimatesPage.cancelNewCustomer()">Cancel</button>
                            </div>
                        </div></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${est.date}"></div>
                    <div class="form-group"><label>Expiration Date</label>
                        <input name="expiration_date" type="date" value="${est.expiration_date || ''}"></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" value="${+((est.tax_rate || 0) * 100).toFixed(4)}"
                            oninput="EstimatesPage.recalc()"></div>
                    ${classGroup}${jobGroup}
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <div class="table-container table-container--scroll"><table class="line-items-table">
                    <thead><tr>
                        <th scope="col">Item</th><th scope="col">Description</th>${EstimatesPage.pkgHead()}${EstimatesPage.kindHead()}${CostCodes.headHtml()}${CostTypes.headHtml()}<th scope="col" title='Unit cost (budget side)'>Cost</th>${EstimatesPage.unitHead()}<th scope="col" class="col-qty">Qty</th>
                        <th scope="col" class="col-rate">Rate</th><th scope="col" title="Sales tax applies to this line">Tax</th><th scope="col" class="col-amount">Amount</th><th scope="col" class="col-actions"></th>
                    </tr></thead>
                    <tbody id="est-lines">
                        ${est.lines.map((l, i) => EstimatesPage.lineRowHtml(i, l, items)).join('')}
                    </tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="EstimatesPage.addLine()">+ Add Line</button>
                ${EstimatesPage.markupsBlockHtml(est)}
                ${Terms.isConstruction() ? '' : `<div class="invoice-totals" id="est-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="est-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="est-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="est-total">$0.00</span></div>
                </div>`}
                ${EstimatesPage.bidTotalsPanelHtml()}
                ${EstimatesPage.termsBlockHtml(est)}
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(est.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Estimate</button>
                </div>
            </form>`, { wide: true });
        typeof LineOrder !== 'undefined' && LineOrder.attach('#est-lines', { add: () => EstimatesPage.addLine(), changed: () => EstimatesPage.recalc() });
        EstimatesPage.recalc();
    },

    // --- Construction bid shape (#551) --------------------------------------
    // Package / kind / unit columns, the markups block, the bid totals panel
    // and the Accept button all show in construction mode only; a Business or
    // Nonprofit company sees exactly the estimate form it always had.
    pkgHead() { return TradePackages.headHtml(); },
    kindHead() { return Terms.isConstruction() ? '<th scope="col" title="Base, allowance, alternate or unit price">Kind</th>' : ''; },
    unitHead() { return Terms.isConstruction() ? '<th scope="col" class="col-qty" title="Unit of measure">Unit</th>' : ''; },

    pkgCell(line) { return TradePackages.cellHtml('line-package', line.trade_package_id || null); },
    kindCell(line) {
        if (!Terms.isConstruction()) return '';
        const k = line.line_kind || 'base';
        const o = (v, label) => `<option value="${v}" ${k === v ? 'selected' : ''}>${label}</option>`;
        // the hidden accept flag rides with the line so a save carries it; the
        // Accept checkbox lives in the bid totals panel and toggles it
        return `<td><select class="line-kind" onchange="EstimatesPage.kindChanged(this)">${o('base', 'Base')}${o('allowance', 'Allowance')}${o('alternate', 'Alternate')}${o('unit_price', 'Unit price')}</select>` +
            `<input type="checkbox" class="line-alt-accepted" style="display:none" ${line.alternate_accepted ? 'checked' : ''}></td>`;
    },
    // Switching a line to Alternate lifts the rate's min=0 so a deduct can be
    // keyed (a deduct is a negative amount); switching back restores it.
    kindChanged(sel) {
        EstimatesPage.liftAlternateMins();
        EstimatesPage.recalc();
    },
    // Every rate box renders with min=0 (the sales forms share that markup);
    // an Alternate row loses it here, after render and on each kind change,
    // and gets it back when the kind changes to anything else.
    liftAlternateMins() {
        $$('#est-lines tr').forEach(row => {
            const kind = row.querySelector('.line-kind');
            const rate = row.querySelector('.line-rate');
            if (!kind || !rate) return;
            if (kind.value === 'alternate') rate.removeAttribute('min');
            else rate.setAttribute('min', '0');
        });
    },
    unitCell(line) {
        if (!Terms.isConstruction()) return '';
        return `<td><input class="line-unit" style="width:48px" value="${escapeHtml(line.unit || '')}" placeholder="EA"></td>`;
    },

    markupsBlockHtml(est) {
        if (!Terms.isConstruction()) return '';
        const f = (name, val, label) =>
            `<div class="form-group"><label>${label} (%)</label><input name="${name}" type="number" step="0.0001" value="${+(Number(val) || 0).toFixed(4)}" oninput="EstimatesPage.recalc()"></div>`;
        return `<h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Markups</h3>
            <div class="form-grid">
                ${f('gc_pct', est.gc_pct, 'General conditions')}
                ${f('insurance_pct', est.insurance_pct, 'Insurance')}
                ${f('fee_pct', est.fee_pct, 'Fee')}
                ${f('bond_pct', est.bond_pct, 'Bond')}
            </div>
            <label style="display:block; margin-top:6px;"><input type="checkbox" name="markup_allowances" ${est.markup_allowances ? 'checked' : ''} onchange="EstimatesPage.recalc()"> Apply the markups to allowances too (off by default)</label>
            <label style="display:block; margin-top:6px;"><input type="checkbox" name="show_markup_rows" ${est.show_markup_rows === false ? '' : 'checked'}> Show the markup rows on the owner's bid</label>
            <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">Off, the owner's PDF spreads the markups into each package's price and prints one subtotal. The totals here do not change.</div>`;
    },

    // The terms / inclusions / exclusions the owner's PDF prints above the
    // acceptance block; construction only. New estimates start from Settings.
    termsBlockHtml(est) {
        if (!Terms.isConstruction()) return '';
        return `<div class="form-group" style="margin-top:12px;"><label>Terms, inclusions and exclusions</label>
            <textarea name="terms" rows="5">${escapeHtml(est.terms || '')}</textarea></div>`;
    },

    bidTotalsPanelHtml() {
        if (!Terms.isConstruction()) return '';
        const row = (label, id) => `<div class="total-row"><span class="label">${label}</span><span class="value" id="${id}">$0.00</span></div>`;
        return `<div class="invoice-totals" id="est-bid-totals" style="margin-top:8px;">
            ${row('Cost of work', 'est-bid-cost')}
            ${row('Priced direct', 'est-bid-direct')}
            ${row('Allowances', 'est-bid-allow')}
            ${row('Subtotal', 'est-bid-subtotal')}
            ${row('General conditions', 'est-bid-gc')}
            ${row('Insurance', 'est-bid-ins')}
            ${row('Fee', 'est-bid-fee')}
            ${row('Bond', 'est-bid-bond')}
            ${row('Markup on allowances', 'est-bid-markup-allow')}
            ${row('Bid total', 'est-bid-total')}
            ${row('Tax', 'est-bid-tax')}
            <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="est-bid-grand">$0.00</span></div>
            <div id="est-bid-alternates" style="margin-top:8px; font-size:11px;"></div>
            <div id="est-bid-unitprices" style="margin-top:4px; font-size:11px;"></div>
        </div>`;
    },

    _r(x) { return Math.round((Number(x) + Number.EPSILON) * 100) / 100; },

    // The bid math the server computes (app/services/estimating.py), mirrored
    // for the live form panel. Lines stay at cost; the four markups are their
    // own figures under the subtotal, and tax covers the lines and the markups
    // together. Reads the current rows and markup inputs (#551).
    bidTotals() {
        const pct = v => (Number(v) || 0) / 100;
        const form = $('#est-form');
        const m = n => pct(form && form[n] ? form[n].value : 0);
        // the marked-up factor an alternate's shown price uses
        const factor = (1 + m('gc_pct')) * (1 + m('insurance_pct')) * (1 + m('fee_pct')) * (1 + m('bond_pct'));
        const markupAllowances = !!(form && form.markup_allowances && form.markup_allowances.checked);
        let markupBase = 0, allowanceTotal = 0, pricedDirect = 0, subtotal = 0, taxableSubtotal = 0, anyTaxable = false;
        const alternates = [], unitPrices = [];
        $$('#est-lines tr').forEach(row => {
            // the stable line index is the data attribute, not the row's
            // position — a removed row must not shift an alternate's handle
            const eline = Number(row.dataset ? row.dataset.eline : row.getAttribute('data-eline'));
            const kind = row.querySelector('.line-kind')?.value || 'base';
            const qty = Number(row.querySelector('.line-qty')?.value) || 0;
            const ucRaw = row.querySelector('.line-unit-cost')?.value;
            const hasCost = ucRaw !== '' && ucRaw != null;
            const cost = hasCost ? EstimatesPage._r(qty * Number(ucRaw)) : 0;
            const rate = Number(row.querySelector('.line-rate')?.value) || 0;
            const amount = EstimatesPage._r(qty * rate);
            const taxable = row.querySelector('.line-taxable')?.checked || false;
            const accepted = row.querySelector('.line-alt-accepted')?.checked || false;
            const desc = row.querySelector('.line-desc')?.value || '';
            const unit = row.querySelector('.line-unit')?.value || '';
            if (kind === 'unit_price') { unitPrices.push({ idx: eline, desc, unit, rate }); return; }
            if (kind === 'alternate') {
                // an alternate not yet taken shows its marked-up price (what
                // taking it adds); one with no cost shows its rate price. A
                // TAKEN one shows its cost, the figure that sits in the scope
                // and on the owner's PDF, its markups in the rows below.
                const shown = hasCost && !accepted ? EstimatesPage._r(cost * factor) : amount;
                alternates.push({ idx: eline, desc, amount: shown, accepted });
                if (!accepted) return;
            }
            subtotal += amount;
            if (taxable) { taxableSubtotal += amount; anyTaxable = true; }
            if (kind === 'allowance') {
                allowanceTotal += amount;
                if (markupAllowances) markupBase += amount;
            } else if (hasCost) markupBase += cost;
            else pricedDirect += amount;
        });
        const gc = EstimatesPage._r(markupBase * m('gc_pct'));
        const insurance = EstimatesPage._r((markupBase + gc) * m('insurance_pct'));
        const fee = EstimatesPage._r((markupBase + gc + insurance) * m('fee_pct'));
        const bond = EstimatesPage._r((markupBase + gc + insurance + fee) * m('bond_pct'));
        const markupTotal = gc + insurance + fee + bond;
        const bid = EstimatesPage._r(subtotal + markupTotal);
        // tax covers the lines and the markups when any included line is
        // taxable (the server's rule); an exempt estimate taxes nothing
        const tax = anyTaxable ? EstimatesPage._r((taxableSubtotal + markupTotal) * m('tax_rate')) : 0;
        const total = EstimatesPage._r(bid + tax);
        return { markupAllowances, markupBase, gc, insurance, fee, bond, markupTotal, pricedDirect, allowanceTotal, subtotal, bid, tax, total, alternates, unitPrices };
    },

    renderBidTotals() {
        if (!Terms.isConstruction() || !$('#est-bid-totals')) return;
        const t = EstimatesPage.bidTotals();
        const set = (id, v) => { const el = $(`#${id}`); if (el) el.textContent = formatCurrency(v); };
        set('est-bid-cost', t.markupBase); set('est-bid-direct', t.pricedDirect); set('est-bid-allow', t.allowanceTotal);
        set('est-bid-subtotal', t.subtotal); set('est-bid-gc', t.gc); set('est-bid-ins', t.insurance);
        set('est-bid-fee', t.fee); set('est-bid-bond', t.bond); set('est-bid-total', t.bid);
        set('est-bid-tax', t.tax); set('est-bid-grand', t.total);
        const ma = $('#est-bid-markup-allow');
        if (ma) ma.textContent = t.markupAllowances ? 'On' : 'Off';
        const alt = $('#est-bid-alternates');
        if (alt) alt.innerHTML = t.alternates.length
            ? '<strong>Alternates</strong> <span style="color:var(--gray-500)">(add / deduct)</span>' + t.alternates.map(a =>
                `<div><label><input type="checkbox" ${a.accepted ? 'checked' : ''} onchange="EstimatesPage.toggleAlternate(${a.idx}, this.checked)"> ${escapeHtml(a.desc || '(alternate)')} — ${formatCurrency(a.amount)}${a.accepted ? ' <span style="color:var(--gray-500)">(taken, at cost; markups in the rows above)</span>' : ''}</label></div>`).join('')
            : '';
        const up = $('#est-bid-unitprices');
        if (up) up.innerHTML = t.unitPrices.length
            ? '<strong>Unit prices</strong>' + t.unitPrices.map(u =>
                `<div>${escapeHtml(u.desc || '(unit price)')} — ${SalesLines.rate(u.rate)}${u.unit ? ' / ' + escapeHtml(u.unit) : ''}</div>`).join('')
            : '';
    },

    toggleAlternate(idx, checked) {
        const row = $(`[data-eline="${idx}"]`);
        const box = row && row.querySelector('.line-alt-accepted');
        if (box) box.checked = checked;
        EstimatesPage.recalc();
    },

    acceptButtonHtml(est) {
        if (!(est.job_id && est.status === 'pending')) return '';
        return `<button class="btn btn-primary" onclick="EstimatesPage.accept(${est.id})">Accept</button>`;
    },

    async accept(id) {
        if (!confirm('Accepting locks the job budget to this estimate.')) return;
        try {
            await API.post(`/estimates/${id}/accept`, {});
            toast('Estimate accepted; job budget locked');
            closeModal();
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    lineRowHtml(idx, line, items) {
        return `<tr data-eline="${idx}">
            <td><select class="line-item" style="min-width:150px" onchange="EstimatesPage.itemSelected(${idx})">
                ${ItemQuickAdd.options(items, line.item_id)}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            ${EstimatesPage.pkgCell(line)}${EstimatesPage.kindCell(line)}${CostCodes.cellHtml('line-cost-code', line.cost_code_id || null)}${CostTypes.cellHtml('line-cost-type', line.cost_type || null)}
            <td><input class="line-unit-cost money" type="number" step="0.0001" value="${line.unit_cost ?? ''}" placeholder="cost" title="Unit cost; in construction mode a base or alternate line is priced at cost (the markups are rows under the subtotal)" oninput="EstimatesPage.recalc()"></td>
            ${EstimatesPage.unitCell(line)}<td><input class="line-qty" type="number" step="0.0001" value="${line.quantity || 1}" oninput="EstimatesPage.recalc()"></td>
            <td><input class="line-rate money" type="number" step="0.0001" min="0" value="${Number(line.rate) || 0}" oninput="EstimatesPage.recalc()"></td>
            <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${TaxDefault.checked(line)} onchange="EstimatesPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="EstimatesPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const tbody = $('#est-lines');
        const idx = EstimatesPage.lineCount++;
        tbody.insertAdjacentHTML('beforeend', EstimatesPage.lineRowHtml(idx, {}, EstimatesPage._items));
        EstimatesPage.recalc();
    },

    removeLine(idx) {
        const row = $(`[data-eline="${idx}"]`);
        if (row) row.remove();
        EstimatesPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-eline="${idx}"]`);
        if (row && ItemQuickAdd.selected(row.querySelector('.line-item'), EstimatesPage._items, () => EstimatesPage.itemSelected(idx))) return;
        const item = row && SalesLines.fillFromItem(row, EstimatesPage._items);
        if (item) {
            // the item's standard cost is the budget side of the line; blank
            // when the item carries none, and the user can overwrite it
            const cost = row.querySelector('.line-unit-cost');
            if (cost) cost.value = item.cost && Number(item.cost) !== 0 ? item.cost : '';
            EstimatesPage.recalc();
        }
    },

    recalc() {
        if (Terms.isConstruction()) { EstimatesPage.liftAlternateMins(); EstimatesPage.syncRatesToCost(); }
        TaxExempt.enforce(EstimatesPage._customers, $('#est-customer-select')?.value, $('#est-lines'));
        const t = SalesLines.totals($('#est-lines'), $('#est-form [name="tax_rate"]')?.value);
        // Construction replaces the plain Subtotal/Tax/Total panel with the
        // bid panel (lines at cost, markups under the subtotal, #551); the
        // business form keeps the standard panel.
        if (Terms.isConstruction()) EstimatesPage.renderBidTotals();
        else SalesLines.show(t, ['est-subtotal', 'est-tax', 'est-total']);
        return t;
    },

    // In construction mode a base/alternate row that carries a unit cost is
    // priced AT COST: the rate box mirrors the cost, is read-only, and the
    // amount follows (#551). Clearing the cost makes the rate editable again.
    // Allowance and unit-price rows keep an editable rate. Runs after render,
    // inside recalc, like liftAlternateMins — the pinned rate markup is
    // untouched; everything here is a runtime change.
    syncRatesToCost() {
        $$('#est-lines tr').forEach(row => {
            const kind = row.querySelector('.line-kind')?.value || 'base';
            const rate = row.querySelector('.line-rate');
            const ucEl = row.querySelector('.line-unit-cost');
            if (!rate || !ucEl) return;
            const atCost = (kind === 'base' || kind === 'alternate') && ucEl.value !== '' && ucEl.value != null;
            if (atCost) {
                rate.value = ucEl.value;
                rate.readOnly = true;
                rate.title = 'At cost; the markups are lines under the subtotal';
            } else {
                // clearing the cost drops the mirrored rate to 0 before it
                // unlocks, so the old cost is not left behind as a price
                if (rate.readOnly) rate.value = '0';
                rate.readOnly = false;
                rate.title = '';
            }
        });
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#est-lines tr').forEach((row, i) => {
            lines.push({
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                is_taxable: row.querySelector('.line-taxable') ? row.querySelector('.line-taxable').checked : null,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                cost_type: CostTypes.fromRow(row, 'line-cost-type'),
                unit_cost: row.querySelector('.line-unit-cost')?.value ? parseFloat(row.querySelector('.line-unit-cost').value) : null,
                trade_package_id: TradePackages.fromRow(row, 'line-package'),
                line_kind: row.querySelector('.line-kind')?.value || 'base',
                unit: row.querySelector('.line-unit')?.value || null,
                alternate_accepted: row.querySelector('.line-alt-accepted')?.checked || false,
                line_order: i,
            });
        });

        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            expiration_date: form.expiration_date.value || null,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            lines,
        };
        // Compounding markups only exist on the construction form.
        if (Terms.isConstruction()) {
            data.gc_pct = parseFloat(form.gc_pct?.value) || 0;
            data.insurance_pct = parseFloat(form.insurance_pct?.value) || 0;
            data.fee_pct = parseFloat(form.fee_pct?.value) || 0;
            data.bond_pct = parseFloat(form.bond_pct?.value) || 0;
            data.markup_allowances = !!form.markup_allowances?.checked;
            data.show_markup_rows = !!form.show_markup_rows?.checked;
            data.terms = form.terms ? form.terms.value : null;
        }

        if (!(await TxnWarn.ok(form, { editing: !!id }))) return;
        if (!(await ClassWarn.ok(form))) return;
        try {
            if (id) { await API.put(`/estimates/${id}`, data); toast('Estimate updated'); }
            else { await API.post('/estimates', data); toast('Estimate created'); }
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },
};
