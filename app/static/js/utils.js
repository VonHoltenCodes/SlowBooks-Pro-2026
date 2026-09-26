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

// A toast that carries one action (e.g. "Saved to … [Show in folder]").
// Stays longer than a plain toast because the user has to read a path.
function toastAction(message, actionLabel, onClick, ms = 8000) {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = 'toast toast-success';
    el.style.display = 'flex';
    el.style.alignItems = 'center';
    el.style.gap = '10px';
    const text = document.createElement('span');
    text.textContent = message;
    text.style.wordBreak = 'break-all';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-secondary';
    btn.textContent = actionLabel;
    btn.addEventListener('click', () => { try { onClick(); } finally { el.remove(); } });
    el.appendChild(text);
    el.appendChild(btn);
    container.appendChild(el);
    setTimeout(() => el.remove(), ms);
}

// Modal accessibility: the dialog takes focus when it opens, Tab and
// Shift+Tab cycle inside it, Escape closes it, and focus returns to
// whatever opened it. (Audit finding 3: role/aria-modal live on #modal in
// index.html; this is the behaviour half.)
let _modalOpener = null;
const _FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// opts.wide: a form whose rows are wider than a dialog — the line-item
// tables with ten or eleven columns (job cost entry). The default 700px
// dialog clipped the last columns of that table with no scrollbar (#174).
function openModal(title, html, opts) {
    _modalOpener = document.activeElement;
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    $('#modal-overlay').classList.remove('hidden');
    const modal = $('#modal');
    modal.classList.toggle('modal--wide', !!(opts && opts.wide));
    const first = modal.querySelector('#modal-body ' + _FOCUSABLE.split(', ').join(', #modal-body ')) || modal;
    setTimeout(() => { try { first.focus(); } catch (e) { /* nothing focusable */ } }, 0);
}

