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
