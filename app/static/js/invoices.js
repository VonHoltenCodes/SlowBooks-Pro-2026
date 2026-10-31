/**
 * The "Create Invoices" form — QB2003's crown jewel, yellow paper
 * texture and all. We use an HTML table instead of a custom grid;
 * auto-fill on item selection lives in itemSelected() below.
 */

/**
 * Line arithmetic shared by every sales form — invoice, sales receipt,
 * estimate, credit memo and recurring schedule. A line rounds to the cent
 * the way the server stores it (accounting._q, half up), tax is taken on
 * the rounded taxable lines, and the total is their sum, so the figure a
 * form shows is the figure that posts. The credit memo and recurring forms
 * had no arithmetic at all: picking an item filled no price, nothing showed
 * a total, and $0.00 documents were saved (2.17.3 exploratory, W-M1 / F14).
 */
const SalesLines = {
    // Half up to the cent. Shifting by exponent avoids binary drift:
    // 1.005 * 100 is 100.49999... in floating point, Number('1.005e2') is 100.5.
    cents(x) {
        const n = Number(x) || 0;
        const a = Math.abs(n);
        const shifted = Number(`${a}e2`);
        const r = Number.isFinite(shifted) ? Math.round(shifted) : Math.round(a * 100);
        return (n < 0 ? -1 : 1) * Number(`${r}e-2`);
    },

    // Fill a line from the item picked in its .line-item select: description,
    // price and the item's tax flag. Returns the item, or null for "--".
    fillFromItem(row, items) {
        const itemId = row.querySelector('.line-item')?.value;
        const item = (items || []).find(i => i.id == itemId);
        if (!item) return null;
        const desc = row.querySelector('.line-desc');
        if (desc) desc.value = item.description || item.name;
        const rate = row.querySelector('.line-rate');
        if (rate) {
            rate.value = item.rate;
            // A Discount item's price is entered negative (-10.00); any other
            // item gets back the floor its box had.
            if (item.is_discount && rate.dataset) {
                if (rate.dataset.floor === undefined) rate.dataset.floor = rate.getAttribute('min') || '';
                rate.removeAttribute('min');
                rate.placeholder = '-10.00';
            } else if (rate.dataset && rate.dataset.floor !== undefined) {
                if (rate.dataset.floor) rate.setAttribute('min', rate.dataset.floor);
                delete rate.dataset.floor;
                rate.placeholder = '';
            }
        }
        const tax = row.querySelector('.line-taxable');
        if (tax) {
            // An exempt customer's boxes are held off (TaxExempt); remember
            // the item's flag for when the form switches back.
            if (tax.disabled) tax.dataset.was = item.is_taxable !== false ? '1' : '0';
            else tax.checked = item.is_taxable !== false;
        }
        return item;
    },

    // The stock a tracked item has, as a small note under the line's
    // quantity box (#642): "2 on hand", in the warning colour once it is
    // below zero. Nothing for a service or an untracked item. Drawn from
    // the items the form already fetched, so it costs no request.
    onHandHtml(itemId, items) {
        const item = (items || []).find(i => i.id == itemId);
        const tracked = !!(item && item.track_inventory);
        const count = tracked ? Number(item.quantity_on_hand || 0) : 0;
        const colour = count < 0 ? 'var(--text-warning)' : 'var(--text-muted)';
        return `<div class="line-on-hand" style="font-size:10px; white-space:nowrap; color:${colour};"${tracked ? '' : ' hidden'}>${tracked ? `${count.toLocaleString()} on hand` : ''}</div>`;
    },

    // Redraw the row's on-hand note for the item now picked.
    showOnHand(row, items) {
        const note = row.querySelector('.line-on-hand');
        if (!note) return;
        note.outerHTML = SalesLines.onHandHtml(row.querySelector('.line-item')?.value, items);
    },

    // Recompute every row of `tbody` and return {subtotal, tax, total}. A
    // row without a Tax box counts as taxable, as it does on the server.
    totals(tbody, taxPct, currency) {
        let subtotal = 0, taxable = 0;
        (tbody ? [...tbody.querySelectorAll('tr')] : []).forEach(row => {
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            const amount = SalesLines.cents(qty * rate);
            subtotal += amount;
            if (row.querySelector('.line-taxable')?.checked !== false) taxable += amount;
            const cell = row.querySelector('.line-amount');
            if (cell) cell.textContent = SalesLines.money(amount, currency);
        });
        subtotal = SalesLines.cents(subtotal);
        const tax = SalesLines.tax(SalesLines.cents(taxable), taxPct);
        return { subtotal, tax, total: SalesLines.cents(subtotal + tax) };
    },

    // Tax on an amount at a percent typed on a form, to the cent, half up:
    // the figure the server stores. It is worked in whole cents and
    // ten-thousandths of a percent (a rate keeps four places), because a
    // binary fraction can put a half cent on the wrong side: $175,800.00
    // at 1.0875% is $1,911.825, so $1,911.83, and floating point showed
    // $1,911.82; a purchase order showed $8.41 for 8.25% of $102.00.
    tax(amount, taxPct) {
        const cents = Math.round((Number(amount) || 0) * 100);
        const units = SalesLines.percentUnits(taxPct);
        const product = Math.abs(cents * units) + 500000;
        if (!Number.isSafeInteger(product)) {
            return SalesLines.cents(SalesLines.cents(amount) * (parseFloat(taxPct) || 0) / 100);
        }
        const whole = (product - product % 1000000) / 1000000;
        return ((cents < 0) !== (units < 0) && whole ? -whole : whole) / 100;
    },

    // A typed percent in ten-thousandths ("8.875" is 88750), read from its
    // digits rather than through a binary fraction. A fifth place rounds
    // half up, as the server rounds the rate it is sent.
    percentUnits(taxPct) {
        const text = String(taxPct ?? '').trim();
        const m = /^(\d*)(?:\.(\d*))?$/.exec(text);
        if (!m || !(m[1] || m[2])) return Math.round((parseFloat(text) || 0) * 10000);
        const places = ((m[2] || '') + '00000').slice(0, 5);
        return Number(m[1] || 0) * 10000 + Number(places.slice(0, 4)) + (places[4] >= '5' ? 1 : 0);
    },

    // Write totals into the form's Subtotal / Tax / Total cells (by id).
    show(t, ids, currency) {
        const put = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = SalesLines.money(v, currency); };
        put(ids[0], t.subtotal); put(ids[1], t.tax); put(ids[2], t.total);
    },

    // Money as the document prints it: dollars in the home currency; a
    // foreign document carries its ISO code ("EUR 850.00"), so a euro
    // invoice never reads as $850.00 (W-M2). Matches the PDF's filter.
    money(amount, currency, places = 2) {
        const home = ((typeof App !== 'undefined' && App.settings && App.settings.home_currency) || 'USD').toUpperCase();
        const code = String(currency || '').trim().toUpperCase();
        const n = Number(amount) || 0;
        if (!code || code === home) {
            if (places === 2) return formatCurrency(amount);
            return new Intl.NumberFormat('en-US', { style: 'currency', currency: home, minimumFractionDigits: 2, maximumFractionDigits: places }).format(n);
        }
        const digits = n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: places });
        return `${code} ${digits}`;
    },

    // A unit price: two places, or up to four when it has them ($0.045).
    rate(value, currency) { return SalesLines.money(value, currency, 4); },

    // A document's tax rate (a fraction) as the percent it prints: at least
    // two places, up to the four a rate keeps ("8.875", "8.25", "7.00"), as
    // the PDFs print it (pdf_service's tax_percent filter).
    taxPercent(fraction) {
        return ((parseFloat(fraction) || 0) * 100).toFixed(4).replace(/0{1,2}$/, '');
    },

    // Send a sales document. One that adds up to $0.00 comes back refused
    // (409, code "zero_total") unless the person says it is meant to be:
    // no-charge warranty work is a real invoice, a form that filled in no
    // price was the accident (2.17.3 exploratory, W-M1 / F14). Ask the
    // server's question ("This invoice adds up to $0.00. Save it anyway?")
    // and on yes send it once more with allow_zero_total. `send(allow)`
    // makes the request; resolves to its answer, or null for no.
    // A refused save that needs only the user's say-so is asked and sent
    // again with the flag the server named: a $0.00 document (409
    // "zero_total") and, since 2.22.2, a customer past their credit limit
    // (409 "credit_limit", #256). `send(flags)` makes the request; flags is
    // {} first, then what the server asked for, so both can be granted in
    // one save. Returns null when the user said no.
    async sendAllowingZero(send, flags = {}) {
        try {
            return await send(flags);
        } catch (err) {
            const code = err && err.status === 409 && err.detail && err.detail.code;
            const flag = code === 'zero_total' ? 'allow_zero_total'
                : code === 'credit_limit' ? 'allow_over_credit_limit' : null;
            if (!flag || flags[flag]) throw err;
            if (!confirm(err.detail.question || err.message)) return null;
            return SalesLines.sendAllowingZero(send, { ...flags, [flag]: true });
        }
    },
};
window.SalesLines = SalesLines;

