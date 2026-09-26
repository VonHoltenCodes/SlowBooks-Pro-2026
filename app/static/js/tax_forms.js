/**
 * Tax Forms — generate W-2, W-3, 940, 941, 1099 PDFs
 * Feature 18: Payroll tax form generation
 */

// Forms open the way invoices do: window.open on the form's GET URL. In a
// browser that is a new tab; in the desktop app the shim fetches it and
// shows the native PDF viewer. A fetched blob: window opened nothing in the
// Mac app (WKWebView), so W-2 / W-3 / 940 / 941 produced nothing there.
function _openForm(url) {
    window.open(url, '_blank');
}

const TaxFormsPage = {
    async render() {
        const currentYear = new Date().getFullYear();

        // Fetch employee list for the W-2 dropdown
        let empOptions = '<option value="">— Select Employee —</option>';
        try {
            const emps = await API.get('/employees?active_only=false');
            for (const e of emps) {
                empOptions += `<option value="${e.id}">${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`;
            }
        } catch (_) {
            empOptions += '<option value="" disabled>Could not load employees</option>';
        }

        return `
            <div class="page-header">
                <h2>Tax Forms</h2>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3>W-2 / W-3</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="w2-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                    <div class="form-group">
                        <label>Employee (for W-2)</label>
                        <select id="w2-employee">${empOptions}</select>
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generateW2()">Generate W-2</button>
                    <button class="btn btn-secondary" onclick="TaxFormsPage.generateW3()">Generate W-3 (All Employees)</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3>Form 940 (FUTA)</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="f940-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generate940()">Generate 940</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3>Form 941 (Payroll Tax)</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="f941-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                    <div class="form-group">
                        <label>Quarter</label>
                        <select id="f941-quarter">
                            <option value="1">Q1 (Jan–Mar)</option>
                            <option value="2">Q2 (Apr–Jun)</option>
                            <option value="3">Q3 (Jul–Sep)</option>
                            <option value="4">Q4 (Oct–Dec)</option>
                        </select>
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generate941()">Generate 941</button>
                </div>
            </div>

            <div class="card" style="padding:16px;background:#fffbe6;border-left:4px solid #f5a623">
                <p style="margin:0"><strong>Note:</strong> Tax forms are for reference. Verify calculations with a licensed tax professional before filing.</p>
            </div>`;
    },

    async generateW2() {
        const yearEl = document.getElementById('w2-year');
        const empEl = document.getElementById('w2-employee');
        const year = yearEl ? yearEl.value : '';
        const empId = empEl ? empEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        if (!empId) { toast('Please select an employee', 'error'); return; }
        _openForm(`/api/payroll/forms/w2/${empId}/pdf?year=${year}`);
    },

    async generateW3() {
        const yearEl = document.getElementById('w2-year');
        const year = yearEl ? yearEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        _openForm(`/api/payroll/forms/w3/${year}/pdf`);
    },

    async generate940() {
        const yearEl = document.getElementById('f940-year');
        const year = yearEl ? yearEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        _openForm(`/api/payroll/forms/940/${year}/pdf`);
    },

    async generate941() {
        const yearEl = document.getElementById('f941-year');
        const quarterEl = document.getElementById('f941-quarter');
        const year = yearEl ? yearEl.value : '';
        const quarter = quarterEl ? quarterEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        if (!quarter) { toast('Please select a quarter', 'error'); return; }
        _openForm(`/api/payroll/forms/941/${year}/${quarter}/pdf`);
    },
};
