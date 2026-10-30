/**
 * Receive Payments — applies a payment across a customer's open invoices.
 * Typing the amount fills the Apply column oldest invoice first, as
 * QuickBooks does; any of it can be changed. Money left over is kept as a
 * credit only when the user ticks the box that says so, and credits the
 * customer already holds (unapplied payments, credit memos) can be applied
 * to open invoices from here, from the payment and from the customer page.
 */
const PaymentsPage = {
    showAll() { PaymentsPage._showAll = true; App.navigate(location.hash); },

    async render() {
        const { rows: payments, note: capNote } = await listRows(PaymentsPage, '/payments', 'PaymentsPage.showAll()', 'payments');
        let html = `
            <div class="page-header">
                <h2>Payments</h2>
                <button class="btn btn-primary" ${App.shortcutAttrs('P', 'Record Payment')} onclick="PaymentsPage.showForm()">+ Record Payment</button>
            </div>` + capNote;

        if (payments.length === 0) {
            html += `<div class="empty-state">
                <p>No payments recorded yet.</p>
                <button class="btn btn-primary" onclick="PaymentsPage.showForm()" style="margin-top:10px;">+ Record your first payment</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">${T('Customer')}</th><th scope="col">Method</th><th scope="col">Reference</th>
                    <th scope="col" class="amount">Amount</th><th scope="col" class="amount">Not Applied</th><th scope="col">Actions</th>
                </tr></thead><tbody>`;
            for (const p of payments) {
                const unapplied = parseFloat(p.unapplied) || 0;
                html += `<tr>
                    <td>${formatDate(p.date)}</td>
                    <td>${escapeHtml(p.customer_name || '')}</td>
                    <td>${escapeHtml(p.method || '')}${p.is_voided ? ' <span style="color:var(--danger);font-weight:700;">[VOID]</span>' : ''}</td>
                    <td>${escapeHtml(p.reference || p.check_number || '')}</td>
                    <td class="amount">${formatCurrency(p.amount)}</td>
                    <td class="amount">${unapplied > 0 ? formatCurrency(unapplied) : ''}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PaymentsPage.view(${p.id})">View</button>
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async view(id) {
        const p = await API.get(`/payments/${id}`);
        // Nonprofit: a pledge payment or an unapplied gift gets a letter; a
        // receipt's own payment does not (the receipt is acknowledged).
        let ack = null;
        if (Terms.isNonprofit() && !p.is_voided) {
            try { ack = await API.get(`/donors/gifts/payment/${id}/acknowledgment/preview`); } catch (e) { ack = null; }
        }
        let allocHtml = '';
        if (p.allocations.length) {
            allocHtml = `<h4 style="margin:12px 0 8px;">Applied to ${T('Invoices')}</h4>
                <div class="table-container"><table><thead><tr>
                <th scope="col">${T('Invoice')}</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>`;
            for (const a of p.allocations) {
                allocHtml += `<tr><td>#${escapeHtml(a.invoice_number || String(a.invoice_id))}</td><td class="amount">${formatCurrency(a.amount)}</td></tr>`;
            }
            allocHtml += `</tbody></table></div>`;
        }
        // the early-payment discounts the customer took with it (#664)
        if ((p.discounts || []).length) {
            allocHtml += `<h4 style="margin:12px 0 8px;">Discounts taken</h4>
                <div class="table-container"><table><thead><tr>
                <th scope="col">${T('Invoice')}</th><th scope="col">Credit memo</th><th scope="col" class="amount">Discount</th></tr></thead><tbody>
                ${p.discounts.map(d => `<tr><td>#${escapeHtml(d.invoice_number || String(d.invoice_id))}</td><td>${escapeHtml(d.memo_number)}</td><td class="amount">${formatCurrency(d.amount)}</td></tr>`).join('')}
                </tbody></table></div>`;
        }
        const unapplied = p.is_voided ? 0 : (parseFloat(p.unapplied) || 0);

        // No "Print Check" here: this is money the customer paid US. A
        // check is printed for money going out (a bill payment).
        openModal('Payment Details', `
            <div style="margin-bottom:12px;">
                <strong>${T('Customer')}:</strong> ${escapeHtml(p.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(p.date)}<br>
                <strong>Amount:</strong> ${formatCurrency(p.amount)}<br>
                <strong>Method:</strong> ${escapeHtml(p.method || 'N/A')}<br>
                ${p.check_number ? `<strong>Check #:</strong> ${escapeHtml(p.check_number)}<br>` : ''}
                ${p.reference ? `<strong>Reference:</strong> ${escapeHtml(p.reference)}<br>` : ''}
                ${p.notes ? `<strong>Notes:</strong> ${escapeHtml(p.notes)}<br>` : ''}
                ${p.deposited_in ? `<strong>Deposited:</strong> in ${escapeHtml(p.deposited_in)}<br>` : ''}
            </div>
            ${allocHtml}
            ${unapplied > 0 ? `<div style="margin:12px 0; padding:8px 10px; border-radius:4px; background:#fff4d6; color:#7a5500;">
                ${formatCurrency(unapplied)} of this payment is not applied to any ${Terms.text('invoice')} yet; it is a credit for this ${Terms.text('customer')}.
                <button class="btn btn-sm btn-primary" style="margin-left:8px;" onclick="PaymentsPage.showApplyCredit('payment', ${p.id}, ${p.customer_id})">Apply to ${T('Invoices')}</button>
            </div>` : ''}
            ${p.is_voided ? '<div style="color:var(--danger);font-weight:700;margin:12px 0;">This payment has been voided.</div>' : ''}
            <div class="form-actions">
                ${ack && ack.eligible ? `<button class="btn btn-secondary" onclick="window.open('/api/donors/gifts/payment/${p.id}/acknowledgment/pdf','_blank')">Acknowledgment (PDF)</button>
                <button class="btn btn-secondary" onclick="Donors.emailAcknowledgment('payment', ${p.id})">Email Acknowledgment</button>` : ''}
                ${!p.is_voided ? `<button data-destructive class="btn btn-danger" onclick="PaymentsPage.void(${p.id}, ${p.has_provider_fee ? 'true' : 'false'})">Void Payment</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/payments/${Number(id)}`);
    },

    // An online payment with a booked processing fee asks one more thing:
    // whether the provider gave the fee back (#597). Other payments keep
    // the plain confirm.
    async void(id, hasFee = false) {
        if (hasFee) {
            openModal('Void Payment', `
                <p>Void this payment? ${T('Invoice')} balances will be restored.</p>
                <label style="display:flex;gap:8px;align-items:center;margin:12px 0;">
                    <input type="checkbox" id="void-fee-refunded"> The provider refunded its processing fee
                </label>
                <div class="form-actions">
                    <button data-destructive class="btn btn-danger" onclick="PaymentsPage.doVoid(${Number(id)})">Void Payment</button>
                    <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                </div>`);
            return;
        }
        if (!confirm(`Void this payment? ${T('Invoice')} balances will be restored.`)) return;
        await PaymentsPage.doVoid(id, false);
    },

    async doVoid(id, ask = true) {
        const box = ask ? document.getElementById('void-fee-refunded') : null;
        const query = box && box.checked ? '?fee_refunded=true' : '';
        try {
            const r = await API.post(`/payments/${id}/void${query}`);
            if (ask) closeModal();
            toast('Payment voided');
            // e.g. already paid out by its online provider (#584)
            if (r && r.warning) toast(r.warning, 'error');
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    _invoices: [],

    // Every invoice still owing — drafts included: a draft already posts to
    // A/R, so a customer can pay it — oldest first, the order money is
    // applied in.
    _openInvoices(invoices) {
        return invoices
            .filter(i => i.status !== 'void' && (parseFloat(i.balance_due) || 0) > 0)
            .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.id - b.id));
    },

    _cents(v) { return Math.round((parseFloat(v) || 0) * 100); },

    async showForm(_ignoredId = null, prefillCustomerId = null) {
        PaymentsPage._requestKey = null; // a new form, a new key (#292)
        const [customers, accounts] = await Promise.all([
            API.get('/customers?active_only=true&tree=1'),
            API.get('/accounts'),
        ]);
        const bankAccts = accounts.filter(a => a.bank_kind === 'bank');
        PaymentsPage._accounts = accounts;

        const custOpts = customers.map(c => `<option value="${c.id}"${prefillCustomerId === c.id ? ' selected' : ''}>${escapeHtml(customerLabel(c))}</option>`).join('');
        const bankOpts = bankAccts.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');

        openModal('Record Payment', `
            <form id="payment-form" onsubmit="PaymentsPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" required onchange="PaymentsPage.loadInvoices(this.value)">
                            <option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}" onchange="PaymentsPage._suggestDiscounts()"></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" class="money" type="number" step="0.01" min="0.01" required oninput="PaymentsPage._autoApply()"></div>
                    <div class="form-group"><label>Method</label>
                        <select name="method">
                            <option value="">--</option>
                            <option>Check</option><option>Cash</option>
                            <option>Credit Card</option><option>ACH/EFT</option><option>Other</option>
                        </select></div>
                    <div class="form-group"><label>Check #</label>
                        <input name="check_number"></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    <div class="form-group"><label>Deposit To</label>
                        <select name="deposit_to_account_id">
                            <option value="">--</option>${bankOpts}</select></div>
                    <div class="form-group full-width"><label>Notes</label>
                        <textarea name="notes"></textarea></div>
                </div>
                <div id="payment-invoices" style="margin-top:16px;"></div>
                <div id="keep-credit-row" hidden style="margin-top:8px; font-size:13px;">
                    <label style="font-weight:normal;"><input type="checkbox" id="keep-credit">
                        <span id="keep-credit-text"></span></label>
                </div>
                <div id="payment-credits" style="margin-top:12px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record Payment</button>
                </div>
            </form>`);
        // If we opened from the customer details flow, the customer is
        // already selected but the change event didn't fire — trigger the
        // invoice list manually so the operator sees the unpaid invoices.
        if (prefillCustomerId) PaymentsPage.loadInvoices(prefillCustomerId);
    },

    async loadInvoices(customerId) {
        if (!customerId) {
            $('#payment-invoices').innerHTML = '';
            $('#payment-credits').innerHTML = '';
            PaymentsPage._invoices = [];
            PaymentsPage._updateAllocStatus();
            return;
        }
        const [invoices, credits] = await Promise.all([
            fetchAllPages(`/invoices?customer_id=${customerId}&open_only=true&billed_to=true`),
            API.get(`/customers/${customerId}/credits`).catch(() => null),
        ]);
        // One payment is in one currency (the server refuses a mix), so the
        // customer's open invoices are offered one currency at a time. A
        // foreign-currency invoice could only be paid through the API: the
        // form sent no currency at all (found integrating 2.17.3's fixes).
        PaymentsPage._home = String(
            (credits && credits.home_currency)
            || (typeof App !== 'undefined' && App.settings && App.settings.home_currency)
            || 'USD').toUpperCase();
        const open = PaymentsPage._openInvoices(invoices);
        PaymentsPage._currencies = [...new Set(open.map(i => (i.currency || PaymentsPage._home).toUpperCase()))];
        if (PaymentsPage._currencyFor !== String(customerId)
            || !PaymentsPage._currencies.includes(PaymentsPage._currency)) {
            PaymentsPage._currencyFor = String(customerId);
            PaymentsPage._currency = PaymentsPage._currencies.includes(PaymentsPage._home)
                ? PaymentsPage._home : (PaymentsPage._currencies[0] || PaymentsPage._home);
        }
        PaymentsPage._invoices = open.filter(i => (i.currency || PaymentsPage._home) === PaymentsPage._currency);
        PaymentsPage._renderCredits(credits, customerId);

        if (PaymentsPage._invoices.length === 0) {
            $('#payment-invoices').innerHTML = PaymentsPage._currencyHtml() + `<p style="color:var(--gray-400);">${Terms.text('No outstanding invoices')}</p>`;
            PaymentsPage._updateAllocStatus();
            return;
        }

        let html = PaymentsPage._currencyHtml() + `<h4 style="margin-bottom:8px;">Apply to ${T('Invoices')}</h4>
            <div class="table-container"><table><thead><tr>
            <th scope="col">${T('Invoice')}</th><th scope="col">Date</th><th scope="col">Due</th><th scope="col" class="amount">Balance</th>
            <th scope="col" title="The last day the terms' early-payment discount can be taken">Disc. Date</th><th scope="col" class="amount">Discount</th><th scope="col" class="amount">Apply</th>
            </tr></thead><tbody>`;
        for (const inv of PaymentsPage._invoices) {
            html += `<tr>
                <td>#${escapeHtml(inv.invoice_number)}${inv.status === 'draft' ? ' <span style="color:var(--text-muted);">(draft)</span>' : ''}</td>
                <td>${formatDate(inv.date)}</td>
                <td>${formatDate(inv.due_date)}</td>
                <td class="amount">${PaymentsPage._fmt(inv.balance_due)}</td>
                <td>${inv.discount_date ? formatDate(inv.discount_date) : ''}</td>
                <td><input class="alloc-discount money" data-invoice="${inv.id}"
                    type="number" step="0.01" min="0" max="${inv.balance_due}"
                    aria-label="Discount on ${escapeHtml(inv.invoice_number)}"
                    oninput="PaymentsPage._discountChanged(this)"
                    style="width:80px;"></td>
                <td><input class="alloc-amount money" data-invoice="${inv.id}" data-max="${inv.balance_due}"
                    type="number" step="0.01" min="0" max="${inv.balance_due}"
                    aria-label="Apply to ${escapeHtml(inv.invoice_number)}"
                    oninput="PaymentsPage._updateAllocStatus()"
                    style="width:100px;"></td>
            </tr>`;
        }
        // Running total — updated live as the user types into any allocation
        // field. Catches over-allocation BEFORE the submit fires.
        html += `</tbody></table></div>
            <div style="margin-top:6px;"><button type="button" class="btn btn-sm btn-secondary" onclick="PaymentsPage._autoApply()">Apply oldest first</button></div>
            <div class="form-group" id="discount-account-row" hidden style="margin-top:8px; max-width:360px;">
                <label for="discount-account">Discount account</label>
                <select id="discount-account" data-no-search>${PaymentTerms.discountAccountOptions(PaymentsPage._accounts || [], 'sales')}</select>
                <div class="hint">Where the discounts the customer took post, as a credit memo applied to each ${T('invoice')}.</div></div>
            <div id="alloc-status" style="margin-top:8px; padding:8px 10px; border-radius:4px;
                background:var(--gray-100); font-size:13px;"></div>`;
        $('#payment-invoices').innerHTML = html;
        PaymentsPage._suggestDiscounts();
        PaymentsPage._prefillRate();
    },

    // Money in the currency the payment is being taken in.
    _fmt(v) {
        return typeof SalesLines !== 'undefined'
            ? SalesLines.money(v, PaymentsPage._currency) : formatCurrency(v);
    },

    _isForeign() {
        return !!PaymentsPage._currency && PaymentsPage._currency !== PaymentsPage._home;
    },

    // Currency picker (only when the customer has invoices in more than one
    // currency, or in a foreign one) and, for a foreign currency, the rate on
    // the payment date.
    _currencyHtml() {
        const list = PaymentsPage._currencies || [];
        if (list.length < 2 && !PaymentsPage._isForeign()) return '';
        const opts = list.map(c => `<option ${c === PaymentsPage._currency ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('');
        const cur = escapeHtml(PaymentsPage._currency);
        return `<div class="form-grid" style="margin-bottom:8px;">
                <div class="form-group"><label>Currency</label>
                    <select name="pay_currency" onchange="PaymentsPage.setCurrency(this.value)">${opts}</select></div>
                ${PaymentsPage._isForeign() ? `<div class="form-group"><label>Exchange rate on the payment date (${escapeHtml(PaymentsPage._home)} per ${cur})</label>
                    <input name="pay_exchange_rate" type="number" step="0.00000001" min="0" required></div>` : ''}
            </div>
            ${PaymentsPage._isForeign() ? `<p style="font-size:12px; color:var(--text-muted); margin:0 0 8px;">Enter the amount received in ${cur}. The rate converts it for the books; a difference from the invoices' own rates posts as an exchange gain or loss.</p>` : ''}`;
    },

    setCurrency(code) {
        PaymentsPage._currency = String(code || '').toUpperCase();
        const customerId = $('#payment-form [name="customer_id"]')?.value;
        if (customerId) PaymentsPage.loadInvoices(customerId);
    },

    // The rate for the payment date: the exchange-rate feed, else the most
    // recent invoice's booked rate. The person can always change it.
    async _prefillRate() {
        const input = $('#payment-form [name="pay_exchange_rate"]');
        if (!input || input.value) return;
        let rate = null;
        try {
            const d = await API.get(`/fx/rate?from_currency=${encodeURIComponent(PaymentsPage._currency)}`);
            rate = d && d.rate;
        } catch (e) { /* offline: fall back to the invoice's rate */ }
        if (!rate) {
            const last = PaymentsPage._invoices[PaymentsPage._invoices.length - 1];
            rate = last && last.exchange_rate;
        }
        if (rate && !input.value) input.value = rate;
    },

    // Fill the Apply column oldest invoice first with the amount received
    // (QuickBooks' behaviour). The user can change any of it afterwards;
    // changing the amount fills it again.
    _autoApply() {
        const host = $('#payment-invoices');
        const inputs = host ? [...host.querySelectorAll('.alloc-amount')] : [];
        let left = PaymentsPage._cents($('[name="amount"]')?.value);
        for (const input of inputs) {
            // what is left to pay once the discount on it is taken (#664):
            // the one the person typed, else the one the terms suggest
            const disc = $(`#payment-invoices .alloc-discount[data-invoice="${input.dataset.invoice}"]`);
            const planned = disc ? PaymentsPage._cents(disc.dataset.edited ? disc.value : disc.dataset.suggested) : 0;
            const owed = PaymentsPage._cents(input.dataset.max) - planned;
            const take = Math.max(0, Math.min(left, owed));
            input.value = take > 0 ? (take / 100).toFixed(2) : '';
            // a discount comes with a payment: none on a row this one doesn't reach
            if (disc) disc.value = take > 0 && planned > 0 ? (planned / 100).toFixed(2) : '';
            left -= take;
        }
        const row = $('#discount-account-row');
        if (row) row.hidden = !PaymentsPage._anyDiscount();
        PaymentsPage._updateAllocStatus();
    },

    // The early-payment discount on each open invoice (#664): its terms'
    // percent of the balance while the payment date is on or before the
    // discount date, else none. The person can change any of them.
    _suggestDiscounts() {
        const host = $('#payment-invoices');
        if (!host) return;
        const payDate = $('#payment-form [name="date"]')?.value;
        for (const input of host.querySelectorAll('.alloc-discount')) {
            const inv = (PaymentsPage._invoices || []).find(i => String(i.id) === input.dataset.invoice);
            const cents = inv ? PaymentTerms.suggestedCents(inv, payDate) : 0;
            input.dataset.suggested = cents > 0 ? (cents / 100).toFixed(2) : '';
            delete input.dataset.edited;
        }
        PaymentsPage._autoApply();
    },

    _discountCents(invoiceId) {
        const input = $(`#payment-invoices .alloc-discount[data-invoice="${invoiceId}"]`);
        return input ? PaymentsPage._cents(input.value) : 0;
    },

    _anyDiscount() {
        return [...$$('#payment-invoices .alloc-discount')].some(i => PaymentsPage._cents(i.value) > 0);
    },

    // The discount account shows while a discount is taken. A discount the
    // person typed stays what they typed when Apply is filled again.
    _discountChanged(input) {
        if (input && input.dataset) input.dataset.edited = '1';
        const row = $('#discount-account-row');
        if (row) row.hidden = !PaymentsPage._anyDiscount();
        PaymentsPage._updateAllocStatus();
    },

    _remainingCents() {
        const total = PaymentsPage._cents($('[name="amount"]')?.value);
        let allocated = 0;
        // Scope to the invoice table so we never pick up a stray .alloc-amount
        // from another view that happens to be in the DOM.
        const host = $('#payment-invoices');
        (host ? host.querySelectorAll('.alloc-amount') : []).forEach(input => {
            allocated += PaymentsPage._cents(input.value);
        });
        return { total, allocated, remaining: total - allocated };
    },

    // The first Apply amount over its invoice's balance, if any.
    _overBalance() {
        const host = $('#payment-invoices');
        for (const input of (host ? host.querySelectorAll('.alloc-amount') : [])) {
            const max = PaymentsPage._cents(input.dataset.max);
            const discount = PaymentsPage._discountCents(input.dataset.invoice);
            if (PaymentsPage._cents(input.value) + discount > max) {
                const inv = (PaymentsPage._invoices || [])
                    .find(i => String(i.id) === String(input.dataset.invoice));
                return { number: inv && inv.invoice_number, max, discount };
            }
        }
        return null;
    },

    _updateAllocStatus() {
        const { total, allocated, remaining } = PaymentsPage._remainingCents();
        // Money left over is kept as a customer credit only when the user
        // says so: the box appears with the amount and starts unticked.
        const keepRow = $('#keep-credit-row');
        if (keepRow) {
            const leftOver = total > 0 && remaining > 0;
            keepRow.hidden = !leftOver;
            if (leftOver) {
                const none = PaymentsPage._invoices.length === 0;
                $('#keep-credit-text').textContent = none
                    ? Terms.text(`Keep the whole ${PaymentsPage._fmt(remaining / 100)} as a credit on this customer's account (there are no open invoices); it can be applied to an invoice later.`)
                    : Terms.text(`Keep the ${PaymentsPage._fmt(remaining / 100)} not applied as a credit on this customer's account; it can be applied to an invoice later.`);
            } else {
                const box = $('#keep-credit');
                if (box) box.checked = false;
            }
        }
        const status = $('#alloc-status');
        if (!status) return;
        const over = PaymentsPage._overBalance();
        if (over) {
            // The field's max blocks the save, so nothing may read as done.
            status.style.background = '#fde2e2';
            status.style.color = '#a4242b';
            status.textContent = Terms.text(over.discount
                ? `The Apply amount and discount for ${over.number ? `invoice #${over.number}` : 'an invoice'} are more than ` +
                  `its balance of ${PaymentsPage._fmt(over.max / 100)}. Lower one of them.`
                : `The Apply amount for ${over.number ? `invoice #${over.number}` : 'an invoice'} is more than ` +
                  `its balance of ${PaymentsPage._fmt(over.max / 100)}. Apply at most the balance; ` +
                  `money left over can be kept as a credit.`);
        } else if (total === 0 && allocated > 0) {
            // Amount cleared (or never entered) but money is allocated — this
            // would submit a NaN/zero payment with real allocations. Warn.
            status.style.background = '#fde2e2';
            status.style.color = '#a4242b';
            status.textContent =
                `${PaymentsPage._fmt(allocated / 100)} allocated but the Payment amount is empty. ` +
                `Enter the amount you received above.`;
        } else if (total === 0) {
            status.style.background = 'var(--gray-100)';
            status.style.color = 'var(--gray-600)';
            status.textContent = 'Enter a payment amount above to begin allocating.';
        } else if (remaining > 0) {
            status.style.background = '#fff4d6';
            status.style.color = '#7a5500';
            status.textContent =
                `${PaymentsPage._fmt(remaining / 100)} not applied of ${PaymentsPage._fmt(total / 100)}.`;
        } else if (remaining < 0) {
            status.style.background = '#fde2e2';
            status.style.color = '#a4242b';
            status.textContent =
                `Over-allocated by ${PaymentsPage._fmt(-remaining / 100)}. ` +
                `Reduce one of the Apply amounts or increase the Payment amount.`;
        } else {
            status.style.background = '#d6f4e0';
            status.style.color = '#1f6f3a';
            status.textContent = `Fully allocated (${PaymentsPage._fmt(total / 100)}).`;
        }
    },

    // Credits the customer already holds, each with an Apply action.
    _renderCredits(credits, customerId) {
        const host = $('#payment-credits');
        if (!host) return;
        if (!credits || !credits.credits.length) { host.innerHTML = ''; return; }
        const items = credits.credits.map(c => `<li style="margin:2px 0;">
                ${escapeHtml(PaymentsPage._creditLabel(c))}: <strong>${formatCurrency(c.available)}</strong>${c.currency !== credits.home_currency ? ` ${escapeHtml(c.currency)}` : ''}
                <button type="button" class="btn btn-sm btn-secondary" style="margin-left:6px;"
                    onclick="PaymentsPage.showApplyCredit('${c.kind}', ${c.id}, ${customerId}, true)">Apply</button>
            </li>`).join('');
        host.innerHTML = `<div style="padding:8px 10px; border-radius:4px; background:var(--gray-100); font-size:13px;">
            ${Terms.text(`This customer has ${formatCurrency(credits.total)} in credits not applied to an invoice yet:`)}
            <ul style="margin:6px 0 0 18px;">${items}</ul></div>`;
    },

    _creditLabel(c) {
        if (c.kind === 'credit_memo') return `Credit memo ${c.number} of ${formatDate(c.date)}`;
        const how = [c.method, c.number ? `#${c.number}` : ''].filter(Boolean).join(' ');
        return `Payment of ${formatDate(c.date)}${how ? ` (${how})` : ''}`;
    },

    // Apply one credit — the unapplied part of a payment, or a credit memo —
    // to the customer's open invoices, filled oldest first up to what the
    // credit has left.
    async showApplyCredit(kind, creditId, customerId, backToForm = false) {
        let credits, invoices;
        try {
            [credits, invoices] = await Promise.all([
                API.get(`/customers/${customerId}/credits`),
                // a payment's credit, and a credit memo's, may go to a
                // sub-customer billed with the customer (#638)
                fetchAllPages(`/invoices?customer_id=${customerId}&open_only=true&billed_to=true`),
            ]);
        } catch (err) { toast(err.message, 'error'); return; }
        const credit = credits.credits.find(c => c.kind === kind && c.id === creditId);
        if (!credit) { toast('That credit has already been applied', 'error'); return; }
        const open = PaymentsPage._openInvoices(invoices)
            .filter(i => (i.currency || credits.home_currency) === credit.currency);
        let left = PaymentsPage._cents(credit.available);
        const rows = open.map(i => {
            const take = Math.max(0, Math.min(left, PaymentsPage._cents(i.balance_due)));
            left -= take;
            return `<tr>
                <td>#${escapeHtml(i.invoice_number)}</td>
                <td>${formatDate(i.date)}</td>
                <td class="amount">${formatCurrency(i.balance_due)}</td>
                <td><input class="credit-alloc money" data-invoice="${i.id}" data-number="${escapeHtml(i.invoice_number)}" data-max="${i.balance_due}" type="number" step="0.01" min="0" max="${i.balance_due}"
                    value="${take > 0 ? (take / 100).toFixed(2) : ''}" aria-label="Apply to ${escapeHtml(i.invoice_number)}"
                    oninput="PaymentsPage._creditStatus(${PaymentsPage._cents(credit.available)})" style="width:100px;"></td>
            </tr>`;
        }).join('');
        openModal(`Apply Credit — ${credits.customer_name}`, `
            <form onsubmit="PaymentsPage.saveApplyCredit(event, '${kind}', ${creditId}, ${customerId}, ${backToForm ? 'true' : 'false'})">
                <p style="margin-bottom:8px;">${escapeHtml(PaymentsPage._creditLabel(credit))}: <strong>${formatCurrency(credit.available)}</strong> available.</p>
                ${open.length ? `<div class="table-container"><table><thead><tr>
                    <th scope="col">${T('Invoice')}</th><th scope="col">Date</th><th scope="col" class="amount">Balance</th><th scope="col" class="amount">Apply</th>
                </tr></thead><tbody>${rows}</tbody></table></div>
                <div id="credit-status" style="margin-top:8px; font-size:13px;"></div>`
                : `<p style="color:var(--gray-400);">${Terms.text('No open invoices to apply it to.')}</p>`}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="${backToForm ? `PaymentsPage.showForm(null, ${customerId})` : 'closeModal()'}">Cancel</button>
                    ${open.length ? '<button type="submit" class="btn btn-primary">Apply Credit</button>' : ''}
                </div>
            </form>`);
        PaymentsPage._creditStatus(PaymentsPage._cents(credit.available));
    },

    _creditStatus(availableCents) {
        const el = $('#credit-status');
        if (!el) return;
        let used = 0, over = null;
        $$('.credit-alloc').forEach(i => {
            const cents = PaymentsPage._cents(i.value);
            used += cents;
            // over its invoice's balance: the field's max blocks the save
            if (!over && i.dataset.max !== undefined && cents > PaymentsPage._cents(i.dataset.max)) over = i;
        });
        el.style.color = over || used > availableCents ? 'var(--text-danger)' : 'var(--gray-600)';
        el.textContent = over
            ? Terms.text(`The Apply amount for invoice #${over.dataset.number} is more than its balance of ${formatCurrency(over.dataset.max)}.`)
            : used > availableCents
            ? `That is ${formatCurrency((used - availableCents) / 100)} more than the credit has.`
            : `Applying ${formatCurrency(used / 100)}; ${formatCurrency((availableCents - used) / 100)} stays as a credit.`;
    },

    // Apply one credit to invoices: an unapplied payment in one request, a
    // credit memo one invoice at a time (its own endpoint). The invoice
    // view's Apply Credit sends through here too.
    async applyCredit(kind, creditId, allocations) {
        if (kind === 'payment') {
            await API.post(`/payments/${creditId}/apply`, { allocations });
            return;
        }
        for (const a of allocations) await API.post(`/credit-memos/${creditId}/apply`, a);
    },

    async saveApplyCredit(e, kind, creditId, customerId, backToForm) {
        e.preventDefault();
        const allocations = [];
        $$('.credit-alloc').forEach(input => {
            const cents = PaymentsPage._cents(input.value);
            if (cents > 0) allocations.push({ invoice_id: parseInt(input.dataset.invoice), amount: cents / 100 });
        });
        if (!allocations.length) { toast('Enter an amount to apply', 'error'); return; }
        try {
            await PaymentsPage.applyCredit(kind, creditId, allocations);
            toast('Credit applied');
            if (backToForm) { PaymentsPage.showForm(null, customerId); return; }
            App.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const { remaining } = PaymentsPage._remainingCents();
        if (remaining < 0) {
            toast('The Apply amounts add up to more than the payment. Lower one of them or raise the amount.', 'error');
            return;
        }
        // Leaving money unapplied is a choice the user makes, not a default.
        if (remaining > 0 && !$('#keep-credit')?.checked) {
            toast(Terms.text(`${PaymentsPage._fmt(remaining / 100)} is not applied to an invoice. Apply it, or tick the box to keep it as a credit.`), 'error');
            return;
        }
        const allocations = [];
        let unpaid = null;
        $$('#payment-invoices .alloc-amount').forEach(input => {
            const cents = PaymentsPage._cents(input.value);
            const discount = PaymentsPage._discountCents(input.dataset.invoice);
            if (discount > 0 && cents === 0) unpaid = unpaid || input;
            if (cents > 0 || discount > 0) {
                const row = { invoice_id: parseInt(input.dataset.invoice), amount: cents / 100 };
                if (discount > 0) row.discount = discount / 100;
                allocations.push(row);
            }
        });
        // a discount comes with a payment on its invoice (#664)
        if (unpaid) {
            const inv = (PaymentsPage._invoices || []).find(i => String(i.id) === String(unpaid.dataset.invoice));
            toast(`Invoice #${inv ? inv.invoice_number : unpaid.dataset.invoice} has a discount but nothing applied to it. Apply some of the payment to it, or clear its discount.`, 'error');
            return;
        }
        // the discounts post to the account picked beside them
        let discountAccountId = null;
        if (allocations.some(a => a.discount)) {
            discountAccountId = parseInt($('#discount-account')?.value) || null;
            if (!discountAccountId) { toast('Pick the account the discount posts to.', 'error'); return; }
            PaymentTerms.rememberDiscountAccount('sales', discountAccountId);
        }

        const foreign = PaymentsPage._isForeign();
        const rate = foreign ? parseFloat(form.pay_exchange_rate?.value) : null;
        if (foreign && !(rate > 0)) {
            toast(`Enter the exchange rate for ${PaymentsPage._currency} on the payment date.`, 'error');
            return;
        }
        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            amount: parseFloat(form.amount.value),
            currency: PaymentsPage._currency || null,
            exchange_rate: foreign ? rate : null,
            method: form.method.value || null,
            check_number: form.check_number.value || null,
            reference: form.reference.value || null,
            deposit_to_account_id: form.deposit_to_account_id.value ? parseInt(form.deposit_to_account_id.value) : null,
            notes: form.notes.value || null,
            allocations,
            discount_account_id: discountAccountId,
        };

        if (!(await TxnWarn.ok(form))) return;

        // One key per form: a second Save while the first is in flight is
        // ignored, and a Save sent again after a lost response answers with
        // the payment already recorded instead of a second one (#292)
        if (PaymentsPage._saving) return;
        PaymentsPage._saving = true;
        const button = form.querySelector ? form.querySelector('button[type="submit"]') : null;
        if (button) button.disabled = true;
        if (!PaymentsPage._requestKey) PaymentsPage._requestKey = App.requestKey ? App.requestKey() : 'k-' + Date.now();
        try {
            try {
                await API.post('/payments', data, null, { 'Idempotency-Key': PaymentsPage._requestKey });
            } catch (err) {
                // a discount the terms don't offer is the person's call:
                // asked, and sent again with the override (#664)
                if (!PaymentTerms.asksOverride(err, data)) throw err;
                data.allow_discount_override = true;
                PaymentsPage._requestKey = App.requestKey ? App.requestKey() : 'k-' + Date.now();
                await API.post('/payments', data, null, { 'Idempotency-Key': PaymentsPage._requestKey });
            }
            PaymentsPage._requestKey = null;
            toastPosted('Payment recorded');
            App.refresh();
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            PaymentsPage._saving = false;
            if (button) button.disabled = false;
        }
    },
};

// Top-level const creates no window property — the topbar's
// data-action dispatch (bootstrap.js callByPath) needs this export.
window.PaymentsPage = PaymentsPage;
