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
    // `tied` is whether the record is tied to a list entry (payment_term_id
    // set); false when it is still untied (#665).
    optionsHtml(selected, blank = null, tied = true) {
        const list = PaymentTerms._list || [];
        const names = PaymentTerms.names();
        // the record's own text exactly as stored. It is offered first and
        // said to be the record's own when it is off the list, or when it
        // matches a list name but the document is still untied from the list
        // (payment_term_id null): then picking the list entry beside it ties
        // the document, while leaving this selected re-sends the text
        // unchanged, so a save never ties or unties a document on its own.
        const current = selected || '';
        const own = current && (!names.includes(current) || !tied);
        // blank terms stay blank (due in 30 days, as always) rather than
        // the first term on the list
        if (blank === null && !current) blank = 'No terms (due in 30 days)';
        // each list option carries its id, so the save can send the term the
        // user picked (#665); the own "(as saved)" option carries none.
        return (blank !== null ? `<option value="" ${!current ? 'selected' : ''}>${escapeHtml(blank)}</option>` : '')
            + (own ? `<option value="${PaymentTerms.attr(current)}" selected>${escapeHtml(current.trim())} (as saved)</option>` : '')
            + list.map(t => `<option value="${PaymentTerms.attr(t.name)}" ${!own && t.name === current ? 'selected' : ''}${t.id != null ? ` data-term-id="${t.id}"` : ''}>${escapeHtml(t.name)}</option>`).join('');
    },

    // What a terms select carries on save: its text, and the id of the list
    // entry when a list option is picked, so the server ties the document to
    // that term; null id when the record's own "(as saved)" option is picked,
    // so the text is sent alone and the tie is left as it was (#665).
    selection(select) {
        if (!select) return { terms: '', payment_term_id: null };
        const opt = select.selectedOptions && select.selectedOptions[0];
        const id = opt && opt.dataset ? opt.dataset.termId : null;
        return { terms: select.value, payment_term_id: id ? parseInt(id, 10) : null };
    },

    // An attribute value that comes back byte for byte: HTML turns a CR
    // (or CR LF) typed in an attribute into LF, so CR and LF are written
    // as character references, which it leaves alone (#665).
    attr(text) {
        return escapeHtml(text).replace(/\r/g, '&#13;').replace(/\n/g, '&#10;');
    },

    // Put the record's terms in the select, adding an option when the list
    // has none for them (a vendor's or customer's own text).
    choose(select, terms) {
        if (!select || !terms) return;
        if (![...select.options].some(o => o.value === terms)) {
            select.insertAdjacentHTML('beforeend', `<option value="${PaymentTerms.attr(terms)}">${escapeHtml(terms)}</option>`);
        }
        select.value = terms;
    },

    // The due date, discount and note the server's parser gives a document
    // dated `date` with `terms`; null when it can't be asked.
    // `docId`: the document being edited, so its own terms answer by its
    // own rule while they are sent back unchanged (#665), not the list's.
    async dates(date, terms, documentKind = 'invoice', docId = null) {
        if (!date) return null;
        const own = docId ? `&${documentKind}_id=${encodeURIComponent(docId)}` : '';
        try {
            return await API.get(`/payment-terms/dates?date=${encodeURIComponent(date)}`
                + `&terms=${encodeURIComponent(terms || '')}&document=${documentKind}${own}`);
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
        // the person sees the plain reason, never the API instruction,
        // whether they take it anyway or press Cancel: the caller then toasts
        // the rewritten message, not the raw one (#665).
        if (err) err.message = why;
        return confirm(`${why}\n\nTake the discount anyway?`);
    },
    OVERRIDE: 'allow_discount_override',

    // The line above a Take-discount modal: the discount the terms offer (or
    // that they offer none), closed with a full stop so the balance sentence
    // after it does not run on to it (#665).
    dueSentence(discountLine) {
        const line = discountLine || 'Its terms offer no early-payment discount';
        return line.replace(/\.?$/, '.');
    },

    // The early-payment discount suggested for an open document paid on
    // `payDate`: its percent of the balance, in cents, while the discount
    // date has not passed; 0 otherwise.
    // Its percent of the document's total, as the server takes it (#665),
    // no more than its balance, and none before the document's date.
    suggestedCents(doc, payDate) {
        const pct = parseFloat(doc.discount_percent);
        if (!(pct > 0) || !doc.discount_date || !payDate || payDate > doc.discount_date) return 0;
        if (doc.date && payDate < doc.date) return 0;
        const balance = Math.round((parseFloat(doc.balance_due) || 0) * 100);
        const total = doc.total !== undefined ? Math.round((parseFloat(doc.total) || 0) * 100) : balance;
        return Math.min(Math.round(total * pct / 100), balance);
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
        // only a discount account for this side: never fall back to the other
        // side's, so Pay Bills does not pre-select Sales Discounts (#665).
        // With none on this side, nothing is pre-selected and the user picks.
        const named = usable.find(a => /discount/i.test(a.name) && !other.test(a.name));
        const pick = usable.find(a => String(a.id) === last) || named || null;
        return '<option value="">Select...</option>' + usable.map(a =>
            `<option value="${a.id}" ${pick && pick.id === a.id ? 'selected' : ''}>${escapeHtml((a.account_number ? a.account_number + ' ' : '') + a.name)}</option>`).join('');
    },

    rememberDiscountAccount(side, id) {
        try { if (id) localStorage.setItem(`slowbooks-discount-account-${side}`, String(id)); } catch (e) { /* this form only */ }
    },
};

if (typeof window !== 'undefined') window.PaymentTerms = PaymentTerms;