// Whether a new sales line's Tax box starts ticked and a new item is taxable:
// the company's "New lines and items are taxable" setting (#486), on unless
// it is "false". A stored line or an item that says is_taxable keeps it.
// Here beside SalesLines, which every sales form already loads.
const TaxDefault = {
    on() {
        const s = (typeof App !== 'undefined' && App && App.settings) || {};
        return s.new_lines_taxable !== 'false';
    },
    // The Tax box's attribute for a line drawn from `line` (blank = new).
    checked(line) {
        const flag = line ? line.is_taxable : undefined;
        return (flag === true || (flag !== false && TaxDefault.on())) ? 'checked' : '';
    },
};
window.TaxDefault = TaxDefault;

const InvoicesPage = {
    // The document's literal face, as the PDF prints it: a flagged pledge is a
    // PLEDGE, everything else an INVOICE regardless of company vocabulary.
    docLabel(inv) { return inv.is_pledge ? 'Pledge' : 'Invoice'; }, // literal face

    showAll() {
        InvoicesPage._showAll = true;
        App.navigate(location.hash || '#/invoices');
    },

    async render() {
        // Sales receipts are invoices under the hood; they get their own
        // page, so keep them out of this list.
        // The newest 500, and a way to see the rest (issue #191).
        const all = InvoicesPage._showAll;
        InvoicesPage._showAll = false;
        const rows = all ? await fetchAllPages('/invoices?is_sales_receipt=false')
            : await API.get('/invoices?is_sales_receipt=false&limit=501');
        const invoices = all ? rows : rows.slice(0, 500);
        return renderListPage({
            title: T('Invoices'),
            headerHtml: `<button class="btn btn-primary" ${App.shortcutAttrs('N', T('New Invoice'))} onclick="InvoicesPage.showForm()">+ ${T('New Invoice')}</button>`
                + (all ? '' : listCapNote(rows, 500, 'InvoicesPage.showAll()', Terms.text('invoices'))),
            filter: {
                id: 'inv-status-filter',
                rowSelector: '.inv-row',
                options: [['draft', 'Draft'], ['sent', 'Sent'], ['partial', 'Partial'], ['paid', 'Paid'], ['void', 'Void']],
            },
            empty: Terms.text(`<p>No invoices yet.</p>
                <button class="btn btn-primary" onclick="InvoicesPage.showForm()" style="margin-top:10px;">+ Create your first invoice</button>`),
            // Every column but Actions sorts (#650); until a heading is
            // pressed the list keeps the server's order, newest first.
            columns: [
                { label: '#', key: 'invoice_number' },
                { label: T('Customer'), key: 'customer_name' },
                { label: 'Date', key: 'date' },
                { label: 'Due Date', key: 'due_date' },
                { label: 'Status', key: 'status' },
                { label: 'Total', cls: 'amount', key: 'total' },
                { label: 'Balance', cls: 'amount', key: 'balance_due' },
                'Actions',
            ],
            sort: { id: 'inv-list' },
            items: invoices,
            row: inv => `<tr class="inv-row" data-status="${inv.status}">
                    <td><strong>${escapeHtml(inv.invoice_number)}</strong></td>
                    <td>${escapeHtml(inv.customer_name || '')}</td>
                    <td>${formatDate(inv.date)}</td>
                    <td>${formatDate(inv.due_date)}</td>
                    <td>${statusBadge(inv.status)}</td>
                    <td class="amount">${SalesLines.money(inv.total, inv.currency)}</td>
                    <td class="amount">${SalesLines.money(inv.balance_due, inv.currency)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.view(${inv.id})">View</button>
                        <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.showForm(${inv.id})">Edit</button>
                        ${inv.status === 'draft' ? `<button class="btn btn-sm btn-primary" onclick="InvoicesPage.markSent(${inv.id})">Mark Sent</button>` : ''}
                        ${inv.status !== 'void' && parseFloat(inv.balance_due) > 0 ? `<button class="btn btn-sm btn-secondary" onclick="InvoicesPage.showWriteOff(${inv.id}, ${parseFloat(inv.balance_due)})">Write Off</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    // Forgive an open balance (a pledge that will never be paid, a balance
    // too small to collect): a write-off credit memo to Bad Debt Expense,
    // applied at once. Any company, since 2.22.2 (#255); it was drawn for
    // a nonprofit only, though the route never minded.
    showWriteOff(id, balance) {
        openModal('Write Off Balance', `
            <form onsubmit="InvoicesPage.saveWriteOff(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" class="money" type="number" step="0.01" min="0.01" max="${balance}" required value="${balance.toFixed(2)}"></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo" placeholder="${Terms.isNonprofit() ? 'e.g. pledge withdrawn' : 'e.g. too small to collect'}"></div>
                </div>
                <div style="font-size:11px;color:var(--gray-500);margin-top:6px">Posts a credit memo to Bad Debt Expense and applies it to this ${T('invoice')}. Void the credit memo to undo.</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Write Off</button>
                </div>
            </form>`);
    },

    async saveWriteOff(e, id) {
        e.preventDefault();
        const form = e.target;
        try {
            const cm = await API.post(`/invoices/${id}/write-off`, {
                date: form.date.value, amount: parseFloat(form.amount.value), memo: form.memo.value || null,
            });
            toastPosted(`Written off as credit memo ${cm.memo_number}`);
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        const [inv, settings] = await Promise.all([
            API.get(`/invoices/${id}`),
            API.get('/settings'),
        ]);
        const money = (v) => SalesLines.money(v, inv.currency);
        let linesHtml = inv.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${SalesLines.rate(l.rate, inv.currency)}</td><td class="amount">${money(l.amount)}</td></tr>`
        ).join('');

        openModal(`${T('Invoice')} #${inv.invoice_number}`, `
            ${InvoicesPage.logoOptionHtml(settings)}
            <div style="margin-bottom:12px;">
                <strong>${T('Customer')}:</strong> ${escapeHtml(inv.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(inv.date)}<br>
                <strong>Due:</strong> ${formatDate(inv.due_date)}<br>
                ${inv.terms ? `<strong>Terms:</strong> ${escapeHtml(inv.terms)}<br>` : ''}
                ${PaymentTerms.viewHtml(inv)}
                <strong>Status:</strong> ${statusBadge(inv.status)}<br>
                ${inv.po_number ? `<strong>PO#:</strong> ${escapeHtml(inv.po_number)}<br>` : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${money(inv.subtotal)}</span></div>
                <div class="total-row"><span class="label">Tax</span><span class="value">${money(inv.tax_amount)}</span></div>
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${money(inv.total)}</span></div>
                ${Number(inv.retainage_held) ? `<div class="total-row"><span class="label">Retainage withheld this period</span><span class="value">-${money(inv.retainage_held)}</span></div>` : ''}
                ${Number(inv.retainage_released) ? `<div class="total-row"><span class="label">Retainage released</span><span class="value">${money(inv.retainage_released)}</span></div>` : ''}
                ${(Number(inv.retainage_held) || Number(inv.retainage_released)) ? `<div class="total-row"><span class="label">Current payment due</span><span class="value">${money((Number(inv.total) - Number(inv.retainage_held) + Number(inv.retainage_released)).toFixed(2))}</span></div>` : ''}
                <div class="total-row"><span class="label">Paid</span><span class="value">${money(inv.amount_paid)}</span></div>
                <div class="total-row grand-total"><span class="label">Balance Due</span><span class="value">${money(inv.balance_due)}</span></div>
            </div>
            ${inv.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(inv.notes)}</p>` : ''}
            <div id="inv-credit-note"></div>
            <div style="margin-top:16px; border-top:1px solid var(--gray-200); padding-top:12px;">
                <h3 style="font-size:13px; margin-bottom:8px;">Attachments</h3>
                <div id="inv-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <input type="file" id="inv-attach-file" aria-label="File to attach" style="font-size:11px;">
                <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.uploadAttachment(${inv.id})" style="margin-left:4px;">Upload</button>
            </div>
            <div class="form-actions">
                <button class="btn btn-secondary" data-invoice-logo-action onclick="window.open('/api/invoices/${inv.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" data-invoice-logo-action onclick="window.open('/api/invoices/${inv.id}/print-preview','_blank')">Print</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.duplicate(${inv.id})">Duplicate</button>
                <button class="btn btn-secondary" data-invoice-logo-action onclick="InvoicesPage.emailInvoice(${inv.id})">Email ${T('Invoice')}</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.copyPaymentLink(${inv.id})">Copy Payment Link</button>
                ${inv.checkout_provider && inv.status !== 'paid' && inv.status !== 'void' ? `<button class="btn btn-secondary" onclick="InvoicesPage.checkPaymentStatus(${inv.id}, '${inv.checkout_provider}')">Check Payment Status</button>` : ''}
                ${inv.status === 'draft' ? `<button class="btn btn-primary" onclick="InvoicesPage.markSent(${inv.id})">Mark Sent</button>` : ''}
                ${inv.status !== 'void' && parseFloat(inv.balance_due) > 0 ? `<button class="btn btn-secondary" onclick="InvoicesPage.showWriteOff(${inv.id}, ${parseFloat(inv.balance_due)})">Write Off</button>` : ''}
                ${inv.status !== 'void' ? `<button data-destructive class="btn btn-danger" onclick="InvoicesPage.void(${inv.id})">Void ${T('Invoice')}</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/invoices/${Number(id)}`);
        InvoicesPage.loadAttachments('invoice', inv.id);
        InvoicesPage.loadCreditNote(inv);
    },

    // Credit the customer already holds — the unapplied part of a payment,
    // a credit memo — can be applied to this invoice from here. The apply
    // existed (Receive Payments, the payment, the customer page), but the
    // invoice, where the balance is looked at, had no way to it (found
    // integrating the 2.17.3 exploratory fixes). Only credit in the
    // invoice's own currency can pay it.
    _usableCredits(inv, credits) {
        if (!credits || !Array.isArray(credits.credits)) return [];
        const code = String(inv.currency || credits.home_currency || '').toUpperCase();
        return credits.credits.filter(c =>
            String(c.currency || '').toUpperCase() === code && (parseFloat(c.available) || 0) > 0);
    },

    async loadCreditNote(inv) {
        const el = $('#inv-credit-note');
        if (!el || inv.status === 'void' || !((parseFloat(inv.balance_due) || 0) > 0)) return;
        let credits;
        try { credits = await API.get(`/customers/${inv.customer_id}/credits`); } catch (e) { return; }
        const usable = InvoicesPage._usableCredits(inv, credits);
        if (!usable.length) return;
        const held = usable.reduce((sum, c) => sum + PaymentsPage._cents(c.available), 0) / 100;
        el.innerHTML = `<div style="margin:12px 0; padding:8px 10px; border-radius:4px; background:#fff4d6; color:#7a5500;">
                ${escapeHtml(Terms.text(`This customer has ${SalesLines.money(held, inv.currency)} in credit not applied to an invoice yet.`))}
                <button type="button" class="btn btn-sm btn-primary" style="margin-left:8px;" onclick="InvoicesPage.showApplyCredit(${inv.id})">Apply Credit</button>
            </div>`;
    },

    // Each usable credit with an amount to take from it, filled oldest first
    // up to what the invoice still owes; any of it can be changed.
    async showApplyCredit(invoiceId) {
        let inv, credits;
        try {
            inv = await API.get(`/invoices/${invoiceId}`);
            credits = await API.get(`/customers/${inv.customer_id}/credits`);
        } catch (err) { toast(err.message, 'error'); return; }
        const usable = InvoicesPage._usableCredits(inv, credits);
        const cents = PaymentsPage._cents;
        const money = (v) => SalesLines.money(v, inv.currency);
        const due = cents(inv.balance_due);
        InvoicesPage._applying = { due, currency: inv.currency };
        let left = due;
        const rows = usable.map(c => {
            const take = Math.max(0, Math.min(left, cents(c.available)));
            left -= take;
            const label = PaymentsPage._creditLabel(c);
            return `<tr>
                <td>${escapeHtml(label)}</td>
                <td class="amount">${money(c.available)}</td>
                <td><input class="credit-take money" data-kind="${escapeHtml(c.kind)}" data-id="${c.id}" data-max="${c.available}"
                    type="number" step="0.01" min="0" max="${c.available}" value="${take > 0 ? (take / 100).toFixed(2) : ''}"
                    aria-label="Apply from ${escapeHtml(label)}" oninput="InvoicesPage._applyCreditStatus()" style="width:100px;"></td>
            </tr>`;
        }).join('');
        openModal(`Apply Credit to ${T('Invoice')} #${inv.invoice_number}`, `
            <form onsubmit="InvoicesPage.saveApplyCredit(event, ${inv.id})">
                <p style="margin-bottom:8px;">${escapeHtml(inv.customer_name || '')} owes <strong>${money(inv.balance_due)}</strong> on this ${T('invoice')}.</p>
                ${usable.length ? `<div class="table-container"><table><thead><tr>
                    <th scope="col">Credit</th><th scope="col" class="amount">Available</th><th scope="col" class="amount">Apply</th>
                </tr></thead><tbody>${rows}</tbody></table></div>
                <div id="apply-credit-status" style="margin-top:8px; font-size:13px;"></div>`
                : `<p style="color:var(--gray-400);">${Terms.text('This customer has no credit in this currency to apply.')}</p>`}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="InvoicesPage.view(${inv.id})">Cancel</button>
                    ${usable.length ? '<button type="submit" class="btn btn-primary">Apply Credit</button>' : ''}
                </div>
            </form>`);
        InvoicesPage._applyCreditStatus();
    },

    // What the amounts come to against what the invoice owes, as they are typed.
    _applyCreditStatus() {
        const el = $('#apply-credit-status');
        const state = InvoicesPage._applying;
        if (!el || !state) return;
        const money = (c) => SalesLines.money(c / 100, state.currency);
        let used = 0, over = false;
        $$('.credit-take').forEach(input => {
            const c = PaymentsPage._cents(input.value);
            used += c;
            if (c > PaymentsPage._cents(input.dataset.max)) over = true;
        });
        const bad = over || used > state.due;
        el.style.color = bad ? 'var(--text-danger)' : 'var(--gray-600)';
        el.textContent = over
            ? 'One of the amounts is more than that credit has left.'
            : used > state.due
                ? `That is ${money(used - state.due)} more than the ${T('invoice')} owes.`
                : `Applying ${money(used)}; ${money(state.due - used)} still due.`;
    },

    async saveApplyCredit(e, invoiceId) {
        e.preventDefault();
        const picks = [];
        $$('.credit-take').forEach(input => {
            const c = PaymentsPage._cents(input.value);
            if (c > 0) picks.push({ kind: input.dataset.kind, id: parseInt(input.dataset.id), amount: c / 100 });
        });
        if (!picks.length) { toast('Enter an amount to apply', 'error'); return; }
        try {
            for (const p of picks) {
                await PaymentsPage.applyCredit(p.kind, p.id, [{ invoice_id: invoiceId, amount: p.amount }]);
            }
            toast('Credit applied');
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    logoOptionHtml(settings) {
        if (!settings.company_logo_path) return '';
        const enabled = settings.invoice_show_logo !== 'false';
        // A company setting: the server takes Settings changes from an
        // administrator only, so another sign-in sees it locked, and why.
        // Marked data-admin as well, for a view opened before the role is
        // known (App.adminPass locks it then).
        const canChange = !App.role || App.role === 'admin';
        return `<div class="invoice-logo-option">
            <img class="invoice-logo-preview" src="${escapeHtml(settings.company_logo_path)}" alt="Company logo" ${enabled ? '' : 'hidden'}>
            <div>
                <label for="inv-show-logo">
                    <input id="inv-show-logo" type="checkbox" data-admin ${enabled ? 'checked' : ''} ${canChange ? '' : 'disabled'} onchange="InvoicesPage.setLogoOption(this)">
                    Show company logo on invoices
                </label>
                <div class="invoice-logo-help">${canChange
                    ? 'Applies to all invoices: PDF, Print, and emailed attachments. Changes save immediately.'
                    : 'Applies to all invoices. Only an administrator can change this, in Settings.'}</div>
                <div class="invoice-logo-status" role="status" aria-live="polite"></div>
            </div>
        </div>`;
    },

    async setLogoOption(input) {
        const enabled = input.checked;
        const option = input.closest('.invoice-logo-option');
        const preview = option.querySelector('.invoice-logo-preview');
        const status = option.querySelector('.invoice-logo-status');
        // Keep document actions from using the previous preference while saving.
        const actions = Array.from(input.closest('#modal-body').querySelectorAll('[data-invoice-logo-action]'))
            .map(button => ({ button, disabled: button.disabled }));
        input.disabled = true;
        actions.forEach(({ button }) => { button.disabled = true; });
        status.classList.remove('invoice-logo-error');
        status.textContent = 'Saving logo option…';
        try {
            await API.put('/settings', { invoice_show_logo: String(enabled) });
            App.settings.invoice_show_logo = String(enabled);
            preview.hidden = !enabled;
            status.textContent = 'Saved for all invoices.';
        } catch (err) {
            input.checked = !enabled;
            status.classList.add('invoice-logo-error');
            status.textContent = `Could not save logo option: ${err.message}`;
        } finally {
            input.disabled = false;
            actions.forEach(({ button, disabled }) => { button.disabled = disabled; });
        }
    },

    async void(id) {
        if (!confirm('Void this invoice? This cannot be undone.')) return;
        try {
            await API.post(`/invoices/${id}/void`);
            toast(`${T('Invoice')} voided`);
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async markSent(id) {
        try {
            await API.post(`/invoices/${id}/send`);
            toast(`${T('Invoice')} marked as sent`);
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async duplicate(id) {
        try {
            const inv = await SalesLines.sendAllowingZero(flags =>
                API.post(`/invoices/${id}/duplicate`, Object.keys(flags).length ? flags : undefined));
            if (!inv) return;
            toast(`Duplicated as ${T('Invoice')} #${inv.invoice_number}`);
            // another customer's job stored on the original was left off
            if (inv.jobs_left_off && inv.jobs_left_off.length) toast(JobPicker.leftOffNote(inv.jobs_left_off), 'info');
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    async copyPaymentLink(id) {
        let data;
        try {
            data = await API.get(`/payments/payment-link/${id}`);
        } catch (err) { toast(err.message, 'error'); return; }
        // Fetching the link and copying it fail for unrelated reasons, and
        // reporting a clipboard refusal as an API error sent people looking
        // in the wrong place.
        return copyToClipboard(data.url, 'Payment link');
    },

    // Desktop-mode fallback: webhooks can't reach 127.0.0.1, so poll the
    // provider for the invoice's last checkout and record it if captured.
    async checkPaymentStatus(id, provider) {
        try {
            const data = await API.post(`/payments/${provider || 'stripe'}/check-status/${id}`);
            if (data.status === 'payment_recorded') {
                toastPosted('Payment received and recorded');
                closeModal();
                App.navigate('#/invoices');
            } else if (data.status === 'already_processed') {
                toast('Payment was already recorded');
            } else if (data.status === 'no_checkout') {
                toast('No checkout has been started for this invoice');
            } else {
                toast(`Not paid yet (provider status: ${data.provider_status || 'unknown'})`);
            }
        } catch (err) { toast(err.message, 'error'); }
    },

    async emailInvoice(id) {
        const inv = await API.get(`/invoices/${id}`);
        // The invoice has no address of its own; send to the customer's. It
        // read a field the invoice never had, so the box was always empty (W-L3).
        let email = '';
        try { email = (await API.get(`/customers/${inv.customer_id}`)).email || ''; }
        catch (e) { /* the user types it */ }
        openModal(Terms.text('Email Invoice'), `
            <form onsubmit="InvoicesPage.sendEmail(event, ${id})">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Recipient Email *</label>
                        <input name="recipient" type="email" required value="${escapeHtml(email)}"></div>
                    <div class="form-group"><label>CC <span class="form-hint">Optional. Separate addresses with commas.</span></label>
                        <input name="cc" placeholder="office@example.com"></div>
                    <div class="form-group"><label>BCC <span class="form-hint">Optional. Hidden from the recipient.</span></label>
                        <input name="bcc" placeholder="books@example.com"></div>
                    <div class="form-group full-width"><label>Subject</label>
                        <input name="subject" placeholder="Loading from your template…"></div>
                    <div class="form-group full-width"><label>Message <span class="form-hint">Appears at the top of the email. Leave it blank to send your template as it is.</span></label>
                        <textarea name="message" oninput="InvoicesPage.queueEmailPreview(${id})"></textarea></div>
                </div>
                <details style="margin-top:4px;" open>
                    <summary style="cursor:pointer; font-size:12px; font-weight:600;">Preview — this is what will be sent</summary>
                    <div id="email-preview" style="border:1px solid var(--border); border-radius:4px; padding:10px; margin-top:6px; max-height:260px; overflow:auto; background:#fff; color:#333;">
                        <em>Loading…</em>
                    </div>
                </details>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Send Email</button>
                </div>
            </form>`);
        InvoicesPage.refreshEmailPreview(id, true);
    },

    // The preview is rendered by the SAME server code that sends, so what an
    // operator reads here is what the customer receives. Before #140 the
    // dialog showed a hardcoded subject and the saved template was never
    // loaded at all — so there was nothing to preview and no way to tell.
    _previewTimer: null,

    queueEmailPreview(id) {
        clearTimeout(InvoicesPage._previewTimer);
        InvoicesPage._previewTimer = setTimeout(() => InvoicesPage.refreshEmailPreview(id), 350);
    },

    async refreshEmailPreview(id, fillSubject = false) {
        const form = $('#modal-body form');
        if (!form) return;
        const target = $('#email-preview');
        try {
            const out = await API.post(`/invoices/${id}/email-preview`, {
                recipient: form.recipient.value || null,
                subject: fillSubject ? null : (form.subject.value || null),
                message: form.message.value || null,
            });
            if (fillSubject && !form.subject.value) form.subject.value = out.subject;
            if (target) target.innerHTML = out.html_body;
        } catch (err) {
            if (target) target.textContent = `Preview unavailable: ${err.message}`;
        }
    },

    async sendEmail(e, id) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post(`/invoices/${id}/email`, {
                recipient: form.recipient.value,
                cc: form.cc.value.trim() || null,
                bcc: form.bcc.value.trim() || null,
                subject: form.subject.value,
                message: form.message.value,
            });
            toast(`${T('Invoice')} emailed`);
            closeModal();
        } catch (err) {
            // An administrator is offered the Recent sends section, where the
            // failure and its reason are listed (#447). Anyone else cannot
            // open it; the server's message says an administrator can see
            // why, so the toast offers no link.
            if (App.isAdmin()) {
                toastAction(err.message, 'Recent sends', () => {
                    try { sessionStorage.setItem('settings_focus', 'settings-h-email-recent-sends'); } catch (e) {}
                    App.navigate('#/settings');
                }, 8000, null, { type: 'error' });
            } else {
                toast(err.message, 'error');
            }
        }
    },

    lineCount: 0,
    _customers: [],
    _messages: [],

    // A saved message copies its text into Notes, where it can still be
    // edited; the invoice keeps the text, not the choice (#392). Picking a
    // message replaces Notes when they are empty or still hold a saved
    // message's text as it is; when they hold something typed by hand the
    // chosen text is appended on a new line, so nothing is thrown away.
    // "(none)" clears Notes only when they hold a saved message's text.
    messageChosen(id) {
        const notes = $('#invoice-form [name="notes"]');
        if (!notes) return;
        const current = notes.value.trim();
        const isSaved = current === '' || InvoicesPage._messages.some(m => m.body.trim() === current);
        const chosen = InvoicesPage._messages.find(m => String(m.id) === String(id));
        if (chosen) {
            notes.value = isSaved ? chosen.body : `${notes.value.replace(/\s+$/, '')}\n${chosen.body}`;
            return;
        }
        if (isSaved && current !== '') notes.value = '';
    },

    async showForm(id = null, prefillCustomerId = null) {
        const [customers, items, settings, messages] = await Promise.all([
            API.get('/customers?active_only=true&tree=1'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
            // the saved messages (#392); the form works without the list
            API.get('/invoice-messages').catch(() => []),
            PaymentTerms.load(),
        ]);
        InvoicesPage._messages = messages;
        const defaultMessage = messages.find(m => m.is_default);

        let inv = {
            customer_id: prefillCustomerId || '',
            date: todayISO(),
            terms: settings.default_terms || 'Net 30',
            po_number: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            // a new invoice starts with the default message; with none, the
            // Default Invoice Notes setting as before
            notes: defaultMessage ? defaultMessage.body : (settings.invoice_notes || ''),
            lines: [],
        };
        if (id) inv = await API.get(`/invoices/${id}`);
        // the invoice being edited, whose own terms answer by its own rule
        InvoicesPage._formInvoiceId = id || null;
        // A tax amount with no rate behind it (QuickBooks Online's, when its
        // tax lines don't make one rate) stays as it is when the invoice is
        // saved, as the server keeps it, until a rate is entered.
        InvoicesPage._keptTax = id && !(parseFloat(inv.tax_rate) > 0) && parseFloat(inv.tax_amount) > 0
            ? parseFloat(inv.tax_amount) : null;
        // what the invoice owed before this edit, for the credit-limit check
        InvoicesPage._editing = id ? { total: parseFloat(inv.total) || 0, paid: parseFloat(inv.amount_paid) || 0 } : null;
        const classGroup = await classFormGroupHtml(inv.class_id);
        const jobGroup = await jobFormGroupHtml(inv.job_id, 'inv-customer-select');
        if (InvoicesPage.lineClasses()) await Nonprofit.loadFunds();
        // Nonprofit: a pledge prints as one; program fees and rentals stay invoices
        const pledgeGroup = Terms.isNonprofit() ? `<div class="form-group"><label>Document</label><label style="font-weight:normal;"><input type="checkbox" name="is_pledge" ${(id ? inv.is_pledge : true) ? 'checked' : ''}> This is a pledge (prints as PLEDGE)</label></div>` : '';
        if (inv.lines.length === 0) inv.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];

        InvoicesPage.lineCount = inv.lines.length;
        InvoicesPage._items = items;
        InvoicesPage._customers = customers;

        const custOpts = customers.map(c => `<option value="${c.id}" ${inv.customer_id==c.id?'selected':''}>${escapeHtml(customerLabel(c))}</option>`).join('');

        openModal(Terms.text(id ? 'Edit Invoice' : 'New Invoice'), `
            <form id="invoice-form" aria-labelledby="modal-title" data-skip-to="#inv-lines" data-skip-text="Skip to the line items" onsubmit="InvoicesPage.save(event, ${id})">
                ${InvoicesPage.logoOptionHtml(settings)}
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="inv-customer-select" required onchange="InvoicesPage.customerSelected(this.value)"><option value="">Select...</option><option value="__new__">+ ${T('New Customer')}</option>${custOpts}</select>
                        <div id="inv-new-customer-form" style="display:none; margin-top:8px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light);">
                            <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add ${T('Customer')}</div>
                            <input id="inv-new-cust-name" placeholder="Name *" aria-label="${T('Customer')} name" aria-required="true" style="width:100%; margin-bottom:4px;">
                            <input id="inv-new-cust-email" placeholder="Email" style="width:100%; margin-bottom:4px;">
                            <input id="inv-new-cust-phone" placeholder="Phone" style="width:100%; margin-bottom:4px;">
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-sm btn-primary" onclick="InvoicesPage.saveNewCustomer()">Save</button>
                                <button type="button" class="btn btn-sm btn-secondary" onclick="InvoicesPage.cancelNewCustomer()">Cancel</button>
                            </div>
                        </div></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${inv.date}"
                            onchange="InvoicesPage._recomputeDueDate()"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="invoice-terms"
                            onchange="InvoicesPage._recomputeDueDate()">
                            ${PaymentTerms.optionsHtml(inv.terms)}
                        </select>
                        <div class="hint" id="invoice-terms-hint">${PaymentTerms.hintHtml({ note: inv.terms_note, discount_line: inv.discount_line })}</div></div>
                    <div class="form-group"><label>Due Date</label>
                        <input name="due_date" type="date" value="${inv.due_date || ''}"
                            title="Auto-calculated from Date + Terms. Edit to override."></div>
                    <div class="form-group"><label>PO #</label>
                        <input name="po_number" value="${escapeHtml(inv.po_number || '')}"></div>
                    ${classGroup}${jobGroup}${pledgeGroup}
                    ${currencyFormGroupsHtml(inv.currency, inv.exchange_rate)}
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" value="${+((inv.tax_rate || 0) * 100).toFixed(4)}"
                            oninput="InvoicesPage.recalc()">
                        ${InvoicesPage._keptTax != null ? `<div class="hint" id="inv-kept-tax">Tax stays at ${formatCurrency(InvoicesPage._keptTax)}, the amount it came in with. Enter a rate to work it out instead, or untick Tax on the lines for none.</div>` : ''}</div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr>
                        <th scope="col">Item</th><th scope="col">Description</th>${InvoicesPage.lineClasses() ? Nonprofit.classHeadHtml() : ''}<th scope="col" class="col-qty">Qty</th>
                        <th scope="col" class="col-rate">Rate</th><th scope="col" title="Sales tax applies to this line">Tax</th><th scope="col" class="col-amount">Amount</th><th scope="col" class="col-actions"></th>
                    </tr></thead>
                    <tbody id="inv-lines">
                        ${inv.lines.map((l, i) => InvoicesPage.lineRowHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="InvoicesPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="inv-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="inv-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="inv-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="inv-total">$0.00</span></div>
                </div>
                ${messages.length ? `<div class="form-group" style="margin-top:12px;"><label>Message</label>
                    <select name="message_id" id="inv-message-select" onchange="InvoicesPage.messageChosen(this.value)">
                        <option value="">(none)</option>
                        ${messages.map(m => `<option value="${m.id}" ${(inv.notes || '').trim() === m.body.trim() ? 'selected' : ''}>${escapeHtml(m.name)}</option>`).join('')}
                    </select></div>` : ''}
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(inv.notes || '')}</textarea></div>
                ${id ? '' : `<div class="hint" id="inv-create-note">Creating this ${T('invoice')} posts it to the books now; Draft means not yet marked sent. Items tracked in inventory come out of stock when it is created.</div>`}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary" data-invoice-logo-action${id ? '' : ' aria-describedby="inv-create-note"'}>${id ? 'Update' : 'Create'} ${T('Invoice')}</button>
                </div>
            </form>`);
        if (!id && inv.customer_id) InvoicesPage.customerSelected(inv.customer_id);
        // the totals carry the document's currency; follow a change of it
        $('#invoice-form [name="currency"]')?.addEventListener('change', () => InvoicesPage.recalc());
        typeof LineOrder !== 'undefined' && LineOrder.attach('#inv-lines', { add: () => InvoicesPage.addLine(), changed: () => InvoicesPage.recalc() });
        InvoicesPage.recalc();
        // Populate due_date for fresh invoices that don't already have one.
        if (!inv.due_date) InvoicesPage._recomputeDueDate();
        // A pay application's invoice is changed on the application, not here:
        // only the notes are editable (the server refuses the rest, #576).
        InvoicesPage._payAppInvoice = !!inv.pay_application_id;
        if (InvoicesPage._payAppInvoice) {
            $$('#invoice-form input, #invoice-form select, #invoice-form textarea, #invoice-form button[type="button"]').forEach(el => {
                if (el.name !== 'notes' && !/Cancel/.test(el.textContent || '')) el.disabled = true;
            });
            $('#invoice-form .form-actions').insertAdjacentHTML('beforebegin',
                '<div class="form-hint">This invoice comes from a payment application. Change the application to change the amounts; only the notes can be edited here.</div>');
        }
    },

    customerSelected(customerId) {
        setTimeout(() => InvoicesPage.recalc(), 0);
        if (customerId === '__new__') {
            const form = $('#inv-new-customer-form');
            if (form) form.style.display = 'block';
            return;
        }
        const ncf = $('#inv-new-customer-form');
        if (ncf) ncf.style.display = 'none';
        const customer = InvoicesPage._customers.find(c => c.id == customerId);
        const termsField = $('#invoice-terms');
        if (customer && termsField && customer.terms) {
            PaymentTerms.choose(termsField, customer.terms);
            // Setting .value programmatically does NOT fire 'change', so the
            // due-date wouldn't recompute on its own — leaving the form with
            // (e.g.) Net 60 terms but a Net 30 due date. Recompute explicitly.
            InvoicesPage._recomputeDueDate();
        }
    },

    async saveNewCustomer() {
        const name = $('#inv-new-cust-name').value.trim();
        if (!name) { toast(`${T('Customer')} name is required`, 'error'); return; }
        try {
            const cust = await API.post('/customers', {
                name, email: $('#inv-new-cust-email').value.trim() || null,
                phone: $('#inv-new-cust-phone').value.trim() || null,
            });
            InvoicesPage._customers.push(cust);
            const sel = $('#inv-customer-select');
            const opt = document.createElement('option');
            opt.value = cust.id; opt.textContent = cust.name; opt.selected = true;
            sel.appendChild(opt);
            $('#inv-new-customer-form').style.display = 'none';
            toast(`${T('Customer')} "${cust.name}" created`);
        } catch (err) { toast(err.message, 'error'); }
    },

    cancelNewCustomer() {
        $('#inv-new-customer-form').style.display = 'none';
        $('#inv-customer-select').value = '';
    },

    // The line Class cell is the shared helper in utils.js (#243); the form
    // still renders where that helper is not loaded (the node probes build a
    // bare page from invoices.js alone), as it did before the cell existed.
    lineClasses() { return typeof Nonprofit !== 'undefined' && !!Nonprofit.classCellHtml; },
    // A line's class for the payload: the cell's choice where one is drawn,
    // else the class the line already carries (data-class-id), so an edit
    // where no cell is drawn keeps a stored class.
    lineClassFromRow(row) {
        if (row.querySelector('.line-class-fund')) return Nonprofit.fundFromRow(row, 'line-class');
        return row.dataset.classId ? parseInt(row.dataset.classId) : null;
    },

    // A line with a negative price is a discount a QuickBooks Online invoice
    // came in with (a discount item, or a negative line of its own): its
    // price box takes a negative number, so the invoice saves as it is.
    lineRowHtml(idx, line, items) {
        // The form has no job / cost-code cells, but a line may carry them
        // (job costing, the API). Keep them on the row so an edit sends them
        // back instead of stripping them from the line and its posting. The
        // class has a cell of its own (#243); the data attribute still holds
        // a stored class where no cell is drawn.
        const dim = (v) => (v == null ? '' : escapeHtml(String(v)));
        return `<tr data-line="${idx}" data-job-id="${dim(line.job_id)}" data-class-id="${dim(line.class_id)}" data-cost-code-id="${dim(line.cost_code_id)}">
            <td><select class="line-item" onchange="InvoicesPage.itemSelected(${idx})">
                ${ItemQuickAdd.options(items, line.item_id)}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            ${InvoicesPage.lineClasses() ? Nonprofit.classCellHtml('line-class', line.class_id) : ''}
            <td><input class="line-qty" type="number" step="0.0001" value="${line.quantity || 1}" oninput="InvoicesPage.recalc()">${SalesLines.onHandHtml(line.item_id, items)}</td>
            <td><input class="line-rate money" type="number" step="0.0001" ${Number(line.rate) < 0 || items.some(i => i.is_discount && i.id == line.item_id) ? '' : 'min="0" '}value="${Number(line.rate) || 0}" oninput="InvoicesPage.recalc()"></td>
            <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${TaxDefault.checked(line)} onchange="InvoicesPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="InvoicesPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const tbody = $('#inv-lines');
        const idx = InvoicesPage.lineCount++;
        tbody.insertAdjacentHTML('beforeend', InvoicesPage.lineRowHtml(idx, {}, InvoicesPage._items));
        InvoicesPage.recalc();
    },

    removeLine(idx) {
        const row = $(`[data-line="${idx}"]`);
        if (row) row.remove();
        InvoicesPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-line="${idx}"]`);
        // "+ Add item" opens the quick add; its save picks the item here (#445)
        if (row && ItemQuickAdd.selected(row.querySelector('.line-item'), InvoicesPage._items, () => InvoicesPage.itemSelected(idx))) return;
        if (row) SalesLines.showOnHand(row, InvoicesPage._items);
        if (row && SalesLines.fillFromItem(row, InvoicesPage._items)) InvoicesPage.recalc();
    },

    recalc() {
        TaxExempt.enforce(InvoicesPage._customers, $('#inv-customer-select')?.value, $('#inv-lines'));
        const cur = $('#invoice-form [name="currency"]')?.value;
        const rate = $('#invoice-form [name="tax_rate"]')?.value;
        const t = SalesLines.totals($('#inv-lines'), rate, cur);
        const kept = InvoicesPage._keptTax;
        const taxable = $$('#inv-lines tr').some(row => row.querySelector('.line-taxable')?.checked !== false);
        const keeping = kept != null && !(parseFloat(rate) > 0) && taxable;
        if (keeping) {
            t.tax = kept;
            t.total = SalesLines.cents(t.subtotal + kept);
        }
        const hint = document.getElementById('inv-kept-tax');
        if (hint) hint.style.display = keeping ? '' : 'none';
        SalesLines.show(t, ['inv-subtotal', 'inv-tax', 'inv-total'], cur);
        return t;
    },

    // Auto-fill due_date from date + terms when either changes, by the
    // server's one terms parser (#664): the terms list's rule, the discount
    // the terms offer, or a sentence when the terms aren't understood. The
    // save makes the same date when due_date arrives null.
    async _recomputeDueDate() {
        // Scope to the invoice form — a backced report page or another modal
        // could also have a [name="date"] input, and a bare document-level
        // query would grab whichever appears first in the DOM.
        const form = $('#invoice-form');
        if (!form) return;
        const dateEl = form.querySelector('[name="date"]');
        const termsEl = form.querySelector('[name="terms"]');
        const dueDateEl = form.querySelector('[name="due_date"]');
        if (!dateEl || !termsEl || !dueDateEl || !dateEl.value) return;
        const d = await PaymentTerms.dates(dateEl.value, termsEl.value, 'invoice', InvoicesPage._formInvoiceId);
        if (!d) return;
        dueDateEl.value = d.due_date;
        const hint = $('#invoice-terms-hint');
        if (hint) hint.innerHTML = PaymentTerms.hintHtml(d);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        if (id && InvoicesPage._payAppInvoice) {
            try {
                // Notes only: nothing reposts, so no closed-period question here.
                await API.put('/invoices/' + id, { notes: form.notes.value || null });
                toastPosted(Terms.text('Invoice updated'));
                App.refresh();
            } catch (err) { toast(err.message, 'error'); }
            return;
        }
        const lines = [];
        $$('#inv-lines tr').forEach((row, i) => {
            lines.push({
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                is_taxable: row.querySelector('.line-taxable') ? row.querySelector('.line-taxable').checked : null,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                // a stored line job of another customer is dropped, so the
                // save clears it (the server refuses one it is asked to keep)
                job_id: JobPicker.ownJob(form, row.dataset.jobId ? parseInt(row.dataset.jobId) : null),
                class_id: InvoicesPage.lineClassFromRow(row),
                cost_code_id: row.dataset.costCodeId ? parseInt(row.dataset.costCodeId) : null,
                line_order: i,
            });
        });

        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            due_date: form.due_date.value || null,
            terms: form.terms.value,
            po_number: form.po_number.value || null,
            is_pledge: form.is_pledge ? form.is_pledge.checked : false,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            ...currencyPayloadFromForm(form),
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };

        if (!(await TxnWarn.ok(form, { editing: !!id }))) return;
        if (!(await ClassWarn.ok(form, { rows: '#inv-lines tr', cls: 'line-class' }))) return;
        // Past the customer's credit limit? Say so, and let the user decide.
        const total = InvoicesPage.recalc().total;
        const before = InvoicesPage._editing;
        if (!before || total > before.total) {
            const customer = InvoicesPage._customers.find(c => c.id == data.customer_id);
            const owed = (total - (before ? before.paid : 0)) * (data.exchange_rate || 1);
            if (!(await InvoicesPage.creditLimitOk(customer, owed, id))) return;
        }

        try {
            const saved = await SalesLines.sendAllowingZero(flags => {
                const body = { ...data, ...flags };
                return id ? API.put(`/invoices/${id}`, body) : API.post('/invoices', body);
            }, InvoicesPage._overLimitOk ? { allow_over_credit_limit: true } : {});
            if (!saved) return; // $0.00 and the user said no: the form stays open
            toastPosted(Terms.text(id ? 'Invoice updated' : 'Invoice created'));
            // a stock item sold past what is on hand (#642): posted, and said
            for (const w of (saved && saved.warnings) || []) toast(w, 'warning');
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    // The customer's credit limit, as a warning the user can pass: an invoice
    // that takes them past it asks first. A $512.13 invoice went to a
    // customer with a $500 limit without a word (2.17.3 exploratory, S-b).
    // What they owe is their open invoices' balances in home dollars, the
    // invoice being edited left out (its new amount is `owed`). Returns true
    // to go ahead; a failure to look is not a reason to stop.
    async creditLimitOk(customer, owed, editingId = null) {
        // set when the user said "save anyway", so the save carries
        // allow_over_credit_limit and the server (which asks too, #256)
        // doesn't ask twice
        InvoicesPage._overLimitOk = false;
        const limit = parseFloat(customer && customer.credit_limit);
        if (!customer || !(limit > 0)) return true;
        let open = 0;
        try {
            const invoices = await fetchAllPages(`/invoices?customer_id=${encodeURIComponent(customer.id)}&open_only=true`);
            invoices.forEach(i => {
                if (i.status === 'void' || (editingId && String(i.id) === String(editingId))) return;
                open += (parseFloat(i.balance_due) || 0) * (parseFloat(i.exchange_rate) || 1);
            });
        } catch (e) { return true; }
        const after = SalesLines.cents(open + (Number(owed) || 0));
        if (after <= limit) return true;
        InvoicesPage._overLimitOk = confirm(`${customer.name}'s credit limit is ${formatCurrency(limit)}. `
            + `With this ${T('invoice')} they would owe ${formatCurrency(after)}. Save it anyway?`);
        return InvoicesPage._overLimitOk;
    },

    async loadAttachments(entityType, entityId) {
        const el = $('#inv-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/${entityType}/${entityId}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="InvoicesPage.deleteAttachment(${a.id},'${entityType}',${entityId})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(entityId) {
        const fileInput = $('#inv-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/invoice/${entityId}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            InvoicesPage.loadAttachments('invoice', entityId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachId, entityType, entityId) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachId}`);
            toast('Attachment deleted');
            InvoicesPage.loadAttachments(entityType, entityId);
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Top-level const creates no window property — the topbar's
// data-action dispatch (bootstrap.js callByPath) needs this export.
window.InvoicesPage = InvoicesPage;