function closeModal() {
    $('#modal-overlay').classList.add('hidden');
    $('#modal').classList.remove('modal--wide');
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

// The accounts an account picker offers for a new entry: active ones of the
// given types, and — for a business — not the nonprofit-only ones (net
// assets, 4400 In-Kind Contributions; the API marks them nonprofit_only).
// keepId: the account the record already uses, listed whatever it is so a
// save never silently drops it.
function pickerAccounts(accounts, types, keepId) {
    const nonprofit = typeof Terms !== 'undefined' && Terms.isNonprofit();
    return (accounts || []).filter(a => (keepId && a.id == keepId) || (
        types.includes(a.account_type)
        && a.is_active !== false
        && !(a.nonprofit_only && !nonprofit)));
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
    return `<div class="form-group"><label>${T('Class')}</label>
        <select name="class_id">${opts}</select></div>`;
}

// ---------------------------------------------------------------------------
// Nonprofit function dimension — program / management / fundraising (the
// Form 990 Part IX columns). Only rendered in nonprofit mode; a blank
// value means "default from the fund" (the server fills it at posting).
// ---------------------------------------------------------------------------
// A customer marked non-taxable (reseller permit, exempt organization) pays
// no sales tax on any line. The server enforces it; this makes the page say
// so instead of showing ticked Tax boxes that will not be charged. Called
// from each sales form's recalc(), so it holds after a customer change, an
// item pick or a new line. A box's own state is kept and restored when the
// form switches back to a taxable customer.
const TaxExempt = {
    enforce(customers, customerId, tbody) {
        const c = (customers || []).find(x => x.id == customerId);
        const exempt = !!c && c.is_taxable === false;
        if (!tbody) return exempt;
        tbody.querySelectorAll('.line-taxable').forEach(box => {
            if (exempt) {
                if (!box.disabled) box.dataset.was = box.checked ? '1' : '0';
                box.checked = false;
                box.disabled = true;
                box.title = `${c.name} is non-taxable (reseller or exempt): no sales tax on any line`;
            } else if (box.disabled) {
                box.disabled = false;
                box.checked = box.dataset.was !== '0';
                box.title = 'Sales tax applies to this line';
            }
        });
        return exempt;
    },
};
window.TaxExempt = TaxExempt;

const Nonprofit = {
    FUNCTIONS: [['program', 'Program services'], ['management', 'Management & general'], ['fundraising', 'Fundraising']],
    enabled() { return Terms.isNonprofit(); },
    NONE: '__none__',
    optionsHtml(selected, blank = 'From fund') {
        return `<option value="">${blank}</option>` + Nonprofit.FUNCTIONS.map(([v, l]) =>
            `<option value="${v}" ${v === selected ? 'selected' : ''}>${l}</option>`).join('')
            + `<option value="${Nonprofit.NONE}" ${selected === null ? '' : ''}>Unassigned (allocate later)</option>`;
    },
    // The API distinction: no key = take the fund's default function;
    // an explicit null = leave the line unassigned for a period-end rule.
    _payload(v) {
        if (!v) return {};
        if (v === Nonprofit.NONE) return { function: null };
        return { function: v };
    },
    linePayload(row, cls) { return Nonprofit._payload(row.querySelector(`.${cls}`)?.value); },
    formPayload(form) { return Nonprofit._payload(form.function ? form.function.value : ''); },
    // Header-level picker for one-line documents (expense, CC charge)
    functionFormGroupHtml(selected) {
        if (!Nonprofit.enabled()) return '';
        return `<div class="form-group"><label>Function</label>
            <select name="function">${Nonprofit.optionsHtml(selected)}</select></div>`;
    },
    label(fn) { const f = Nonprofit.FUNCTIONS.find(([v]) => v === fn); return f ? f[1] : (fn || ''); },
    // Per-line cells + header for multi-line documents (journal, bill):
    // a fund, a function, and the Split button that expands the line by a
    // saved allocation rule. Call loadFunds() before rendering rows.
    _funds: null,
    _rules: null,
    async loadFunds() {
        if (!Nonprofit.enabled()) return;
        try {
            [Nonprofit._funds, Nonprofit._rules] = await Promise.all([API.get('/classes'), API.get('/nonprofit/allocation-rules')]);
        } catch (e) { Nonprofit._funds = Nonprofit._funds || []; Nonprofit._rules = Nonprofit._rules || []; }
    },
    headHtml() { return Nonprofit.enabled() ? `<th scope="col">${T('Class')}</th><th scope="col">Function</th>` : ''; },
    cellHtml(cls, selected, fundSelected) {
        if (!Nonprofit.enabled()) return '';
        const funds = (Nonprofit._funds || []).map(f => `<option value="${f.id}" ${fundSelected === f.id ? 'selected' : ''}>${escapeHtml(f.name)}</option>`).join('');
        const split = (Nonprofit._rules || []).length ? ` <button type="button" class="btn btn-sm btn-secondary np-split" title="Split this line by an allocation rule" onclick="Nonprofit.splitRow(this)">Split</button>` : '';
        return `<td><select class="${cls}-fund"><option value="">header</option>${funds}</select></td>` +
            `<td style="white-space:nowrap"><select class="${cls}">${Nonprofit.optionsHtml(selected, '—')}</select>${split}</td>`;
    },
    fromRow(row, cls) { return row.querySelector(`.${cls}`)?.value || null; },
    fundFromRow(row, cls) { const v = row.querySelector(`.${cls}-fund`)?.value; return v ? parseInt(v) : null; },
    fromForm(form) { return form.function ? (form.function.value || null) : null; },

    // Split: an inline chooser under the row; on Apply the page's
    // splitApply(row, lines) clones the row into one line per share.
    splitRow(btn) {
        const row = btn.closest('tr');
        const next = row.nextElementSibling;
        if (next && next.classList.contains('np-split-row')) { next.remove(); return; }
        const rules = (Nonprofit._rules || []).map(r => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join('');
        row.insertAdjacentHTML('afterend', `<tr class="np-split-row"><td colspan="12" style="background:var(--gray-50);font-size:11px;">
            Split this line by <select class="np-split-rule">${rules}</select>
            <button type="button" class="btn btn-sm btn-primary" onclick="Nonprofit.splitApply(this)">Apply</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="this.closest('tr').remove()">Cancel</button>
            <span class="np-split-msg" style="margin-left:8px;color:var(--gray-500)"></span></td></tr>`);
    },
    async splitApply(btn) {
        const chooser = btn.closest('tr');
        const row = chooser.previousElementSibling;
        const page = row.dataset.jeline !== undefined ? JournalPage : (row.dataset.billline !== undefined ? BillsPage : null);
        if (!page || !page.splitApply) return;
        const amount = page.lineAmount(row);
        const msg = chooser.querySelector('.np-split-msg');
        if (!(amount > 0)) { msg.textContent = 'Enter an amount first'; return; }
        const ruleId = chooser.querySelector('.np-split-rule').value;
        const form = row.closest('form');
        const headerFund = form && form.class_id && form.class_id.value ? `&class_id=${form.class_id.value}` : '';
        const rowFund = row.querySelector('select[class$="-fund"]')?.value;
        try {
            const res = await API.get(`/nonprofit/allocation-rules/${ruleId}/split?amount=${amount}${rowFund ? `&class_id=${rowFund}` : headerFund}`);
            chooser.remove();
            page.splitApply(row, res);
        } catch (err) { msg.textContent = err.message; }
    },
};

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
    return `<div class="form-group"><label>${T('Job')}</label>
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

// ---------------------------------------------------------------------------
// Clipboard — one helper, because the failure is invisible to whoever built it
//
// `navigator.clipboard` requires a SECURE CONTEXT. The desktop app is served
// over plain HTTP and it works anyway, for exactly one reason: loopback is a
// secure origin by specification. `http://127.0.0.1` and `http://localhost`
// qualify; `http://192.168.x.x` does not.
//
// So every copy button in this application works on the machine running it and
// silently stops working for anyone reaching it over a LAN — `--serve-lan`,
// Server Edition, Docker published on a host address, a tablet on the Wi-Fi.
// A developer cannot reproduce that, and neither can a QA gate: both run on
// loopback. Found on the 2.11.1 gate by measuring `isSecureContext` rather
// than by anything failing (issue #137).
//
// Hence: name the real cause when we know it, and leave the text SELECTED so
// the fallback is one keystroke rather than an instruction to aim a mouse.
function _selectElementText(el) {
    if (!el || !window.getSelection || !document.createRange) return false;
    try {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        return true;
    } catch (e) {
        return false;
    }
}

/**
 * Copy `text`, reporting honestly when it cannot.
 *
 * @param {string} text      what to copy
 * @param {string} label     what to call it in the message, e.g. 'Token'
 * @param {Element} [el]     the element showing it; selected on failure so
 *                           the operator can just press the copy shortcut
 * @returns {Promise<boolean>} whether it reached the clipboard
 */
async function copyToClipboard(text, label = 'Text', el = null) {
    if (!text) {
        toast(`No ${label.toLowerCase()} to copy.`, 'error');
        return false;
    }

    const manual = _selectElementText(el)
        ? 'It is selected — press Ctrl+C (⌘C on a Mac).'
        : `Select the ${label.toLowerCase()} and copy it manually.`;

    // The cause worth naming, because it is the one nobody can reproduce.
    if (!window.isSecureContext) {
        toast(
            `Copying needs a secure connection, and this page was opened over `
            + `plain HTTP on a network address. ${manual} `
            + `(Opening SlowBooks on this machine copies normally.)`,
            'error',
        );
        return false;
    }

    if (!navigator.clipboard || !navigator.clipboard.writeText) {
        toast(`This browser will not let the page copy for you. ${manual}`, 'error');
        return false;
    }

    try {
        await navigator.clipboard.writeText(text);
        toast(`${label} copied to clipboard`);
        return true;
    } catch (e) {
        // Permission refused, or the window was not focused at the moment of
        // the write. Both are recoverable by hand.
        toast(`Couldn't copy. ${manual}`, 'error');
        return false;
    }
}
