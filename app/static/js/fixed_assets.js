/**
 * Fixed Assets — register, depreciation runs, disposal, CSV import.
 * Designed from the joelmacklow fork's fixed-assets slice.
 */
const FixedAssetsPage = {
    async render() {
        const assets = await API.get('/fixed-assets');
        return renderListPage({
            title: 'Fixed Assets',
            headerHtml: `<div class="btn-group">
                    <button class="btn btn-primary" onclick="FixedAssetsPage.showAssetForm()">+ Register Asset</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showTypeForm()">+ Asset Type</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showDepreciationForm()">Run Depreciation</button>
                    <button class="btn btn-secondary" onclick="FixedAssetsPage.showImportForm()">Import CSV</button>
                </div>`,
            empty: `<p>No fixed assets registered yet.</p>
                <button class="btn btn-primary" onclick="FixedAssetsPage.showAssetForm()" style="margin-top:10px;">+ Register your first asset</button>`,
            columns: [
                { label: 'Asset #', key: 'asset_number' },
                { label: 'Name', key: 'name' },
                { label: 'Type', key: 'asset_type_name' },
                { label: 'Purchased', key: 'purchase_date' },
                { label: 'Status', key: 'status' },
                { label: 'Cost', cls: 'amount', key: 'purchase_price' },
                { label: 'Accum. Depr.', cls: 'amount', key: 'accumulated_depreciation' },
                { label: 'Book Value', cls: 'amount', key: 'book_value' },
                'Actions',
            ],
            sort: { id: 'fixed-assets', column: 'asset_number', direction: 'asc' },
            items: assets,
            row: a => `<tr data-status="${a.status}">
                    <td><strong>${escapeHtml(a.asset_number)}</strong></td>
                    <td>${escapeHtml(a.name)}</td>
                    <td>${escapeHtml(a.asset_type_name || '')}</td>
                    <td>${formatDate(a.purchase_date)}</td>
                    <td>${escapeHtml(a.status)}</td>
                    <td class="amount">${formatCurrency(a.purchase_price)}</td>
                    <td class="amount">${formatCurrency(a.accumulated_depreciation)}</td>
                    <td class="amount">${formatCurrency(a.book_value)}</td>
                    <td class="actions">
                        ${a.status === 'registered' && !a.posted ? `<button class="btn btn-sm btn-secondary" title="This asset's purchase has no posting of its own" onclick="FixedAssetsPage.showPostPurchaseForm(${a.id})">Post purchase…</button>` : ''}
                        ${a.status === 'registered' ? `<button class="btn btn-sm btn-danger" onclick="FixedAssetsPage.showDisposeForm(${a.id})">Dispose</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    async showTypeForm() {
        const accounts = await API.get('/accounts');
        const opts = kind => accounts
            .filter(a => kind ? a.account_type === kind : true)
            .map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`)
            .join('');
        openModal('New Asset Type', `
            <form onsubmit="FixedAssetsPage.saveType(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required placeholder="e.g. Computer Equipment"></div>
                    <div class="form-group"><label>Depreciation Method</label>
                        <select name="depreciation_method" onchange="FixedAssetsPage.methodChanged(this)">
                            <option value="straight_line" selected>Straight line</option>
                            <option value="declining_balance">Declining balance</option>
                        </select></div>
                    <div class="form-group"><label id="fa-life-label">Effective Life (years)</label>
                        <input name="effective_life_years" type="number" step="0.5" value="5"></div>
                    <div class="form-group"><label>Annual Rate (e.g. 0.20)</label>
                        <input name="annual_rate" type="number" step="0.0001"></div>
                    <div class="form-group"><label>Fixed Asset Account</label>
                        <select name="asset_account_id"><option value="">--</option>${opts('asset')}</select></div>
                    <div class="form-group"><label>Accumulated Depreciation Account</label>
                        <select name="accumulated_depreciation_account_id"><option value="">--</option>${opts('asset')}</select></div>
                    <div class="form-group"><label>Depreciation Expense Account</label>
                        <select name="depreciation_expense_account_id"><option value="">--</option>${opts('expense')}</select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Type</button>
                </div>
            </form>`);
    },

    methodChanged() { /* labels stay visible; both inputs accepted */ },

    async saveType(e) {
        e.preventDefault();
        const form = e.target;
        const num = v => v ? parseFloat(v) : null;
        const intOrNull = v => v ? parseInt(v) : null;
        try {
            await API.post('/fixed-assets/types', {
                name: form.name.value,
                depreciation_method: form.depreciation_method.value,
                effective_life_years: num(form.effective_life_years.value),
                annual_rate: num(form.annual_rate.value),
                asset_account_id: intOrNull(form.asset_account_id.value),
                accumulated_depreciation_account_id: intOrNull(form.accumulated_depreciation_account_id.value),
                depreciation_expense_account_id: intOrNull(form.depreciation_expense_account_id.value),
            });
            toast('Asset type created');
            closeModal();
        } catch (err) { toast(err.message, 'error'); }
    },

    // How the purchase reaches the books. Registering used to post nothing,
    // while depreciation credited accumulated depreciation, so net equipment
    // went negative (exploratory 2.17.3, W-M19). The form asks now.
    async _acquisitionFields(includeInBooks) {
        const [banks, bills, expenses] = await Promise.all([
            API.get('/accounts?bank=1&active_only=true'),
            API.get('/bills'),
            API.get('/expenses'),
        ]);
        const bankOpts = banks.map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}</option>`).join('');
        const docs = bills.filter(b => b.status !== 'void' && b.status !== 'draft').slice(0, 100)
            .map(b => `<option value="bill:${b.id}">Bill ${escapeHtml(b.bill_number)} · ${escapeHtml(b.vendor_name || '')} · ${formatDate(b.date)} · ${formatCurrency(b.total)}</option>`)
            .concat(expenses.filter(x => x.status !== 'void').slice(0, 100)
                .map(x => `<option value="expense:${x.id}">Expense · ${escapeHtml(x.payee || '')} · ${formatDate(x.date)} · ${formatCurrency(x.amount)}${x.reference ? ' · ' + escapeHtml(x.reference) : ''}</option>`))
            .join('');
        return `
            <div class="form-group full-width"><label>How was it paid for? *</label>
                <select name="acq_method" onchange="FixedAssetsPage._acqChanged(this)">
                    <option value="paid_from">Paid from a bank or card account</option>
                    <option value="document">On a bill or expense already entered</option>
                    <option value="opening_balance">Owned before these books began</option>
                    ${includeInBooks ? '<option value="in_books">Already in the books (an opening balance or journal entry put it in the asset account)</option>' : ''}
                </select></div>
            <div class="form-group acq acq-paid_from"><label>Paid from *</label>
                <select name="acq_account_id">${bankOpts}</select></div>
            <div class="form-group acq acq-paid_from"><label>Check / ref #</label>
                <input name="acq_reference" maxlength="100"></div>
            <div class="form-group full-width acq acq-document hidden"><label>Bill or expense *</label>
                <select name="acq_document"><option value="">Choose…</option>${docs}</select>
                <div style="font-size:10px; color:var(--gray-500);">Its cost moves from the expense account the bill or expense used into the asset account.</div></div>
            <div class="form-group acq acq-opening_balance hidden"><label>The day your books began</label>
                <input name="acq_as_of" type="date"></div>
            <div class="form-group acq acq-opening_balance hidden"><label>Depreciation already taken by then</label>
                <input name="acq_taken" type="number" step="0.01" min="0" value="0"></div>
            <div class="form-group full-width acq acq-opening_balance hidden" style="font-size:10px; color:var(--gray-500);">
                Posts the cost to the asset account against Opening Balance Equity (3900), less what was already depreciated.</div>
            <div class="form-group full-width acq acq-in_books hidden" style="font-size:10px; color:var(--gray-500);">
                Nothing is posted. The asset account must already hold the cost of every asset registered to it.</div>`;
    },

    _acqChanged(sel) {
        const form = sel.form;
        form.querySelectorAll('.acq').forEach(el => el.classList.toggle('hidden', !el.classList.contains(`acq-${sel.value}`)));
    },

    _acquisitionFromForm(form) {
        const method = form.acq_method.value;
        if (method === 'paid_from') {
            return { method, account_id: parseInt(form.acq_account_id.value, 10) || null, reference: form.acq_reference.value || null };
        }
        if (method === 'document') {
            const [kind, id] = (form.acq_document.value || ':').split(':');
            if (!kind) return null;
            return kind === 'bill' ? { method: 'bill', bill_id: parseInt(id, 10) } : { method: 'expense', expense_id: parseInt(id, 10) };
        }
        if (method === 'opening_balance') {
            return { method, as_of: form.acq_as_of.value || null, accumulated_depreciation: form.acq_taken.value || '0' };
        }
        return { method };
    },

    async showAssetForm() {
        const types = await API.get('/fixed-assets/types');
        if (!types.length) { toast('Create an asset type first', 'error'); return; }
        const typeOpts = types.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
        const acquisition = await FixedAssetsPage._acquisitionFields(true);
        openModal('Register Fixed Asset', `
            <form onsubmit="FixedAssetsPage.saveAsset(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Asset Type *</label>
                        <select name="asset_type_id" required>${typeOpts}</select></div>
                    <div class="form-group"><label>Purchase Date *</label>
                        <input name="purchase_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Purchase Price *</label>
                        <input name="purchase_price" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group"><label>Salvage Value</label>
                        <input name="salvage_value" type="number" step="0.01" min="0" value="0">
                        <div style="font-size:10px; color:var(--gray-500);">No more than the purchase price.</div></div>
                    ${acquisition}
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Register Asset</button>
                </div>
            </form>`);
    },

    async saveAsset(e) {
        e.preventDefault();
        const form = e.target;
        const price = parseFloat(form.purchase_price.value);
        const salvage = parseFloat(form.salvage_value.value) || 0;
        if (salvage > price) { toast(`Salvage value can't be more than the purchase price (${formatCurrency(price)}).`, 'error'); return; }
        const acquisition = FixedAssetsPage._acquisitionFromForm(form);
        if (!acquisition) { toast('Choose the bill or expense the asset was bought on.', 'error'); return; }
        try {
            await API.post('/fixed-assets', {
                name: form.name.value,
                asset_type_id: parseInt(form.asset_type_id.value),
                purchase_date: form.purchase_date.value,
                purchase_price: form.purchase_price.value,
                salvage_value: form.salvage_value.value || '0',
                description: form.description.value || null,
                acquisition,
            });
            toast(acquisition.method === 'in_books' ? 'Asset registered' : 'Asset registered and its purchase posted');
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showPostPurchaseForm(assetId) {
        const acquisition = await FixedAssetsPage._acquisitionFields(false);
        openModal('Post the purchase', `
            <form onsubmit="FixedAssetsPage.postPurchase(event, ${assetId})">
                <p style="font-size:11px; margin-bottom:8px;">
                    This asset's purchase has no posting of its own. Post it only if it isn't in the books yet —
                    if an opening balance or a journal entry already put it in the asset account, leave it.
                </p>
                <div class="form-grid">${acquisition}</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Purchase</button>
                </div>
            </form>`);
    },

    async postPurchase(e, assetId) {
        e.preventDefault();
        const acquisition = FixedAssetsPage._acquisitionFromForm(e.target);
        if (!acquisition) { toast('Choose the bill or expense the asset was bought on.', 'error'); return; }
        try {
            await API.post(`/fixed-assets/${assetId}/post-purchase`, acquisition);
            toast('Purchase posted');
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    showDepreciationForm() {
        openModal('Run Depreciation', `
            <form onsubmit="FixedAssetsPage.runDepreciation(event)">
                <div class="form-group"><label>Depreciate through *</label>
                    <input name="run_date" type="date" required value="${todayISO()}"></div>
                <div style="font-size:11px; color:var(--text-muted); margin:8px 0;">
                    Posts one journal per asset (DR depreciation expense /
                    CR accumulated depreciation) for the full months elapsed
                    since each asset's last run.
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Run</button>
                </div>
            </form>`);
    },

    async runDepreciation(e) {
        e.preventDefault();
        try {
            const result = await API.post('/fixed-assets/run-depreciation', {
                run_date: e.target.run_date.value,
            });
            toast(`Depreciation posted for ${result.posted} assets (${formatCurrency(result.total)}); ${result.skipped} skipped`);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showDisposeForm(assetId) {
        const accounts = await API.get('/accounts?account_type=asset');
        const opts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</option>`).join('');
        openModal('Dispose Asset', `
            <form onsubmit="FixedAssetsPage.dispose(event, ${assetId})">
                <div class="form-grid">
                    <div class="form-group"><label>Disposal Date *</label>
                        <input name="disposal_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Proceeds</label>
                        <input name="proceeds" type="number" step="0.01" value="0"></div>
                    <div class="form-group full-width"><label>Deposit Proceeds To *</label>
                        <select name="deposit_account_id" required>${opts}</select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-danger">Dispose</button>
                </div>
            </form>`);
    },

    async dispose(e, assetId) {
        e.preventDefault();
        const form = e.target;
        try {
            const result = await API.post(`/fixed-assets/${assetId}/dispose`, {
                disposal_date: form.disposal_date.value,
                proceeds: parseFloat(form.proceeds.value) || 0,
                deposit_account_id: parseInt(form.deposit_account_id.value),
            });
            const gl = result.gain_loss;
            toast(gl === 0 ? 'Asset disposed' : `Asset disposed (${gl > 0 ? 'gain' : 'loss'} ${formatCurrency(Math.abs(gl))})`);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },

    showImportForm() {
        openModal('Import Assets from CSV', `
            <form onsubmit="FixedAssetsPage.importCsv(event)">
                <div class="form-group">
                    <label>CSV file (columns: name, asset_type, purchase_date, purchase_price, salvage_value, description)</label>
                    <input type="file" id="fa-import-file" accept=".csv" required>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Import</button>
                </div>
            </form>`);
    },

    async importCsv(e) {
        e.preventDefault();
        const file = $('#fa-import-file').files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        try {
            const resp = await fetch('/api/fixed-assets/import-csv', { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
            const data = await resp.json();
            const errs = data.errors.length ? ` (${data.errors.length} rows failed)` : '';
            toast(`Imported ${data.imported} assets${errs}`);
            if (data.errors.length) console.warn('Asset import errors:', data.errors);
            closeModal();
            App.navigate('#/fixed-assets');
        } catch (err) { toast(err.message, 'error'); }
    },
};
