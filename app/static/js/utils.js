/**
 * Shared formatting + DOM helpers. Negative currency prints
 * parentheses instead of a minus sign — classic accountant move.
 */

function $(sel, parent = document) { return parent.querySelector(sel); }
function $$(sel, parent = document) { return [...parent.querySelectorAll(sel)]; }

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount || 0);
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = dateStr.includes('T')
        ? new Date(dateStr)
        : new Date(dateStr + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return 'Invalid date';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function todayISO() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function toast(message, type = 'success') {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// Modal accessibility: the dialog takes focus when it opens, Tab and
// Shift+Tab cycle inside it, Escape closes it, and focus returns to
// whatever opened it. (Audit finding 3: role/aria-modal live on #modal in
// index.html; this is the behaviour half.)
let _modalOpener = null;
const _FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function openModal(title, html) {
    _modalOpener = document.activeElement;
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    $('#modal-overlay').classList.remove('hidden');
    const modal = $('#modal');
    const first = modal.querySelector('#modal-body ' + _FOCUSABLE.split(', ').join(', #modal-body ')) || modal;
    setTimeout(() => { try { first.focus(); } catch (e) { /* nothing focusable */ } }, 0);
}

function closeModal() {
    $('#modal-overlay').classList.add('hidden');
    const opener = _modalOpener;
    _modalOpener = null;
    if (opener && document.contains(opener)) { try { opener.focus(); } catch (e) { /* gone */ } }
}

document.addEventListener('keydown', (e) => {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;
    if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
    if (e.key !== 'Tab') return;
    const modal = document.getElementById('modal');
    const nodes = Array.from(modal.querySelectorAll(_FOCUSABLE)).filter(n => n.offsetParent !== null);
    if (!nodes.length) { e.preventDefault(); modal.focus(); return; }
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === modal)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

function statusBadge(status) {
    return `<span class="badge badge-${status}">${status}</span>`;
}

function escapeHtml(str) {
    str = String(str ?? '');
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function disableSubmitButtons() {
    document.querySelectorAll('#modal .btn-primary').forEach(b => { b.disabled = true; b.dataset.origText = b.textContent; b.textContent = 'Saving...'; });
}
function enableSubmitButtons() {
    document.querySelectorAll('#modal .btn-primary').forEach(b => { b.disabled = false; if(b.dataset.origText) b.textContent = b.dataset.origText; });
}

function closeSearchDropdown() {
    const dd = $('#search-results');
    if (dd) dd.classList.add('hidden');
    const input = $('#global-search');
    if (input) input.value = '';
}

/**
 * Shared scaffolding for the document list pages (invoices, bills,
 * estimates, purchase orders, credit memos). Each page previously built
 * the same page-header / status-filter toolbar / empty-state / table
 * skeleton by hand; only the title, buttons, columns, and row markup
 * actually differ, so those stay page-owned.
 *
 *   title:      page heading text
 *   headerHtml: raw HTML rendered next to the heading (action buttons)
 *   filter:     optional {id, rowSelector, options: [[value, label], ...]}
 *               status dropdown; filtering is client-side via filterRows()
 *   empty:      raw HTML rendered inside .empty-state when items is empty
 *   columns:    array of header labels; use {label, cls} for styled columns.
 *               Add `key` to make a column sortable: the value at
 *               item[key] is compared numerically when both sides parse
 *               as numbers, otherwise as case-insensitive strings.
 *   items:      the fetched rows
 *   row:        item => '<tr ...>...</tr>' (page keeps escaping/actions)
 *   sort:       optional {id, column, direction} enabling click-to-sort
 *               on columns that carry a `key`. `column`/`direction` set
 *               the initial order ('asc'|'desc', default 'asc').
 */
const _listSortState = {};

function _listSortCompare(a, b, key) {
    const av = a?.[key], bv = b?.[key];
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!Number.isNaN(an) && !Number.isNaN(bn)) return an - bn;
    return String(av ?? '').toLowerCase().localeCompare(String(bv ?? '').toLowerCase());
}

function _listTableHtml(state) {
    const { columns, items, row, sort } = state;
    let rows = items;
    if (sort && sort.column) {
        const sign = sort.direction === 'desc' ? -1 : 1;
        rows = items.slice().sort((a, b) => sign * _listSortCompare(a, b, sort.column));
    }
    const ths = columns.map(c => {
        const col = typeof c === 'string' ? { label: c } : c;
        const cls = [col.cls || ''];
        let arrow = '', click = '';
        if (sort && col.key) {
            cls.push('sortable');
            if (sort.column === col.key) {
                cls.push('sort-active');
                arrow = ` <span class="sort-arrow">${sort.direction === 'asc' ? '▲' : '▼'}</span>`;
            }
            click = ` onclick="sortListRows('${sort.id}', '${col.key}')"`;
        }
        return `<th scope="col" class="${cls.filter(Boolean).join(' ')}"${click}>${col.label}${arrow}</th>`;
    }).join('');
    return `<table>
        <thead><tr>${ths}</tr></thead><tbody>${rows.map(row).join('')}</tbody></table>`;
}

function renderListPage({ title, headerHtml = '', filter = null, empty, columns, items, row, sort = null }) {
    let html = `
        <div class="page-header">
            <h2>${title}</h2>
            ${headerHtml}
        </div>`;
    if (filter) {
        const opts = filter.options
            .map(([value, label]) => `<option value="${value}">${label}</option>`)
            .join('');
        html += `
            <div class="toolbar">
                <select id="${filter.id}" onchange="filterRows('${filter.id}', '${filter.rowSelector}')">
                    <option value="">All Statuses</option>
                    ${opts}
                </select>
            </div>`;
    }
    if (items.length === 0) {
        return html + `<div class="empty-state">${empty}</div>`;
    }
    if (sort) {
        sort.direction = sort.direction || 'asc';
        const state = { columns, items, row, sort, filter };
        _listSortState[sort.id] = state;
        return html + `<div class="table-container" id="${sort.id}-table">${_listTableHtml(state)}</div>`;
    }
    const state = { columns, items, row, sort: null };
    return html + `<div class="table-container">${_listTableHtml(state)}</div>`;
}

function sortListRows(sortId, key) {
    const state = _listSortState[sortId];
    if (!state) return;
    if (state.sort.column === key) {
        state.sort.direction = state.sort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        state.sort.column = key;
        // Date-like columns feel more natural newest-first on first
        // click; everything else (text, money) defaults to ascending.
        state.sort.direction = /(^|_)date$/.test(key) ? 'desc' : 'asc';
    }
    const wrap = document.getElementById(`${sortId}-table`);
    if (!wrap) return;
    wrap.innerHTML = _listTableHtml(state);
    if (state.filter) filterRows(state.filter.id, state.filter.rowSelector);
}

function filterRows(selectId, rowSelector) {
    const status = $(`#${selectId}`)?.value;
    $$(rowSelector).forEach(row => {
        row.style.display = (!status || row.dataset.status === status) ? '' : 'none';
    });
}
const COUNTRIES = [
    { code: 'US', name: 'United States' },
    { code: 'CA', name: 'Canada' },
    { code: 'IE', name: 'Ireland' },
    { code: 'GB', name: 'United Kingdom' },
    { code: 'AU', name: 'Australia' },
    { code: '-', name: '──────────', disabled: true },
    { code: 'AR', name: 'Argentina' },
    { code: 'AT', name: 'Austria' },
    { code: 'BE', name: 'Belgium' },
    { code: 'BR', name: 'Brazil' },
    { code: 'BG', name: 'Bulgaria' },
    { code: 'CL', name: 'Chile' },
    { code: 'CN', name: 'China' },
    { code: 'CO', name: 'Colombia' },
    { code: 'HR', name: 'Croatia' },
    { code: 'CZ', name: 'Czech Republic' },
    { code: 'DK', name: 'Denmark' },
    { code: 'EG', name: 'Egypt' },
    { code: 'EE', name: 'Estonia' },
    { code: 'FI', name: 'Finland' },
    { code: 'FR', name: 'France' },
    { code: 'DE', name: 'Germany' },
    { code: 'GR', name: 'Greece' },
    { code: 'HK', name: 'Hong Kong' },
    { code: 'HU', name: 'Hungary' },
    { code: 'IS', name: 'Iceland' },
    { code: 'IN', name: 'India' },
    { code: 'ID', name: 'Indonesia' },
    { code: 'IL', name: 'Israel' },
    { code: 'IT', name: 'Italy' },
    { code: 'JP', name: 'Japan' },
    { code: 'KE', name: 'Kenya' },
    { code: 'LV', name: 'Latvia' },
    { code: 'LT', name: 'Lithuania' },
    { code: 'LU', name: 'Luxembourg' },
    { code: 'MY', name: 'Malaysia' },
    { code: 'MX', name: 'Mexico' },
    { code: 'MA', name: 'Morocco' },
    { code: 'NL', name: 'Netherlands' },
    { code: 'NZ', name: 'New Zealand' },
    { code: 'NG', name: 'Nigeria' },
    { code: 'NO', name: 'Norway' },
    { code: 'PK', name: 'Pakistan' },
    { code: 'PE', name: 'Peru' },
    { code: 'PH', name: 'Philippines' },
    { code: 'PL', name: 'Poland' },
    { code: 'PT', name: 'Portugal' },
    { code: 'RO', name: 'Romania' },
    { code: 'SA', name: 'Saudi Arabia' },
    { code: 'SG', name: 'Singapore' },
    { code: 'SK', name: 'Slovakia' },
    { code: 'SI', name: 'Slovenia' },
    { code: 'ZA', name: 'South Africa' },
    { code: 'KR', name: 'South Korea' },
    { code: 'ES', name: 'Spain' },
    { code: 'SE', name: 'Sweden' },
    { code: 'CH', name: 'Switzerland' },
    { code: 'TW', name: 'Taiwan' },
    { code: 'TH', name: 'Thailand' },
    { code: 'TR', name: 'Turkey' },
    { code: 'UA', name: 'Ukraine' },
    { code: 'AE', name: 'United Arab Emirates' },
    { code: 'UY', name: 'Uruguay' },
    { code: 'VN', name: 'Vietnam' },
];

function countryOptions(selected) {
    return COUNTRIES.map(c =>
        `<option value="${c.code}"${c.disabled ? ' disabled' : ''}${c.code === selected ? ' selected' : ''}>${c.name}</option>`
    ).join('');
}

// ---------------------------------------------------------------------------
// Class tracking dimension — shared dropdown for entry forms.
// Returns a labeled form-group; the system-default class ("Uncategorized")
// lists first and is preselected when no selectedId is given. Archived
// classes are excluded (historical rows keep them; new entries can't).
// ---------------------------------------------------------------------------
async function classFormGroupHtml(selectedId) {
    let classes = [];
    try { classes = await API.get('/classes'); } catch (e) { return ''; }
    if (!classes.length) return '';
    const opts = classes.map(c =>
        `<option value="${c.id}" ${selectedId ? (c.id === selectedId ? 'selected' : '') : (c.is_system_default ? 'selected' : '')}>${escapeHtml(c.name)}</option>`
    ).join('');
    return `<div class="form-group"><label>Class</label>
        <select name="class_id">${opts}</select></div>`;
}

// Normalize a form's class_id string to int-or-null for the API payload.
function classIdFromForm(form) {
    const v = form.class_id ? form.class_id.value : '';
    return v ? parseInt(v) : null;
}

// ---------------------------------------------------------------------------
// Job-costing dimension — shared "Customer: Job" dropdown for entry forms.
// Lists every active job; when the form has a customer select (pass its id),
// the list narrows to that customer's jobs each time the picker gets focus,
// so the customer can be changed at any point and the jobs follow. Returns
// '' when the company has no jobs yet — the field simply doesn't exist.
// ---------------------------------------------------------------------------
async function jobFormGroupHtml(selectedId, customerSelectId) {
    let jobs = [];
    try { jobs = await API.get('/jobs'); } catch (e) { return ''; }
    if (!jobs.length) return '';
    const opts = jobs.map(j =>
        `<option value="${j.id}" data-customer="${j.customer_id}" ${selectedId === j.id ? 'selected' : ''}>${escapeHtml(j.full_name || j.name)}</option>`
    ).join('');
    const bind = customerSelectId ? `data-customer-select="${customerSelectId}" onfocus="JobPicker.sync(this)"` : '';
    return `<div class="form-group"><label>Job</label>
        <select name="job_id" ${bind}><option value="">— No job —</option>${opts}</select></div>`;
}

const JobPicker = {
    // Hide jobs that belong to other customers than the one selected.
    sync(select) {
        const custSel = document.getElementById(select.dataset.customerSelect);
        const cid = custSel ? custSel.value : '';
        for (const opt of select.options) {
            if (!opt.value) continue;
            const mine = !cid || cid === '__new__' || opt.dataset.customer === cid;
            opt.hidden = !mine;
            if (!mine && opt.selected) select.value = '';
        }
    },
};

// ---------------------------------------------------------------------------
// Cost codes — the job-costing chart, chosen per LINE on cost forms.
// CostCodes.load() caches the active list for the open form; optionsHtml()
// renders the <option>s for a line select; a company with no cost codes
// gets no column at all.
// ---------------------------------------------------------------------------
const CostCodes = {
    _list: null,
    async load() {
        try { CostCodes._list = await API.get('/cost-codes'); } catch (e) { CostCodes._list = []; }
        return CostCodes._list;
    },
    any() { return !!(CostCodes._list && CostCodes._list.length); },
    optionsHtml(selectedId) {
        return '<option value="">--</option>' + (CostCodes._list || []).map(c =>
            `<option value="${c.id}" ${selectedId === c.id ? 'selected' : ''}>${'\u00a0\u00a0'.repeat(c.depth || 0)}${escapeHtml(c.label || (c.code + ' ' + c.name))}</option>`).join('');
    },
    // <td> for a line row, or '' when the company has no cost codes
    cellHtml(cls, selectedId) {
        return CostCodes.any() ? `<td><select class="${cls}">${CostCodes.optionsHtml(selectedId)}</select></td>` : '';
    },
    headHtml(label = 'Cost Code') { return CostCodes.any() ? `<th scope="col">${label}</th>` : ''; },
    fromRow(row, cls) {
        const v = row.querySelector(`.${cls}`)?.value;
        return v ? parseInt(v) : null;
    },
};

// Normalize a form's job_id string to int-or-null for the API payload.
function jobIdFromForm(form) {
    const v = form.job_id ? form.job_id.value : '';
    return v ? parseInt(v) : null;
}

// ---------------------------------------------------------------------------
// Multi-currency: currency + exchange-rate inputs for document forms.
// Selecting a foreign currency prefills the rate from /api/fx/rate
// (Bank of Canada feed); the operator can always override.
// ---------------------------------------------------------------------------
const CURRENCIES = ['USD', 'CAD', 'EUR', 'GBP', 'AUD', 'JPY', 'CHF', 'MXN', 'INR', 'CNY'];

function currencyFormGroupsHtml(selected, rate) {
    const sel = (selected || 'USD').toUpperCase();
    const opts = CURRENCIES.map(c => `<option ${c === sel ? 'selected' : ''}>${c}</option>`).join('');
    return `<div class="form-group"><label>Currency</label>
            <select name="currency" onchange="prefillFxRate(this)">${opts}</select></div>
        <div class="form-group"><label>Exchange Rate</label>
            <input name="exchange_rate" type="number" step="0.00000001" value="${rate || 1}"></div>`;
}

async function prefillFxRate(select) {
    const form = select.closest('form');
    const rateInput = form?.querySelector('[name=exchange_rate]');
    if (!rateInput) return;
    try {
        const data = await API.get(`/fx/rate?from_currency=${select.value}`);
        if (data.rate) rateInput.value = data.rate;
    } catch (e) { /* operator enters the rate manually */ }
}

function currencyPayloadFromForm(form) {
    const currency = form.currency ? form.currency.value : null;
    const rate = form.exchange_rate ? parseFloat(form.exchange_rate.value) : null;
    return { currency: currency || null, exchange_rate: rate || null };
}
