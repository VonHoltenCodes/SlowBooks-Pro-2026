/**
 * Item picker with inline quick-add, shared by every line that picks an
 * item: invoice, estimate, sales receipt, credit memo, recurring invoice,
 * purchase order, vendor credit, bill and Quick Entry (#445). Modelled on
 * VendorQuickAdd (vendors.js): the line's "+ Add item" opens a small form in
 * the item cell, Save creates the item through POST /api/items and picks it
 * on that line, and the document stays as it was.
 *
 * A page wires it in three places:
 *   options(items, selectedId)   the select's options, "+ Add item" included
 *   selected(selectEl, items, onPicked)   first thing in the page's change
 *       handler; true when it took the change ("+ Add item"), so the page
 *       stops there. onPicked() runs after a quick-added item is selected.
 *   itemId(selectEl)   the line's item_id for the save: a number or null
 */
const ItemQuickAdd = {
    NEW: '__new__',
    TYPES: ['service', 'product', 'material', 'labor'],
    // The seeded chart's income account for each type (seed/chart_of_accounts.py);
    // a chart without one leaves the account blank, and the posting falls
    // back to 4000 as it does for any item without one.
    INCOME_BY_TYPE: { service: '4000', product: '4100', material: '4200', labor: '4300' },

    // What a picker shows for an item: its code first, when it has one, so
    // the type-ahead finds "2x6-16" as well as "2x6x16 SPF".
    label(item) {
        return item.code ? `${item.code} — ${item.name}` : item.name;
    },

    options(items, selectedId = null) {
        const picked = selectedId === null || selectedId === undefined || selectedId === '' ? null : String(selectedId);
        const opts = (items || []).map(i =>
            `<option value="${i.id}"${picked === String(i.id) ? ' selected' : ''}>${escapeHtml(ItemQuickAdd.label(i))}</option>`).join('');
        return `<option value="">--</option><option value="${ItemQuickAdd.NEW}">+ Add item</option>${opts}`;
    },

    // Only a whole number is an item: "--", "+ Add item" or anything else
    // a select can hold saves as no item, not a 500.
    itemId(selectEl) {
        const value = selectEl ? String(selectEl.value) : '';
        return /^\d+$/.test(value) ? Number(value) : null;
    },

    _box(selectEl) {
        const cell = selectEl.closest('td') || selectEl.parentElement;
        return cell ? cell.querySelector('.iqa-form') : null;
    },

    selected(selectEl, items, onPicked) {
        if (!selectEl) return false;
        const box = ItemQuickAdd._box(selectEl);
        if (selectEl.value !== ItemQuickAdd.NEW) {
            if (box) box.remove();
            return false;
        }
        if (box) { box.querySelector('.iqa-name')?.focus(); return true; }
        ItemQuickAdd._open(selectEl, items, onPicked);
        return true;
    },

    // The form goes in the item's own cell, not a row of its own: the
    // pages read every <tr> of the lines table as a line.
    _open(selectEl, items, onPicked) {
        const cell = selectEl.closest('td') || selectEl.parentElement;
        const types = ItemQuickAdd.TYPES.map(t =>
            `<option value="${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</option>`).join('');
        cell.insertAdjacentHTML('beforeend', `
            <div class="iqa-form" style="margin-top:6px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light); min-width:200px;">
                <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add Item</div>
                <input class="iqa-name" placeholder="Name *" aria-label="Item name" aria-required="true" maxlength="200" style="width:100%; margin-bottom:4px;">
                <input class="iqa-code" placeholder="Code" aria-label="Item code" maxlength="50" style="width:100%; margin-bottom:4px;">
                <select class="iqa-type" aria-label="Item type" data-no-search style="width:100%; margin-bottom:4px;">${types}</select>
                <input class="iqa-rate" type="number" step="0.0001" placeholder="Rate" aria-label="Item rate" style="width:100%; margin-bottom:4px;">
                <select class="iqa-income" aria-label="${T('Income')} account" data-no-search style="width:100%; margin-bottom:4px;"><option value="">-- None --</option></select>
                <div style="display:flex; gap:6px;">
                    <button type="button" class="btn btn-sm btn-primary iqa-save">Save</button>
                    <button type="button" class="btn btn-sm btn-secondary iqa-cancel">Cancel</button>
                </div>
            </div>`);
        const box = ItemQuickAdd._box(selectEl);
        const save = () => ItemQuickAdd._save(selectEl, items, onPicked);
        box.querySelector('.iqa-save').addEventListener('click', save);
        box.querySelector('.iqa-cancel').addEventListener('click', () => ItemQuickAdd.cancel(selectEl));
        box.querySelector('.iqa-type').addEventListener('change', () => ItemQuickAdd._defaultIncome(box));
        // Enter saves the item, not the document; Escape closes this form,
        // not the dialog.
        box.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.preventDefault(); save(); }
            else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); ItemQuickAdd.cancel(selectEl); }
        });
        box.querySelector('.iqa-name').focus();
        ItemQuickAdd._loadAccounts(box);
    },

    async _loadAccounts(box) {
        let accounts = [];
        try { accounts = await API.get('/accounts'); } catch (e) { return; }
        const sel = box.querySelector('.iqa-income');
        if (!sel) return;
        const income = (typeof pickerAccounts === 'function')
            ? pickerAccounts(accounts, ['income'])
            : accounts.filter(a => a.account_type === 'income' && a.is_active !== false);
        sel.insertAdjacentHTML('beforeend', income.map(a =>
            `<option value="${a.id}" data-number="${escapeHtml(a.account_number || '')}">${a.account_number ? escapeHtml(a.account_number) + ' - ' : ''}${escapeHtml(a.name)}</option>`).join(''));
        ItemQuickAdd._defaultIncome(box);
    },

    // The type's income account, until the operator picks one of their own.
    _defaultIncome(box) {
        const sel = box.querySelector('.iqa-income');
        if (!sel || sel.dataset.touched) return;
        const number = ItemQuickAdd.INCOME_BY_TYPE[box.querySelector('.iqa-type').value];
        const opt = [...sel.options].find(o => o.dataset.number && o.dataset.number === number);
        sel.value = opt ? opt.value : '';
        if (!sel.dataset.wired) {
            sel.dataset.wired = '1';
            sel.addEventListener('change', () => { sel.dataset.touched = '1'; });
        }
    },

    cancel(selectEl) {
        const box = ItemQuickAdd._box(selectEl);
        if (box) box.remove();
        selectEl.value = '';
    },

    async _save(selectEl, items, onPicked) {
        const box = ItemQuickAdd._box(selectEl);
        if (!box || box.dataset.saving) return null;
        const name = box.querySelector('.iqa-name').value.trim();
        if (!name) { toast('Item name is required', 'error'); box.querySelector('.iqa-name').focus(); return null; }
        const income = box.querySelector('.iqa-income').value;
        const data = {
            name,
            code: box.querySelector('.iqa-code').value.trim() || null,
            item_type: box.querySelector('.iqa-type').value,
            rate: parseFloat(box.querySelector('.iqa-rate').value) || 0,
            income_account_id: /^\d+$/.test(income) ? Number(income) : null,
        };
        box.dataset.saving = '1';
        let item;
        try {
            item = await API.post('/items', data);
        } catch (err) {
            delete box.dataset.saving;
            toast(err.message, 'error');
            return null;
        }
        if (items && !items.some(i => i.id === item.id)) items.push(item);
        // This line gets the item picked; the other lines get it to choose,
        // their own choice untouched.
        const tbody = selectEl.closest('tbody');
        const option = `<option value="${item.id}">${escapeHtml(ItemQuickAdd.label(item))}</option>`;
        (tbody ? [...tbody.querySelectorAll('select.line-item')] : []).forEach(s => {
            if (s !== selectEl && !s.querySelector(`option[value="${item.id}"]`)) s.insertAdjacentHTML('beforeend', option);
        });
        selectEl.innerHTML = ItemQuickAdd.options(items || [item], item.id);
        selectEl.value = String(item.id);
        box.remove();
        toast(`Item "${item.name}" added`);
        if (onPicked) onPicked(item);
        return item;
    },
};
