/**
 * Field tickets — the job's Field tickets tab (construction mode only).
 *
 * The paper that comes back from the site, on the job: T&M tickets (extra
 * work at cost and bill rates, signed by the owner's rep), material slips
 * (stock taken onto the job) and delivery tickets (quantity received on a
 * purchase order's lines). A list with kind, number, date, status, signed
 * by, total and what it turned into; a form per kind; the photo of the
 * signed paper as an attachment. See docs/contractor-mode.md "Field tickets".
 */
const FieldTickets = {
    KINDS: { tm: 'T&M ticket', material: 'Material slip', delivery: 'Delivery ticket' },
    STATUS: { draft: 'Draft', signed: 'Signed', closed: 'Closed', void: 'Void' },
    LINE_TYPES: { labor: 'Labor', equipment: 'Equipment', material: 'Material', other: 'Other' },
    _job: null,
    _list: [],
    _editing: null,
    _codes: [],
    _employees: [],
    _equipment: [],
    _items: [],
    _pos: [],

    money(v) { return (v === null || v === undefined) ? '' : formatCurrency(v); },

    async html(job) {
        FieldTickets._job = job;
        const [list, codes, employees, equipment, items, pos] = await Promise.all([
            API.get(`/field-tickets?job_id=${job.id}`),
            API.get('/cost-codes').catch(() => []),
            API.get('/employees?active_only=true').catch(() => []),
            API.get('/equipment').catch(() => []),
            API.get('/items?active_only=true').catch(() => []),
            API.get(`/field-tickets/purchase-orders?job_id=${job.id}`).catch(() => []),
        ]);
        Object.assign(FieldTickets, { _list: list, _codes: codes, _employees: employees, _equipment: equipment, _items: items, _pos: pos });
        return FieldTickets.listHtml(list) + '<div id="ft-form-area" style="margin-top:16px"></div>';
    },

    // What a ticket turned into, as the list's last column.
    turnedInto(t) {
        const parts = [];
        if (t.change_order_number) parts.push(`CO ${escapeHtml(t.change_order_number)}`);
        if (t.sub_change_order_number) parts.push(`Sub CO ${escapeHtml(t.sub_change_order_number)}`);
        if (t.invoice_number) parts.push(`Invoice ${escapeHtml(t.invoice_number)}`);
        if (t.job_cost_number) parts.push(`Job cost ${escapeHtml(t.job_cost_number)}`);
        if (t.purchase_order_number) parts.push(`PO ${escapeHtml(t.purchase_order_number)}`);
        return parts.join(' · ');
    },

    listHtml(list) {
        const m = FieldTickets.money;
        const body = (list || []).map(t => `<tr>
            <td>${t.kind === 'tm' && t.status === 'signed' && !t.change_order_number && !t.invoice_number ? `<input type="checkbox" class="ft-pick" value="${t.id}" aria-label="Select ${escapeHtml(t.number)}"> ` : ''}${escapeHtml(t.number)}</td>
            <td>${escapeHtml(FieldTickets.KINDS[t.kind] || t.kind)}</td>
            <td>${escapeHtml(t.date || '')}</td>
            <td><span class="badge">${escapeHtml(FieldTickets.STATUS[t.status] || t.status)}</span></td>
            <td>${escapeHtml(t.signed_by_name || '')}${t.signed_at ? ` <span style="color:var(--text-muted)">${escapeHtml(t.signed_at)}</span>` : ''}</td>
            <td>${escapeHtml(t.description || '')}</td>
            <td class="amount" title="${t.kind === 'tm' ? 'At bill rates' : 'At cost'}">${m(t.kind === 'tm' ? t.bill_total : t.cost_total)}</td>
            <td>${FieldTickets.turnedInto(t)}</td>
            <td style="white-space:nowrap">${FieldTickets.rowActions(t)}</td>
        </tr>`).join('');
        return `<div class="form-actions" data-write style="justify-content:flex-start;margin-bottom:8px;flex-wrap:wrap;gap:6px">
                <button class="btn btn-primary" onclick="FieldTickets.newTicket('tm')">+ T&amp;M ticket</button>
                <button class="btn btn-secondary" onclick="FieldTickets.newTicket('material')">+ Material slip</button>
                <button class="btn btn-secondary" onclick="FieldTickets.newTicket('delivery')">+ Delivery ticket</button>
                <button class="btn btn-secondary" onclick="FieldTickets.makeChangeOrder()" title="Tick signed T&amp;M tickets to price them as one potential change order, at cost with the job's markup">Make change order</button>
                <button class="btn btn-secondary" onclick="FieldTickets.billSelected()" title="Tick signed T&amp;M tickets to bill them on an invoice at their bill rates (a job with no schedule of values)">Bill it</button>
            </div>
            <div class="table-container"><table class="data-table" id="ft-list" style="font-size:12px">
                <thead><tr>
                    <th scope="col">Number</th><th scope="col">Kind</th><th scope="col">Date</th><th scope="col">Status</th>
                    <th scope="col">Signed by</th><th scope="col">Description</th><th scope="col" class="amount">Total</th>
                    <th scope="col">Turned into</th><th scope="col"></th>
                </tr></thead>
                <tbody>${body || '<tr><td colspan="9" style="color:var(--text-muted)">No field tickets yet. A T&amp;M ticket records extra work for the owner to sign; a material slip puts stock on the job; a delivery ticket receives a purchase order.</td></tr>'}</tbody>
            </table></div>`;
    },

    rowActions(t) {
        const parts = [`<button class="btn btn-sm btn-secondary" onclick="FieldTickets.edit(${t.id})">${t.status === 'draft' ? 'Edit' : 'View'}</button>`];
        if (t.status === 'draft' && t.kind !== 'material') parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="FieldTickets.sign(${t.id})">Sign</button>`);
        if (t.status === 'draft' && t.kind === 'material') parts.push(`<button class="btn btn-sm btn-secondary" data-write onclick="FieldTickets.post(${t.id})" title="Put the stock on the job at its average cost">Post</button>`);
        if (t.status === 'draft') parts.push(`<button data-destructive data-write class="btn btn-sm btn-danger" onclick="FieldTickets.del(${t.id})">Delete</button>`);
        // can_void: the server says whether it would accept the void now
        if (t.status !== 'void' && t.status !== 'draft' && t.can_void) parts.push(`<button data-destructive data-write class="btn btn-sm btn-danger" onclick="FieldTickets.voidTicket(${t.id})">Void</button>`);
        return parts.join(' ');
    },

    async refresh() { await JobsPage.setTab('fieldtickets'); },

    newTicket(kind) {
        FieldTickets._editing = { kind, status: 'draft', lines: [] };
        FieldTickets.showForm(FieldTickets._editing);
    },

    async edit(id) {
        FieldTickets._editing = await API.get(`/field-tickets/${id}`);
        FieldTickets.showForm(FieldTickets._editing);
    },

    showForm(t) {
        const area = $('#ft-form-area');
        if (!area) return;
        area.innerHTML = FieldTickets.formHtml(t);
        if (t.kind === 'delivery' && !t.id && t.purchase_order_id) FieldTickets.poChanged();
        FieldTickets.recalc();
        if (t.id) FieldTickets.loadAttachments(t.id);
    },

    _codeOpts(selected) {
        return ['<option value="">—</option>'].concat((FieldTickets._codes || []).map(c =>
            `<option value="${c.id}" ${String(selected) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.label || `${c.code} ${c.name}`)}</option>`)).join('');
    },

    formHtml(t) {
        const ro = t.id && t.status !== 'draft';
        const d = ro ? 'disabled' : '';
        const kind = t.kind;
        const pos = FieldTickets._pos || [];
        const openPos = pos.filter(p => ['sent', 'partial', 'received'].includes(p.status) || String(p.id) === String(t.purchase_order_id));
        const poOpts = ['<option value="">— purchase order —</option>'].concat(openPos.map(p =>
            `<option value="${p.id}" ${String(t.purchase_order_id) === String(p.id) ? 'selected' : ''}>${escapeHtml(p.number)} · ${escapeHtml(p.vendor_name)}</option>`)).join('');
        const commitOpts = ['<option value="">— none (own crew) —</option>'].concat(pos.map(p =>
            `<option value="${p.id}" ${String(t.commitment_id) === String(p.id) ? 'selected' : ''}>${escapeHtml(p.number)} · ${escapeHtml(p.vendor_name)}</option>`)).join('');
        const head = kind === 'delivery'
            ? `<th scope="col">PO line</th><th scope="col" class="amount">Ordered</th><th scope="col" class="amount">Received to date</th><th scope="col" class="amount">Received now</th><th scope="col">Unit</th>`
            : `<th scope="col">Type</th><th scope="col">Who / what</th><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col">Unit</th>
               <th scope="col" class="amount">Cost rate</th>${kind === 'tm' ? '<th scope="col" class="amount">Bill rate</th>' : ''}<th scope="col">Cost code</th>
               <th scope="col" class="amount">Cost</th>${kind === 'tm' ? '<th scope="col" class="amount">Bill</th>' : ''}<th scope="col"></th>`;
        const lines = (t.lines || []).map((l, i) => kind === 'delivery' ? FieldTickets.deliveryRowHtml(l, d) : FieldTickets.lineRowHtml(l, i, kind, d)).join('');
        const signed = t.signed_by_name ? `<p style="font-size:12px;margin:4px 0">Signed by <strong>${escapeHtml(t.signed_by_name)}</strong> on ${escapeHtml(t.signed_at || '')}.</p>` : '';
        return `<form id="ft-form" data-write onsubmit="FieldTickets.save(event)">
            <h4 style="font-size:12px;text-transform:uppercase;color:var(--text-muted);margin:4px 0 6px 0">${t.id ? `${escapeHtml(FieldTickets.KINDS[kind])} ${escapeHtml(t.number)}` : `New ${escapeHtml(FieldTickets.KINDS[kind].toLowerCase())}`}</h4>
            ${signed}
            <div class="form-grid">
                <div class="form-group"><label for="ft-date">Date</label><input id="ft-date" ${d} type="date" value="${t.date || todayISO()}"></div>
                <div class="form-group"><label for="ft-code">Cost code</label><select id="ft-code" ${d}>${FieldTickets._codeOpts(t.cost_code_id)}</select></div>
                <div class="form-group"><label for="ft-by">Submitted by</label><input id="ft-by" ${d} maxlength="120" value="${escapeHtml(t.submitted_by || '')}"></div>
                ${kind === 'tm' ? `<div class="form-group"><label for="ft-commitment">Sub's commitment</label><select id="ft-commitment" ${d}>${commitOpts}</select></div>` : ''}
                ${kind === 'delivery' ? `<div class="form-group"><label for="ft-po">Purchase order</label><select id="ft-po" ${d || (t.id ? 'disabled' : '')} onchange="FieldTickets.poChanged()">${poOpts}</select></div>
                    <div class="form-group"><label for="ft-ref">Supplier's ticket no.</label><input id="ft-ref" ${d} maxlength="60" value="${escapeHtml(t.vendor_ticket_ref || '')}"></div>` : ''}
                <div class="form-group full-width"><label for="ft-desc">Description</label><textarea id="ft-desc" ${d} rows="2">${escapeHtml(t.description || '')}</textarea></div>
            </div>
            <div class="table-container"><table class="data-table" id="ft-lines" style="font-size:12px">
                <thead><tr>${head}</tr></thead>
                <tbody id="ft-line-body">${lines}</tbody>
                <tfoot id="ft-line-foot"></tfoot>
            </table></div>
            ${kind === 'delivery' ? '' : '<p id="ft-on-save-why" role="status" style="color:var(--text-muted); font-size:11px; margin:4px 0 0 0;"></p>'}
            ${ro ? '' : `<div class="form-actions" data-write style="justify-content:flex-start;gap:8px">
                ${kind === 'delivery' ? '' : '<button type="button" class="btn btn-sm btn-secondary" onclick="FieldTickets.addLine()">+ Add line</button>'}
                <button type="submit" class="btn btn-primary">${t.id ? 'Save ticket' : 'Create ticket'}</button>
                <button type="button" class="btn btn-secondary" onclick="FieldTickets.refresh()">Cancel</button>
            </div>`}
            ${t.id ? `<div style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
                <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments (the signed paper, photos)</div>
                <div id="ft-attachments" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <div style="display:flex; gap:6px; align-items:center;" data-write>
                    <input type="file" id="ft-attach-file" aria-label="File to attach" style="font-size:11px;">
                    <button type="button" class="btn btn-sm btn-secondary" onclick="FieldTickets.upload(${t.id})">Attach</button>
                </div></div>` : ''}
        </form>`;
    },

    _whoOpts(line) {
        const sel = line.employee_id ? `emp:${line.employee_id}` : line.equipment_id ? `eq:${line.equipment_id}` : line.item_id ? `item:${line.item_id}` : '';
        const o = (v, label) => `<option value="${v}" ${sel === v ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        return '<option value="">—</option>'
            + (FieldTickets._employees.length ? `<optgroup label="Employees">${FieldTickets._employees.map(e => o(`emp:${e.id}`, `${e.first_name} ${e.last_name}`)).join('')}</optgroup>` : '')
            + (FieldTickets._equipment.length ? `<optgroup label="Equipment">${FieldTickets._equipment.map(q => o(`eq:${q.id}`, q.name)).join('')}</optgroup>` : '')
            + (FieldTickets._items.length ? `<optgroup label="Items">${FieldTickets._items.map(it => o(`item:${it.id}`, it.name)).join('')}</optgroup>` : '');
    },

    lineRowHtml(line, i, kind, d) {
        line = line || {};
        const type = kind === 'material' ? 'material' : (line.line_type || 'labor');
        const typeOpts = Object.entries(FieldTickets.LINE_TYPES).map(([k, v]) => `<option value="${k}" ${type === k ? 'selected' : ''}>${v}</option>`).join('');
        const num = (cls, v, step) => `<input class="${cls}" ${d} type="number" step="${step}" value="${v ?? ''}" oninput="FieldTickets.recalc()" style="width:80px">`;
        return `<tr class="ft-line">
            <td><select class="ft-type" ${d || (kind === 'material' ? 'disabled' : '')} onchange="FieldTickets.recalc()">${typeOpts}</select></td>
            <td><select class="ft-who" ${d} onchange="FieldTickets.recalc()">${FieldTickets._whoOpts(line)}</select></td>
            <td><input class="ft-ldesc" ${d} value="${escapeHtml(line.description || '')}"></td>
            <td class="amount">${num('ft-qty', line.quantity, '0.0001')}</td>
            <td><input class="ft-unit" ${d} maxlength="20" value="${escapeHtml(line.unit || '')}" style="width:50px"></td>
            <td class="amount">${num('ft-cost', line.cost_rate, '0.0001')}</td>
            ${kind === 'tm' ? `<td class="amount">${num('ft-bill', line.bill_rate, '0.0001')}</td>` : ''}
            <td><select class="ft-lcode" ${d}>${FieldTickets._codeOpts(line.cost_code_id)}</select></td>
            <td class="amount ft-cost-amt"></td>
            ${kind === 'tm' ? '<td class="amount ft-bill-amt"></td>' : ''}
            <td>${d ? '' : '<button type="button" class="btn btn-sm btn-secondary" aria-label="Remove line" onclick="this.closest(\'tr\').remove(); FieldTickets.recalc()">✕</button>'}</td>
        </tr>`;
    },

    // A delivery line per PO line: ordered, received so far, received now.
    deliveryRowHtml(line, d) {
        const received = line.received_qty ?? 0;
        return `<tr class="ft-line" data-po-line="${line.po_line_id}">
            <td>${escapeHtml(line.description || '')}</td>
            <td class="amount">${line.ordered_qty ?? ''}</td>
            <td class="amount">${received}${line.over_receipt ? ' <span class="badge" title="This delivery took the line past what was ordered">over</span>' : ''}</td>
            <td class="amount"><input class="ft-qty" ${d} type="number" step="0.0001" min="0" value="${line.quantity ?? ''}" style="width:90px"></td>
            <td><input class="ft-unit" ${d} maxlength="20" value="${escapeHtml(line.unit || '')}" style="width:50px"></td>
        </tr>`;
    },

    poChanged() {
        const id = $('#ft-po')?.value;
        const po = (FieldTickets._pos || []).find(p => String(p.id) === String(id));
        const body = $('#ft-line-body');
        if (!body) return;
        body.innerHTML = po ? po.lines.map(l => FieldTickets.deliveryRowHtml({
            po_line_id: l.id, description: l.description, ordered_qty: l.quantity, received_qty: l.received_qty,
        }, '')).join('') : '';
    },

    addLine() {
        const body = $('#ft-line-body');
        if (!body) return;
        const kind = FieldTickets._editing.kind;
        body.insertAdjacentHTML('beforeend', FieldTickets.lineRowHtml({ line_type: kind === 'material' ? 'material' : 'labor' }, body.children.length, kind, ''));
        FieldTickets.recalc();
    },

    // ---- What the lines add up to -------------------------------------
    // One reading of the lines for the figures the form shows and for the
    // $0.00 question, so the two cannot disagree. A rate left blank is the
    // server's to fill when the ticket is saved (set_lines): the cost rate
    // from the employee on a labor line, the equipment on an equipment
    // line, else the item picked; the bill rate from the cost rate with the
    // job's markup. The form does not know those rates (an employee's pay
    // is not sent to everyone who writes tickets), so such a figure is
    // null here, "Set on save" on the form, never $0.00. A blank rate with
    // nothing to fill it from is 0, as the server stores it.

    // Whether the server's default cost rate for the line is 0 for certain:
    // nobody and nothing picked that the line's type takes a rate from, or
    // equipment or an item whose own rate the list shows as 0.
    _defaultIsZero(type, who) {
        who = who || '';
        const zero = v => v !== undefined && v !== null && v !== '' && Number(v) === 0;
        const byId = (list, id) => (list || []).find(x => String(x.id) === id);
        if (type === 'labor' && who.startsWith('emp:')) return false;
        if (type === 'equipment' && who.startsWith('eq:')) {
            const q = byId(FieldTickets._equipment, who.slice(3));
            return !!q && zero(q.hourly_rate);
        }
        if (who.startsWith('item:')) {
            const it = byId(FieldTickets._items, who.slice(5));
            return !!it && zero(it.cost) && it.avg_cost !== undefined && !(Number(it.avg_cost) > 0);
        }
        return true;
    },

    // One line's cost and bill amounts from what is typed on it ({type, who,
    // qty, cost, bill}, the boxes' own text): a number, or null where the
    // rate is set on save.
    lineFigures(kind, l) {
        const q = parseFloat(l.qty) || 0;
        const typed = v => v !== undefined && v !== null && v !== '';
        const unknown = q !== 0 && !FieldTickets._defaultIsZero(l.type, l.who);
        const cost = typed(l.cost) ? q * (parseFloat(l.cost) || 0) : (unknown ? null : 0);
        if (kind !== 'tm') return { cost, bill: null };
        let bill;
        if (typed(l.bill)) bill = q * (parseFloat(l.bill) || 0);
        else if (typed(l.cost)) bill = q !== 0 && (parseFloat(l.cost) || 0) !== 0 ? null : 0;
        else bill = unknown ? null : 0;
        return { cost, bill };
    },

    // The footer's figures: what the known amounts add up to, whether any
    // amount is set on save, and whether every total the form shows reads
    // $0.00 (a T&M ticket shows two, cost and bill; a material slip, cost).
    totals(kind, lines) {
        const t = { cost: 0, bill: 0, costPending: false, billPending: false, lines: [] };
        for (const l of lines) {
            const f = FieldTickets.lineFigures(kind, l);
            t.lines.push(f);
            if (f.cost === null) t.costPending = true; else t.cost += f.cost;
            if (kind !== 'tm') continue;
            if (f.bill === null) t.billPending = true; else t.bill += f.bill;
        }
        t.zero = !t.costPending && ZeroTotal.isZero(t.cost)
            && (kind !== 'tm' || (!t.billPending && ZeroTotal.isZero(t.bill)));
        return t;
    },

    ON_SAVE: 'Set on save',
    ON_SAVE_WHY: "The rate is blank, so it is filled in when the ticket is saved: the employee's, equipment's or item's cost rate, and the bill rate from the cost rate with the job's markup.",
    // The reason in the open, under the lines: a tooltip alone is read by
    // a mouse or a screen reader, never by a keyboard or touch user, and
    // the cells that show "Set on save" take no focus.
    ON_SAVE_NOTE: "Set on save: the rate is blank, so it is filled in when the ticket is saved: the employee's, equipment's or item's cost rate, and the bill rate from the cost rate with the job's markup.",

    // A total as the footer words it: the known amounts, and that more is
    // set on save.
    _totalText(sum, pending) {
        if (!pending) return formatCurrency(sum);
        return ZeroTotal.isZero(sum) ? FieldTickets.ON_SAVE : `${formatCurrency(sum)} + ${FieldTickets.ON_SAVE.toLowerCase()}`;
    },

    // Draws every line's amounts and the footer, and returns the figures
    // (totals) they were drawn from. A delivery ticket carries no money.
    recalc() {
        const kind = FieldTickets._editing?.kind;
        if (kind === 'delivery') return null;
        const rows = $$('#ft-line-body tr.ft-line');
        const t = FieldTickets.totals(kind, rows.map(tr => ({
            type: kind === 'material' ? 'material' : tr.querySelector('.ft-type')?.value,
            who: tr.querySelector('.ft-who')?.value,
            qty: tr.querySelector('.ft-qty')?.value,
            cost: tr.querySelector('.ft-cost')?.value,
            bill: tr.querySelector('.ft-bill')?.value,
        })));
        const put = (el, v) => {
            if (!el) return;
            el.textContent = v === null ? FieldTickets.ON_SAVE : formatCurrency(v);
            if (v === null) el.title = FieldTickets.ON_SAVE_WHY; else el.removeAttribute('title');
        };
        rows.forEach((tr, i) => {
            put(tr.querySelector('.ft-cost-amt'), t.lines[i].cost);
            put(tr.querySelector('.ft-bill-amt'), t.lines[i].bill);
        });
        // the note under the lines says why while any amount is set on save,
        // and each blank rate it is about is described by it, so the reason
        // is read on the field a person tabs to
        const note = $('#ft-on-save-why');
        const anyPending = t.costPending || t.billPending;
        if (note) note.textContent = anyPending ? FieldTickets.ON_SAVE_NOTE : '';
        rows.forEach((tr, i) => {
            for (const [box, v] of [['.ft-cost', t.lines[i].cost], ['.ft-bill', t.lines[i].bill]]) {
                const el = tr.querySelector(box);
                if (!el) continue;
                if (v === null && note) el.setAttribute('aria-describedby', 'ft-on-save-why');
                else el.removeAttribute('aria-describedby');
            }
        });
        const cell = (sum, pending) => `<td class="amount"${pending ? ` title="${escapeHtml(FieldTickets.ON_SAVE_WHY)}"` : ''}>${escapeHtml(FieldTickets._totalText(sum, pending))}</td>`;
        const foot = $('#ft-line-foot');
        if (foot) foot.innerHTML = `<tr style="font-weight:600"><td colspan="${kind === 'tm' ? 8 : 7}">Total</td>${cell(t.cost, t.costPending)}${kind === 'tm' ? cell(t.bill, t.billPending) : ''}<td></td></tr>`;
        return t;
    },

    // The body the API takes, from the form. A blank rate is left out so
    // the server fills the default (the employee's, equipment's or item's).
    collect() {
        const t = FieldTickets._editing;
        const val = (sel) => $(sel)?.value ?? '';
        const intOrNull = (v) => (v === '' || v === undefined) ? null : parseInt(v, 10);
        const body = {
            date: val('#ft-date') || null,
            cost_code_id: intOrNull(val('#ft-code')),
            submitted_by: val('#ft-by') || null,
            description: val('#ft-desc') || null,
        };
        if (t.kind === 'tm') body.commitment_id = intOrNull(val('#ft-commitment'));
        if (t.kind === 'delivery') {
            if (!t.id) body.purchase_order_id = intOrNull(val('#ft-po'));
            body.vendor_ticket_ref = val('#ft-ref') || null;
        }
        body.lines = [];
        $$('#ft-line-body tr.ft-line').forEach(tr => {
            if (t.kind === 'delivery') {
                const q = tr.querySelector('.ft-qty').value;
                if (q === '' || parseFloat(q) === 0) return;
                body.lines.push({ line_type: 'material', po_line_id: parseInt(tr.dataset.poLine, 10), quantity: q, unit: tr.querySelector('.ft-unit').value || null });
                return;
            }
            const who = tr.querySelector('.ft-who').value;
            const line = {
                line_type: t.kind === 'material' ? 'material' : tr.querySelector('.ft-type').value,
                employee_id: who.startsWith('emp:') ? parseInt(who.slice(4), 10) : null,
                equipment_id: who.startsWith('eq:') ? parseInt(who.slice(3), 10) : null,
                item_id: who.startsWith('item:') ? parseInt(who.slice(5), 10) : null,
                description: tr.querySelector('.ft-ldesc').value || null,
                quantity: tr.querySelector('.ft-qty').value || '0',
                unit: tr.querySelector('.ft-unit').value || null,
                cost_code_id: intOrNull(tr.querySelector('.ft-lcode').value),
            };
            const c = tr.querySelector('.ft-cost').value;
            if (c !== '') line.cost_rate = c;
            const b = tr.querySelector('.ft-bill')?.value;
            if (b !== undefined && b !== '') line.bill_rate = b;
            body.lines.push(line);
        });
        return body;
    },

    // A delivery past what was ordered comes back refused (409, code
    // "over_receipt") with a question; on yes the same ticket is sent again
    // with over_receipt, and the response's warnings are shown.
    async save(e) {
        e.preventDefault();
        const t = FieldTickets._editing;
        const body = FieldTickets.collect();
        // a ticket whose totals read $0.00 on the form is asked about first,
        // as every line form asks (ZeroTotal): the figures the footer shows
        // decide it (recalc), so the form never reads Total $0.00 and saves
        // without the question. An amount that is set on save is not $0.00
        // on the form, so it is not asked about. A delivery ticket counts
        // what arrived and carries no money, so it is not asked.
        if (t.kind !== 'delivery') {
            if (!ZeroTotal.ask('Field Ticket', FieldTickets.recalc().zero ? 0 : 1)) return;
        }
        const send = (extra) => t.id
            ? API.put(`/field-tickets/${t.id}`, { ...body, ...extra })
            : API.post('/field-tickets', { job_id: FieldTickets._job.id, kind: t.kind, ...body, ...extra });
        try {
            let saved;
            try {
                saved = await send({});
            } catch (err) {
                if (!(err.status === 409 && err.detail && err.detail.code === 'over_receipt')) throw err;
                if (!confirm(err.detail.question || err.message)) return;
                saved = await send({ over_receipt: true });
            }
            toast('Field ticket saved', 'success');
            (saved.warnings || []).forEach(w => toast(w, 'warning'));
            await FieldTickets.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async sign(id) {
        const name = prompt('Who signed the ticket? (Type their name; attach a photo of the signed paper as well.)');
        if (name === null) return;
        try {
            await API.post(`/field-tickets/${id}/sign`, { signed_by_name: name });
            toast('Ticket signed', 'success');
            await FieldTickets.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    _picked() { return $$('.ft-pick:checked').map(el => parseInt(el.value, 10)); },

    // Price the ticked T&M tickets as one PCO (a sub's ticket also makes a
    // draft sub change order on its commitment); they close when the owner
    // change order is approved.
    async makeChangeOrder() {
        const ids = FieldTickets._picked();
        if (!ids.length) { toast('Tick one or more signed T&M tickets first', 'error'); return; }
        try {
            const made = await API.post('/field-tickets/make-change-order', { ticket_ids: ids });
            const subs = made.sub_change_orders.map(s => s.number).join(', ');
            toast(`Made ${made.change_order.number}${subs ? ` and sub change order ${subs}` : ''}. Find it on the Change orders tab.`, 'success');
            await FieldTickets.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async billSelected() {
        const ids = FieldTickets._picked();
        if (!ids.length) { toast('Tick one or more signed T&M tickets first', 'error'); return; }
        if (!confirm('Bill the ticked tickets on an invoice to the customer, at their bill rates?')) return;
        try {
            const billed = await API.post('/field-tickets/bill', { ticket_ids: ids });
            toast(`${T('Invoice')} ${billed.invoice_number} made for ${formatCurrency(billed.total)}`, 'success');
            await FieldTickets.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async post(id) {
        if (!confirm('Post this material slip? The stock comes off the shelf onto the job at its average cost.')) return;
        try {
            const t = await API.post(`/field-tickets/${id}/post`, {});
            toast(`Posted as job cost ${t.job_cost_number}`, 'success');
            (t.warnings || []).forEach(w => toast(w, 'warning'));
            await FieldTickets.refresh();
        } catch (err) { toast(err.message, 'error'); }
    },

    async del(id) {
        if (!confirm('Delete this draft ticket?')) return;
        try { await API.del(`/field-tickets/${id}`); await FieldTickets.refresh(); } catch (err) { toast(err.message, 'error'); }
    },

    async voidTicket(id) {
        if (!confirm('Void this ticket? A posted material slip is reversed and a delivery gives its quantity back.')) return;
        try { await API.post(`/field-tickets/${id}/void`, {}); toast('Ticket voided'); await FieldTickets.refresh(); } catch (err) { toast(err.message, 'error'); }
    },

    async loadAttachments(id) {
        const el = $('#ft-attachments');
        if (!el) return;
        try {
            const list = await API.get(`/attachments/field_ticket/${id}`);
            el.innerHTML = list.length ? list.map(a => `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                <button type="button" data-destructive data-write aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="FieldTickets.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
            </div>`).join('') : '<span style="color:var(--text-muted);">No attachments</span>';
        } catch (e) { el.innerHTML = ''; }
    },

    async upload(id) {
        const input = $('#ft-attach-file');
        if (!input?.files[0]) { toast('Select a file first', 'error'); return; }
        const fd = new FormData();
        fd.append('file', input.files[0]);
        try {
            const resp = await fetch(`/api/attachments/field_ticket/${id}`, { method: 'POST', body: fd });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            input.value = '';
            FieldTickets.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try { await API.del(`/attachments/${attachmentId}`); FieldTickets.loadAttachments(id); } catch (err) { toast(err.message, 'error'); }
    },
};
