/**
 * Bills & Bill Payments — Accounts Payable workflow
 * Feature 1: Enter bills, pay bills
 */
const BillsPage = {
    showAll() {
        BillsPage._showAll = true;
        App.navigate(location.hash || '#/bills');
    },

    async render() {
        // The newest 500, and a way to see the rest (issue #191).
        const all = BillsPage._showAll;
        BillsPage._showAll = false;
        const rows = all ? await fetchAllPages('/bills') : await API.get('/bills?limit=501');
        const bills = all ? rows : rows.slice(0, 500);
        return renderListPage({
            title: 'Bills (Accounts Payable)',
            headerHtml: `<div class="btn-group">
                    <button class="btn btn-primary" onclick="BillsPage.showForm()">+ Enter Bill</button>
                    <button class="btn btn-secondary" onclick="BillsPage.showPayForm()">Pay Bills</button>
                </div>` + (all ? '' : listCapNote(rows, 500, 'BillsPage.showAll()', 'bills')),
            filter: {
                id: 'bill-status-filter',
                rowSelector: '.bill-row',
                options: [['unpaid', 'Unpaid'], ['partial', 'Partial'], ['paid', 'Paid'], ['void', 'Void']],
            },
            empty: `<p>No bills entered yet.</p>
                <button class="btn btn-primary" onclick="BillsPage.showForm()" style="margin-top:10px;">+ Enter your first bill</button>`,
            columns: [
                { label: 'Bill #', key: 'bill_number' },
                { label: 'Vendor', key: 'vendor_name' },
                { label: 'Date', key: 'date' },
                { label: 'Due', key: 'due_date' },
                { label: 'Status', key: 'status' },
                { label: 'Total', cls: 'amount', key: 'total' },
                { label: 'Balance', cls: 'amount', key: 'balance_due' },
                'Actions',
            ],
            sort: { id: 'bills', column: 'date', direction: 'desc' },
            items: bills,
            row: b => `<tr class="bill-row" data-status="${b.status}">
                    <td><strong>${escapeHtml(b.bill_number)}</strong></td>
                    <td>${escapeHtml(b.vendor_name || '')}</td>
                    <td>${formatDate(b.date)}</td>
                    <td>${formatDate(b.due_date)}</td>
                    <td>${statusBadge(b.status)}</td>
                    <td class="amount">${formatCurrency(b.total)}</td>
                    <td class="amount">${formatCurrency(b.balance_due)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="BillsPage.view(${b.id})">View</button>
                        ${b.status !== 'void' ? `<button class="btn btn-sm btn-secondary" data-write onclick="BillsPage.showForm(${b.id})">Edit</button>` : ''}
                        ${b.status !== 'void' && b.status !== 'paid' ? `<button data-destructive class="btn btn-sm btn-danger" onclick="BillsPage.void(${b.id})">Void</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    async view(id) {
        const [bill, accounts, payments] = await Promise.all([
            API.get(`/bills/${id}`),
            API.get('/accounts'),
            API.get(`/bill-payments?bill_id=${id}`),
        ]);
        const acctName = Object.fromEntries(accounts.map(a => [a.id, `${a.account_number || ''} ${a.name}`.trim()]));
        let linesHtml = bill.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td>${escapeHtml(acctName[l.account_id] || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');

        openModal(`Bill ${bill.bill_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Vendor:</strong> ${escapeHtml(bill.vendor_name || '')}<br>
                <strong>Date:</strong> ${formatDate(bill.date)}<br>
                <strong>Terms:</strong> ${escapeHtml(bill.terms || '')}<br>
                <strong>Due:</strong> ${formatDate(bill.due_date)}<br>
                <strong>Status:</strong> ${statusBadge(bill.status)}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col">Account</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(bill.subtotal)}</span></div>
                ${Number(bill.tax_amount) ? `<div class="total-row" title="Part of what the goods cost: it posts with the lines, not to Sales Tax Payable"><span class="label">Tax</span><span class="value">${formatCurrency(bill.tax_amount)}</span></div>` : ''}
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(bill.total)}</span></div>
                <div class="total-row"><span class="label">Paid</span><span class="value">${formatCurrency(bill.amount_paid)}</span></div>
                <div class="total-row grand-total"><span class="label">Balance</span><span class="value">${formatCurrency(bill.balance_due)}</span></div>
            </div>
            ${BillsPage._paymentsHtml(bill, payments)}
            <div style="margin-top:16px; border-top:1px solid var(--gray-200); padding-top:12px;">
                <h3 style="font-size:13px; margin-bottom:8px;">Attachments</h3>
                <div id="bill-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <input type="file" id="bill-attach-file" aria-label="File to attach" style="font-size:11px;">
                <button class="btn btn-sm btn-secondary" onclick="BillsPage.uploadAttachment(${bill.id})" style="margin-left:4px;">Upload</button>
            </div>
            <div class="form-actions">
                ${bill.status !== 'void' ? `<button class="btn btn-primary" data-write onclick="BillsPage.showForm(${bill.id})">Edit</button>` : ''}
                <button class="btn btn-secondary" onclick="window.open('/api/bills/${bill.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/bills/${bill.id}/print-preview','_blank')">Print</button>
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        BillsPage.loadAttachments(bill.id);
    },

    // The payments that paid this bill, each with its own Void — a paid
    // bill's payment could not be voided anywhere on screen (W-L19) — and,
    // for a check, Print Check: the only Print Check button sat on payments
    // RECEIVED, and went when that was put right (W-M9).
    _paymentsHtml(bill, payments) {
        if (!payments.length) return '';
        const rows = payments.map(p => {
            const applied = (p.allocations || []).filter(a => a.bill_id === bill.id)
                .reduce((sum, a) => sum + Number(a.amount), 0);
            const how = [p.method ? p.method.replace('_', ' ') : '', p.check_number ? `#${p.check_number}` : ''].filter(Boolean).join(' ');
            return `<tr${p.is_voided ? ' class="row--dim"' : ''}>
                <td>${formatDate(p.date)}</td>
                <td>${escapeHtml(how)}</td>
                <td class="amount">${formatCurrency(applied)}</td>
                <td class="actions">${p.is_voided ? statusBadge('void') : `${BillsPage._printCheckButton(p)}<button data-destructive class="btn btn-sm btn-danger" onclick="BillsPage.voidBillPayment(${p.id}, ${bill.id})">Void</button>`}</td>
            </tr>`;
        }).join('');
        return `<div style="margin-top:16px;">
                <h3 style="font-size:13px; margin-bottom:8px;">Payments</h3>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Date</th><th scope="col">Paid by</th><th scope="col" class="amount">Applied</th><th scope="col"></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
            </div>`;
    },

    // One bill payment and the bills it paid, with Print Check and Void as
    // on a bill's own view. The bank register links a bill payment to
    // #/bill-payments/{id}, which said "Page not found" (explore 2.17.3).
    async viewPayment(id) {
        const p = await API.get(`/bill-payments/${id}`);
        const allocations = p.allocations || [];
        const bills = await Promise.all(allocations.map(a =>
            API.get(`/bills/${a.bill_id}`).catch(() => ({ id: a.bill_id }))));
        const billById = Object.fromEntries(bills.map(b => [b.id, b]));
        const rows = allocations.map(a => {
            const b = billById[a.bill_id] || {};
            return `<tr>
                <td>${escapeHtml(b.bill_number || '')}</td>
                <td>${formatDate(b.date)}</td>
                <td class="amount">${formatCurrency(a.amount)}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="BillsPage.view(${a.bill_id})">View Bill</button></td>
            </tr>`;
        }).join('');
        const applied = allocations.reduce((sum, a) => sum + Number(a.amount), 0);
        const ahead = Math.round((Number(p.amount) - applied) * 100) / 100;
        const how = [p.method ? p.method.replace('_', ' ') : '', p.check_number ? `#${p.check_number}` : ''].filter(Boolean).join(' ');
        const firstBill = allocations.length ? allocations[0].bill_id : null;
        openModal(`Bill Payment — ${p.vendor_name || ''}`, `
            <div style="margin-bottom:12px;">
                <strong>Vendor:</strong> ${escapeHtml(p.vendor_name || '')}<br>
                <strong>Date:</strong> ${formatDate(p.date)}<br>
                <strong>Paid by:</strong> ${escapeHtml(how)}<br>
                <strong>Amount:</strong> ${formatCurrency(p.amount)}<br>
                <strong>Status:</strong> ${p.is_voided ? statusBadge('void') : 'Paid'}
            </div>
            ${rows ? `<div class="table-container"><table>
                <thead><tr><th scope="col">Bill #</th><th scope="col">Date</th><th scope="col" class="amount">Applied</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>` : ''}
            ${!p.is_voided && ahead > 0 ? `<p style="font-size:11px; color:var(--text-muted);">${formatCurrency(ahead)} paid ahead, not applied to a bill.</p>` : ''}
            <div class="form-actions">
                ${p.is_voided ? '' : `${BillsPage._printCheckButton(p)}<button data-destructive type="button" class="btn btn-danger" onclick="BillsPage.voidBillPayment(${p.id}, ${firstBill})">Void</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    // A payment by check (or one that carries a check number) prints its
    // check; ACH, cash and card payments have none to print.
    _printCheckButton(p) {
        const byCheck = !p.method || p.method === 'check' || !!p.check_number;
        if (p.is_voided || !byCheck) return '';
        return `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/checks/print?bill_payment_id=${p.id}','_blank')">Print Check</button> `;
    },

    _items: [],
    _vendors: [],
    _accounts: [],
    _defaultExpenseAccountId: null,
    lineCount: 0,

    // A vendor brings its terms (Enter Bill always said Net 30, so a Net 15
    // supplier's bill fell due two weeks late — F11) and its default
    // expense account, which fills every line not already pointed somewhere.
    vendorSelected(vendorId) {
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = BillsPage._vendors.find(v => v.id == vendorId);
        BillsPage._defaultExpenseAccountId = (vendor && vendor.default_expense_account_id) || null;
        const terms = document.querySelector('#bill-form [name="terms"]');
        if (vendor && vendor.terms && terms) {
            if (![...terms.options].some(o => o.value === vendor.terms)) {
                terms.insertAdjacentHTML('beforeend', `<option value="${escapeHtml(vendor.terms)}">${escapeHtml(vendor.terms)}</option>`);
            }
            terms.value = vendor.terms;
        }
        if (BillsPage._defaultExpenseAccountId) {
            $$('#bill-lines .line-account').forEach(sel => {
                if (!sel.disabled && !sel.value) sel.value = BillsPage._defaultExpenseAccountId;
            });
        }
    },

    // Enter Bill, or with an id the same form filled from the bill, to
    // edit it (#225): a posted bill can be changed the way an invoice can,
    // and the save re-posts its journal. For a new bill, the vendor's own
    // page sets `_startVendor` just before opening the form (#223, NEW-21);
    // read once and cleared.
    _startVendor: null,
    async showForm(id = null) {
        const startVendor = id ? null : BillsPage._startVendor;
        BillsPage._startVendor = null;
        const [vendors, items, accounts, bill] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/accounts'),
            id ? API.get(`/bills/${id}`) : Promise.resolve(null),
        ]);
        if (bill && bill.status === 'void') { toast('A voided bill cannot be edited', 'error'); return; }
        // the bill's own vendor stays listed even if it has since gone inactive
        if (bill && !vendors.some(v => v.id === bill.vendor_id)) {
            vendors.push({ id: bill.vendor_id, name: bill.vendor_name || `Vendor #${bill.vendor_id}` });
        }
        BillsPage._items = items;
        BillsPage._accounts = PurchaseAccounts.filter(accounts, ...(bill ? bill.lines.map(l => l.account_id) : []));
        BillsPage._defaultExpenseAccountId = null;
        // A bill's tax rate (set through the API, #250) is kept by the save,
        // so the form shows its tax and a total that is the bill's (2.22.1
        // gate, NEW-47: a $35.42 bill opened as $33.72).
        BillsPage._taxRate = bill ? Number(bill.tax_rate) || 0 : 0;
        BillsPage.lineCount = 1;
        const classGroup = await classFormGroupHtml(bill ? bill.class_id : undefined);
        const jobGroup = await jobFormGroupHtml(bill ? bill.job_id : null);
        await CostCodes.load();
        await Nonprofit.loadFunds();

        BillsPage._vendors = vendors;
        const terms = bill && bill.terms ? bill.terms : 'Net 30';
        const termChoices = ['Net 15','Net 30','Net 45','Net 60','Due on Receipt'];
        if (!termChoices.includes(terms)) termChoices.push(terms);

        openModal(bill ? `Edit Bill ${bill.bill_number}` : 'Enter Bill', `
            <form id="bill-form" onsubmit="BillsPage.save(event, ${bill ? bill.id : 'null'})">
                ${bill ? '' : ScanHelper.scanRowHtml()}
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'bill-vendor', onchange: 'BillsPage.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label>Bill Number</label>
                        <input name="bill_number" placeholder="from the receipt, or left blank" value="${bill ? escapeHtml(bill.bill_number || '') : ''}"></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${bill ? bill.date : todayISO()}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" title="The vendor's terms; the due date follows from them">
                            ${termChoices.map(t =>
                                `<option ${t===terms?'selected':''}>${escapeHtml(t)}</option>`).join('')}
                        </select></div>
                    ${classGroup}${jobGroup}
                    ${currencyFormGroupsHtml(bill ? bill.currency : undefined, bill ? bill.exchange_rate : undefined)}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col" title="Where the line is recorded">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}${Nonprofit.headHtml()}<th scope="col" title="Billable to the job's customer">Bill?</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="bill-lines">${bill ? '' : BillsPage.lineHtml(0)}</tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="BillsPage.addLine()">+ Add Line</button>
                <div class="invoice-totals">
                    ${BillsPage._taxRate ? `<div class="total-row"><span class="label">Subtotal</span><span class="value" id="bill-subtotal">$0.00</span></div>
                    <div class="total-row" title="Part of what the goods cost: it posts with the lines, not to Sales Tax Payable"><span class="label">Tax (${Number((BillsPage._taxRate * 100).toFixed(4))}%)</span><span class="value" id="bill-tax">$0.00</span></div>` : ''}
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="bill-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${bill ? escapeHtml(bill.notes || '') : ''}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="ScanHelper.discard(); closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${bill ? 'Save Changes' : 'Save Bill'}</button>
                </div>
            </form>`);
        if (bill) {
            const vendorSel = document.getElementById('bill-vendor');
            if (vendorSel) vendorSel.value = bill.vendor_id;
            BillsPage.lineCount = 0;
            bill.lines.forEach(l => BillsPage.addLine(l));
            BillsPage.recalc();
        } else {
            ScanHelper.wire(BillsPage._applyScan, BillsPage._applyScanField, BillsPage._scanFieldTarget);
            if (startVendor) {
                const sel = document.getElementById('bill-vendor');
                if (sel) { sel.value = String(startVendor); BillsPage.vendorSelected(startVendor); }
            }
        }
    },

    // One line of Enter Bill. The Account cell says where the line is
    // recorded — the item's expense account or the vendor's default fill it
    // in; with neither, it waits to be chosen (a line with no account used
    // to land on 6000, Advertising & Marketing — W-H5, F8).
    // With `line`, a stored line drawn as the bill has it (an edit, #225):
    // its item, account, words, cost code, fund, quantity and rate; a stock
    // item's account is Inventory and not a choice, as when it's picked.
    lineHtml(idx, line = null) {
        const l = line || {};
        const item = l.item_id ? BillsPage._items.find(i => i.id == l.item_id) : null;
        const stock = !!(item && item.track_inventory);
        const itemOpts = BillsPage._items.map(i => `<option value="${i.id}"${l.item_id == i.id ? ' selected' : ''}>${escapeHtml(i.name)}</option>`).join('');
        const acctOpts = PurchaseAccounts.options(BillsPage._accounts, stock ? null : (l.account_id || BillsPage._defaultExpenseAccountId));
        return `<tr data-billline="${idx}">
                <td><select class="line-item" onchange="BillsPage.itemSelected(this)"><option value="">--</option>${itemOpts}</select></td>
                <td><select class="line-account"${stock ? ' disabled title="Stock items are recorded in Inventory"' : ''}><option value="">Choose...</option>${acctOpts}</select></td>
                <td><input class="line-desc" value="${escapeHtml(l.description || '')}"></td>
                ${CostCodes.cellHtml('line-cost-code', l.cost_code_id)}${Nonprofit.cellHtml('line-function', l.function, l.class_id)}
                <td style="text-align:center;"><input type="checkbox" class="line-billable" title="Billable"${l.is_billable ? ' checked' : ''}></td>
                <td><input class="line-qty" type="number" step="0.01" value="${line ? Number(l.quantity) : 1}" oninput="BillsPage.recalc()"></td>
                <td><input class="line-rate" type="number" step="0.0001" value="${line ? Number(l.rate) : 0}" oninput="BillsPage.recalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
            </tr>`;
    },

    // Picking an item fills what it costs and where it is recorded; a stock
    // item is recorded in Inventory, so its account is not a choice.
    itemSelected(select) {
        const row = select.closest('tr');
        const item = BillsPage._items.find(i => i.id == select.value);
        const acct = row.querySelector('.line-account');
        if (acct) {
            acct.disabled = !!(item && item.track_inventory);
            acct.title = acct.disabled ? 'Stock items are recorded in Inventory' : '';
            if (acct.disabled) acct.value = '';
            else if (item && item.expense_account_id) acct.value = item.expense_account_id;
            else if (!acct.value && BillsPage._defaultExpenseAccountId) acct.value = BillsPage._defaultExpenseAccountId;
        }
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = PurchaseLines.price(item);
        }
        BillsPage.recalc();
    },

    // Where each canvas field lands — the canvas outlines these inputs in
    // the field's color so the form doubles as the legend. Mirrors
    // _applyScanField below.
    _scanFieldTarget(fieldKey) {
        const form = document.querySelector('#modal-body form');
        if (!form) return null;
        const row = document.querySelector('#bill-lines tr');
        if (fieldKey === 'date') return form.querySelector('[name="date"]');
        if (fieldKey === 'merchant') {
            return VendorQuickAdd.nameInput('bill-vendor') || (row && row.querySelector('.line-desc'));
        }
        if (fieldKey === 'total' || fieldKey === 'subtotal') return row && row.querySelector('.line-rate');
        if (fieldKey === 'tax') return form.querySelector('[name="notes"]');
        if (fieldKey === 'reference') return form.querySelector('[name="bill_number"]');
        return null;
    },

    // Box-to-fix canvas: apply one re-read field into the bill form.
    _applyScanField(fieldKey, value) {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const row = document.querySelector('#bill-lines tr');
        if (fieldKey === 'date') {
            form.querySelector('[name="date"]').value = value;
        } else if (fieldKey === 'merchant') {
            const match = VendorQuickAdd.prefill('bill-vendor', value, BillsPage._vendors);
            if (match) BillsPage.vendorSelected(match.id);
            const desc = row && row.querySelector('.line-desc');
            if (desc) desc.value = value;
        } else if (fieldKey === 'total' || fieldKey === 'subtotal') {
            const rate = row && row.querySelector('.line-rate');
            if (rate) rate.value = parseFloat(value).toFixed(2);
        } else if (fieldKey === 'reference') {
            const num = form.querySelector('[name="bill_number"]');
            if (num) num.value = value;
        } else if (fieldKey === 'tax') {
            const notes = form.querySelector('[name="notes"]');
            if (notes) {
                // Replace an earlier "Tax detected" line rather than stacking
                // one per re-read.
                const kept = notes.value.split('\n').filter(l => !/^Tax detected:/.test(l)).join('\n').replace(/\s*$/, '');
                notes.value = (kept ? kept + '\n' : '') + `Tax detected: $${value}`;
            }
        }
        BillsPage.recalc();
    },

    _applyScan(result) {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        if (result.date) form.querySelector('[name="date"]').value = result.date;
        // The vendor's invoice number; left blank, the server generates one.
        const num = form.querySelector('[name="bill_number"]');
        if (result.reference && num && !num.value) num.value = result.reference;

        const merchant = result.merchant && result.merchant.value;
        if (merchant) {
            const match = VendorQuickAdd.prefill('bill-vendor', merchant, BillsPage._vendors);
            if (match) {
                BillsPage.vendorSelected(match.id);
            } else {
                const statusEl = $('#scan-status');
                if (statusEl) {
                    statusEl.textContent = `Detected: ${merchant} — new vendor; it's added when you save (or pick one from the list).`;
                }
            }
        }

        const total = parseFloat(result.total || '0');
        if (total > 0) {
            const row = document.querySelector('#bill-lines tr');
            if (row) {
                const rateInput = row.querySelector('.line-rate');
                const descInput = row.querySelector('.line-desc');
                if (rateInput) {
                    // Bill rule (spec §6.4): grand total as the line — the
                    // amount owed includes tax; tax noted in Notes.
                    rateInput.value = total.toFixed(2);
                }
                if (descInput && merchant) descInput.value = merchant;
                BillsPage.recalc();
                if (result.tax_detected && result.tax) {
                    const notes = form.querySelector('[name="notes"]');
                    if (notes) {
                        const existing = notes.value ? notes.value.replace(/\s*$/, '') + '\n' : '';
                        notes.value = existing + `Tax detected: $${result.tax}`;
                    }
                }
            }
        }
    },

    recalc() {
        let subtotal = 0;
        $$('#bill-lines tr').forEach(row => {
            const amount = PurchaseLines.lineAmount(row);
            subtotal += amount;
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        subtotal = PurchaseLines.cents(subtotal);
        // the tax as the server works it: the rate on the subtotal, to the cent
        const tax = PurchaseLines.cents(subtotal * (BillsPage._taxRate || 0));
        const total = PurchaseLines.cents(subtotal + tax);
        const subEl = $('#bill-subtotal');
        if (subEl) subEl.textContent = formatCurrency(subtotal);
        const taxEl = $('#bill-tax');
        if (taxEl) taxEl.textContent = formatCurrency(tax);
        const totalEl = $('#bill-total');
        if (totalEl) totalEl.textContent = formatCurrency(total);
        return total;
    },

    // Split support (nonprofit): one line becomes the rule's shares, each
    // with quantity 1 and the share as its rate.
    lineAmount(row) {
        return (parseFloat(row.querySelector('.line-qty')?.value) || 0) * (parseFloat(row.querySelector('.line-rate')?.value) || 0);
    },
    splitApply(row, res) {
        const baseDesc = row.querySelector('.line-desc')?.value || '';
        let anchor = row;
        res.lines.forEach(ln => {
            const clone = row.cloneNode(true);
            clone.dataset.billline = BillsPage.lineCount++;
            row.querySelectorAll('select').forEach((sel, k) => { clone.querySelectorAll('select')[k].value = sel.value; });
            clone.querySelector('.line-desc').value = `${baseDesc} (${res.rule_name}: ${Nonprofit.label(ln.function) || ln.class_name || 'share'})`;
            clone.querySelector('.line-qty').value = 1;
            clone.querySelector('.line-rate').value = Number(ln.amount).toFixed(2);
            const fund = clone.querySelector('.line-function-fund'); if (fund) fund.value = ln.class_id || '';
            const fn = clone.querySelector('.line-function'); if (fn) fn.value = ln.function || '';
            anchor.insertAdjacentElement('afterend', clone);
            anchor = clone;
        });
        row.remove();
        BillsPage.recalc();
    },

    addLine(line = null) {
        const idx = BillsPage.lineCount++;
        $('#bill-lines').insertAdjacentHTML('beforeend', BillsPage.lineHtml(idx, line));
    },

    async save(e, id = null) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        let missing = null;
        $$('#bill-lines tr').forEach((row, i) => {
            const acct = row.querySelector('.line-account');
            const accountId = acct && !acct.disabled && acct.value ? parseInt(acct.value) : null;
            if (!missing && acct && !acct.disabled && !accountId && PurchaseLines.lineAmount(row) > 0) {
                missing = { n: i + 1, sel: acct };
            }
            lines.push({
                item_id: row.querySelector('.line-item')?.value ? parseInt(row.querySelector('.line-item').value) : null,
                account_id: accountId,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                class_id: Nonprofit.fundFromRow(row, 'line-function'),
                ...Nonprofit.linePayload(row, 'line-function'),
                is_billable: !!row.querySelector('.line-billable')?.checked,
                line_order: i,
            });
        });
        // A bill for $0.00 is refused (item picks used to leave the rate at
        // 0 and save one silently — W-M1).
        if (BillsPage.recalc() <= 0) {
            toast('Enter what the vendor charged: a quantity and rate on at least one line.', 'error');
            return;
        }
        if (missing) {
            toast(`Choose an account for line ${missing.n}: where should it be recorded?`, 'error');
            missing.sel.focus();
            return;
        }
        if (!(await ClassWarn.ok(form, { rows: '#bill-lines tr', cls: 'line-function' }))) return;
        try {
            const vendorId = await VendorQuickAdd.ensure('bill-vendor');
            const body = {
                vendor_id: vendorId,
                bill_number: form.bill_number.value.trim() || null,
                date: form.date.value,
                terms: form.terms.value,
                notes: form.notes.value || null,
                class_id: classIdFromForm(form),
                job_id: jobIdFromForm(form),
                ...currencyPayloadFromForm(form),
                lines,
            };
            if (id) {
                await API.put(`/bills/${id}`, body);
                toast('Bill updated');
            } else {
                const result = await API.post('/bills', body);
                await ScanHelper.attachAfterSave('bill', result.id);
                toast('Bill saved');
            }
            closeModal();
            App.navigate('#/bills');
        } catch (err) { toast(err.message, 'error'); }
    },

    async void(id) {
        if (!confirm('Void this bill?')) return;
        try {
            await API.post(`/bills/${id}/void`);
            toast('Bill voided');
            App.navigate('#/bills');
        } catch (err) { toast(err.message, 'error'); }
    },

    // `vendorId`: only that vendor's open bills, from the vendor's own page
    // (#223, NEW-21); the list says so and offers every vendor's.
    async showPayForm(vendorId = null) {
        const [vendors, bills, accounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            fetchAllPages('/bills?open_only=true'),
            API.get('/accounts?bank=1&active_only=true'),
        ]);
        // every unpaid or part-paid bill, not just the newest page (#191)
        const openBills = vendorId ? bills.filter(b => b.vendor_id == vendorId) : bills;
        const onlyVendor = vendorId ? (vendors.find(v => v.id == vendorId) || {}).name : null;

        const vendorOpts = vendors.map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');
        const acctOpts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');

        let billRows = openBills.map(b => `
            <tr>
                <td><input type="checkbox" class="pay-check" data-bill="${b.id}" data-balance="${b.balance_due}" aria-label="Pay bill ${escapeHtml(b.bill_number)}"></td>
                <td>${escapeHtml(b.bill_number)}</td>
                <td>${escapeHtml(b.vendor_name || '')}</td>
                <td>${formatDate(b.due_date)}</td>
                <td class="amount">${formatCurrency(b.balance_due)}</td>
                <td><input type="number" step="0.01" class="pay-amount" data-bill="${b.id}" data-vendor="${b.vendor_id}" data-vendor-name="${escapeHtml(b.vendor_name || '')}" value="0" style="width:80px;"></td>
            </tr>`).join('');

        if (!billRows) billRows = '<tr><td colspan="6" style="color:var(--text-muted);">No open bills</td></tr>';
        const onlyNote = onlyVendor
            ? `<p id="pay-only-vendor" style="margin:0 0 8px; font-size:12px; color:var(--text-muted);">${escapeHtml(onlyVendor)}'s open bills. <a href="javascript:void(0)" onclick="BillsPage.showPayForm()">Show every vendor's</a></p>`
            : '';

        openModal('Pay Bills', `
            <form onsubmit="BillsPage.savePay(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Pay From Account</label>
                        <select name="pay_from_account_id"><option value="">Select...</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Method</label>
                        <select name="method">
                            <option value="check">Check</option><option value="ach">ACH</option>
                            <option value="cash">Cash</option><option value="credit_card">Credit Card</option>
                        </select></div>
                    <div class="form-group"><label>Check #</label>
                        <input name="check_number"></div>
                </div>
                ${onlyNote}
                <div class="table-container" style="margin-top:12px;"><table>
                    <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Bill #</th><th scope="col">Vendor</th><th scope="col">Due</th>
                    <th scope="col" class="amount">Balance</th><th scope="col" class="amount">Payment</th></tr></thead>
                    <tbody>${billRows}</tbody>
                </table></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Pay Selected Bills</button>
                </div>
            </form>`);

        // Auto-fill payment amount on check
        $$('.pay-check').forEach(cb => {
            cb.addEventListener('change', () => {
                const billId = cb.dataset.bill;
                const amtInput = $(`.pay-amount[data-bill="${billId}"]`);
                amtInput.value = cb.checked ? cb.dataset.balance : '0';
            });
        });
    },

    async savePay(e) {
        e.preventDefault();
        const form = e.target;
        // One payment per vendor, as one check per vendor: a payment to one
        // vendor cannot pay another vendor's bill (the server refuses it).
        const byVendor = new Map();
        $$('.pay-amount').forEach(input => {
            const amt = parseFloat(input.value) || 0;
            if (amt > 0) {
                const vid = parseInt(input.dataset.vendor);
                if (!byVendor.has(vid)) byVendor.set(vid, { name: input.dataset.vendorName, total: 0, allocations: [] });
                const v = byVendor.get(vid);
                v.allocations.push({ bill_id: parseInt(input.dataset.bill), amount: amt });
                v.total += amt;
            }
        });
        if (byVendor.size === 0) { toast('Select bills to pay', 'error'); return; }
        const checkNumber = form.check_number.value || null;
        if (checkNumber && byVendor.size > 1) {
            toast('One check number cannot pay several vendors. Pay one vendor at a time, or leave Check # blank.', 'error');
            return;
        }
        // Blank Pay From pays from 1000 Checking (the server's default).
        const fromId = form.pay_from_account_id.value ? parseInt(form.pay_from_account_id.value) : null;
        const outgoing = [...byVendor.values()].reduce((sum, v) => sum + v.total, 0);
        if (!(await Overdraft.confirm(fromId, outgoing, '1000'))) return;

        const paid = [];
        try {
            for (const [vendorId, v] of byVendor) {
                await API.post('/bill-payments', {
                    vendor_id: vendorId,
                    date: form.date.value,
                    amount: Math.round(v.total * 100) / 100,
                    method: form.method.value,
                    check_number: checkNumber,
                    pay_from_account_id: fromId,
                    allocations: v.allocations,
                });
                paid.push(v.name);
            }
            toast(byVendor.size > 1 ? `Bills paid: ${byVendor.size} payments, one per vendor` : 'Bills paid');
            closeModal();
            App.navigate('#/bills');
        } catch (err) {
            // Each vendor's payment is its own record: say which went through.
            if (paid.length) {
                toast(`Paid ${paid.join(', ')}; the next payment was refused: ${err.message}`, 'error');
                closeModal();
                App.navigate('#/bills');
            } else {
                toast(err.message, 'error');
            }
        }
    },

    // From the bill's view: the payment is reversed and every bill it paid
    // is open again; the view comes back showing the new balance.
    async voidBillPayment(id, billId) {
        if (!confirm('Void this bill payment? A reversing entry is posted, and every bill it paid is open again.')) return;
        try {
            await API.post(`/bill-payments/${id}/void`);
            toast('Bill payment voided');
            closeModal();
            await App.navigate('#/bills');
            if (billId) BillsPage.view(billId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadAttachments(billId) {
        const el = $('#bill-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/bill/${billId}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="BillsPage.deleteAttachment(${a.id},${billId})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(billId) {
        const fileInput = $('#bill-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/bill/${billId}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            BillsPage.loadAttachments(billId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachId, billId) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachId}`);
            toast('Attachment deleted');
            BillsPage.loadAttachments(billId);
        } catch (err) { toast(err.message, 'error'); }
    },
};
