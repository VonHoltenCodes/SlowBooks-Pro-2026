/**
 * Decompiled from QBW32.EXE!CItemListView  Offset: 0x000F6200
 * The item list in QB2003 supported a tree hierarchy (parent/sub-items)
 * via a self-referencing ParentRef field in ITEM.DAT. Sub-items inherited
 * the income/expense accounts from their parent unless overridden — this
 * was handled by CItem::GetEffectiveAccount() at 0x000F50C0 which walked
 * up the tree. We skipped the hierarchy. Life is too short.
 */
const ItemsPage = {
    async render() {
        const items = await API.get('/items');
        let html = `
            <div class="page-header">
                <h2>Items & Services</h2>
                <button class="btn btn-primary" onclick="ItemsPage.showForm()">+ New Item</button>
            </div>`;

        if (items.length === 0) {
            html += `<div class="empty-state">
                <p>No items yet.</p>
                <button class="btn btn-primary" onclick="ItemsPage.showForm()" style="margin-top:10px;">+ Create your first item</button>
            </div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Type</th><th>Description</th>
                    <th class="amount">Rate</th><th class="amount">Cost</th>
                    <th class="amount">Qty on Hand</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const item of items) {
                const qtyCell = item.track_inventory
                    ? `<strong>${Number(item.quantity_on_hand || 0).toLocaleString()}</strong>`
                    : `<span style="color:var(--text-muted)">—</span>`;
                const adjustBtn = item.track_inventory
                    ? `<button class="btn btn-sm btn-secondary" onclick="ItemsPage.showAdjust(${item.id})" title="Adjust quantity on hand">Adjust</button>
                       <button class="btn btn-sm btn-secondary" onclick="ItemsPage.showMovements(${item.id})" title="Inventory movement history">History</button>`
                    : '';
                html += `<tr>
                    <td><strong>${escapeHtml(item.name)}</strong></td>
                    <td>${statusBadge(item.item_type)}</td>
                    <td>${escapeHtml(item.description) || ''}</td>
                    <td class="amount">${formatCurrency(item.rate)}</td>
                    <td class="amount">${formatCurrency(item.cost)}</td>
                    <td class="amount">${qtyCell}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="ItemsPage.showForm(${item.id})">Edit</button>
                        ${adjustBtn}
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async showForm(id = null) {
        let item = { name:'', item_type:'service', description:'', rate:0, cost:0,
            income_account_id:'', expense_account_id:'', is_taxable:true,
            track_inventory:false, quantity_on_hand:0, reorder_point:0,
            asset_account_id:'', avg_cost:0 };
        if (id) item = await API.get(`/items/${id}`);

        const accounts = await API.get('/accounts');
        const incomeAccts = accounts.filter(a => ['income','cogs'].includes(a.account_type));
        const expenseAccts = accounts.filter(a => ['expense','cogs'].includes(a.account_type));
        const assetAccts = accounts.filter(a => a.account_type === 'asset');

        // Phase 11 inventory only makes sense for tangible items.
        const trackable = ['product','material'].includes(item.item_type);
        const showInv = trackable && item.track_inventory;
        const qtyEditableOnCreate = !id;

        openModal(id ? 'Edit Item' : 'New Item', `
            <form id="item-form" onsubmit="ItemsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(item.name)}"></div>
                    <div class="form-group"><label>Type *</label>
                        <select name="item_type" id="item-type-sel">
                            ${['service','product','material','labor'].map(t =>
                                `<option value="${t}" ${item.item_type===t?'selected':''}>${t.charAt(0).toUpperCase()+t.slice(1)}</option>`).join('')}
                        </select></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description">${escapeHtml(item.description || '')}</textarea></div>
                    <div class="form-group"><label>Rate (sell price)</label>
                        <input name="rate" type="number" step="0.01" value="${item.rate}"></div>
                    <div class="form-group"><label>Cost</label>
                        <input name="cost" type="number" step="0.01" value="${item.cost}"></div>
                    <div class="form-group"><label>Income Account</label>
                        <select name="income_account_id">
                            <option value="">-- None --</option>
                            ${incomeAccts.map(a => `<option value="${a.id}" ${item.income_account_id==a.id?'selected':''}>${a.account_number} - ${escapeHtml(a.name)}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Expense Account</label>
                        <select name="expense_account_id">
                            <option value="">-- None --</option>
                            ${expenseAccts.map(a => `<option value="${a.id}" ${item.expense_account_id==a.id?'selected':''}>${a.account_number} - ${escapeHtml(a.name)}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>
                        <input type="checkbox" name="is_taxable" ${item.is_taxable?'checked':''}>
                        Taxable</label></div>
                </div>

                <fieldset id="inv-fieldset" style="margin-top:16px; padding:10px 14px; border:1px solid var(--panel-border); border-radius:4px; ${trackable ? '' : 'display:none;'}">
                    <legend style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted); padding:0 6px;">Inventory Tracking</legend>
                    <div class="form-group">
                        <label>
                            <input type="checkbox" name="track_inventory" id="track-inv-chk" ${item.track_inventory?'checked':''}>
                            Track inventory for this item
                        </label>
                        <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                            Posting an invoice or bill that uses this item will adjust quantity on hand and post to the asset account below.
                        </div>
                    </div>
                    <div id="inv-detail-grid" class="form-grid" style="${showInv ? '' : 'display:none;'}">
                        <div class="form-group"><label>Quantity on Hand
                            ${id ? '<span style="font-size:10px;color:var(--text-muted)">(saving a change posts an inventory adjustment)</span>' : ''}
                        </label>
                            <input name="quantity_on_hand" type="number" step="1" min="0"
                                   value="${item.quantity_on_hand || 0}"
                                   data-original="${item.quantity_on_hand || 0}"></div>
                        <div class="form-group"><label>Reorder Point</label>
                            <input name="reorder_point" type="number" step="1" min="0" value="${item.reorder_point || 0}"></div>
                        <div class="form-group"><label>Asset Account ${id ? '' : '<span style="font-size:10px;color:var(--text-muted)">(blank = Inventory 1300)</span>'}</label>
                            <select name="asset_account_id">
                                <option value="">-- Default (Inventory 1300) --</option>
                                ${assetAccts.map(a => `<option value="${a.id}" ${item.asset_account_id==a.id?'selected':''}>${a.account_number} - ${escapeHtml(a.name)}</option>`).join('')}
                            </select></div>
                        ${id ? `<div class="form-group"><label>Avg Cost <span style="font-size:10px;color:var(--text-muted)">(weighted, computed from movements)</span></label>
                            <div style="padding:8px 10px; border:1px solid var(--panel-border); border-radius:4px; background:transparent; color:var(--text-primary); font-variant-numeric:tabular-nums;">${formatCurrency(item.avg_cost || 0)}</div></div>` : ''}
                    </div>
                </fieldset>

                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Item</button>
                </div>
            </form>`);

        // Wire conditional visibility: fieldset shown only for product/material;
        // detail grid shown only when the track checkbox is on.
        const typeSel = document.getElementById('item-type-sel');
        const fs = document.getElementById('inv-fieldset');
        const trackChk = document.getElementById('track-inv-chk');
        const detailGrid = document.getElementById('inv-detail-grid');

        const syncFieldset = () => {
            const isTrackable = ['product','material'].includes(typeSel.value);
            fs.style.display = isTrackable ? '' : 'none';
            if (!isTrackable) trackChk.checked = false;
            detailGrid.style.display = (isTrackable && trackChk.checked) ? '' : 'none';
        };
        if (typeSel) typeSel.addEventListener('change', syncFieldset);
        if (trackChk) trackChk.addEventListener('change', syncFieldset);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const trackInv = form.track_inventory ? form.track_inventory.checked : false;
        const data = {
            name: form.name.value,
            item_type: form.item_type.value,
            description: form.description.value,
            rate: parseFloat(form.rate.value) || 0,
            cost: parseFloat(form.cost.value) || 0,
            income_account_id: form.income_account_id.value ? parseInt(form.income_account_id.value) : null,
            expense_account_id: form.expense_account_id.value ? parseInt(form.expense_account_id.value) : null,
            is_taxable: form.is_taxable.checked,
            track_inventory: trackInv,
            reorder_point: trackInv && form.reorder_point ? parseFloat(form.reorder_point.value) || 0 : 0,
            asset_account_id: trackInv && form.asset_account_id && form.asset_account_id.value
                ? parseInt(form.asset_account_id.value) : null,
        };
        // Initial quantity is only settable on create — backend recomputes
        // it from inventory_movements after that, so PUT silently ignores it.
        if (!id && trackInv && form.quantity_on_hand) {
            data.quantity_on_hand = parseFloat(form.quantity_on_hand.value) || 0;
        }

        // For an existing item, detect a manual qty edit and convert it to
        // an /adjust call so the inventory_movements ledger stays coherent
        // (PUT alone can't change qty_on_hand by design).
        let pendingAdjust = null;
        if (id && trackInv && form.quantity_on_hand) {
            const original = parseFloat(form.quantity_on_hand.dataset.original) || 0;
            const requested = parseFloat(form.quantity_on_hand.value) || 0;
            if (requested < 0) {
                toast('Quantity on hand cannot be negative', 'error');
                return;
            }
            const delta = requested - original;
            if (Math.abs(delta) > 1e-9) {
                pendingAdjust = { quantity_delta: delta, memo: 'Direct edit via item form' };
            }
        }

        try {
            if (id) { await API.put(`/items/${id}`, data); }
            else { await API.post('/items', data); }
            if (pendingAdjust) {
                await API.post(`/items/${id}/adjust`, pendingAdjust);
                toast('Item updated and quantity adjusted');
            } else {
                toast(id ? 'Item updated' : 'Item created');
            }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ----- Phase 11: manual inventory adjustment -----
    // POST /items/{id}/adjust accepts {quantity_delta, unit_cost?, memo?}.
    // The form lets the user think in three modes (Add / Remove / Set to)
    // and computes the delta for them. unit_cost is only relevant on Add
    // (i.e., a receipt that changes the weighted-average cost).

    async showMovements(id) {
        const [item, movements] = await Promise.all([
            API.get(`/items/${id}`),
            API.get(`/items/${id}/movements`),
        ]);
        // Movement type → human label + color. Receipts add stock, sales +
        // negative adjustments remove it.
        const typeLabel = {
            purchase: ['Purchase', 'badge-paid'],
            sale: ['Sale', 'badge-sent'],
            adjustment: ['Adjustment', 'badge-draft'],
            return: ['Return', 'badge-paid'],
        };
        let rows = movements.map(m => {
            const [label, cls] = typeLabel[m.movement_type] || [m.movement_type, 'badge-draft'];
            const qty = Number(m.quantity);
            const qtyStr = (qty > 0 ? '+' : '') + qty.toLocaleString();
            const src = m.source_type
                ? `${escapeHtml(m.source_type)}${m.source_id ? ' #' + m.source_id : ''}`
                : '';
            return `<tr>
                <td style="white-space:nowrap;font-size:11px;">${formatDate(m.date)}</td>
                <td><span class="badge ${cls}">${label}</span></td>
                <td class="amount" style="${qty < 0 ? 'color:var(--qb-red);' : ''}">${qtyStr}</td>
                <td class="amount">${formatCurrency(m.unit_cost)}</td>
                <td class="amount"><strong>${Number(m.balance_qty).toLocaleString()}</strong></td>
                <td class="amount">${formatCurrency(m.balance_avg_cost)}</td>
                <td style="font-size:11px;">${src}${m.memo ? '<br><span style="color:var(--text-muted);">' + escapeHtml(m.memo) + '</span>' : ''}</td>
            </tr>`;
        }).join('');
        if (!rows) {
            rows = '<tr><td colspan="7" style="text-align:center;color:var(--gray-400);">No movements recorded yet</td></tr>';
        }
        openModal(`Inventory History — ${escapeHtml(item.name)}`, `
            <div style="margin-bottom:10px;font-size:12px;color:var(--text-muted);">
                On hand: <strong>${Number(item.quantity_on_hand || 0).toLocaleString()}</strong>
                &nbsp;·&nbsp; Weighted avg cost: <strong>${formatCurrency(item.avg_cost || 0)}</strong>
            </div>
            <div class="table-container" style="max-height:60vh;overflow:auto;"><table>
                <thead><tr>
                    <th>Date</th><th>Type</th><th class="amount">Qty</th><th class="amount">Unit Cost</th>
                    <th class="amount">Bal Qty</th><th class="amount">Bal Avg</th><th>Source</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
    },

    async showAdjust(id) {
        const item = await API.get(`/items/${id}`);
        if (!item.track_inventory) {
            toast('This item is not inventory-tracked', 'error');
            return;
        }
        const currentQty = Number(item.quantity_on_hand || 0);
        const currentAvg = Number(item.avg_cost || 0);

        openModal('Adjust Inventory — ' + item.name, `
            <form id="adjust-form" onsubmit="ItemsPage.submitAdjust(event, ${id})">
                <div class="form-grid">
                    <div class="form-group full-width">
                        <label>Current</label>
                        <div style="font-size:14px;">
                            <strong>${currentQty.toLocaleString()}</strong> on hand
                            &middot; avg cost <strong>${formatCurrency(currentAvg)}</strong>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Mode *</label>
                        <select name="mode" id="adjust-mode">
                            <option value="add">Add stock (receipt / found)</option>
                            <option value="remove">Remove stock (shrinkage / spoilage)</option>
                            <option value="set">Set to count (physical-count correction)</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label id="adjust-qty-label">Quantity to add *</label>
                        <input name="quantity" id="adjust-qty" type="number" step="1" min="0" required>
                    </div>
                    <div class="form-group" id="adjust-cost-wrap">
                        <label>Unit cost (optional)
                            <span style="font-size:10px;color:var(--text-muted)">— affects avg cost</span>
                        </label>
                        <input name="unit_cost" type="number" step="0.01" min="0"
                               placeholder="${currentAvg.toFixed(2)}">
                    </div>
                    <div class="form-group full-width">
                        <label>Memo</label>
                        <input name="memo" placeholder="e.g. Annual physical count, vendor return, etc.">
                    </div>
                    <div class="form-group full-width" id="adjust-preview"
                         style="font-size:12px; color:var(--text-muted); padding:8px 10px;
                                background:var(--gray-100,#f7f7fa); border-radius:4px;">
                        Enter a quantity to see the resulting balance.
                    </div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Adjustment</button>
                </div>
            </form>`);

        // Live label/preview wiring
        const modeSel = document.getElementById('adjust-mode');
        const qtyInp = document.getElementById('adjust-qty');
        const lbl = document.getElementById('adjust-qty-label');
        const costWrap = document.getElementById('adjust-cost-wrap');
        const preview = document.getElementById('adjust-preview');

        const sync = () => {
            const mode = modeSel.value;
            const qty = parseFloat(qtyInp.value) || 0;
            // Label
            if (mode === 'add') lbl.textContent = 'Quantity to add *';
            else if (mode === 'remove') lbl.textContent = 'Quantity to remove *';
            else lbl.textContent = 'New quantity *';
            // Cost only meaningful for receipts
            costWrap.style.display = mode === 'add' ? '' : 'none';
            // Preview
            let delta = 0, newQty = currentQty;
            if (mode === 'add') { delta = qty; newQty = currentQty + qty; }
            else if (mode === 'remove') { delta = -qty; newQty = currentQty - qty; }
            else { delta = qty - currentQty; newQty = qty; }
            const sign = delta >= 0 ? '+' : '';
            preview.innerHTML =
                `Delta: <strong>${sign}${delta.toLocaleString()}</strong>` +
                ` &nbsp;&middot;&nbsp; New on-hand: <strong>${newQty.toLocaleString()}</strong>`;
        };
        modeSel.addEventListener('change', sync);
        qtyInp.addEventListener('input', sync);
    },

    async submitAdjust(e, id) {
        e.preventDefault();
        const form = e.target;
        const mode = form.mode.value;
        const qty = parseFloat(form.quantity.value);
        if (!isFinite(qty) || qty < 0) {
            toast('Quantity must be a non-negative number', 'error');
            return;
        }

        // Recompute current qty fresh — modal value could be stale if user
        // sat on it for a while.
        const item = await API.get(`/items/${id}`);
        const currentQty = Number(item.quantity_on_hand || 0);

        let delta;
        if (mode === 'add') delta = qty;
        else if (mode === 'remove') delta = -qty;
        else delta = qty - currentQty;

        if (delta === 0) {
            toast('No change to post (delta is zero)', 'error');
            return;
        }

        const payload = { quantity_delta: delta };
        if (mode === 'add' && form.unit_cost.value) {
            const c = parseFloat(form.unit_cost.value);
            if (isFinite(c) && c >= 0) payload.unit_cost = c;
        }
        if (form.memo.value.trim()) payload.memo = form.memo.value.trim();

        try {
            await API.post(`/items/${id}/adjust`, payload);
            toast('Inventory adjusted');
            closeModal();
            App.navigate(location.hash);
        } catch (err) {
            toast(err.message || 'Adjustment failed', 'error');
        }
    },
};
