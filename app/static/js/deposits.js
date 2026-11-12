/**
 * Make Deposits — Move funds from Undeposited Funds to a bank account
 * Classic QB deposit slip workflow. A deposit remembers the payments it
 * took; voiding it (Recent deposits) puts them back on the list, which is
 * how one payment comes out of a deposit.
 *
 * Other deposits (#792): money that was never invoiced — a reimbursement,
 * a vendor's refund, a rebate — goes on the same slip as lines of its own,
 * each with who it came from, the account it goes to, a memo, check
 * number, method, class, a job (construction) and the amount. The deposit
 * is the payments ticked plus these lines.
 */
const DepositsPage = {
    async render() {
        const construction = typeof Terms !== 'undefined' && Terms.isConstruction && Terms.isConstruction();
        const [pending, accounts, classes, recent, customers, vendors, jobs] = await Promise.all([
            API.get('/deposits/pending'),
            API.get('/accounts'),
            API.get('/classes').catch(() => []),
            API.get('/deposits?limit=20').catch(() => []),
            API.get('/customers?active_only=true').catch(() => []),
            API.get('/vendors?active_only=true').catch(() => []),
            construction ? API.get('/jobs').catch(() => []) : Promise.resolve([]),
        ]);
        DepositsPage._ctx = DepositsPage._otherContext({ accounts, classes, customers, vendors, jobs, construction });
        // The Class picker starts on the system default; with Settings'
        // "Warn when a transaction is saved without a class" on (#243) it
        // starts blank, as every entry form does, so Deposit asks first.
        const blank = classes.length && ClassWarn.enabled();
        const classOpts = (blank ? `<option value="" selected>— choose a ${T('class').toLowerCase()} —</option>` : '') + classes.map(c =>
            `<option value="${c.id}" ${c.is_system_default && !blank ? 'selected' : ''}>${escapeHtml(c.name)}</option>`
        ).join('');

        // Settings' "Deposit into these accounts" (#735) narrows the list
        // to the ones chosen; Deposit To opens on the default deposit
        // account (#734), when it is on the list; none chosen, the browser
        // opens on the first.
        const allBanks = accounts.filter(a => a.bank_kind === 'bank');
        const bankAccts = typeof DepositAccounts !== 'undefined' ? DepositAccounts.banks(allBanks) : allBanks;
        const startId = typeof DepositAccounts !== 'undefined' ? DepositAccounts.startId(bankAccts) : '';
        const bankOpts = bankAccts.map(a => `<option value="${a.id}"${String(a.id) === startId ? ' selected' : ''}>${escapeHtml(a.name)} (${formatCurrency(a.balance)})</option>`).join('');

        let html = `
            <div class="page-header">
                <h2>Make Deposits</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Select payments to deposit from Undeposited Funds to a bank account
                </div>
            </div>
            <div class="toolbar" data-write>
                <label for="deposit-bank-acct" style="font-size:10px;font-weight:700;">Deposit To:</label>
                <select id="deposit-bank-acct">${bankOpts.length ? bankOpts : '<option>No bank accounts</option>'}</select>
                <label for="deposit-date" style="font-size:10px;font-weight:700;">Date:</label>
                <input type="date" id="deposit-date" value="${todayISO()}">
                <label for="deposit-ref" style="font-size:10px;font-weight:700;">Reference:</label>
                <input type="text" id="deposit-ref" placeholder="Deposit slip #" style="width:120px;">
                ${classOpts ? `<label for="deposit-class" style="font-size:10px;font-weight:700;">${T('Class')}:</label>
                <select id="deposit-class">${classOpts}</select>` : ''}
            </div>`;

        if (pending.length === 0) {
            html += '<div class="empty-state"><p>No payments waiting to be deposited</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col" style="width:30px;"><input type="checkbox" id="dep-select-all" data-write aria-label="Select all payments" onchange="DepositsPage.toggleAll()"></th>
                    <th scope="col">Date</th><th scope="col">Received From</th><th scope="col">Description</th>
                    <th scope="col">Check # / Ref</th><th scope="col">Method</th>
                    <th scope="col" class="amount">Amount</th>
                </tr></thead><tbody>`;
            for (const p of pending) {
                html += `<tr>
                    <td><input type="checkbox" class="dep-check" data-write data-lineid="${p.transaction_line_id}" data-amount="${p.amount}" aria-label="Deposit ${escapeHtml(p.description)}" onchange="DepositsPage.recalc()"></td>
                    <td>${formatDate(p.date)}</td>
                    <td>${escapeHtml(p.received_from || '')}</td>
                    <td>${escapeHtml(p.description)}${DepositsPage._paid(p)}</td>
                    <td>${escapeHtml(DepositsPage._ref(p))}</td>
                    <td>${escapeHtml(p.method || '')}</td>
                    <td class="amount">${formatCurrency(p.amount)}</td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        // money never invoiced, on the same slip (#792)
        html += `<div id="dep-other" data-write>
                <h3 style="margin:16px 0 4px; font-size:14px;">Other deposits</h3>
                <div style="font-size:10px; color:var(--text-muted); margin-bottom:6px;">
                    Money that was never invoiced (a reimbursement, a vendor's refund, a rebate) goes on the same slip: choose the account it goes to.
                </div>
                ${DepositsPage._otherTableHtml(DepositsPage._ctx, 'dep-other-rows', [])}
                <div style="margin-top:12px; display:flex; justify-content:space-between; align-items:center;">
                    <div id="deposit-total" style="font-size:16px; font-weight:700; color:var(--qb-navy);" aria-live="polite">
                        ${escapeHtml(DepositsPage._totalText(0, 0, 0))}
                    </div>
                    <button class="btn btn-primary" data-write onclick="DepositsPage.makeDeposit()">Make Deposit</button>
                </div>
            </div>`;
        html += DepositsPage._recentHtml(recent);
        return html;
    },

    // The pickers an other line draws from (#792): every active account a
    // deposit line may post to — not a bank or card account (a transfer),
    // Undeposited Funds (its payments are ticked above), Accounts
    // Receivable or Accounts Payable, which the server refuses too.
    _otherContext({ accounts = [], classes = [], customers = [], vendors = [], jobs = [], construction = false } = {}) {
        return {
            accounts: DepositsPage.otherAccounts(accounts),
            classes: Array.isArray(classes) ? classes.filter(c => !c.is_archived) : [],
            customers: Array.isArray(customers) ? customers : [],
            vendors: Array.isArray(vendors) ? vendors : [],
            jobs: construction && Array.isArray(jobs) ? jobs.filter(j => j.is_active !== false) : [],
            construction: !!construction,
        };
    },

    otherAccounts(accounts) {
        const refused = a => a.bank_kind
            || (a.is_control && ['1100', '1200', '2000'].includes(a.account_number))
            || a.detail_type === 'accounts_receivable' || a.detail_type === 'accounts_payable';
        return (accounts || []).filter(a => a.is_active !== false && !refused(a));
    },

    // Who an other line came from: a customer's or vendor's name as typed
    // picks that record (a customer first, as QuickBooks' name list does);
    // any other name is kept as typed; blank is no one.
    resolveName(name, customers, vendors) {
        const typed = String(name || '').trim();
        if (!typed) return {};
        const key = typed.toLowerCase();
        const c = (customers || []).find(x => String(x.name || '').toLowerCase() === key);
        if (c) return { customer_id: c.id };
        const v = (vendors || []).find(x => String(x.name || '').toLowerCase() === key);
        if (v) return { vendor_id: v.id };
        return { received_from: typed };
    },

    // The rows as typed ([{name, account_id, memo, check_number, method,
    // class_id, job_id, amount}]) into the request's other_lines, or the
    // sentence that says what to fix. A row left wholly blank is skipped.
    buildOtherLines(rows, ctx) {
        const lines = [];
        let n = 0;
        for (const r of rows || []) {
            const blank = !r.account_id && !String(r.amount || '').trim() && !String(r.name || '').trim()
                && !String(r.memo || '').trim() && !String(r.check_number || '').trim();
            if (blank) continue;
            n += 1;
            const cents = Math.round((parseFloat(r.amount) || 0) * 100);
            if (!r.account_id) return { error: `Other deposits, line ${n}: choose the account the money goes to. Nothing was saved.` };
            if (cents <= 0) return { error: `Other deposits, line ${n}: enter the amount that came in (more than $0.00), or remove the line. Nothing was saved.` };
            const line = {
                ...DepositsPage.resolveName(r.name, ctx && ctx.customers, ctx && ctx.vendors),
                account_id: parseInt(r.account_id, 10),
                amount: (cents / 100).toFixed(2),
            };
            if (String(r.memo || '').trim()) line.memo = String(r.memo).trim();
            if (String(r.check_number || '').trim()) line.check_number = String(r.check_number).trim();
            if (r.method) line.method = r.method;
            if (r.class_id) line.class_id = parseInt(r.class_id, 10);
            if (r.job_id) line.job_id = parseInt(r.job_id, 10);
            lines.push(line);
        }
        return { lines };
    },

    _otherCents(rows) {
        return (rows || []).reduce((sum, r) => sum + Math.max(0, Math.round((parseFloat(r.amount) || 0) * 100)), 0);
    },

    _totalText(paymentCents, count, otherCents) {
        const parts = [`Selected: ${formatCurrency(paymentCents / 100)} (${count} items)`];
        if (otherCents) parts.push(`Other deposits: ${formatCurrency(otherCents / 100)}`);
        parts.push(`Deposit total: ${formatCurrency((paymentCents + otherCents) / 100)}`);
        return parts.join(' · ');
    },

    _methods: ['Check', 'Cash', 'Credit Card', 'ACH/EFT', 'Other'],

    // The other lines' grid: the shared header, a row per line given (an
    // edit's lines; a new slip starts with one blank row) and Add line.
    _otherTableHtml(ctx, bodyId, lines, locked = false) {
        const c = ctx || DepositsPage._otherContext();
        const rows = (lines && lines.length ? lines : (locked ? [] : [{}]))
            .map((ln, i) => DepositsPage.otherRowHtml(c, ln, i + 1, bodyId, locked)).join('');
        const names = c.customers.map(x => `<option value="${escapeHtml(x.name)}"></option>`).join('')
            + c.vendors.map(x => `<option value="${escapeHtml(x.name)}"></option>`).join('');
        return `<datalist id="${bodyId}-names">${names}</datalist>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Received From</th><th scope="col">From Account</th><th scope="col">Memo</th>
                <th scope="col">Check #</th><th scope="col">Method</th>${c.classes.length ? `<th scope="col">${T('Class')}</th>` : ''}
                ${c.construction ? `<th scope="col">${T('Job')}</th>` : ''}<th scope="col" class="amount">Amount</th><th scope="col"><span class="visually-hidden">Remove</span></th></tr></thead>
                <tbody id="${bodyId}">${rows}</tbody>
            </table></div>
            ${locked ? '' : `<button type="button" class="btn btn-sm btn-secondary" data-write onclick="DepositsPage.addOtherLine('${bodyId}')" style="margin-top:6px;">Add line</button>`}`;
    },

    otherRowHtml(ctx, ln = {}, n = 1, bodyId = 'dep-other-rows', locked = false) {
        const c = ctx || DepositsPage._otherContext();
        const dis = locked ? ' disabled' : '';
        const sel = (v, id) => String(v ?? '') === String(id) ? ' selected' : '';
        // a line's own account stays offered on its edit, even if the chart
        // no longer lists it as one a new line may take
        const accounts = c.accounts.some(a => String(a.id) === String(ln.account_id)) || !ln.account_id
            ? c.accounts : c.accounts.concat([{ id: ln.account_id, name: ln.account_name || `Account ${ln.account_id}` }]);
        const acctOpts = '<option value="">— choose an account —</option>' + accounts.map(a =>
            `<option value="${a.id}"${sel(ln.account_id, a.id)}>${escapeHtml((a.account_number ? a.account_number + ' · ' : '') + a.name)}</option>`).join('');
        const methodOpts = '<option value="">--</option>' + DepositsPage._methods.map(m =>
            `<option${ln.method === m ? ' selected' : ''}>${m}</option>`).join('');
        const classOpts = '<option value=""></option>' + c.classes.map(x =>
            `<option value="${x.id}"${sel(ln.class_id, x.id)}>${escapeHtml(x.name)}</option>`).join('');
        const jobOpts = '<option value=""></option>' + c.jobs.map(j =>
            `<option value="${j.id}"${sel(ln.job_id, j.id)}>${escapeHtml(j.full_name || j.name || '')}</option>`).join('');
        const amount = ln.amount != null && ln.amount !== '' ? Number(ln.amount).toFixed(2) : '';
        return `<tr class="dep-other-row">
            <td><input class="dep-other-name" list="${bodyId}-names" value="${escapeHtml(ln.received_from || '')}" aria-label="Line ${n} received from"${dis}></td>
            <td><select class="dep-other-account" aria-label="Line ${n} from account"${dis}>${acctOpts}</select></td>
            <td><input class="dep-other-memo" value="${escapeHtml(ln.memo || '')}" aria-label="Line ${n} memo"${dis}></td>
            <td><input class="dep-other-check" value="${escapeHtml(ln.check_number || '')}" style="width:80px;" aria-label="Line ${n} check number"${dis}></td>
            <td><select class="dep-other-method" aria-label="Line ${n} payment method"${dis}>${methodOpts}</select></td>
            ${c.classes.length ? `<td><select class="dep-other-class" aria-label="Line ${n} ${T('class').toLowerCase()}"${dis}>${classOpts}</select></td>` : ''}
            ${c.construction ? `<td><select class="dep-other-job" aria-label="Line ${n} ${T('job').toLowerCase()}"${dis}>${jobOpts}</select></td>` : ''}
            <td class="amount"><input class="dep-other-amount money" type="number" step="0.01" min="0.01" value="${amount}" style="width:100px;" aria-label="Line ${n} amount" oninput="DepositsPage.recalcAll()"${dis}></td>
            <td>${locked ? '' : `<button type="button" class="btn btn-sm btn-secondary" data-write onclick="DepositsPage.removeOtherLine(this)" aria-label="Remove line ${n}">×</button>`}</td>
        </tr>`;
    },

    addOtherLine(bodyId = 'dep-other-rows') {
        const body = document.getElementById(bodyId);
        if (!body) return;
        body.insertAdjacentHTML('beforeend', DepositsPage.otherRowHtml(DepositsPage._ctx, {}, body.rows.length + 1, bodyId));
        const name = body.lastElementChild && body.lastElementChild.querySelector('.dep-other-name');
        if (name) name.focus();
    },

    removeOtherLine(btn) {
        const row = btn && btn.closest('tr');
        if (row) row.remove();
        DepositsPage.recalcAll();
    },

    // what the rows of one grid hold, as buildOtherLines reads them
    _otherRows(bodyId) {
        const body = document.getElementById(bodyId);
        if (!body) return [];
        const val = (tr, cls) => { const el = tr.querySelector(cls); return el ? el.value : ''; };
        return [...body.querySelectorAll('tr.dep-other-row')].map(tr => ({
            name: val(tr, '.dep-other-name'),
            account_id: val(tr, '.dep-other-account'),
            memo: val(tr, '.dep-other-memo'),
            check_number: val(tr, '.dep-other-check'),
            method: val(tr, '.dep-other-method'),
            class_id: val(tr, '.dep-other-class'),
            job_id: val(tr, '.dep-other-job'),
            amount: val(tr, '.dep-other-amount'),
        }));
    },

    recalcAll() {
        if (document.getElementById('dep-edit-other-rows')) DepositsPage.recalcEdit();
        if (document.getElementById('dep-other-rows')) DepositsPage.recalc();
    },

    // "Check 4420 · REF-9": the check number and the payment's own
    // reference, else whatever the posting carried.
    _ref(p) {
        const parts = [];
        if (p.check_number) parts.push(`Check ${p.check_number}`);
        if (p.payment_reference && p.payment_reference !== p.check_number) parts.push(p.payment_reference);
        return parts.length ? parts.join(' · ') : (p.reference || '');
    },

    // What a plain payment paid ("for Invoice #1001"); a sales receipt's
    // number is already its description.
    _paid(p) {
        if (!p.document || (p.description || '').includes(p.document)) return '';
        return ` <span style="color:var(--text-muted);">for ${escapeHtml(p.document)}</span>`;
    },

    _recentHtml(recent) {
        if (!recent.length) return '';
        const rows = recent.map(d => {
            const status = d.voided ? 'Void' : (d.reconciled ? 'Reconciled' : '');
            const canVoid = !d.voided && !d.reconciled;
            return `<tr style="${d.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(d.date)}</td>
                <td>${escapeHtml(d.account_name || '')}</td>
                <td>${escapeHtml(d.reference || '')}</td>
                <td class="amount">${d.items == null ? '' : d.items}${d.other_items ? ` <span style="color:var(--text-muted);">+ ${d.other_items} other</span>` : ''}</td>
                <td class="amount">${formatCurrency(d.amount)}</td>
                <td>${escapeHtml(status)}</td>
                <td class="actions"><button class="btn btn-sm btn-secondary" onclick="DepositsPage.view(${d.id})">View</button>
                    ${d.voided ? '' : `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/deposits/${d.id}/print-preview','_blank')" title="The deposit slip">Print</button>`}
                    ${canVoid ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="DepositsPage.voidDeposit(${d.id})">Void</button>` : ''}</td>
            </tr>`;
        }).join('');
        return `
            <h3 style="margin:20px 0 8px; font-size:14px;">Recent deposits</h3>
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:6px;">
                To take a payment out of a deposit, void the deposit: its payments go back on the list above to deposit again.
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Deposited To</th><th scope="col">Slip #</th>
                <th scope="col" class="amount">Payments</th><th scope="col" class="amount">Amount</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`;
    },

    // One deposit and the payments it took, with Void while it can still be
    // voided. The bank register links a deposit to #/deposits/{id}, which
    // said "Page not found" (explore 2.17.3).
    async view(id) {
        const d = await API.get(`/deposits/${id}`);
        const status = d.voided ? 'Void' : (d.reconciled ? 'Reconciled' : 'Deposited');
        const rows = (d.payments || []).map(p => `<tr>
                <td>${formatDate(p.date)}</td>
                <td>${escapeHtml(p.received_from || '')}</td>
                <td>${escapeHtml(p.description)}${DepositsPage._paid(p)}</td>
                <td>${escapeHtml(DepositsPage._ref(p))}</td>
                <td>${escapeHtml(p.method || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');
        const others = d.other_lines || [];
        const otherRows = others.map(o => `<tr>
                <td>${formatDate(d.date)}</td>
                <td>${escapeHtml(o.received_from || '')}</td>
                <td>${escapeHtml(o.account_name || '')}${o.memo ? ` <span style="color:var(--text-muted);">${escapeHtml(o.memo)}</span>` : ''}</td>
                <td>${escapeHtml(o.check_number ? `Check ${o.check_number}` : '')}</td>
                <td>${escapeHtml(o.method || '')}</td>
                <td class="amount">${formatCurrency(o.amount)}</td>
            </tr>`).join('');
        // money never invoiced on the same slip (#792): the account it went
        // to stands where a payment's description does
        const otherHtml = otherRows
            ? `<h4 style="margin:12px 0 4px; font-size:12px;">Other deposits</h4>
                <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Received From</th><th scope="col">From Account</th>
                <th scope="col">Check #</th><th scope="col">Method</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${otherRows}</tbody></table></div>`
            : '';
        const payments = rows
            ? `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Received From</th><th scope="col">Description</th>
                <th scope="col">Check # / Ref</th><th scope="col">Method</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows}</tbody></table></div>`
            : others.length && !d.voided ? '' : `<p style="font-size:11px; color:var(--text-muted);">${d.voided
                ? 'This deposit is void: its payments went back on the Make Deposits list.'
                : 'This deposit does not list its payments: it was recorded as an amount (imported, or made before deposits kept their list).'}</p>`;
        const canVoid = !d.voided && !d.reconciled;
        openModal(`Deposit — ${formatDate(d.date)}`, `
            <div style="margin-bottom:12px;">
                <strong>Deposited to:</strong> ${escapeHtml(d.account_name || '')}<br>
                <strong>Date:</strong> ${formatDate(d.date)}<br>
                ${d.reference ? `<strong>Slip #:</strong> ${escapeHtml(d.reference)}<br>` : ''}
                <strong>Amount:</strong> ${formatCurrency(d.amount)}<br>
                <strong>Status:</strong> ${status}${d.reconciled && !d.voided ? ' — on a reconciled bank statement, so it can\'t be voided' : ''}
            </div>
            ${payments}
            ${otherHtml}
            ${typeof activityPanelHtml === 'undefined' ? '' : activityPanelHtml('deposit', d.id)}
            <div class="form-actions">
                ${canVoid ? `<button data-destructive type="button" class="btn btn-danger" onclick="DepositsPage.voidDeposit(${d.id})">Void</button>` : ''}
                ${d.voided ? '' : `<button type="button" class="btn btn-secondary" onclick="window.open('/api/deposits/${d.id}/pdf','_blank')">Save PDF</button>
                <button type="button" class="btn btn-secondary" onclick="window.open('/api/deposits/${d.id}/print-preview','_blank')">Print</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/deposits/${Number(id)}`);
    },

    // Edit (#388): a deposit opens from its register row in a form filled
    // in — its date, slip reference, and the payments it takes (the ones it
    // holds now, ticked, plus any still waiting). Save sends a PUT that
    // reposts the deposit and moves the payments. A deposit on a reconciled
    // bank statement keeps its amount and date; only its reference changes.
    async editDeposit(id) {
        const [d, pending] = await Promise.all([
            API.get(`/deposits/${id}`),
            API.get('/deposits/pending').catch(() => []),
        ]);
        if (d.voided) { toast('A void deposit is not edited.', 'error'); return; }
        const held = d.payments || [];
        const heldIds = new Set(held.map(p => p.transaction_line_id));
        const rows = held.concat(pending.filter(p => !heldIds.has(p.transaction_line_id)));
        const locked = d.reconciled;
        // the lines for money never invoiced (#792), editable beside the
        // payments; a reconciled deposit's stay as they are
        if (!DepositsPage._ctx) {
            const construction = typeof Terms !== 'undefined' && Terms.isConstruction && Terms.isConstruction();
            const [accounts, classes, customers, vendors, jobs] = await Promise.all([
                API.get('/accounts').catch(() => []),
                API.get('/classes').catch(() => []),
                API.get('/customers?active_only=true').catch(() => []),
                API.get('/vendors?active_only=true').catch(() => []),
                construction ? API.get('/jobs').catch(() => []) : Promise.resolve([]),
            ]);
            DepositsPage._ctx = DepositsPage._otherContext({ accounts, classes, customers, vendors, jobs, construction });
        }
        const others = d.other_lines || [];
        DepositsPage._editPaidStart = Math.round((Number(d.payments_total) || 0) * 100);
        const rowsHtml = rows.map(p => `<tr>
                <td><input type="checkbox" class="dep-edit-check" data-lineid="${p.transaction_line_id}" data-amount="${p.amount}" ${heldIds.has(p.transaction_line_id) ? 'checked' : ''} ${locked ? 'disabled' : 'onchange="DepositsPage.recalcEdit()"'} aria-label="Include ${escapeHtml(p.description || '')}"></td>
                <td>${formatDate(p.date)}</td>
                <td>${escapeHtml(p.received_from || '')}</td>
                <td>${escapeHtml(p.description || '')}</td>
                <td class="amount">${formatCurrency(p.amount)}</td>
            </tr>`).join('');
        openModal(`Edit deposit — ${formatDate(d.date)}`, `
            <form onsubmit="event.preventDefault();DepositsPage.saveEdit(${d.id})">
                <div class="form-grid">
                    <div class="form-group"><label>Date</label>
                        <input name="date" type="date" value="${escapeHtml(d.date)}" ${locked ? 'disabled' : ''}></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference" value="${escapeHtml(d.reference || '')}" placeholder="Deposit slip #"></div>
                </div>
                ${locked ? `<div class="form-group full-width" role="status" style="font-size:11px; color:var(--text-muted);">This deposit was reconciled with a bank statement, so its amount and date stay as they are; its reference can still be corrected.</div>` : ''}
                <div class="table-container"><table>
                    <thead><tr><th scope="col"></th><th scope="col">Date</th><th scope="col">Received From</th><th scope="col">Description</th><th scope="col" class="amount">Amount</th></tr></thead>
                    <tbody>${rowsHtml || '<tr><td colspan="5">No payments to show</td></tr>'}</tbody>
                </table></div>
                <h4 style="margin:12px 0 4px; font-size:12px;">Other deposits</h4>
                ${DepositsPage._otherTableHtml(DepositsPage._ctx, 'dep-edit-other-rows', others, locked)}
                <div style="margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
                    <div id="dep-edit-total" style="font-weight:700;">Selected: ${formatCurrency(d.amount)}</div>
                    <div class="form-actions" style="margin:0;">
                        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                        <button type="submit" class="btn btn-primary">Save</button>
                    </div>
                </div>
            </form>`);
    },

    recalcEdit() {
        let cents = 0, count = 0;
        $$('.dep-edit-check').forEach(cb => { if (cb.checked) { cents += Math.round((parseFloat(cb.dataset.amount) || 0) * 100); count++; } });
        // nothing re-ticked leaves the payments as they are (the PUT omits them)
        if (!count) cents = DepositsPage._editPaidStart || 0;
        const other = DepositsPage._otherCents(DepositsPage._otherRows('dep-edit-other-rows'));
        const el = $('#dep-edit-total');
        if (el) el.textContent = other ? `Selected: ${formatCurrency(cents / 100)} · Other deposits: ${formatCurrency(other / 100)} · Deposit total: ${formatCurrency((cents + other) / 100)}` : `Selected: ${formatCurrency(cents / 100)}`;
    },

    async saveEdit(id) {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const lineIds = [];
        $$('.dep-edit-check').forEach(cb => { if (cb.checked) lineIds.push(parseInt(cb.dataset.lineid, 10)); });
        const body = {
            reference: form.reference.value || null,
            line_ids: lineIds,
        };
        // the other lines (#792): the whole list, unless the deposit is
        // reconciled and its lines stay as they are
        if (!form.date.disabled) {
            const built = DepositsPage.buildOtherLines(DepositsPage._otherRows('dep-edit-other-rows'), DepositsPage._ctx);
            if (built.error) { toast(built.error, 'error'); return; }
            body.other_lines = built.lines;
        }
        if (!form.date.disabled) body.date = form.date.value;
        // the transaction warnings (#481): a date far from today (a
        // reconciled deposit's date is locked, so it is not asked about),
        // and changing a deposit already saved
        if (!(await TxnWarn.ok(form, { date: form.date.disabled ? '' : form.date.value, editing: true }))) return;
        try {
            const saved = await API.put(`/deposits/${id}`, body);
            toastPosted(saved && saved.note ? `Deposit saved. ${saved.note}` : 'Deposit saved');
            closeModal();
            App.navigate(location.hash.startsWith('#/banking') ? location.hash : '#/deposits');
        } catch (err) { toast(err.message, 'error'); }
    },

    toggleAll() {
        const checked = $('#dep-select-all').checked;
        $$('.dep-check').forEach(cb => { cb.checked = checked; });
        DepositsPage.recalc();
    },

    recalc() {
        let cents = 0, count = 0;
        $$('.dep-check').forEach(cb => {
            if (cb.checked) {
                cents += Math.round((parseFloat(cb.dataset.amount) || 0) * 100);
                count++;
            }
        });
        const other = DepositsPage._otherCents(DepositsPage._otherRows('dep-other-rows'));
        const el = $('#deposit-total');
        if (el) el.textContent = DepositsPage._totalText(cents, count, other);
    },

    async makeDeposit() {
        const lineIds = [];
        let cents = 0;
        $$('.dep-check').forEach(cb => {
            if (cb.checked) {
                lineIds.push(parseInt(cb.dataset.lineid));
                cents += Math.round((parseFloat(cb.dataset.amount) || 0) * 100);
            }
        });
        const built = DepositsPage.buildOtherLines(DepositsPage._otherRows('dep-other-rows'), DepositsPage._ctx);
        if (built.error) { toast(built.error, 'error'); return; }
        cents += built.lines.reduce((sum, ln) => sum + Math.round(parseFloat(ln.amount) * 100), 0);
        const total = cents / 100;

        if (lineIds.length === 0 && built.lines.length === 0) { toast('Tick the payments to deposit, or add a line for money that was never invoiced.', 'error'); return; }

        const bankAcctId = $('#deposit-bank-acct')?.value;
        if (!bankAcctId) { toast('Select a bank account', 'error'); return; }
        if (!(await TxnWarn.ok(null, { date: $('#deposit-date').value }))) return;
        if ($('#deposit-class') && !ClassWarn.okValue($('#deposit-class').value)) return;

        try {
            await API.post('/deposits', {
                deposit_to_account_id: parseInt(bankAcctId),
                date: $('#deposit-date').value,
                total: total,
                reference: $('#deposit-ref')?.value || null,
                class_id: $('#deposit-class')?.value ? parseInt($('#deposit-class').value) : null,
                line_ids: lineIds,
                other_lines: built.lines,
            });
            toastPosted(`Deposited ${formatCurrency(total)}`);
            App.navigate('#/deposits');
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidDeposit(id) {
        if (!confirm('Void this deposit? A reversing entry is posted, and its payments go back on the list to deposit again.')) return;
        try {
            await API.post(`/deposits/${id}/void`);
            toast('Deposit voided; its payments are back on the list');
            closeModal();
            // off a deposit's own address (#/deposits/12), back to the list
            if (location.hash === '#/deposits') App.navigate('#/deposits');
            else location.hash = '#/deposits';
        } catch (err) { toast(err.message, 'error'); }
    },
};
