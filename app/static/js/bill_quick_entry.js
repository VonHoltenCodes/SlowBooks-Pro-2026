/**
 * Bill Quick Entry (#487): a stack of vendor bills keyed one after another,
 * as invoice Quick Entry (#/quick-entry) does for paper invoices. Vendor
 * (with the vendor quick add), date, the vendor's bill number, terms, and
 * lines of an Account, a description and an amount — no quantity or rate, as
 * a bill line may be an amount alone since 2.26 (#391). An item can be
 * picked on a line too, and fills its account, words and cost.
 *
 * Save & Next (or Ctrl+Enter) posts /api/bills, adds the bill to the batch
 * log under the form and starts the next one with the vendor kept, the
 * focus back on the vendor picker. The Account cell has the shared account
 * quick add (#485) and the Item cell the item quick add (#445), both on the
 * purchase side.
 */
const BillQuickEntry = {
    _vendors: [],
    _items: [],
    _accounts: [],
    _defaultAccountId: null,
    _lineCount: 0,
    _batch: [],

    async render() {
        const [vendors, items, accounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/accounts?active_only=true'),
            // the terms list (#664), as Enter Bill offers it
            PaymentTerms.load(),
        ]);
        BillQuickEntry._vendors = vendors;
        BillQuickEntry._items = items;
        BillQuickEntry._accounts = PurchaseAccounts.filter(accounts);
        BillQuickEntry._defaultAccountId = null;
        BillQuickEntry._lineCount = 0;
        BillQuickEntry._batch = [];
        return `
            <div class="page-header">
                <h2>Bill Quick Entry</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Batch bill entry — for entering a stack of vendor bills quickly
                </div>
            </div>
            <div class="quick-entry-info" style="background:var(--primary-light); padding:8px 12px; margin-bottom:12px; border:1px solid var(--qb-gold); font-size:11px;">
                Enter the bill and press <strong>Save &amp; Next</strong> (or Ctrl+Enter) to save it and start the next one. Each line is an account and an amount.
            </div>
            <form id="bqe-form" aria-label="Bill Quick Entry" onsubmit="BillQuickEntry.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'bqe-vendor', onchange: 'BillQuickEntry.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label for="bqe-date">Date *</label>
                        <input name="date" id="bqe-date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label for="bqe-number">Bill Number</label>
                        <input name="bill_number" id="bqe-number" placeholder="from the bill, or left blank"></div>
                    <div class="form-group"><label for="bqe-terms">Terms</label>
                        <select name="terms" id="bqe-terms" title="The vendor's terms; the due date follows from them">
                            ${PaymentTerms.optionsHtml('Net 30')}
                        </select></div>
                </div>
                <h3 style="margin:12px 0 8px; font-size:14px;">Lines</h3>
                <div class="table-container"><table class="line-items-table bill-lines-table">
                    <thead><tr><th scope="col">Item</th><th scope="col" title="Where the line is recorded">Account</th><th scope="col">Description</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="bqe-lines">${BillQuickEntry.lineHtml(BillQuickEntry._lineCount++)}</tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="BillQuickEntry.addLine()">+ Add Line</button>
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                    <div id="bqe-total" aria-live="polite" style="font-size:16px; font-weight:700; color:var(--qb-navy);">Total: $0.00</div>
                    <div class="form-actions" style="margin:0;">
                        <button type="submit" class="btn btn-primary" data-write>Save &amp; Next (Ctrl+Enter)</button>
                    </div>
                </div>
            </form>
            <div id="bqe-log" style="margin-top:16px;"></div>`;
    },

    lineHtml(idx) {
        const acctOpts = PurchaseAccounts.options(BillQuickEntry._accounts, BillQuickEntry._defaultAccountId);
        return `<tr data-bqeline="${idx}">
                <td><select class="line-item" onchange="BillQuickEntry.itemSelected(this)">${ItemQuickAdd.options(BillQuickEntry._items)}</select></td>
                <td>${AccountQuickAdd.picker(`<select class="line-account"><option value="">Choose...</option>${acctOpts}</select>`, 'bill-entry')}</td>
                <td><input class="line-desc"></td>
                <td class="col-amount"><input class="line-amt money" type="number" step="0.01" min="0" oninput="BillQuickEntry.recalc()"></td>
            </tr>`;
    },

    addLine() {
        $('#bqe-lines').insertAdjacentHTML('beforeend', BillQuickEntry.lineHtml(BillQuickEntry._lineCount++));
    },

    // A vendor brings its terms and its default account, which fills every
    // line not already pointed somewhere (as Enter Bill does, F11).
    vendorSelected(vendorId) {
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = BillQuickEntry._vendors.find(v => v.id == vendorId);
        BillQuickEntry._defaultAccountId = (vendor && vendor.default_expense_account_id) || null;
        const terms = $('#bqe-terms');
        if (vendor && vendor.terms && terms) PaymentTerms.choose(terms, vendor.terms);
        if (BillQuickEntry._defaultAccountId) {
            $$('#bqe-lines .line-account').forEach(sel => {
                if (!sel.disabled && !sel.value) sel.value = BillQuickEntry._defaultAccountId;
            });
        }
    },

    // An item fills its words, its cost and the account it is recorded in;
    // a stock item is recorded in Inventory, so its account is not a choice.
    itemSelected(select) {
        if (ItemQuickAdd.selected(select, BillQuickEntry._items, () => BillQuickEntry.itemSelected(select), 'purchase')) return;
        const row = select.closest('tr');
        const item = BillQuickEntry._items.find(i => i.id == select.value);
        const acct = row.querySelector('.line-account');
        const stock = !!(item && item.track_inventory);
        acct.disabled = stock;
        acct.title = stock ? 'Stock items are recorded in Inventory' : '';
        const add = row.querySelector('.aqa-add');
        if (add) add.disabled = stock;
        if (stock && acct._aqaPanel) AccountQuickAdd.cancel(acct._aqaPanel);
        if (stock) acct.value = '';
        else if (item && item.expense_account_id) acct.value = item.expense_account_id;
        else if (!acct.value && BillQuickEntry._defaultAccountId) acct.value = BillQuickEntry._defaultAccountId;
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            const price = PurchaseLines.price(item);
            if (Number(price)) row.querySelector('.line-amt').value = Number(price).toFixed(2);
        }
        BillQuickEntry.recalc();
    },

    _amount(row) {
        return PurchaseLines.cents(parseFloat(row.querySelector('.line-amt')?.value) || 0);
    },

    recalc() {
        let total = 0;
        $$('#bqe-lines tr').forEach(row => { total += BillQuickEntry._amount(row); });
        const el = $('#bqe-total');
        if (el) el.textContent = `Total: ${formatCurrency(total)}`;
        return PurchaseLines.cents(total);
    },

    // The lines worth sending: an amount, or words with an amount. A line
    // left blank is skipped, as invoice Quick Entry skips one.
    _lines() {
        const lines = [];
        let missing = null;
        $$('#bqe-lines tr').forEach((row, i) => {
            const amount = BillQuickEntry._amount(row);
            const desc = row.querySelector('.line-desc')?.value || '';
            const itemId = ItemQuickAdd.itemId(row.querySelector('.line-item'));
            if (!amount && !desc && !itemId) return;
            const acct = row.querySelector('.line-account');
            const accountId = acct && !acct.disabled && /^\d+$/.test(acct.value) ? Number(acct.value) : null;
            if (!missing && acct && !acct.disabled && !accountId && amount > 0) missing = { n: i + 1, sel: acct };
            lines.push({ item_id: itemId, account_id: accountId, description: desc, amount, line_order: lines.length });
        });
        return { lines, missing };
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        if (form.dataset.saving) return;
        const { lines, missing } = BillQuickEntry._lines();
        if (BillQuickEntry.recalc() <= 0) {
            toast('Enter what the vendor charged: an amount on at least one line.', 'error');
            return;
        }
        if (missing) {
            toast(`Choose an account for line ${missing.n}: where should it be recorded?`, 'error');
            AccountQuickAdd._focusPicker(missing.sel);
            return;
        }
        // a date far from today asks first, as every bill form does (#481)
        if (!(await TxnWarn.ok(form))) return;
        form.dataset.saving = '1';
        try {
            const vendorId = await VendorQuickAdd.ensure('bqe-vendor');
            const bill = await API.post('/bills', {
                vendor_id: vendorId,
                bill_number: form.bill_number.value.trim() || null,
                date: form.date.value,
                terms: form.terms.value,
                lines,
            });
            BillQuickEntry._logged(bill);
            toastPosted(`Bill ${bill.bill_number} saved`);
            BillQuickEntry._next(form);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            delete form.dataset.saving;
        }
    },

    // The batch so far, newest first, each bill opening from its number;
    // a running count and total over it.
    _logged(bill) {
        BillQuickEntry._batch.unshift(bill);
        const total = BillQuickEntry._batch.reduce((s, b) => s + (Number(b.total) || 0), 0);
        const rows = BillQuickEntry._batch.map(b => `<tr>
                <td><a href="#/bills/${b.id}">${escapeHtml(b.bill_number || '')}</a></td>
                <td>${escapeHtml(b.vendor_name || '')}</td>
                <td>${formatDate(b.date)}</td>
                <td>${formatDate(b.due_date)}</td>
                <td class="amount">${formatCurrency(b.total)}</td>
            </tr>`).join('');
        const n = BillQuickEntry._batch.length;
        $('#bqe-log').innerHTML = `<div class="card">
            <div class="card-header" id="bqe-log-title">This batch — ${n} bill${n === 1 ? '' : 's'}, ${formatCurrency(total)}</div>
            <div class="table-container"><table aria-labelledby="bqe-log-title">
                <thead><tr><th scope="col">Bill #</th><th scope="col">Vendor</th><th scope="col">Date</th><th scope="col">Due</th><th scope="col" class="amount">Total</th></tr></thead>
                <tbody>${rows}</tbody></table></div></div>`;
    },

    // The next bill: the vendor, date and terms stay (a stack is often one
    // vendor's, or one day's); the number and the lines start again.
    _next(form) {
        form.bill_number.value = '';
        BillQuickEntry._lineCount = 0;
        $('#bqe-lines').innerHTML = BillQuickEntry.lineHtml(BillQuickEntry._lineCount++);
        BillQuickEntry.recalc();
        const vendor = $('#bqe-vendor');
        focusFor(vendor && vendor._cbx ? vendor._cbx.box : vendor, true);
    },
};

// The Account cell's new account (#485): expense or cost of goods, on every
// line and picked on its own.
if (typeof AccountQuickAdd !== 'undefined') {
    AccountQuickAdd.register('bill-entry', {
        types: AccountQuickAdd.PURCHASE_TYPES,
        compact: true,
        chart: () => BillQuickEntry._accounts,
        added: () => AccountQuickAdd.refillPurchase($$('#bqe-lines select.line-account'), BillQuickEntry._accounts),
    });
}
