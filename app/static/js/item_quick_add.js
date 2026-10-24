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
 *       for a sign-in that can save an item (read-only and accountant
 *       sign-ins get the items alone: the server would refuse the save)
 *   selected(selectEl, items, onPicked, side)   first thing in the page's
 *       change handler; true when it took the change ("+ Add item"), so the
 *       page stops there. onPicked() runs after a quick-added item is
 *       selected. side is 'purchase' on a bill, purchase order or vendor
 *       credit: the form then asks what the item costs and the cost of goods
 *       account it is recorded in, not a price and an income account.
 *   itemId(selectEl)   the line's item_id for the save: a number or null
 *
 * "+ Add item" opens only when it is chosen: arrowed to and taken, or
 * clicked. A name typed that matches no item highlights nothing, so Enter
 * or Tab never opens the form on its own (data-explicit, combobox.js), and
 * Tab goes on to the next cell. Cancel and Save put the focus back on the
 * line's item picker, Save with the new item showing in it.
 */
const ItemQuickAdd = {
    NEW: '__new__',
    TYPES: ['service', 'product', 'material', 'labor'],
    // The seeded chart's income account for each type (seed/chart_of_accounts.py);
    // a chart without one leaves the account blank, and the posting falls
    // back to 4000 as it does for any item without one.
    INCOME_BY_TYPE: { service: '4000', product: '4100', material: '4200', labor: '4300' },
    // On a purchase line, the seeded chart's cost of goods account for each
    // type: what the item's expense_account_id is (a stock item's purchases
    // go to Inventory; this is the COGS its sales post). An item bought for a
    // job or for resale is a cost of goods, never an operating expense, so
    // the form offers only COGS accounts; any of them can be picked (a
    // company's own 5020 or 5040, say). A chart without the seeded number
    // starts on its first COGS account.
    COST_BY_TYPE: { service: '5300', product: '5000', material: '5100', labor: '5200' },
    STOCK_TYPES: ['product', 'material'],

    // A sign-in that can save an item. The server is the enforcement; this
    // only keeps a read-only or accountant sign-in from being offered a form
    // whose Save is refused.
    canAdd() {
        return !(typeof App !== 'undefined' && App && typeof App.isLocked === 'function' && App.isLocked());
    },

    // What a picker shows for an item: its code first, when it has one, so
    // the type-ahead finds "2x6-16" as well as "2x6x16 SPF".
    label(item) {
        return item.code ? `${item.code} — ${item.name}` : item.name;
    },

    options(items, selectedId = null) {
        const picked = selectedId === null || selectedId === undefined || selectedId === '' ? null : String(selectedId);
        const opts = (items || []).map(i =>
            `<option value="${i.id}"${picked === String(i.id) ? ' selected' : ''}>${escapeHtml(ItemQuickAdd.label(i))}</option>`).join('');
        const add = ItemQuickAdd.canAdd() ? `<option value="${ItemQuickAdd.NEW}" data-explicit>+ Add item</option>` : '';
        return `<option value="">--</option>${add}${opts}`;
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

    selected(selectEl, items, onPicked, side = 'sales') {
        if (!selectEl) return false;
        const box = ItemQuickAdd._box(selectEl);
        if (selectEl.value !== ItemQuickAdd.NEW) {
            if (box) box.remove();
            return false;
        }
        if (box) { box.querySelector('.iqa-name')?.focus(); return true; }
        ItemQuickAdd._open(selectEl, items, onPicked, side === 'purchase' ? 'purchase' : 'sales');
        return true;
    },

    // What the keyboard is on for the line's item: the type-ahead box when
    // the select has one (the select itself is out of the Tab order).
    _focusPicker(selectEl) {
        const target = selectEl && selectEl._cbx ? selectEl._cbx.box : selectEl;
        if (target && target.isConnected) target.focus();
    },

    // The form goes in the item's own cell, not a row of its own: the
    // pages read every <tr> of the lines table as a line.
    _open(selectEl, items, onPicked, side = 'sales') {
        const cell = selectEl.closest('td') || selectEl.parentElement;
        const purchase = side === 'purchase';
        const types = ItemQuickAdd.TYPES.map(t =>
            `<option value="${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</option>`).join('');
        // A sale asks for a price and the income account the sale posts to;
        // a purchase for a cost and the cost of goods account the item is
        // recorded in, and, for a product or material, whether it is stock.
        const money = purchase
            ? '<input class="iqa-cost" type="number" step="0.0001" placeholder="Cost" aria-label="Item cost" style="width:100%; margin-bottom:4px;">'
            : '<input class="iqa-rate" type="number" step="0.0001" placeholder="Rate" aria-label="Item rate" style="width:100%; margin-bottom:4px;">';
        const account = purchase
            ? `<label class="iqa-stock-row" style="display:flex; align-items:center; gap:4px; font-size:11px; margin-bottom:4px;"><input type="checkbox" class="iqa-stock"> Track quantity on hand (stock)</label>
                <select class="iqa-account" aria-label="Cost of goods account" data-no-search style="width:100%; margin-bottom:4px;"><option value="">-- None --</option></select>`
            : `<select class="iqa-account iqa-income" aria-label="${T('Income')} account" data-no-search style="width:100%; margin-bottom:4px;"><option value="">-- None --</option></select>`;
        cell.insertAdjacentHTML('beforeend', `
            <div class="iqa-form" data-side="${side}" style="margin-top:6px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light); min-width:200px;">
                <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add Item</div>
                <input class="iqa-name" placeholder="Name *" aria-label="Item name" aria-required="true" maxlength="200" style="width:100%; margin-bottom:4px;">
                <input class="iqa-code" placeholder="Code" aria-label="Item code" maxlength="50" style="width:100%; margin-bottom:4px;">
                <select class="iqa-type" aria-label="Item type" data-no-search style="width:100%; margin-bottom:4px;">${types}</select>
                ${money}
                ${account}
                <div style="display:flex; gap:6px;">
                    <button type="button" class="btn btn-sm btn-primary iqa-save">Save</button>
                    <button type="button" class="btn btn-sm btn-secondary iqa-cancel">Cancel</button>
                </div>
            </div>`);
        const box = ItemQuickAdd._box(selectEl);
        const save = () => ItemQuickAdd._save(selectEl, items, onPicked);
        box.querySelector('.iqa-save').addEventListener('click', save);
        box.querySelector('.iqa-cancel').addEventListener('click', () => ItemQuickAdd.cancel(selectEl));
        box.querySelector('.iqa-type').addEventListener('change', () => ItemQuickAdd._typeChanged(box));
        const stock = box.querySelector('.iqa-stock');
        if (stock) stock.addEventListener('change', () => ItemQuickAdd._typeChanged(box));
        // Enter saves the item, not the document; Escape closes this form,
        // not the dialog.
        box.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.preventDefault(); save(); }
            else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); ItemQuickAdd.cancel(selectEl); }
        });
        ItemQuickAdd._typeChanged(box);
        box.querySelector('.iqa-name').focus();
        ItemQuickAdd._loadAccounts(box);
    },

    _purchase(box) { return box.dataset.side === 'purchase'; },

    // The accounts the form offers: income on a sale; cost of goods on a
    // purchase (operating expenses are a bill's Account cell, not an item).
    _accounts(box, accounts) {
        const types = ItemQuickAdd._purchase(box) ? ['cogs'] : ['income'];
        const usable = (typeof pickerAccounts === 'function')
            ? pickerAccounts(accounts, types)
            : accounts.filter(a => types.includes(a.account_type) && a.is_active !== false);
        return types.flatMap(t => usable.filter(a => a.account_type === t));
    },

    async _loadAccounts(box) {
        let accounts = [];
        try { accounts = await API.get('/accounts'); } catch (e) { return; }
        const sel = box.querySelector('.iqa-account');
        if (!sel) return;
        sel.insertAdjacentHTML('beforeend', ItemQuickAdd._accounts(box, accounts).map(a =>
            `<option value="${a.id}" data-number="${escapeHtml(a.account_number || '')}">${a.account_number ? escapeHtml(a.account_number) + ' - ' : ''}${escapeHtml(a.name)}</option>`).join(''));
        ItemQuickAdd._defaultAccount(box);
    },

    // A purchase line's product or material can be stock; a service or
    // labor item cannot (TRACKABLE_ITEM_TYPES, app/models/items.py).
    _typeChanged(box) {
        const stock = box.querySelector('.iqa-stock');
        if (stock) {
            const trackable = ItemQuickAdd.STOCK_TYPES.includes(box.querySelector('.iqa-type').value);
            box.querySelector('.iqa-stock-row').hidden = !trackable;
            if (!trackable) stock.checked = false;
        }
        ItemQuickAdd._defaultAccount(box);
    },

    // The type's income account (a sale) or cost account (a purchase),
    // until the operator picks one of their own.
    _defaultAccount(box) {
        const sel = box.querySelector('.iqa-account');
        if (!sel || sel.dataset.touched) return;
        const byType = ItemQuickAdd._purchase(box) ? ItemQuickAdd.COST_BY_TYPE : ItemQuickAdd.INCOME_BY_TYPE;
        const number = byType[box.querySelector('.iqa-type').value];
        const opt = [...sel.options].find(o => o.dataset.number && o.dataset.number === number)
            || (ItemQuickAdd._purchase(box) ? [...sel.options].find(o => o.value) : null);
        sel.value = opt ? opt.value : '';
        if (!sel.dataset.wired) {
            sel.dataset.wired = '1';
            sel.addEventListener('change', () => { sel.dataset.touched = '1'; });
        }
    },

    cancel(selectEl) {
        const box = ItemQuickAdd._box(selectEl);
        selectEl.value = '';
        if (box) box.remove();
        // the form held the focus: it goes back to the line's item picker,
        // not to the page behind the dialog
        ItemQuickAdd._focusPicker(selectEl);
    },

    async _save(selectEl, items, onPicked) {
        const box = ItemQuickAdd._box(selectEl);
        if (!box || box.dataset.saving) return null;
        const name = box.querySelector('.iqa-name').value.trim();
        if (!name) { toast('Item name is required', 'error'); box.querySelector('.iqa-name').focus(); return null; }
        const account = box.querySelector('.iqa-account').value;
        const accountId = /^\d+$/.test(account) ? Number(account) : null;
        const data = {
            name,
            code: box.querySelector('.iqa-code').value.trim() || null,
            item_type: box.querySelector('.iqa-type').value,
        };
        if (ItemQuickAdd._purchase(box)) {
            // what it costs, and where buying it is recorded: the item's
            // cost of goods account; a stock item's purchases go to
            // Inventory and this is the COGS its sales post
            data.cost = parseFloat(box.querySelector('.iqa-cost').value) || 0;
            data.expense_account_id = accountId;
            data.track_inventory = !!box.querySelector('.iqa-stock')?.checked;
        } else {
            data.rate = parseFloat(box.querySelector('.iqa-rate').value) || 0;
            data.income_account_id = accountId;
        }
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
        // back on the line's item picker, which shows the new item
        ItemQuickAdd._focusPicker(selectEl);
        return item;
    },
};
