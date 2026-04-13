/**
 * Decompiled from QBW32.EXE!CQBFormatUtils  Offset: 0x0008C200
 * Original formatting used Win32 GetCurrencyFormat() / GetDateFormat()
 * with the system locale. The BCD-to-string conversion in the original
 * had a special case for negative values that printed parentheses instead
 * of a minus sign — classic accountant move.
 */

function $(sel, parent = document) { return parent.querySelector(sel); }
function $$(sel, parent = document) { return [...parent.querySelectorAll(sel)]; }

function getFormatSettings(settings = null) {
    if (settings) return settings;
    if (typeof App !== 'undefined' && App.settings) return App.settings;
    return {};
}

function formatCurrency(amount, settings = null) {
    const formatSettings = getFormatSettings(settings);
    const locale = formatSettings.locale || 'en-US';
    const currency = formatSettings.currency || 'USD';
    try {
        return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(amount || 0);
    } catch (err) {
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD' }).format(amount || 0);
    }
}

function formatDate(dateStr, settings = null) {
    if (!dateStr) return '';
    const formatSettings = getFormatSettings(settings);
    const d = dateStr.includes('T')
        ? new Date(dateStr)
        : new Date(dateStr + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return 'Invalid date';
    try {
        return d.toLocaleDateString(formatSettings.locale || 'en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch (err) {
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
}

function gstCodesForCalculation() {
    if (typeof App !== 'undefined' && Array.isArray(App.gstCodes)) return App.gstCodes;
    return [
        { code: 'GST15', rate: 0.15, category: 'taxable' },
        { code: 'ZERO', rate: 0, category: 'zero_rated' },
        { code: 'EXEMPT', rate: 0, category: 'exempt' },
        { code: 'NO_GST', rate: 0, category: 'no_gst' },
    ];
}

function roundMoney(amount) {
    return Math.round((Number(amount) + Number.EPSILON) * 100) / 100;
}

function gstCodeForLine(line) {
    const code = line.gst_code || 'GST15';
    return gstCodesForCalculation().find(g => g.code === code) || { code, rate: line.gst_rate || 0, category: 'taxable' };
}

function calculateGstTotals(lines, settings = null) {
    const formatSettings = getFormatSettings(settings);
    const inclusive = String(formatSettings.prices_include_gst || 'false').toLowerCase() === 'true';
    const result = {
        subtotal: 0,
        tax_amount: 0,
        total: 0,
        taxable_total: 0,
        zero_rated_total: 0,
        exempt_total: 0,
        no_gst_total: 0,
        lines: [],
    };
    for (const line of lines) {
        const gst = gstCodeForLine(line);
        const grossOrNet = (parseFloat(line.quantity) || 0) * (parseFloat(line.rate) || 0);
        let net = roundMoney(grossOrNet);
        let tax = 0;
        let gross = net;
        if ((gst.category || 'taxable') === 'taxable' && (parseFloat(gst.rate) || parseFloat(line.gst_rate) || 0) > 0) {
            const rate = parseFloat(gst.rate) || parseFloat(line.gst_rate) || 0;
            if (inclusive) {
                gross = roundMoney(grossOrNet);
                tax = roundMoney(gross * rate / (1 + rate));
                net = roundMoney(gross - tax);
            } else {
                tax = roundMoney(net * rate);
                gross = roundMoney(net + tax);
            }
        }
        result.subtotal = roundMoney(result.subtotal + net);
        result.tax_amount = roundMoney(result.tax_amount + tax);
        result.total = roundMoney(result.total + gross);
        if ((gst.category || 'taxable') === 'zero_rated') result.zero_rated_total = roundMoney(result.zero_rated_total + net);
        else if ((gst.category || 'taxable') === 'exempt') result.exempt_total = roundMoney(result.exempt_total + net);
        else if ((gst.category || 'taxable') === 'no_gst') result.no_gst_total = roundMoney(result.no_gst_total + net);
        else result.taxable_total = roundMoney(result.taxable_total + net);
        result.lines.push({ net_amount: net, gst_amount: tax, gross_amount: gross });
    }
    return result;
}

function readGstLinePayload(row) {
    const gstCode = row.querySelector('.line-gst')?.value || 'GST15';
    const gst = gstCodesForCalculation().find(g => g.code === gstCode);
    return {
        quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
        rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
        gst_code: gstCode,
        gst_rate: gst ? parseFloat(gst.rate) || 0 : 0,
    };
}

function gstOptionsHtml(selectedCode = 'GST15') {
    return gstCodesForCalculation().map(g =>
        `<option value="${g.code}" ${g.code === selectedCode ? 'selected' : ''}>${escapeHtml(g.name || g.code)}</option>`
    ).join('');
}

function todayISO() {
    return new Date().toISOString().slice(0, 10);
}

function toast(message, type = 'success') {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function openModal(title, html) {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    $('#modal-overlay').classList.remove('hidden');
}

function closeModal() {
    $('#modal-overlay').classList.add('hidden');
}

function statusBadge(status) {
    return `<span class="badge badge-${status}">${status}</span>`;
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function closeSearchDropdown() {
    const dd = $('#search-results');
    if (dd) dd.classList.add('hidden');
    const input = $('#global-search');
    if (input) input.value = '';
}
