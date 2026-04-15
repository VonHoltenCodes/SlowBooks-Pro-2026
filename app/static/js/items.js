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
        const canManageItems = App.hasPermission ? App.hasPermission('items.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Items & Services</h2>
                ${canManageItems ? `<button class="btn btn-primary" onclick="ItemsPage.showForm()">+ New Item</button>` : ''}
            </div>`;

        if (items.length === 0) {
            html += `<div class="empty-state"><p>No items yet</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>Name</th><th>Type</th><th>Description</th>
                    <th class="amount">Rate</th><th class="amount">Cost</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const item of items) {
                html += `<tr>
                    <td><strong>${escapeHtml(item.name)}</strong></td>
                    <td>${statusBadge(item.item_type)}</td>
                    <td>${escapeHtml(item.description) || ''}</td>
                    <td class="amount">${formatCurrency(item.rate)}</td>
                    <td class="amount">${formatCurrency(item.cost)}</td>
                    <td class="actions">
                        ${canManageItems ? `<button class="btn btn-sm btn-secondary" onclick="ItemsPage.showForm(${item.id})">Edit</button>` : ''}
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async showForm(id = null) {
        let item = { name:'', item_type:'service', description:'', rate:0, cost:0,
            income_account_id:'', expense_account_id:'', is_taxable:true };
        if (id) item = await API.get(`/items/${id}`);

        const accounts = await API.get('/accounts');
        const incomeAccts = accounts.filter(a => ['income','cogs'].includes(a.account_type));
        const expenseAccts = accounts.filter(a => ['expense','cogs'].includes(a.account_type));

        openModal(id ? 'Edit Item' : 'New Item', `
            <form id="item-form" onsubmit="ItemsPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required value="${escapeHtml(item.name)}"></div>
                    <div class="form-group"><label>Type *</label>
                        <select name="item_type">
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
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Item</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const data = {
            name: form.name.value,
            item_type: form.item_type.value,
            description: form.description.value,
            rate: parseFloat(form.rate.value) || 0,
            cost: parseFloat(form.cost.value) || 0,
            income_account_id: form.income_account_id.value ? parseInt(form.income_account_id.value) : null,
            expense_account_id: form.expense_account_id.value ? parseInt(form.expense_account_id.value) : null,
            is_taxable: form.is_taxable.checked,
        };
        try {
            if (id) { await API.put(`/items/${id}`, data); toast('Item updated'); }
            else { await API.post('/items', data); toast('Item created'); }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },
};
