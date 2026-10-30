/**
 * The terms list (#664) for every form that takes terms: invoices, bills,
 * Bill Quick Entry, customers, vendors, recurring invoices, memorized bills,
 * Quick Entry and Settings' default terms. The forms offer the list's
 * active terms; a document's due date, discount and any note come from the
 * server's one parser (GET /payment-terms/dates), never from a table of
 * days kept here. (Named PaymentTerms: `Terms` is the company's wording.)
 */
const PaymentTerms = {
    _list: null,

    // The active terms, fetched once per page load; Settings reloads it
    // after an edit.
    async load(force = false) {
        if (!PaymentTerms._list || force) {
            try {
                const list = await API.get('/payment-terms');
                PaymentTerms._list = Array.isArray(list) ? list : [];
            } catch (e) {
                PaymentTerms._list = null;
                return [];
            }
        }
        return PaymentTerms._list;
    },

    names() {
        return (PaymentTerms._list || []).map(t => t.name);
    },

    // The <option>s for a terms select: the list's active terms, and the
    // record's own terms when they are not on it (free text from before the
    // list, an import, or a term made inactive), so a save never changes
    // them silently. `blank` adds a first option with that label and value "".
    optionsHtml(selected, blank = null) {
        const names = PaymentTerms.names();
        // the record's own text exactly as stored, never a list name that
        // only matches it case- or space-blind: sent back unchanged, it
        // leaves a document saved before that term untied (#665)
        const current = selected || '';
        const choices = current && !names.includes(current) ? [...names, current] : names;
        // blank terms stay blank (due in 30 days, as always) rather than
        // the first term on the list
        if (blank === null && !current) blank = 'No terms (due in 30 days)';
        return (blank !== null ? `<option value="" ${!current ? 'selected' : ''}>${escapeHtml(blank)}</option>` : '')
            + choices.map(t => `<option value="${escapeHtml(t)}" ${t === current ? 'selected' : ''}>${escapeHtml(t)}</option>`).join('');
    },

    // Put the record's terms in the select, adding an option when the list
    // has none for them (a vendor's or customer's own text).
    choose(select, terms) {
        if (!select || !terms) return;
        if (![...select.options].some(o => o.value === terms)) {
            select.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(terms)}">${escapeHtml(terms)}</option>`);
        }
        select.value = terms;
    },

    // The due date, discount and note the server's parser gives a document
    // dated `date` with `terms`; null when it can't be asked.
    async dates(date, terms, documentKind = 'invoice') {
        if (!date) return null;
        try {
            return await API.get(`/payment-terms/dates?date=${encodeURIComponent(date)}`
                + `&terms=${encodeURIComponent(terms || '')}&document=${documentKind}`);
        } catch (e) {
            return null;
        }
    },

    // The line under a form's Terms: the discount the terms offer, or the
    // sentence saying the terms aren't understood. Empty when neither.
    hintHtml(d) {
        if (!d) return '';
        if (d.note) return `<span class="terms-note" style="color:#7a5500;">${escapeHtml(d.note)}</span>`;
        if (d.discount_line) return `<span class="terms-discount">${escapeHtml(d.discount_line)}</span>`;
        return '';
    },

    // A document's view: its discount line and any terms note.
    viewHtml(doc) {
        if (doc.terms_note) {
            return `<div class="terms-note" style="margin:6px 0; padding:6px 10px; border-radius:4px; background:#fff4d6; color:#7a5500;">${escapeHtml(doc.terms_note)}</div>`;
        }
        if (doc.discount_line) return `<strong>Discount:</strong> ${escapeHtml(doc.discount_line)}<br>`;
        return '';
    },

    // A refused discount the terms don't offer (no discount, after its
    // date, more than its percent, a second one): the server's sentence,
    // asked as a question; true when the person says take it anyway.
    asksOverride(err, body) {
        const msg = String((err && err.message) || '');
        if (body.allow_discount_override || !msg.includes(PaymentTerms.OVERRIDE)) return false;
        const why = msg.replace(` Send ${PaymentTerms.OVERRIDE} to take it anyway.`, '');
        return confirm(`${why}\n\nTake the discount anyway?`);
    },
    OVERRIDE: 'allow_discount_override',

    // The early-payment discount suggested for an open document paid on
    // `payDate`: its percent of the balance, in cents, while the discount
    // date has not passed; 0 otherwise.
    suggestedCents(doc, payDate) {
        const pct = parseFloat(doc.discount_percent);
        if (!(pct > 0) || !doc.discount_date || !payDate || payDate > doc.discount_date) return 0;
        const balance = Math.round((parseFloat(doc.balance_due) || 0) * 100);
        return Math.round(balance * pct / 100);
    },

    // The accounts a discount can post to (income, expense or cost of
    // goods), and the one picked last time on this kind of payment.
    discountAccountOptions(accounts, side) {
        const usable = accounts.filter(a => ['income', 'expense', 'cogs'].includes(a.account_type)
            && a.is_active !== false);
        let last = null;
        try { last = localStorage.getItem(`slowbooks-discount-account-${side}`); } catch (e) { /* none */ }
        // a discount account for this side: sales discounts given on
        // Receive Payments, purchase discounts taken on Pay Bills (#665)
        const other = side === 'sales' ? /purchase|taken|earned|vendor/i : /sales|given|allowed|customer/i;
        const named = usable.find(a => /discount/i.test(a.name) && !other.test(a.name))
            || usable.find(a => /discount/i.test(a.name));
        const pick = usable.find(a => String(a.id) === last) || named || null;
        return '<option value="">Select...</option>' + usable.map(a =>
            `<option value="${a.id}" ${pick && pick.id === a.id ? 'selected' : ''}>${escapeHtml((a.account_number ? a.account_number + ' ' : '') + a.name)}</option>`).join('');
    },

    rememberDiscountAccount(side, id) {
        try { if (id) localStorage.setItem(`slowbooks-discount-account-${side}`, String(id)); } catch (e) { /* this form only */ }
    },
};

if (typeof window !== 'undefined') window.PaymentTerms = PaymentTerms;
