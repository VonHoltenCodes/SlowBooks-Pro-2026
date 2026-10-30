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
                    <button class="btn btn-primary" ${App.shortcutAttrs('B', 'Enter Bill')} onclick="BillsPage.showForm()">+ Enter Bill</button>
                    <button class="btn btn-secondary" onclick="BillsPage.showPayForm()">Pay Bills</button>
                    <button class="btn btn-secondary" onclick="BillsPage.showPrintChecks()" title="Print the checks Pay Bills wrote, in one go">Print Checks</button>
                    <button class="btn btn-secondary" data-write onclick="App.navigate('#/bill-entry')" title="A stack of bills, one after another">Quick Entry</button>
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
        const acctName = Object.fromEntries(accounts.map(a => [a.id, accountWords(a, null, ' ')]));
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
                ${PaymentTerms.viewHtml(bill)}
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
                ${bill.status !== 'void' ? `<button class="btn btn-secondary" data-write title="Keep this bill as a memorized transaction, to enter again on a schedule" onclick="MemorizedPage.memorizeBill(${bill.id})">Memorize</button>` : ''}
                <button class="btn btn-secondary" onclick="window.open('/api/bills/${bill.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/bills/${bill.id}/print-preview','_blank')">Print</button>
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/bills/${Number(id)}`);
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
                <td>${escapeHtml(how)}${p.coi_override ? ` <span class="compliance-flag" style="color:var(--text-danger);font-size:11px" title="${escapeHtml(p.coi_override_note || '')}">(COI override)</span>` : ''}</td>
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
                ${p.coi_override ? `<br><strong>COI override:</strong> ${escapeHtml(p.coi_override_note || 'Paid with an expired certificate of insurance')}` : ''}
            </div>
            ${rows ? `<div class="table-container"><table>
                <thead><tr><th scope="col">Bill #</th><th scope="col">Date</th><th scope="col" class="amount">Applied</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>` : ''}
            ${(p.discounts || []).length ? `<p style="font-size:12px; margin:8px 0;"><strong>Discounts taken:</strong> ${p.discounts.map(d =>
                `${formatCurrency(d.amount)} on bill ${escapeHtml(d.bill_number || String(d.bill_id))} (vendor credit ${escapeHtml(d.credit_number)})`).join('; ')}</p>` : ''}
            ${BillsPage._checkMemoHtml(p)}
            ${!p.is_voided && ahead > 0 ? `<p style="font-size:11px; color:var(--text-muted);">${formatCurrency(ahead)} paid ahead, not applied to a bill.</p>` : ''}
            <div class="form-actions">
                ${p.is_voided ? '' : `${BillsPage._printCheckButton(p, true)}<button data-destructive type="button" class="btn btn-danger" onclick="BillsPage.voidBillPayment(${p.id}, ${firstBill})">Void</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    // The check's memo line, editable before the check prints (#602): a
    // payment of one bill starts with that bill's memo, a payment of
    // several starts blank. Only a payment that prints a check shows it.
    _checkMemoHtml(p) {
        const byCheck = !p.method || p.method === 'check' || !!p.check_number;
        if (p.is_voided || !byCheck) return '';
        return `<div class="form-group" style="margin-bottom:12px;">
                <label for="bill-payment-memo">Check memo</label>
                <input id="bill-payment-memo" value="${escapeHtml(p.notes || '')}" ${typeof App !== 'undefined' && App.isLocked && App.isLocked() ? 'disabled' : ''}>
            </div>`;
    },

    // Save the memo as typed, then open the check with it on the memo line.
    async printPaymentCheck(id, url) {
        const memo = document.getElementById('bill-payment-memo');
        const tab = window.open('', '_blank');
        try {
            if (memo) await API.put(`/bill-payments/${id}/memo`, { notes: memo.value });
            if (tab) tab.location = url; else window.open(url, '_blank');
        } catch (err) {
            if (tab) tab.close();
            toast(err.message, 'error');
        }
    },

    // A payment by check (or one that carries a check number) prints its
    // check; ACH, cash and card payments have none to print.
    // `withMemo`: the payment's own view, which saves its memo field first.
    _printCheckButton(p, withMemo = false) {
        const byCheck = !p.method || p.method === 'check' || !!p.check_number;
        if (p.is_voided || !byCheck) return '';
        const url = `/api/checks/print?bill_payment_id=${p.id}`;
        // a sign-in that cannot save the memo prints the check as it stands
        // (the read-only role: the memo field is locked for it below)
        const saves = withMemo && !(typeof App !== 'undefined' && App.isLocked && App.isLocked());
        const open = saves ? `BillsPage.printPaymentCheck(${p.id}, '${url}')` : `window.open('${url}','_blank')`;
        return `<button class="btn btn-sm btn-secondary" onclick="${open}">Print Check</button> `;
    },

    // ==== Print Checks: the checks Pay Bills wrote, in one go (#603) ====

    // The numbers the checked rows take: the starting number for the first,
    // each next one a step up; an unchecked row has none. Digits only, as a
    // check number is; anything else leaves every row blank to be typed.
    _batchNumbers(start, checked) {
        const first = /^\d+$/.test(String(start || '').trim()) ? parseInt(start, 10) : null;
        let n = first;
        return checked.map(on => {
            if (!on || n === null) return '';
            return String(n++);
        });
    },

    // `ticked` is the set of payment ids ticked to start with: the checks
    // Pay Bills just wrote when the list opens after it, none when it is
    // opened on its own. Every waiting check is listed either way; a
    // company that paid by check for years without typing numbers is not
    // handed all of them ticked, to renumber in one click.
    _printChecksHtml(accounts, accountId, list, ticked = null) {
        const opts = accounts.map(a =>
            `<option value="${a.id}"${a.id == accountId ? ' selected' : ''}>${escapeHtml(a.name)}</option>`).join('');
        const on = c => !!ticked && ticked.has(c.bill_payment_id);
        const rows = list.checks.map(c => `
            <tr class="batch-check-row" data-id="${c.bill_payment_id}">
                <td><input type="checkbox" class="batch-check"${on(c) ? ' checked' : ''} aria-label="Print the check to ${escapeHtml(c.payee)}" onchange="BillsPage.renumberChecks()"></td>
                <td><input class="batch-number" inputmode="numeric" style="width:90px;" aria-label="Check number for ${escapeHtml(c.payee)}"></td>
                <td>${escapeHtml(c.payee)}</td>
                <td>${formatDate(c.date)}</td>
                <td class="amount">${formatCurrency(c.amount)}</td>
                <td>${escapeHtml(c.memo || '')}</td>
            </tr>`).join('');
        const body = list.checks.length
            ? `<div class="table-container"><table>
                    <thead><tr><th scope="col" style="width:30px;"><input type="checkbox" id="batch-select-all"${list.checks.every(on) ? ' checked' : ''} aria-label="Select all" title="Select all" onchange="BillsPage.toggleAllChecks()"></th><th scope="col">Check #</th><th scope="col">Pay to</th>
                    <th scope="col">Date</th><th scope="col" class="amount">Amount</th><th scope="col">Memo</th></tr></thead>
                    <tbody>${rows}</tbody></table></div>`
            : '<p style="color:var(--text-muted);">No checks are waiting to be printed on this account. Pay Bills by check with Check # left blank to queue them here.</p>';
        return `
            <div class="form-grid">
                <div class="form-group"><label for="batch-account">Bank account</label>
                    <select id="batch-account" onchange="BillsPage.showPrintChecks(this.value)">${opts}</select></div>
                <div class="form-group"><label for="batch-start">Starting check #</label>
                    <input id="batch-start" inputmode="numeric" value="${escapeHtml(list.next_check_number)}" oninput="BillsPage.renumberChecks()"></div>
            </div>
            ${body}
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
                ${list.checks.length ? '<button type="button" id="batch-print" class="btn btn-primary" onclick="BillsPage.printSelectedChecks()">Print Checks</button>' : ''}
            </div>`;
    },

    // Opens the list for a bank account (the first when none is named),
    // with the payments in `tickIds` ticked (Pay Bills passes the ones it
    // just wrote) and the rest left for the person to tick.
    async showPrintChecks(accountId = null, tickIds = null) {
        try {
            const accounts = await API.get('/accounts?bank=1&active_only=true');
            const banks = accounts.filter(a => a.bank_kind === 'bank');
            const id = accountId || (banks[0] && banks[0].id);
            if (!id) { toast('There is no bank account to print checks on', 'error'); return; }
            const list = await API.get(`/checks/unprinted?bank_account_id=${id}`);
            const ticked = new Set((tickIds || []).map(x => parseInt(x)));
            openModal('Print Checks', BillsPage._printChecksHtml(banks, id, list, ticked));
            BillsPage.renumberChecks();
        } catch (err) { toast(err.message, 'error'); }
    },

    // The Select all box ticks or clears every row; the numbers follow.
    toggleAllChecks() {
        const all = document.getElementById('batch-select-all');
        document.querySelectorAll('.batch-check').forEach(cb => { cb.checked = !!all && all.checked; });
        BillsPage.renumberChecks();
    },

    // Fills the numbers of the checked rows from the starting number.
    renumberChecks() {
        const rows = [...document.querySelectorAll('.batch-check-row')];
        const checked = rows.map(r => r.querySelector('.batch-check').checked);
        const numbers = BillsPage._batchNumbers(document.getElementById('batch-start').value, checked);
        rows.forEach((r, i) => { r.querySelector('.batch-number').value = numbers[i]; });
        const all = document.getElementById('batch-select-all');
        if (all) all.checked = rows.length > 0 && checked.every(Boolean);
    },

    // Prints the checked rows as one PDF, in the order listed, each with the
    // number in its box (adjust any before printing); the server numbers
    // them, so they leave the list. A number the account already has (#481)
    // asks the same keep-or-edit question a single check does.
    async printSelectedChecks() {
        const checks = [...document.querySelectorAll('.batch-check-row')]
            .filter(r => r.querySelector('.batch-check').checked)
            .map(r => ({ bill_payment_id: parseInt(r.dataset.id), check_number: r.querySelector('.batch-number').value.trim() }));
        if (!checks.length) { toast('Check the checks to print', 'error'); return; }
        if (checks.some(c => !/^\d+$/.test(c.check_number))) { toast('Every check to print needs a number (digits only)', 'error'); return; }
        const button = document.getElementById('batch-print');
        if (button) button.disabled = true;
        const accountId = parseInt(document.getElementById('batch-account').value);
        const send = async (q) => {
            // two plain literals so the wiring audit reads the method
            const res = await fetch(q ? '/api/checks/print-batch?allow_duplicate_check_number=true' : '/api/checks/print-batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Slowbooks-Desktop': '1' },
                body: JSON.stringify({ bank_account_id: accountId, checks }),
            });
            if (!res.ok) {
                let body = null;
                try { body = await res.json(); } catch (e) { body = null; }
                const detail = body && typeof body === 'object' ? body.detail : undefined;
                const blank = detail === undefined || detail === null || detail === '';
                const err = new Error(blank ? `Could not print the checks (HTTP ${res.status})` : API.errorMessage(detail, 'Could not print the checks'));
                err.status = res.status;
                err.detail = detail;
                throw err;
            }
            return res.blob();
        };
        try {
            const blob = await CheckNumberWarn.send(send);
            if (!blob) { if (button) button.disabled = false; return; }
            const desktop = window.SlowbooksDesktop;
            if (!(desktop && await desktop.saveFile(blob, 'Checks.pdf'))) {
                const url = URL.createObjectURL(blob);
                window.open(url, '_blank');
                setTimeout(() => URL.revokeObjectURL(url), 60000);
            }
            toast(checks.length === 1 ? 'Check printed' : `${checks.length} checks printed`);
            // the printed checks have their numbers and are off the list
            await BillsPage.showPrintChecks(accountId);
        } catch (err) {
            toast(err.message, 'error');
            if (button) button.disabled = false;
        }
    },

    _items: [],
    _vendors: [],
    _accounts: [],
    _defaultExpenseAccountId: null,
    lineCount: 0,

    // Under Terms: the due date the terms give the bill, the discount they
    // offer, or the sentence saying they aren't understood (#664), from the
    // server's one parser.
    async termsHint() {
        const form = $('#bill-form');
        const hint = $('#bill-terms-hint');
        if (!form || !hint) return;
        const d = await PaymentTerms.dates(form.date.value, form.terms.value, 'bill');
        if (!d) { hint.innerHTML = ''; return; }
        const extra = PaymentTerms.hintHtml(d);
        hint.innerHTML = `Due ${escapeHtml(formatDate(d.due_date))}${extra ? ` &middot; ${extra}` : ''}`;
    },

    // A vendor brings its terms (Enter Bill always said Net 30, so a Net 15
    // supplier's bill fell due two weeks late — F11) and its default
    // expense account, which fills every line not already pointed somewhere.
    vendorSelected(vendorId) {
        BillsPage.showVendorSummary(vendorId);
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = BillsPage._vendors.find(v => v.id == vendorId);
        BillsPage._defaultExpenseAccountId = (vendor && vendor.default_expense_account_id) || null;
        // the open form's Terms box: Enter Bill's, or a memorized bill's (#605)
        const terms = document.querySelector('#modal-body form [name="terms"]');
        if (vendor && vendor.terms && terms) {
            PaymentTerms.choose(terms, vendor.terms);
            BillsPage.termsHint();
        }
        if (BillsPage._defaultExpenseAccountId) {
            $$('#bill-lines .line-account').forEach(sel => {
                if (!sel.disabled && !sel.value) sel.value = BillsPage._defaultExpenseAccountId;
            });
        }
    },

    // The panel beside the form (#601): what the vendor is owed, its last
    // bills and payments, and its notes. Loaded in the background, so the
    // form is never held up; a late answer for a vendor no longer picked is
    // dropped, and a failure just leaves the panel empty.
    async showVendorSummary(vendorId) {
        const box = document.getElementById('bill-vendor-summary');
        if (!box) return;
        const id = vendorId && vendorId !== VendorQuickAdd.NEW ? vendorId : null;
        BillsPage._summaryFor = id;
        box.hidden = true;
        box.innerHTML = '';
        if (!id) return;
        let s;
        try { s = await API.get(`/vendors/${id}/summary`); } catch (e) { return; }
        if (BillsPage._summaryFor !== id || !box.isConnected) return;
        const rows = (list, cells) => list.length
            ? `<table class="bill-vendor-summary__table">${list.map(r => `<tr>${cells(r)}</tr>`).join('')}</table>`
            : '<p class="bill-vendor-summary__none">None yet.</p>';
        box.innerHTML = `
            <h3>${escapeHtml(s.name)}</h3>
            <p class="bill-vendor-summary__owed">Owed: <strong>${formatCurrency(s.balance)}</strong>${s.is_1099_vendor ? ' <span class="badge">1099</span>' : ''}</p>
            <h4>Recent bills</h4>
            ${rows(s.bills, b => `<td>${escapeHtml(b.date)}</td><td>${escapeHtml(b.bill_number || '')}</td><td class="amount">${formatCurrency(b.total)}</td><td>${escapeHtml(b.status || '')}${Number(b.balance_due) > 0 && b.status !== 'draft' ? ` (${formatCurrency(b.balance_due)} open)` : ''}</td>`)}
            <h4>Recent payments</h4>
            ${rows(s.payments, p => `<td>${escapeHtml(p.date)}</td><td class="amount">${formatCurrency(p.amount)}</td><td>${escapeHtml([p.method, p.check_number ? '#' + p.check_number : ''].filter(Boolean).join(' '))}</td>`)}
            ${s.notes ? `<h4>Notes</h4><p class="bill-vendor-summary__notes">${escapeHtml(s.notes)}</p>` : ''}
            <p><a href="#/vendors/${s.vendor_id}">Open ${escapeHtml(s.name)}'s page</a></p>`;
        box.hidden = false;
    },
    _summaryFor: null,

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
        await CostTypes.load();
        await Nonprofit.loadFunds();

        BillsPage._vendors = vendors;
        const terms = bill && bill.terms ? bill.terms : 'Net 30';
        await PaymentTerms.load();

        openModal(bill ? `Edit Bill ${bill.bill_number}` : 'Enter Bill', `
            <div class="bill-with-summary"><div class="bill-with-summary__form">
            <form id="bill-form" aria-labelledby="modal-title" data-skip-to="#bill-lines" data-skip-text="Skip to the line items" onsubmit="BillsPage.save(event, ${bill ? bill.id : 'null'})">
                ${bill ? '' : ScanHelper.scanRowHtml()}
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'bill-vendor', onchange: 'BillsPage.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label>Bill Number</label>
                        <input name="bill_number" placeholder="from the receipt, or left blank" value="${bill ? escapeHtml(bill.bill_number || '') : ''}"></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${bill ? bill.date : todayISO()}" onchange="BillsPage.termsHint()"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" title="The vendor's terms; the due date follows from them" onchange="BillsPage.termsHint()">
                            ${PaymentTerms.optionsHtml(terms)}
                        </select>
                        <div class="hint" id="bill-terms-hint"></div></div>
                    ${classGroup}${jobGroup}
                    ${currencyFormGroupsHtml(bill ? bill.currency : undefined, bill ? bill.exchange_rate : undefined)}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <div class="table-container table-container--scroll"><table class="line-items-table bill-lines-table">
                    <thead><tr><th scope="col">Item</th><th scope="col" title="Where the line is recorded">Account</th><th scope="col">Description</th>${CostCodes.headHtml()}${CostTypes.headHtml()}${Nonprofit.headHtml()}<th scope="col" title="Billable to the job's customer">Bill?</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" class="col-amount">Amount</th></tr></thead>
                    <tbody id="bill-lines">${bill ? '' : BillsPage.lineHtml(0)}</tbody>
                </table></div>
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
            </form></div>
            <aside id="bill-vendor-summary" class="bill-vendor-summary" aria-label="Vendor summary" aria-live="polite" hidden></aside></div>`, { wide: true });
        BillsPage._summaryFor = null;
        typeof LineOrder !== 'undefined' && LineOrder.attach('#bill-lines', { add: () => BillsPage.addLine(), changed: () => BillsPage.recalc() });
        BillsPage.termsHint();
        if (bill) {
            const vendorSel = document.getElementById('bill-vendor');
            if (vendorSel) vendorSel.value = bill.vendor_id;
            BillsPage.showVendorSummary(bill.vendor_id);
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
    // Qty and Rate start blank: a line is an amount alone until one of them
    // is typed (#391). A stored line of quantity 1 whose rate is its amount
    // is drawn that way too; saving it again stores the same numbers.
    lineHtml(idx, line = null) {
        const l = line || {};
        const item = l.item_id ? BillsPage._items.find(i => i.id == l.item_id) : null;
        const stock = !!(item && item.track_inventory);
        // A stored 1 x amount line is drawn as an amount alone, Qty and Rate
        // blank — but not a stock item: it is received as that quantity 1, so
        // the 1 is shown rather than hidden by a blank Qty box (reviewer, #401).
        const amountOnly = !!line && !stock && Number(l.quantity) === 1 && Number(l.rate) === Number(l.amount);
        const qty = line && !amountOnly ? Number(l.quantity) : '';
        const rate = line && !amountOnly ? Number(l.rate) : '';
        const amount = line ? Number(l.amount).toFixed(2) : '';
        const acctOpts = PurchaseAccounts.options(BillsPage._accounts, stock ? null : (l.account_id || BillsPage._defaultExpenseAccountId));
        return `<tr data-billline="${idx}" data-po-line="${l.purchase_order_line_id || ''}">
                <td><select class="line-item" onchange="BillsPage.itemSelected(this)">${ItemQuickAdd.options(BillsPage._items, l.item_id)}</select></td>
                <td>${AccountQuickAdd.picker(`<select class="line-account"${stock ? ' disabled title="Stock items are recorded in Inventory"' : ''}><option value="">Choose...</option>${acctOpts}</select>`, 'bill', { disabled: stock })}</td>
                <td><input class="line-desc" value="${escapeHtml(l.description || '')}"></td>
                ${CostCodes.cellHtml('line-cost-code', l.cost_code_id)}${CostTypes.cellHtml('line-cost-type', l.cost_type)}${Nonprofit.cellHtml('line-function', l.function, l.class_id)}
                <td style="text-align:center;"><input type="checkbox" class="line-billable" title="Billable"${l.is_billable ? ' checked' : ''}></td>
                <td><input class="line-qty" type="number" step="0.0001" value="${qty}" oninput="BillsPage.qtyChanged(this)"></td>
                <td><input class="line-rate money" type="number" step="0.0001" value="${rate}" oninput="BillsPage.recalc()"></td>
                <td class="col-amount line-amount"><input class="line-amt money" type="number" step="0.01" value="${amount}" title="The line's amount; type a quantity and rate instead to price it" oninput="BillsPage.recalc()" onchange="BillsPage.amtChanged(this)"></td>
            </tr>`;
    },

    // Picking an item fills what it costs and where it is recorded; a stock
    // item is recorded in Inventory, so its account is not a choice.
    itemSelected(select) {
        if (ItemQuickAdd.selected(select, BillsPage._items, () => BillsPage.itemSelected(select), 'purchase')) return;
        const row = select.closest('tr');
        const item = BillsPage._items.find(i => i.id == select.value);
        const acct = row.querySelector('.line-account');
        if (acct) {
            acct.disabled = !!(item && item.track_inventory);
            acct.title = acct.disabled ? 'Stock items are recorded in Inventory' : '';
            // a stock line's account is not a choice, so nothing to add either
            const add = row.querySelector('.aqa-add');
            if (add) add.disabled = acct.disabled;
            if (acct.disabled && acct._aqaPanel) AccountQuickAdd.cancel(acct._aqaPanel);
            if (acct.disabled) acct.value = '';
            else if (item && item.expense_account_id) acct.value = item.expense_account_id;
            else if (!acct.value && BillsPage._defaultExpenseAccountId) acct.value = BillsPage._defaultExpenseAccountId;
        }
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            // an item with no cost or price leaves the line an amount alone
            const price = PurchaseLines.price(item);
            row.querySelector('.line-rate').value = Number(price) ? price : '';
            // A stock item is received as a quantity, so show the 1 it starts
            // at rather than a blank box that is still received as 1 (#401
            // review). An amount typed next prices it at 1 x amount.
            if (item.track_inventory) {
                const qtyBox = row.querySelector('.line-qty');
                if (qtyBox && qtyBox.value === '') qtyBox.value = 1;
            }
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
            const amount = BillsPage.lineAmount(row);
            subtotal += amount;
            // a priced line's Amount box shows quantity x rate and takes no
            // typing; one with neither is the box itself
            const amountInput = row.querySelector('.line-amt');
            if (amountInput) {
                const priced = BillsPage.priced(row);
                amountInput.readOnly = priced;
                if (priced) amountInput.value = amount.toFixed(2);
            }
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

    // A line is priced once it has a rate; otherwise its Amount box is the
    // line (#391). A quantity alone does not price it — typing a quantity on
    // an amount-alone line seeds the rate from the amount (see qtyChanged),
    // so the quantity takes effect through the rate rather than multiplying a
    // blank rate to zero (reviewer, #401).
    priced(row) {
        return !!(row.querySelector('.line-rate')?.value);
    },
    // Typing a quantity on an amount-alone line (no rate yet) seeds the rate
    // from the amount, so the line prices at quantity x amount instead of
    // zeroing: the amount typed first is read as the per-unit cost. Clearing
    // the quantity undoes the seed and the amount alone comes back. A rate the
    // user typed is left as it is.
    qtyChanged(input) {
        const row = input.closest('tr');
        const rateBox = row.querySelector('.line-rate');
        const amtBox = row.querySelector('.line-amt');
        if (input.value !== '') {
            if (rateBox && !rateBox.value && amtBox && amtBox.value) {
                rateBox.value = amtBox.value;
                rateBox.dataset.seeded = 'qty';
            }
        } else if (rateBox && rateBox.dataset.seeded) {
            // Restore the amount alone: the typed line total when the amount
            // seeded the rate, else the per-unit rate the quantity seeded.
            amtBox.value =
                rateBox.dataset.seeded === 'amt'
                    ? rateBox.dataset.lineTotal || rateBox.value
                    : rateBox.value;
            rateBox.value = '';
            delete rateBox.dataset.seeded;
            delete rateBox.dataset.lineTotal;
        }
        BillsPage.recalc();
    },
    // Typing an amount on a line that already has a quantity (and no rate yet)
    // reads the amount as the line total for those units, so the rate is
    // amount / quantity — Qty 3 then Amount 30 gives Rate 10 and posts 3 x 10.
    // Without this the line stayed unpriced and posted 1 x 30, dropping the
    // quantity the box still showed (reviewer, #401). The typed total is kept
    // so clearing the quantity brings it back whole. A line with no quantity
    // is still the amount alone.
    // It runs on change (blur, Tab, Enter), not on each keystroke: seeding on
    // input read the first digit typed as the whole amount, priced the line
    // and made the box read-only, so "30" with Qty 3 came out 3 x 1 and
    // "125.50" on a fresh stock line (Qty 1) came out 1.00 (reviewer, #401).
    amtChanged(input) {
        const row = input.closest('tr');
        const rateBox = row.querySelector('.line-rate');
        const qtyBox = row.querySelector('.line-qty');
        const qty = qtyBox && qtyBox.value !== '' ? parseFloat(qtyBox.value) : 0;
        if (input.value !== '' && qty > 0 && rateBox && !rateBox.value) {
            rateBox.dataset.lineTotal = input.value;
            rateBox.value = Number((parseFloat(input.value) / qty).toFixed(4));
            rateBox.dataset.seeded = 'amt';
        }
        BillsPage.recalc();
    },
    // What the line comes to, rounded to the cent as the server rounds: a
    // priced line's quantity (blank is 1) x rate, else the amount typed.
    // Also the split support (nonprofit): one line becomes the rule's
    // shares, each with quantity 1 and the share as its rate.
    lineAmount(row) {
        if (!BillsPage.priced(row)) return PurchaseLines.cents(parseFloat(row.querySelector('.line-amt')?.value) || 0);
        const qtyBox = row.querySelector('.line-qty');
        const qty = qtyBox && qtyBox.value !== '' ? parseFloat(qtyBox.value) || 0 : 1;
        return PurchaseLines.cents(qty * (parseFloat(row.querySelector('.line-rate')?.value) || 0));
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

    // The grid's lines as the API takes them, and the first line with an
    // amount but no account (`missing`), for Save Bill and for a memorized
    // bill's form (#605), which draws the same grid.
    collectLines() {
        // an amount still being typed has had no change event yet: settle it
        // as leaving the box would, so its quantity is not dropped
        const active = document.activeElement;
        if (active && active.classList && active.classList.contains('line-amt') && !active.readOnly) {
            BillsPage.amtChanged(active);
        }
        const lines = [];
        let missing = null;
        $$('#bill-lines tr').forEach((row, i) => {
            const acct = row.querySelector('.line-account');
            const accountId = acct && !acct.disabled && acct.value ? parseInt(acct.value) : null;
            if (!missing && acct && !acct.disabled && !accountId && BillsPage.lineAmount(row) > 0) {
                missing = { n: i + 1, sel: acct };
            }
            // a priced line sends its quantity and rate; one with neither
            // sends its amount alone and the server stores it as 1 x amount
            const priced = BillsPage.priced(row)
                ? { quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1, rate: parseFloat(row.querySelector('.line-rate')?.value) || 0 }
                : { amount: parseFloat(row.querySelector('.line-amt')?.value) || 0 };
            lines.push({
                // a converted bill's line keeps its commitment link (#553)
                purchase_order_line_id: row.dataset.poLine ? parseInt(row.dataset.poLine) : null,
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                account_id: accountId,
                description: row.querySelector('.line-desc')?.value || '',
                ...priced,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                cost_type: CostTypes.fromRow(row, 'line-cost-type'),
                class_id: Nonprofit.fundFromRow(row, 'line-function'),
                ...Nonprofit.linePayload(row, 'line-function'),
                is_billable: !!row.querySelector('.line-billable')?.checked,
                line_order: i,
            });
        });
        return { lines, missing };
    },

    async save(e, id = null) {
        e.preventDefault();
        const form = e.target;
        const { lines, missing } = BillsPage.collectLines();
        // A bill for $0.00 is refused (item picks used to leave the rate at
        // 0 and save one silently — W-M1).
        if (BillsPage.recalc() <= 0) {
            toast('Enter what the vendor charged: an amount, or a quantity and rate, on at least one line.', 'error');
            return;
        }
        if (missing) {
            toast(`Choose an account for line ${missing.n}: where should it be recorded?`, 'error');
            missing.sel.focus();
            return;
        }
        if (!(await TxnWarn.ok(form, { editing: !!id }))) return;
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
                const saved = await API.put(`/bills/${id}`, body);
                toastPosted('Bill updated');
                // stock already sold that the edit took below zero (W-67): posted, and said
                for (const w of (saved && saved.warnings) || []) toast(w, 'warning');
            } else {
                const result = await API.post('/bills', body);
                await ScanHelper.attachAfterSave('bill', result.id);
                toastPosted('Bill saved');
            }
            closeModal();
            App.navigate('#/bills');
        } catch (err) { toast(err.message, 'error'); }
    },

    async void(id) {
        if (!confirm('Void this bill?')) return;
        try {
            const voided = await API.post(`/bills/${id}/void`);
            toast('Bill voided');
            // stock already sold that the void took below zero (W-67): posted, and said
            for (const w of (voided && voided.warnings) || []) toast(w, 'warning');
            App.navigate('#/bills');
        } catch (err) { toast(err.message, 'error'); }
    },

    // A Sub's missing W-9 or expired/missing COI, as a compact flag (#553).
    // A missing W-9 or COI only warns; an expired COI asks to be overridden at
    // Pay (savePay).
    LIEN_WAIVERS: { not_required: 'Not required', pending: 'Pending', conditional: 'Conditional', unconditional: 'Unconditional' },

    complianceFlagHtml(c) {
        if (!c || !c.is_sub) return '';
        const bits = [];
        if (!c.w9_on_file) bits.push('W-9 missing');
        if (c.coi_expired) bits.push(c.coi_expires ? 'COI expired' : 'COI missing');
        if (!bits.length) return '';
        return ` <span class="compliance-flag" style="color:var(--text-danger);font-size:11px">(${bits.join(', ')})</span>`;
    },

    lienWaiverSelectHtml(b) {
        const cur = b.lien_waiver_status || '';
        const opts = ['<option value="">—</option>'].concat(
            Object.entries(BillsPage.LIEN_WAIVERS).map(([k, v]) =>
                `<option value="${k}" ${cur === k ? 'selected' : ''}>${v}</option>`)).join('');
        return `<select class="lien-waiver" data-write data-bill="${b.id}" onchange="BillsPage.saveLienWaiver(${b.id}, this.value)" aria-label="Lien waiver for bill ${escapeHtml(b.bill_number)}">${opts}</select>`;
    },

    async saveLienWaiver(billId, value) {
        try {
            await API.put(`/bills/${billId}`, { lien_waiver_status: value || null });
            toast('Lien waiver status saved');
        } catch (err) { toast(err.message, 'error'); }
    },

    // `vendorId`: only that vendor's open bills, from the vendor's own page
    // (#223, NEW-21); the list says so and offers every vendor's.
    async showPayForm(vendorId = null) {
        BillsPage._payKey = null; // a new form, a new key (#292)
        const [vendors, bills, accounts, allAccounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            fetchAllPages('/bills?open_only=true'),
            API.get('/accounts?bank=1&active_only=true'),
            // where an early-payment discount we take posts (#664)
            API.get('/accounts?active_only=true'),
        ]);
        BillsPage._payBills = bills;
        // every unpaid or part-paid bill, not just the newest page (#191)
        const openBills = vendorId ? bills.filter(b => b.vendor_id == vendorId) : bills;
        const onlyVendor = vendorId ? (vendors.find(v => v.id == vendorId) || {}).name : null;

        const vendorOpts = vendors.map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');
        const acctOpts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');

        // Construction shows a Sub's compliance flags and a lien-waiver select
        // (#553); an expired COI asks for an override on Pay. Other companies keep
        // today's six columns.
        const con = Terms.isConstruction();
        let billRows = openBills.map(b => `
            <tr>
                <td><input type="checkbox" class="pay-check" data-bill="${b.id}" data-balance="${b.balance_due}" aria-label="Pay bill ${escapeHtml(b.bill_number)}"></td>
                <td>${escapeHtml(b.bill_number)}${con ? BillsPage.complianceFlagHtml(b.vendor_compliance) : ''}</td>
                <td>${escapeHtml(b.vendor_name || '')}</td>
                <td>${formatDate(b.due_date)}</td>
                <td class="amount">${formatCurrency(b.balance_due)}</td>
                <td>${b.discount_date ? formatDate(b.discount_date) : ''}</td>
                <td><input type="number" step="0.01" min="0" class="pay-discount money" data-bill="${b.id}" value="" aria-label="Discount on bill ${escapeHtml(b.bill_number)}" oninput="BillsPage.payDiscountChanged()" style="width:70px;"></td>
                <td><input type="number" step="0.01" class="pay-amount money" data-bill="${b.id}" data-vendor="${b.vendor_id}" data-vendor-name="${escapeHtml(b.vendor_name || '')}" data-coi-expires="${con && b.vendor_compliance && b.vendor_compliance.is_sub && b.vendor_compliance.coi_expires ? b.vendor_compliance.coi_expires : ''}" value="0" style="width:80px;"></td>
                ${con ? `<td>${BillsPage.lienWaiverSelectHtml(b)}</td>` : ''}
            </tr>`).join('');

        if (!billRows) billRows = `<tr><td colspan="${con ? 9 : 8}" style="color:var(--text-muted);">No open bills</td></tr>`;
        const onlyNote = onlyVendor
            ? `<p id="pay-only-vendor" style="margin:0 0 8px; font-size:12px; color:var(--text-muted);">${escapeHtml(onlyVendor)}'s open bills. <a href="javascript:void(0)" onclick="BillsPage.showPayForm()">Show every vendor's</a></p>`
            : '';

        openModal('Pay Bills', `
            <form onsubmit="BillsPage.savePay(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Pay From Account</label>
                        <select name="pay_from_account_id"><option value="">Select...</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}" onchange="BillsPage.suggestPayDiscounts()"></div>
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
                    <th scope="col" class="amount">Balance</th><th scope="col" title="The last day the vendor's early-payment discount can be taken">Disc. Date</th><th scope="col" class="amount">Discount</th><th scope="col" class="amount">Payment</th>${con ? '<th scope="col">Lien waiver</th>' : ''}</tr></thead>
                    <tbody>${billRows}</tbody>
                </table></div>
                <div class="form-group" id="pay-discount-account-row" hidden style="margin-top:8px; max-width:360px;">
                    <label for="pay-discount-account">Discount account</label>
                    <select id="pay-discount-account" data-no-search>${PaymentTerms.discountAccountOptions(allAccounts, 'purchases')}</select>
                    <div class="hint">Where the discounts we take post, as a vendor credit applied to each bill.</div></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Pay Selected Bills</button>
                </div>
            </form>`);

        // Auto-fill payment amount on check: the balance less the
        // discount the vendor's terms offer while its date has not passed
        $$('.pay-check').forEach(cb => {
            cb.addEventListener('change', () => BillsPage.fillPayRow(cb));
        });
    },

    // One ticked row: its discount suggested from the bill's terms and the
    // payment date (#664), and the payment the balance less it.
    fillPayRow(cb) {
        const billId = cb.dataset.bill;
        const amtInput = $(`.pay-amount[data-bill="${billId}"]`);
        const discInput = $(`.pay-discount[data-bill="${billId}"]`);
        const bill = (BillsPage._payBills || []).find(b => String(b.id) === String(billId));
        const payDate = $('#modal-body form [name="date"]')?.value;
        const disc = cb.checked && bill ? PaymentTerms.suggestedCents(bill, payDate) : 0;
        if (discInput) discInput.value = disc > 0 ? (disc / 100).toFixed(2) : '';
        const balance = Math.round((parseFloat(cb.dataset.balance) || 0) * 100);
        amtInput.value = cb.checked ? ((balance - disc) / 100).toFixed(2) : '0';
        BillsPage.payDiscountChanged();
    },

    // A new payment date: the ticked rows' discounts are suggested again.
    suggestPayDiscounts() {
        $$('.pay-check').forEach(cb => { if (cb.checked) BillsPage.fillPayRow(cb); });
    },

    // The discount account shows while a discount is taken.
    payDiscountChanged() {
        const row = $('#pay-discount-account-row');
        if (row) row.hidden = ![...$$('.pay-discount')].some(i => (parseFloat(i.value) || 0) > 0);
    },

    async savePay(e) {
        e.preventDefault();
        const form = e.target;
        // One payment per vendor, as one check per vendor: a payment to one
        // vendor cannot pay another vendor's bill (the server refuses it).
        const byVendor = new Map();
        $$('.pay-amount').forEach(input => {
            const amt = parseFloat(input.value) || 0;
            const discInput = $(`.pay-discount[data-bill="${input.dataset.bill}"]`);
            const discount = Math.round((parseFloat(discInput && discInput.value) || 0) * 100) / 100;
            if (amt > 0 || discount > 0) {
                const vid = parseInt(input.dataset.vendor);
                if (!byVendor.has(vid)) byVendor.set(vid, { name: input.dataset.vendorName, total: 0, allocations: [] });
                const v = byVendor.get(vid);
                v.coiExpires = input.dataset.coiExpires || null;
                const row = { bill_id: parseInt(input.dataset.bill), amount: amt };
                if (discount > 0) { row.discount = discount; v.discounted = true; }
                v.allocations.push(row);
                v.total += amt;
            }
        });
        if (byVendor.size === 0) { toast('Select bills to pay', 'error'); return; }
        // a discount comes with a payment on its bill (#664)
        const unpaid = [...byVendor.values()].flatMap(v => v.allocations).find(a => a.discount && !(a.amount > 0));
        if (unpaid) {
            const bill = (BillsPage._payBills || []).find(b => b.id === unpaid.bill_id);
            toast(`Bill ${bill ? bill.bill_number : unpaid.bill_id} has a discount but no payment. Pay some of it, or clear its discount.`, 'error');
            return;
        }
        // the discounts post to the account picked under the list (#664)
        let discountAccountId = null;
        if ([...byVendor.values()].some(v => v.discounted)) {
            discountAccountId = parseInt($('#pay-discount-account')?.value) || null;
            if (!discountAccountId) { toast('Pick the account the discount posts to.', 'error'); return; }
            PaymentTerms.rememberDiscountAccount('purchases', discountAccountId);
        }
        const checkNumber = form.check_number.value || null;
        if (checkNumber && byVendor.size > 1) {
            toast('One check number cannot pay several vendors. Pay one vendor at a time, or leave Check # blank.', 'error');
            return;
        }
        // Blank Pay From pays from 1000 Checking (the server's default).
        const fromId = form.pay_from_account_id.value ? parseInt(form.pay_from_account_id.value) : null;
        const outgoing = [...byVendor.values()].reduce((sum, v) => sum + v.total, 0);
        if (!(await Overdraft.confirm(fromId, outgoing, '1000'))) return;
        // A Sub whose certificate of insurance has expired is paid only on an
        // explicit override, which the payment records (#553).
        const expired = [...byVendor.values()].filter(v => v.coiExpires && v.coiExpires < form.date.value);
        if (expired.length) {
            const who = expired.map(v => `${v.name} (expired ${v.coiExpires})`).join(', ');
            if (!confirm(`Certificate of insurance expired: ${who}.\n\nA sub without a current certificate leaves you exposed if someone is hurt on the job. Pay anyway? The payment will be recorded as paid with an expired COI.`)) return;
        }
        if (!(await TxnWarn.ok(form))) return;

        const paid = [];
        const written = [];  // the ids of the payments made, for Print Checks
        // One key per form and vendor: a second Pay while one is in flight is
        // ignored, and a Pay sent again after a lost response answers with
        // the payment already recorded instead of a second one (#292)
        if (BillsPage._paying) return;
        BillsPage._paying = true;
        const payButton = form.querySelector ? form.querySelector('button[type="submit"]') : null;
        if (payButton) payButton.disabled = true;
        if (!BillsPage._payKey) BillsPage._payKey = App.requestKey ? App.requestKey() : 'k-' + Date.now();
        try {
            for (const [vendorId, v] of byVendor) {
                const body = {
                    vendor_id: vendorId,
                    date: form.date.value,
                    amount: Math.round(v.total * 100) / 100,
                    method: form.method.value,
                    check_number: checkNumber,
                    pay_from_account_id: fromId,
                    allocations: v.allocations,
                    discount_account_id: v.discounted ? discountAccountId : null,
                };
                if (v.coiExpires && v.coiExpires < form.date.value) body.coi_override = true;
                // a check number the paying account already has (#481):
                // keep it, or go back to the form with nothing paid
                const post = q => API.post('/bill-payments', body, q ? { query: q } : null,
                    { 'Idempotency-Key': `${BillsPage._payKey}:${vendorId}${body.allow_discount_override ? ':override' : ''}` });
                // a discount the vendor's terms don't offer is the person's
                // call: asked, and sent again with the override (#664)
                const send = async q => {
                    try { return await post(q); } catch (err) {
                        if (!PaymentTerms.asksOverride(err, body)) throw err;
                        body.allow_discount_override = true;
                        return post(q);
                    }
                };
                const done = await CheckNumberWarn.send(send, form.check_number);
                if (!done) return;
                paid.push(v.name);
                written.push(done.id);
            }
            BillsPage._payKey = null;
            toastPosted(byVendor.size > 1 ? `Bills paid: ${byVendor.size} payments, one per vendor` : 'Bills paid');
            closeModal();
            await App.navigate('#/bills');
            // checks written with no number wait to be printed together: the
            // list opens with the ones just written ticked, any older ones not
            if (form.method.value === 'check' && !checkNumber) BillsPage.showPrintChecks(fromId, written);
        } catch (err) {
            // Each vendor's payment is its own record: say which went through.
            if (paid.length) {
                toast(`Paid ${paid.join(', ')}; the next payment was refused: ${err.message}`, 'error');
                closeModal();
                App.navigate('#/bills');
            } else {
                toast(err.message, 'error');
            }
        } finally {
            BillsPage._paying = false;
            if (payButton) payButton.disabled = false;
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

// A line's Account cell adds an expense or cost of goods account (#485);
// the new account is on every line's picker, picked on the line it was
// added from.
if (typeof AccountQuickAdd !== 'undefined') {
    AccountQuickAdd.register('bill', {
        types: AccountQuickAdd.PURCHASE_TYPES,
        compact: true,
        chart: () => BillsPage._accounts,
        added: () => AccountQuickAdd.refillPurchase($$('#bill-lines select.line-account'), BillsPage._accounts),
    });
}
