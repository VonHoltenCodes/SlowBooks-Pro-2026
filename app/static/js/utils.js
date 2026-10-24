/**
 * Shared formatting + DOM helpers. Negative currency prints with the
 * minus before the dollar sign ("-$10.00"), as the printed documents do.
 */

function $(sel, parent = document) { return parent.querySelector(sel); }
function $$(sel, parent = document) { return [...parent.querySelectorAll(sel)]; }

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount || 0);
}

// A file's size for a person: "18 bytes", "4.2 KB", "1.3 MB". Everything
// was shown in KB to one decimal, so an 18-byte attachment read "0.0 KB"
// (2.17.3 exploratory test, W-L7).
function formatFileSize(bytes) {
    const n = Math.max(0, Number(bytes) || 0);
    if (n < 1024) return `${n} ${n === 1 ? 'byte' : 'bytes'}`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// The note beside an attachment or document the upgrade to 2.18.0 copied in
// from the folder every company shared before (nothing there said whose a
// file was, so it may be another company's), or found missing from it.
// `verb` is what the person does to put it right: 'attach' or 'upload'.
function storedFileNote(file, verb = 'attach') {
    if (!file) return '';
    let text = '';
    if (file.missing) {
        text = `Missing: this file was not in the shared folder when these books were upgraded. Delete this entry and ${verb} the file again.`;
    } else if (file.from_shared_folder) {
        text = `Copied from the folder earlier versions shared between companies. If it isn't the right file, delete it and ${verb} the right one.`;
    }
    // flex-basis: a line of its own under the file's name in a flex row
    return text ? `<div class="stored-file-note" style="flex-basis:100%; font-size:10px; color:var(--text-muted);">${escapeHtml(text)}</div>` : '';
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = dateStr.includes('T')
        ? new Date(dateStr)
        : new Date(dateStr + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return 'Invalid date';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// A posting's kind in the app's words rather than its source_type key: a
// drill-down line says "Bill payment #3", "Card charge #2223", "Journal
// entry #2224" (NEW-40). A void, an edit or an applied payment says so
// after the document's word; a key nobody listed is spelled out.
function sourceWord(type) {
    const words = {
        invoice: () => T('Invoice'), bill: 'Bill', payment: 'Payment', bill_payment: 'Bill payment',
        vendor_credit: 'Vendor credit', credit_memo: 'Credit memo', sales_receipt: 'Sales receipt',
        journal: 'Journal entry', manual_journal: 'Journal entry', manual: 'Journal entry',
        expense: 'Expense', deposit: 'Deposit', cc_charge: 'Card charge', transfer: 'Transfer',
        bank_entry: 'Bank entry', opening_balance: 'Opening balance', item_opening: 'Opening inventory', check: 'Check',
        job_cost: () => `${T('Job')} cost`, payroll: 'Pay run', pto: 'Time off accrual',
        in_kind_gift: 'In-kind gift', restriction_release: 'Release from restriction',
        functional_allocation: 'Functional allocation', late_fee: 'Late fee',
        adjustment: 'Inventory adjustment', sales_tax_payment: 'Sales tax payment',
        iif_import: 'IIF import', depreciation: 'Depreciation',
        asset_acquisition: 'Asset acquisition', asset_disposal: 'Asset disposal',
        qbo_ledger: 'QuickBooks import', qbo_journal: 'QuickBooks journal', qbo_cogs: 'QuickBooks cost of goods',
        qbo_export_import: 'QuickBooks Online export', xero_import: 'Xero import', myob_import: 'MYOB import',
        sage_import: 'Sage 50 import', wave_import: 'Wave import', zoho_import: 'Zoho Books import',
        gnucash_import: 'GnuCash import', qb_ledger_import: 'QuickBooks Desktop import',
    };
    const t = String(type || 'journal');
    for (const [end, word] of [['_void', 'void'], ['_edit', 'edit'], ['_apply', 'applied']]) {
        if (t.endsWith(end) && t.length > end.length) return `${sourceWord(t.slice(0, -end.length))} ${word}`;
    }
    const w = words[t];
    if (w) return typeof w === 'function' ? w() : w;
    const plain = t.replace(/_/g, ' ');
    return plain.charAt(0).toUpperCase() + plain.slice(1);
}

// The dates an address (or a saved report's parameters) carries:
// start_date and end_date must each be a date, and From must not be after
// To. What is not is said so and comes back as '' — ignored, so the page
// starts on its usual period and rewrites the address to the dates in use
// (docs/dev/report-views.md; NEW-35).
function datesFromQuery(q) {
    q = q || {};
    // A real calendar date, not just the shape of one: Chromium's Date
    // rolls an impossible day over (new Date('2026-02-30') is March 2),
    // so 2026-02-30 passed as a date, the date box then refused it, and
    // the report opened on another day without a word (W-5). The day the
    // Date gives back must be the day asked for.
    const isDate = (v) => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(v || '')) return false;
        const d = new Date(v);
        return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
    };
    const out = {};
    for (const key of ['start_date', 'end_date']) {
        if (q[key] && !isDate(q[key])) toast(`${key} in the address is not a date (${q[key]}) — ignored`, 'error');
        out[key] = isDate(q[key]) ? q[key] : '';
    }
    if (out.start_date && out.end_date && out.start_date > out.end_date) {
        toast(`start_date in the address (${out.start_date}) is after end_date (${out.end_date}) — both ignored`, 'error');
        out.start_date = '';
        out.end_date = '';
    }
    return out;
}

// From after To typed into a page's boxes — a report's custom range, the
// Classes list's or a class page's, a register's, the job page's — is
// refused the one way (NEW-35; W-3, W-4): said, naming both dates and the
// range kept, which the page stays on. The caller puts the boxes back and
// leaves the address alone. `kept` is { start, end }; a register or the
// job page may have one of them (or, off the boxes, neither). True when
// refused.
function reversedRangeRefused(start, end, kept) {
    if (!(start && end && start > end)) return false;
    const k = kept || {};
    const was = k.start && k.end ? `${formatDate(k.start)} to ${formatDate(k.end)}`
        : k.start ? `from ${formatDate(k.start)}`
            : k.end ? `to ${formatDate(k.end)}` : 'every date';
    toast(`From (${formatDate(start)}) is after To (${formatDate(end)}) — kept ${was}`, 'error');
    return true;
}

// An account's words in a picker or a list: "1000 - Checking", and for an
// account with no number (optional since 2.23.5) its name alone, not
// "- Checking" at the top of the list (2.23.5 round 2, NEW-144). `name`
// overrides the name shown (a path, say).
function accountLabel(a, name = null) {
    const shown = name !== null ? name : (a.name || '');
    const number = a.account_number || '';
    return number ? `${escapeHtml(number)} - ${escapeHtml(shown)}` : escapeHtml(shown);
}

function todayISO() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

// How long a toast stays: long enough to read. Three seconds for a few
// words, more for a longer message, at least six for an error, never more
// than fifteen. Every toast went after three seconds, so a two-line
// refusal (the closing-date lock) was gone before it was read (2.18.0
// gate, macbase1 NEW-13). Hovering holds a toast; a click dismisses it.
function toastMs(message, type) {
    const length = String(message == null ? '' : message).length;
    const ms = 3000 + Math.max(0, length - 40) * 60;
    return Math.min(15000, Math.max(type === 'error' ? 6000 : 3000, ms));
}

// An optional short chime when a transaction posts (#476, asked for by a
// QuickBooks Desktop user who keys a stack and misses the toast). Off by
// default, per-user (the preference post_sound, set on the Settings page).
// toast() rings it on any success toast — save, post, record payment and
// finish reconcile all end in one — and never on an error or warning.
// App.init reads the preference once and calls setPostSoundEnabled (the
// fetch lives in app.js, which already sends its own; this file never does),
// the Settings toggle keeps the cache current, and playPostSound() reads the
// cache so toast() stays synchronous.
let _postSoundOn = false;
let _postAudioCtx = null;

function setPostSoundEnabled(on) { _postSoundOn = !!on; }

// Two short sine tones, about 120 ms, quiet — an original chime, not the
// QuickBooks sound, and no audio asset. Guarded for a browser or WebView
// with no AudioContext and for an autoplay refusal before the first user
// gesture: both are caught, so a post never throws because of the sound.
// The context is made on first use (after a gesture).
function playPostSound() {
    if (!_postSoundOn) return;
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        if (!_postAudioCtx) _postAudioCtx = new Ctx();
        const ctx = _postAudioCtx;
        const now = ctx.currentTime;
        // a rising two-note chime; each tone fades in and out so it doesn't click
        [[880, 0], [1320, 0.06]].forEach(([freq, at]) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, now + at);
            gain.gain.exponentialRampToValueAtTime(0.05, now + at + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.06);
            osc.connect(gain).connect(ctx.destination);
            osc.start(now + at);
            osc.stop(now + at + 0.06);
        });
    } catch (e) { /* no audio, or autoplay refused: silent */ }
}

function toast(message, type = 'success') {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    let timer = setTimeout(() => el.remove(), toastMs(message, type));
    el.addEventListener('mouseenter', () => clearTimeout(timer));
    el.addEventListener('mouseleave', () => { timer = setTimeout(() => el.remove(), 2000); });
    el.addEventListener('click', () => el.remove());
    if (type === 'success') playPostSound();  // #476: the post chime, when it's on
}

// Chart colours for the current theme, from the --chart-* colours the
// stylesheets set per theme. A chart's lines, bars and legend keys are
// graphics, which need 3:1 on the card (WCAG 1.4.11): the bright colours
// read well on the dark card but were too faint on the light one (#00c48f
// was 2.26:1, #facc15 1.53). A legend key in HTML uses var(--chart-*)
// itself, so it follows a theme switch at once; a canvas reads this when
// it draws, and the pages redraw on slowbooks:themechange.
const CHART_FALLBACK = {
    green: '#0a9a6c', red: '#e5484d', orange: '#d9730d', amber: '#cc6a0a', crimson: '#d63240',
    blue: '#4c6ef5', purple: '#8b5cf6', pink: '#c026d3', sky: '#0b8bc4', yellow: '#b08900',
};
function chartColor(name) {
    let value = '';
    try {
        value = getComputedStyle(document.documentElement).getPropertyValue('--chart-' + name).trim();
    } catch (e) { /* no stylesheet (a probe) */ }
    return value || CHART_FALLBACK[name] || name;
}

// Money the size a chart label can hold: $1.2k, $12k, $3.4M, -$850, $45.50.
function compactMoney(v) {
    const n = Number(v) || 0;
    const a = Math.abs(n);
    let body;
    if (a >= 1e6) body = (a / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    else if (a >= 1e3) body = (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
    else if (a >= 100 || a === Math.round(a)) body = String(Math.round(a));
    else body = a.toFixed(2);
    return `${n < 0 ? '-' : ''}$${body}`;
}

// A colour as the three channels it shows over the page: a translucent one
// blended with the theme's ground first (a pale green at 35% is pale,
// whatever its RGB). null for a colour that cannot be read.
function _channels(color, dark) {
    const hex = /^#?([0-9a-f]{6})$/i.exec(String(color || '').trim());
    const rgb = /^rgba?\(([^)]+)\)/i.exec(String(color || ''));
    let r, g, b, a = 1;
    if (hex) { const n = parseInt(hex[1], 16); r = n >> 16; g = (n >> 8) & 255; b = n & 255; }
    else if (rgb) { [r, g, b, a] = rgb[1].split(',').map(x => parseFloat(x)); if (Number.isNaN(a)) a = 1; }
    else return null;
    const ground = dark ? [30, 34, 48] : [255, 255, 255];
    return [r, g, b].map((c, i) => a * c + (1 - a) * ground[i]);
}

// WCAG relative luminance and contrast, as the accessibility sweep measures
function _luminance([r, g, b]) {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function _contrast(l1, l2) { return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); }
const _INK = '#1a1a28', _INK_LUM = _luminance([26, 26, 40]);

// The text that reads best on a colour (a bar's, a slice's, a segment's):
// the app's ink or white, whichever contrasts more, with the ratio it
// gets. A caller that must meet AA (text in the page) leaves the figure
// off under 4.5; a chart label takes the better of the two.
function figureTextOn(color, dark) {
    const ch = _channels(color, dark);
    if (!ch) return { fill: dark ? '#e6e6f0' : _INK, ratio: 0 };
    const l = _luminance(ch);
    const onInk = _contrast(l, _INK_LUM), onWhite = _contrast(l, 1);
    return onInk >= onWhite ? { fill: _INK, ratio: onInk } : { fill: '#ffffff', ratio: onWhite };
}

function _textOn(color, dark) {
    return figureTextOn(color, dark).fill;
}

// A theme variable's value ('' in a probe with no stylesheet)
function cssVar(name) {
    try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (e) { return ''; }
}

// Does a line's own path run through a box? The segments into and out of
// point `i`, each sampled along its curve (Chart.js keeps a tension line's
// bezier control points on the point elements as cp1x/cp1y/cp2x/cp2y; a
// straight segment when there are none), the box grown by the stroke's
// half-width. Used to keep a point's figure off its own line (W-6).
function _lineCrosses(points, i, box, grow) {
    const x0 = box.x - grow, x1 = box.x + box.w + grow, y0 = box.y - grow, y1 = box.y + box.h + grow;
    const hit = (x, y) => x > x0 && x < x1 && y > y0 && y < y1;
    const pairs = [[points[i - 1], points[i]], [points[i], points[i + 1]]];
    for (const [a, b] of pairs) {
        if (!a || !b || a.skip || b.skip) continue;
        const curved = [a.cp2x, a.cp2y, b.cp1x, b.cp1y].every((v) => typeof v === 'number' && !Number.isNaN(v));
        for (let k = 0; k <= 24; k++) {
            const t = k / 24, u = 1 - t;
            let x, y;
            if (curved) {
                x = u * u * u * a.x + 3 * u * u * t * a.cp2x + 3 * u * t * t * b.cp1x + t * t * t * b.x;
                y = u * u * u * a.y + 3 * u * u * t * a.cp2y + 3 * u * t * t * b.cp1y + t * t * t * b.y;
            } else {
                x = a.x + (b.x - a.x) * t;
                y = a.y + (b.y - a.y) * t;
            }
            if (hit(x, y)) return true;
        }
    }
    return false;
}

// A Chart.js plugin: every bar, slice and point gets its figure as text on
// or beside the mark (#265), so the colour is not the only way from a mark
// to its amount — the legend and the table under the chart stay. A bar's
// figure sits past its end, inside the bar when the chart area has no room
// there; a stacked segment's sits in the segment when it fits; a slice's
// sits in the slice (one under 4% is too thin to hold text); a point's
// sits above it (below, for every other line), sliding sideways to stay in
// the chart area at a first or last point, and moving to the other side,
// beside the point or to a corner when its own line would run through it.
// A label that would land on one already drawn, or outside the chart area,
// or across its own line wherever it goes, is left off, and so is one
// on a mark whose colour gives neither ink nor white 4.5:1: a crowded line
// shows what fits, and the table has the rest. Colours follow the theme at
// draw time, so a theme change needs only a redraw.
function chartFigures(opts = {}) {
    const fmt = opts.format || compactMoney;
    const size = opts.size || 10;
    return {
        id: 'sbFigures',
        afterDatasetsDraw(chart) {
            const ctx = chart.ctx, area = chart.chartArea;
            if (!ctx || !area || !chart.data || !chart.data.datasets) return;
            const dark = typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'dark';
            const ink = opts.color || (dark ? '#e6e6f0' : '#1a1a28');
            const placed = [];
            const inside = (b) => b.x >= area.left && b.x + b.w <= area.right && b.y >= area.top && b.y + b.h <= area.bottom;
            const free = (b) => !placed.some(p => !(b.x + b.w < p.x || p.x + p.w < b.x || b.y + b.h < p.y || p.y + p.h < b.y));
            // 'drawn', or why not: 'outside' the chart area, 'taken' by a label
            // already there, or 'contrast' — no fill reads on the mark
            // 'crossed' — the mark's own line runs through the label (W-6)
            const draw = (text, cx, cy, fill, keepIn, crossed) => {
                if (!fill) return 'contrast';
                const w = ctx.measureText(text).width + 4, h = size + 2;
                // a point's label slides sideways to stay in the chart area (a
                // first or last month's point sits on the area's edge — NEW-81
                // / W-5); a bar's does not, since 'outside' is how it learns
                // to go inside the bar instead
                if (keepIn) cx = Math.min(Math.max(cx, area.left + w / 2), area.right - w / 2);
                const box = { x: cx - w / 2, y: cy - h / 2, w, h };
                if (!inside(box)) return 'outside';
                if (!free(box)) return 'taken';
                if (crossed && crossed(box)) return 'crossed';
                ctx.fillStyle = fill;
                ctx.fillText(text, cx, cy);
                placed.push(box);
                return 'drawn';
            };
            // text on a mark: ink or white, whichever reads better, and only
            // when it reaches AA (4.5:1) — a slice or segment where neither
            // does keeps its figure in the table, as the dashboard's aging
            // bar does (2.22.3 gate, NEW-75: the doughnut's red, blue and
            // purple drew figures at 4.2–4.4:1)
            const on = (color) => { const t = figureTextOn(color, dark); return t.ratio >= 4.5 ? t.fill : null; };
            ctx.save();
            ctx.font = `${size}px ${opts.family || 'system-ui, sans-serif'}`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            chart.data.datasets.forEach((ds, di) => {
                const meta = chart.getDatasetMeta(di);
                if (!meta || meta.hidden || !meta.data) return;
                const total = (meta.type === 'doughnut' || meta.type === 'pie')
                    ? ds.data.reduce((a, b) => a + Math.abs(Number(b) || 0), 0) : 0;
                meta.data.forEach((el, i) => {
                    const v = ds.data[i];
                    const n = Number(v);
                    if (v == null || Number.isNaN(n) || n === 0) return;
                    const text = fmt(n, ds, i);
                    const w = ctx.measureText(text).width + 4, h = size + 2;
                    const own = el.options && el.options.backgroundColor;
                    if (meta.type === 'doughnut' || meta.type === 'pie') {
                        if (total && Math.abs(n) / total < 0.04) return;
                        const p = el.tooltipPosition ? el.tooltipPosition() : el.getCenterPoint();
                        draw(text, p.x, p.y, on(own));
                    } else if (meta.type === 'bar') {
                        const base = el.base == null ? el.y : el.base;
                        const stacked = ds.stack != null;
                        // text inside a bar needs the bar's thickness as well as
                        // its length (gate round 1, NEW-61 / W-5: ~7px bars)
                        if (el.horizontal) {
                            const left = Math.min(el.x, base), right = Math.max(el.x, base);
                            const thick = el.height == null || el.height >= h;
                            if (stacked) {
                                if (thick && right - left >= w + 2) draw(text, (left + right) / 2, el.y, on(own));
                                return;
                            }
                            const past = n >= 0 ? el.x + w / 2 + 2 : el.x - w / 2 - 2;
                            if (draw(text, past, el.y, ink) !== 'drawn' && thick) draw(text, n >= 0 ? el.x - w / 2 - 2 : el.x + w / 2 + 2, el.y, on(own));
                        } else {
                            const top = Math.min(el.y, base), bottom = Math.max(el.y, base);
                            const thick = el.width == null || el.width >= w;
                            if (stacked) {
                                if (thick && bottom - top >= h + 2) draw(text, el.x, (top + bottom) / 2, on(own));
                                return;
                            }
                            const past = n >= 0 ? el.y - h / 2 - 2 : el.y + h / 2 + 2;
                            if (draw(text, el.x, past, ink) !== 'drawn' && thick) draw(text, el.x, n >= 0 ? el.y + h / 2 + 2 : el.y - h / 2 - 2, on(own));
                        }
                    } else {
                        // a point: above it, below for every other line — its
                        // own side, so a label never reads as the other
                        // line's; then the other side, beside the point, and
                        // the four corners, the first place that is on the
                        // chart, free, and not crossed by the point's own line
                        // (the segments into and out of it, along their curve
                        // — 2.22.3 gate, W-6: a label under a peak sat on the
                        // line rising into it); one with no such place is left
                        // off, and the table has it
                        const crossed = (box) => _lineCrosses(meta.data, i, box, 2);
                        // its own side first — straight, then the two corners —
                        // so a moved label still reads as this line's; then
                        // beside the point; the other side last
                        const up = di % 2 ? 1 : -1, gx = w / 2 + 10, gy = h / 2 + 6;
                        const spots = [
                            [0, up], [-gx, up], [gx, up], [-gx, 0], [gx, 0],
                            [0, -up], [-gx, -up], [gx, -up],
                        ];
                        for (const [sx, sy] of spots) {
                            if (draw(text, el.x + sx, el.y + sy * gy, ink, true, crossed) === 'drawn') break;
                        }
                    }
                });
            });
            ctx.restore();
        },
    };
}

// A toast that carries one action (e.g. "Saved to … [Show in folder]").
// Stays longer than a plain toast because the user has to read a path.
// With dismissLabel it is a question ("Add … as a vendor?" Add / Not now):
// the second button only closes it, as the timeout does.
//
// opts (#386 review — a question has to be answerable by keyboard):
//   focusOnShow  move focus to the action button when the toast appears
//   returnFocus  an element, or a function returning one, to focus when the
//                toast goes (the row the question is about)
// The auto-dismiss never fires while the toast has focus or the pointer is
// over it, so a keyboard user is not raced; it re-arms when both leave.
function toastAction(message, actionLabel, onClick, ms = 8000, dismissLabel = null, opts = {}) {
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
    let gone = false;
    // answered: the action or dismiss button was clicked. Focus goes back to
    // the row only when the toast is answered, or the toast itself still had
    // focus as it went — never on a plain auto-dismiss, which would yank the
    // caret out of whatever the user moved on to (#386 review: typing in Memo
    // past the timeout jumped to Payee).
    const remove = (answered) => {
        if (gone) return;
        gone = true;
        const hadFocus = el.contains(document.activeElement);
        el.remove();
        if (!answered && !hadFocus) return;
        const back = typeof opts.returnFocus === 'function' ? opts.returnFocus() : opts.returnFocus;
        if (back && document.contains(back) && typeof back.focus === 'function') focusFor(back, true);
    };
    btn.addEventListener('click', () => { try { onClick(); } finally { remove(true); } });
    el.appendChild(text);
    el.appendChild(btn);
    if (dismissLabel) {
        const no = document.createElement('button');
        no.type = 'button';
        no.className = 'btn btn-sm btn-secondary';
        no.textContent = dismissLabel;
        no.addEventListener('click', () => remove(true));
        el.appendChild(no);
    }
    container.appendChild(el);
    if (opts.focusOnShow) focusFor(btn, true);
    // auto-dismiss, but hold while the pointer or the keyboard is on it
    const arm = () => setTimeout(() => {
        if (gone) return;
        if (el.matches(':hover') || el.contains(document.activeElement)) { arm(); return; }
        remove(false);
    }, ms);
    arm();
}

// Modal accessibility: the dialog takes focus when it opens, Tab and
// Shift+Tab cycle inside it, Escape closes it, and focus returns to
// whatever opened it. (Audit finding 3: role/aria-modal live on #modal in
// index.html; this is the behaviour half.)
let _modalOpener = null;

// What Tab can land on, before the browser's own rules thin it out.
const _TAB_SEL = 'a[href], area[href], button, input, select, textarea, summary, iframe, [tabindex], [contenteditable]:not([contenteditable="false"])';

// Drawn, as the browser's Tab judges it: display, visibility and a folded
// <details> all count. checkVisibility is the browser's own answer; an
// older browser is asked the long way.
function _drawn(el) {
    if (typeof el.checkVisibility === 'function') return el.checkVisibility({ visibilityProperty: true });
    return el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
}

// The controls Tab walks under `root`, in the order the browser's own Tab
// takes them: a tabindex above 0 first, lowest first, then everything
// else as the document has it. What the browser skips, this skips: a
// disabled control (a disabled fieldset's too), tabindex="-1" (a
// type-ahead's hidden select), anything not drawn (a folded chooser, a
// hidden input), and the radios of a group bar one: the checked one, or
// the first where none is checked (Chromium lands there from either
// side). From one of its own (`from`, the control Tab leaves) the whole
// group is stepped over.
function _tabStops(root, from) {
    const radio = (el) => (el.tagName === 'INPUT' && el.type === 'radio' && el.name) ? el.name : null;
    const all = [...root.querySelectorAll(_TAB_SEL)]
        .filter(el => el.tabIndex >= 0 && !el.matches(':disabled') && _drawn(el));
    const picked = new Set(all.filter(el => radio(el) && el.checked).map(radio));
    const leaving = from ? radio(from) : null;
    const seen = new Set();
    const stops = all.filter(el => {
        const g = radio(el);
        if (!g) return true;
        if (g === leaving) return false;
        if (picked.has(g)) return el.checked;
        if (seen.has(g)) return false;
        seen.add(g);
        return true;
    });
    const ahead = stops.filter(el => el.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex);
    return ahead.concat(stops.filter(el => el.tabIndex === 0));
}

// A date or time field: Tab walks its own segments (month, day, year)
// before leaving it, and only the browser knows which has the caret.
function _hasSegments(el) {
    return !!el && el.tagName === 'INPUT' && /^(date|time|datetime-local|month|week)$/.test(el.type);
}

// Escape in a date or time field closes the field's calendar; WebKit
// sends the key on to the page too, where it closed the whole dialog with
// it, an unsaved invoice lost (macOS gate NEW-33, the owner's own
// keyboard). That Escape leaves the field instead, the dialog itself
// taking focus, so the next one closes the dialog. True when it was one.
// (Both Escape handlers, this file's and app.js's, ask.)
function escapeLeavesPicker(e) {
    if (!_hasSegments(e.target)) return false;
    const modal = document.getElementById('modal');
    const overlay = document.getElementById('modal-overlay');
    if (modal && overlay && !overlay.classList.contains('hidden') && modal.contains(e.target)) modal.focus();
    return true;
}

// Focus the app moves itself because of the keyboard — Tab inside a
// dialog, Back's return to the row left, a disabled button handing over —
// says so outright. WebKit counts focus moved in script as the keyboard's,
// and draws :focus-visible (the ring), only when the focus before it was
// the keyboard's, so in a dialog opened with a click Tab moved focus with
// no ring to see (macOS gate NEW-43, round 3). After a pointer, plain
// focus leaves the browser's own rule: no ring for a click.
function focusFor(el, keyboard) {
    if (!el) return false;
    try { el.focus(keyboard ? { focusVisible: true } : undefined); return true; } catch (e) { return false; }
}

// A skip link inside a long form or page (#266): hidden until the keyboard
// reaches it, then shown where it sits, so Tab from the top of an invoice
// offers "Skip to the line items" before the forty stops of the address
// block. The landing is the first control inside the target (a line's
// first box, the chart's filter), with the keyboard's ring on it.
function skipLinkHtml(target, text) {
    return `<a href="#" class="skip-link skip-link--inline" onclick="return skipTo(event, '${escapeHtml(target)}')">${escapeHtml(text)}</a>`;
}

function skipTo(e, target) {
    if (e && e.preventDefault) e.preventDefault();
    const root = document.querySelector(target);
    if (!root) return false;
    const into = root.matches(_TAB_SEL) && root.tabIndex >= 0 ? root : _tabStops(root, null)[0];
    if (into) { focusFor(into, true); return false; }
    // nothing to land on inside: the place itself, as the main skip link does
    root.setAttribute('tabindex', '-1');
    root.addEventListener('blur', () => root.removeAttribute('tabindex'), { once: true });
    focusFor(root, true);
    return false;
}

// Whether the last input was the keyboard (App.keyboardFocus), for focus
// the app moves after either.
function _byKeyboard() {
    return !!(window.App && typeof window.App.keyboardFocus === 'function' && window.App.keyboardFocus());
}

// Focus as Tab gives it: a text box's words selected, as the browser
// does; a textarea keeps its caret (the browser selects nothing there,
// and a note being edited must not vanish under the next keystroke).
function _tabTo(el) {
    if (!focusFor(el, true)) return;
    if (el.tagName === 'INPUT' && /^(text|search|url|tel|password|email|number)$/.test(el.type)) {
        try { el.select(); } catch (e) { /* not selectable */ }
    }
}

// opts.wide: a form whose rows are wider than a dialog — the line-item
// tables with ten or eleven columns (job cost entry). The default 700px
// dialog clipped the last columns of that table with no scrollbar (#174).
function openModal(title, html, opts) {
    _modalOpener = document.activeElement;
    const wasOpen = !$('#modal-overlay').classList.contains('hidden');
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    // A read-only sign-in sees the form locked, not a 403 after filling it
    // in (app.js App.lockForms).
    if (window.App && typeof window.App.lockForms === 'function') window.App.lockForms($('#modal-body'));
    if (window.App && typeof window.App.hideWriteControls === 'function') window.App.hideWriteControls($('#modal-body'));
    // A long form names where its lines are (data-skip-to, with the link's
    // words in data-skip-text) and gets a skip link as its first child
    // (#266) — placed here, so the page scripts call nothing new and the
    // link is the dialog's first stop wherever the form came from.
    for (const form of $('#modal-body').querySelectorAll('form[data-skip-to]')) {
        if (!form.querySelector(':scope > a.skip-link--inline')) {
            form.insertAdjacentHTML('afterbegin', skipLinkHtml(form.dataset.skipTo, form.dataset.skipText || 'Skip to the lines'));
        }
    }
    $('#modal-overlay').classList.remove('hidden');
    const modal = $('#modal');
    modal.classList.toggle('modal--wide', !!(opts && opts.wide));
    // No address of its own until the router gives it one (a document, a
    // page, a report view: App.dialogAddressed); over a plain form the
    // toolbar's Back is inert, and sits under the overlay (App.syncBack).
    // A mark that is the opener's own stays: one found while no dialog was
    // open (set since the last close, so by this opener, its fetch awaited
    // or not), or one set in this very task over an open dialog (a view
    // that put its address on the bar and opens at once). Any other — a
    // form over an addressed dialog — goes.
    const own = modal.dataset.address === (location.hash || '#/') && (!wasOpen || !!(window.App && window.App._marking));
    if (!own) delete modal.dataset.address;
    if (window.App && typeof window.App.syncBack === 'function') window.App.syncBack();
    // The first control takes focus once the dialog's own enhancements (the
    // type-ahead boxes) are in place. Never a control that undoes (Void,
    // Delete: data-destructive), where Return would do it: the dialog takes
    // focus itself then, as one with no control does, and a screen reader
    // says its title (macOS gate NEW-32: the journal entry, job cost and
    // deposit views opened on Void).
    // A form's own skip link (skipLinkHtml) is the first stop for Tab and
    // a screen reader, not where a dialog opens: the first field is.
    setTimeout(() => {
        const stops = _tabStops($('#modal-body'), null);
        const first = stops.find(el => !el.matches('.skip-link')) || stops[0];
        const target = first && !first.matches('[data-destructive]') ? first : modal;
        focusFor(target, _byKeyboard());
    }, 0);
}

// opts.keepAddress: the app is moving on by itself (App.navigate, Back or
// Forward, a view's "Back to …"), and the address is the move's to set.
// Without it the dialog was dismissed — Close, ×, Escape — and one that
// had an address of its own (a report view, a customer's page) leaves
// it: the page under it goes on the bar (App.dialogClosed), so the next
// refresh, reload or Back does not reopen what was closed (NEW-23).
function closeModal(opts) {
    const overlay = $('#modal-overlay');
    const wasOpen = !overlay.classList.contains('hidden');
    overlay.classList.add('hidden');
    $('#modal').classList.remove('modal--wide');
    delete $('#modal').dataset.address;
    if (window.App && typeof window.App.syncBack === 'function') window.App.syncBack();
    const opener = _modalOpener;
    _modalOpener = null;
    if (opener && document.contains(opener)) focusFor(opener, _byKeyboard());
    if (wasOpen && !(opts && opts.keepAddress) && window.App && typeof App.dialogClosed === 'function') App.dialogClosed();
}

// Keys inside an open dialog. Escape closes it. Tab and Shift+Tab are
// moved in script, through every control the browser's own Tab would
// reach, in its order, wrapping at the ends: WebKit's Tab, under macOS's
// default keyboard setting ("text boxes and lists only"), skipped buttons
// and links and left the dialog for the page, so "Back to …", Prev/Next
// and a report's links were out of reach (macOS gate NEW-25). Chromium
// walks the same sequence either way. A date or time field is the one
// place the browser keeps Tab (its segments, _hasSegments): a Tab from
// one moves to its next segment or leaves it, and a Tab into one lands
// on the segment the browser chooses; where the browser then leaves the
// control for the wrong one, the walk puts that right a moment later. A
// control that has already claimed the key (e.defaultPrevented) is left
// alone.
let _tabFix = null;
function modalKeydown(e) {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
        if (escapeLeavesPicker(e)) return;
        e.preventDefault(); closeModal(); return;
    }
    if (e.key !== 'Tab' || e.defaultPrevented) return;
    clearTimeout(_tabFix);
    const modal = document.getElementById('modal');
    const active = document.activeElement;
    // A toastAction toast sits outside the dialog (in #toast-container), so
    // the dialog's trapped Tab could never reach its buttons: the + Entry
    // form's "Add this payee?" offer was unanswerable by keyboard (#386
    // review). Fold the toast's stops into the cycle so Tab walks out to it
    // and wraps back into the form.
    const toasts = document.getElementById('toast-container');
    const stops = _tabStops(modal, active).concat(toasts ? _tabStops(toasts, active) : []);
    if (!stops.length) { e.preventDefault(); modal.focus(); return; }
    const back = e.shiftKey;
    const i = stops.indexOf(active);
    let next;
    if (i >= 0) {
        next = stops[(i + (back ? -1 : 1) + stops.length) % stops.length];
    } else if (active && modal.contains(active)) {
        // from something that is not a stop (the dialog itself, a button
        // disabled while it had focus): the next stop along from it
        const follows = (s) => active.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING;
        next = back ? (stops.filter(s => !follows(s)).pop() || stops[stops.length - 1]) : (stops.find(follows) || stops[0]);
    } else {
        next = back ? stops[stops.length - 1] : stops[0];
    }
    const inDialog = !!active && active !== modal && modal.contains(active);
    if (inDialog && (_hasSegments(active) || _hasSegments(next))) {
        // The control says when the browser's Tab leaves it, and for where
        // (relatedTarget); a segment move leaves it on, and the listener
        // is taken off again once this key is done with.
        const left = (ev) => { if (ev.relatedTarget !== next) _tabFix = setTimeout(() => _tabTo(next), 0); };
        active.addEventListener('focusout', left, { once: true });
        setTimeout(() => active.removeEventListener('focusout', left), 0);
        return;
    }
    e.preventDefault();
    _tabTo(next);
}
document.addEventListener('keydown', modalKeydown);

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

// Every row a list endpoint has, a page at a time. For lists that must be
// complete — the open invoices a payment can go to — never just the newest
// page (issue #191). It asks until a page comes back empty, so a server that
// sends fewer than asked for (payroll sends at most 500) is read to the end.
async function fetchAllPages(path, pageSize = 1000) {
    const sep = path.includes('?') ? '&' : '?';
    let all = [];
    let skip = 0;
    for (let n = 0; n < 1000; n++) { // a million rows; never an endless loop
        const url = path + sep + 'skip=' + skip + '&limit=' + pageSize;
        const page = await API.get(url);
        if (!page.length) break;
        all = all.concat(page);
        skip += page.length;
    }
    return all;
}

// A list page's rows: the newest `cap`, with a note offering Show all, or
// every row once the page's Show all was clicked (page._showAll).
async function listRows(page, path, showAllCall, noun, cap = 500) {
    const all = !!page._showAll;
    page._showAll = false;
    const sep = path.includes('?') ? '&' : '?';
    const raw = all ? await fetchAllPages(path) : await API.get(path + sep + 'limit=' + (cap + 1));
    return { rows: all ? raw : raw.slice(0, cap), note: all ? '' : listCapNote(raw, cap, showAllCall, noun) };
}

// A list page shows the newest `cap` rows and says so, with a way to see
// them all; `rows` came back from a request for cap + 1.
function listCapNote(rows, cap, showAllCall, noun) {
    if (rows.length <= cap) return '';
    return `<p class="list-cap-note" style="margin:0 0 8px; font-size:12px; color:var(--text-muted);">
        Showing the newest ${cap} ${noun}. <button type="button" class="btn btn-sm btn-secondary" onclick="${showAllCall}">Show all</button></p>`;
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
                <select id="${filter.id}" aria-label="Status" onchange="filterRows('${filter.id}', '${filter.rowSelector}')">
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
// With Settings' "Warn when a transaction is saved without a class" on
// (#243), a new document starts with no class chosen — a blank first
// choice — and ClassWarn.ok asks before it is saved that way.
async function classFormGroupHtml(selectedId) {
    let classes = [];
    try { classes = await API.get('/classes'); } catch (e) { return ''; }
    if (!classes.length) return '';
    const blank = !selectedId && ClassWarn.enabled();
    const opts = (blank ? `<option value="" selected>— choose a ${T('class').toLowerCase()} —</option>` : '') + classes.map(c =>
        `<option value="${c.id}" ${selectedId ? (c.id === selectedId ? 'selected' : '') : (c.is_system_default && !blank ? 'selected' : '')}>${escapeHtml(c.name)}</option>`
    ).join('');
    return `<div class="form-group"><label>${T('Class')}</label>
        <select name="class_id">${opts}</select></div>`;
}

// Settings → Classes → "Warn when a transaction is saved without a class"
// (#243, QuickBooks' "Prompt to assign classes"). Every document form asks
// ClassWarn.ok before it saves: with the setting off, or a class chosen on
// the header, or one on every line, it says yes; otherwise it asks, and the
// person decides — a transaction saved without a class is reported under
// Uncategorized, which is what the by-class reports always promised. The
// setting arrives with the company settings the shell loads (App.settings).
const ClassWarn = {
    enabled() {
        return typeof App !== 'undefined' && !!App.settings && App.settings.class_warn_blank === 'true';
    },
    message() {
        return Terms.text('No class is chosen, so this will be reported under Uncategorized on the P&L by Class. Save it anyway?');
    },
    // `lines`, for a multi-line form: { rows: '#bill-lines tr', cls: 'line-function' }
    // — the line selects are `.${cls}-fund`; a class on every line is as good
    // as one on the header.
    async ok(form, lines = null) {
        if (!ClassWarn.enabled()) return true;
        if (!form || !form.class_id) return true;   // no class picker on this form
        if (form.class_id.value) return true;
        if (lines) {
            const selects = $$(lines.rows).map(r => r.querySelector(`.${lines.cls}-fund`)).filter(Boolean);
            if (selects.length && selects.every(sel => sel.value)) return true;
        }
        return confirm(ClassWarn.message());
    },
    // a page form with its own class select (Make Deposits)
    okValue(value) {
        if (!ClassWarn.enabled() || value) return true;
        return confirm(ClassWarn.message());
    },
};

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
    // Per-line cells + header for multi-line documents (journal, bill,
    // invoice): a class, and in nonprofit mode a function and the Split
    // button that expands the line by a saved allocation rule. Call
    // loadFunds() before rendering rows.
    //
    // The class cell is for every company (#243): a bill from the lumber
    // yard splits across two divisions on its lines, and posting has always
    // honoured a line's class over the header's. It is drawn once the
    // company has a class of its own (Uncategorized alone has nothing to
    // choose); a nonprofit's funds always draw it. An archived class a line
    // already carries stays a choice, so an edit does not strip it.
    // Neither cell names itself: nameFields names a field in a table from
    // its column and its line ("Class, line 2", "Function, line 2"), as
    // the Item and Account cells beside them are named. A fixed "Class for
    // this line" read the same on every line (macOS gate NEW-37).
    _funds: null,
    _rules: null,
    async loadFunds() {
        try {
            const [funds, rules] = await Promise.all([
                API.get('/classes?include_archived=true'),
                Nonprofit.enabled() ? API.get('/nonprofit/allocation-rules') : Promise.resolve([]),
            ]);
            Nonprofit._funds = funds;
            Nonprofit._rules = rules;
        } catch (e) { Nonprofit._funds = Nonprofit._funds || []; Nonprofit._rules = Nonprofit._rules || []; }
    },
    lineClassShown() {
        return Nonprofit.enabled() || (Nonprofit._funds || []).some(f => !f.is_system_default && !f.is_archived);
    },
    classHeadHtml() { return Nonprofit.lineClassShown() ? `<th scope="col">${T('Class')}</th>` : ''; },
    classCellHtml(cls, fundSelected) {
        if (!Nonprofit.lineClassShown()) return '';
        const funds = (Nonprofit._funds || [])
            .filter(f => !f.is_archived || f.id === fundSelected)
            .map(f => `<option value="${f.id}" ${fundSelected === f.id ? 'selected' : ''}>${escapeHtml(f.name)}${f.is_archived ? ' (archived)' : ''}</option>`).join('');
        return `<td><select class="${cls}-fund" data-no-search><option value="">Same as header</option>${funds}</select></td>`;
    },
    headHtml() { return Nonprofit.classHeadHtml() + (Nonprofit.enabled() ? `<th scope="col">Function</th>` : ''); },
    cellHtml(cls, selected, fundSelected) {
        const split = Nonprofit.enabled() && (Nonprofit._rules || []).length ? ` <button type="button" class="btn btn-sm btn-secondary np-split" title="Split this line by an allocation rule" onclick="Nonprofit.splitRow(this)">Split</button>` : '';
        return Nonprofit.classCellHtml(cls, fundSelected) + (Nonprofit.enabled()
            ? `<td style="white-space:nowrap"><select class="${cls}">${Nonprofit.optionsHtml(selected, '—')}</select>${split}</td>`
            : '');
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
// the list narrows to that customer's jobs — the moment the customer is
// chosen, and whenever the picker is reached — so the customer can be
// changed at any point and the jobs follow, and a chosen job of another
// customer is cleared. Returns '' when the company has no jobs yet — the
// field simply doesn't exist.
// ---------------------------------------------------------------------------
async function jobFormGroupHtml(selectedId, customerSelectId) {
    let jobs = [];
    try { jobs = await API.get('/jobs'); } catch (e) { return ''; }
    if (!jobs.length) return '';
    const opts = jobs.map(j =>
        `<option value="${j.id}" data-customer="${j.customer_id}" ${selectedId === j.id ? 'selected' : ''}>${escapeHtml(j.full_name || j.name)}</option>`
    ).join('');
    const bind = customerSelectId ? `data-customer-select="${customerSelectId}"` : '';
    return `<div class="form-group"><label>${T('Job')}</label>
        <select name="job_id" ${bind}><option value="">— No job —</option>${opts}</select></div>`;
}

const JobPicker = {
    // Hide jobs that belong to other customers than the one selected, and
    // clear a chosen one that does. No customer yet: every job; a customer
    // being quick-added ("+ New Customer") has none; a form whose blank
    // choice stands for a customer (the sales receipt's walk-in, or
    // "__new__" until there is one) says so with data-blank-customer on
    // its customer select.
    sync(select) {
        const custSel = document.getElementById(select.dataset.customerSelect);
        const cid = custSel ? (custSel.value || custSel.dataset.blankCustomer || '') : '';
        for (const opt of select.options) {
            if (!opt.value) continue;
            const mine = !cid || (cid !== '__new__' && opt.dataset.customer === cid);
            opt.hidden = !mine;
            if (!mine && opt.selected) select.value = '';
        }
    },
    // The job pickers a customer select narrows.
    boundTo(custSel) {
        if (!custSel || !custSel.id) return [];
        return Array.from(document.querySelectorAll(`select[data-customer-select="${CSS.escape(custSel.id)}"]`));
    },
    // A line's stored job id, kept only if it is the form's customer's: a
    // job of another customer stored on a line (books from before the rule
    // that a document carries its own customer's jobs) is dropped, so the
    // save clears it. A job the picker doesn't list (an inactive one) is
    // kept — it can't be told apart from the customer's own.
    ownJob(form, jobId) {
        if (!jobId || !form.job_id || !form.customer_id || !form.customer_id.value) return jobId;
        const opt = Array.from(form.job_id.options).find(o => o.value === String(jobId));
        if (!opt || !opt.dataset.customer) return jobId;
        return opt.dataset.customer === form.customer_id.value ? jobId : null;
    },
    // What to say when a copy (a converted estimate, a duplicated invoice)
    // left another customer's jobs off: the server names them.
    leftOffNote(names) {
        if (!names || !names.length) return '';
        const one = names.length === 1;
        return `${T(one ? 'Job' : 'Jobs')} ${names.join(', ')} ${one ? "isn't" : "aren't"} this ${T('customer')}'s and ${one ? 'was' : 'were'} left off`;
    },
};

// The select's own focus ran the filter, and the type-ahead box in front of
// it (combobox.js) never gives it that, so the list was never narrowed
// (2.22.0 gate, NEW-36). Now: the customer's choice narrows it as it is
// made, and reaching the picker — its box or the select itself — does too.
// (The node probes load this file with a bare document.)
if (typeof document !== 'undefined' && document.body && typeof document.addEventListener === 'function') {
    document.addEventListener('change', (e) => {
        if (e.target instanceof HTMLSelectElement) JobPicker.boundTo(e.target).forEach(s => JobPicker.sync(s));
    });
    document.addEventListener('focusin', (e) => {
        const el = e.target;
        if (!(el instanceof Element)) return;
        const wrap = el.closest('.cbx');
        const sel = el.matches('select[data-customer-select]') ? el
            : wrap ? wrap.querySelector('select[data-customer-select]') : null;
        if (sel) JobPicker.sync(sel);
    });
}

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

// The fetched rate fills the field only while nobody has typed in it since
// the currency was chosen: a rate typed while the feed answers is the
// operator's, and the document books at it. An answer for a currency that
// has since been changed is dropped too.
async function prefillFxRate(select) {
    const form = select.closest('form');
    const rateInput = form?.querySelector('[name=exchange_rate]');
    if (!rateInput) return;
    if (!rateInput.dataset.fxWatched) {
        rateInput.dataset.fxWatched = '1';
        rateInput.addEventListener('input', () => { rateInput.dataset.fxTyped = '1'; });
    }
    const asked = String((Number(rateInput.dataset.fxAsked) || 0) + 1);
    rateInput.dataset.fxAsked = asked;
    delete rateInput.dataset.fxTyped;
    const currency = select.value;
    try {
        const data = await API.get(`/fx/rate?from_currency=${encodeURIComponent(currency)}`);
        const untouched = rateInput.dataset.fxAsked === asked && !rateInput.dataset.fxTyped;
        if (data.rate && untouched && select.value === currency) rateInput.value = data.rate;
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
    if (!el) return false;
    // a text box selects its own value (a range over the element would
    // only take its first text)
    if (typeof el.select === 'function' && typeof el.setSelectionRange === 'function') {
        try { el.focus(); el.select(); return true; } catch (e) { return false; }
    }
    if (!window.getSelection || !document.createRange) return false;
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
 * One line of something shown once (an invitation, a copy's passphrase) for
 * a three-column grid: its label, the value as text a click selects whole,
 * and a Copy button of its own (NEW-167: a box that selected all of its text
 * on any click gave no way to copy the password alone).
 * @param {string} id       the value element's id, for the Copy fallback
 * @param {string} label    what the line is, e.g. 'One-time password'
 * @param {string} value    the text
 * @param {string} handler  the button's onclick
 * @returns {string} HTML
 */
function copyLineHtml(id, label, value, handler) {
    return `<span style="font-size:12px; color:var(--text-muted);">${escapeHtml(label)}</span>`
        + `<code id="${id}" style="font-family:var(--font-mono); font-size:12px; user-select:all; overflow-wrap:anywhere;">${escapeHtml(value || '')}</code>`
        + `<button type="button" class="btn btn-secondary btn-sm" aria-label="Copy the ${escapeHtml(label.toLowerCase())}" onclick="${handler}">Copy</button>`;
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

// ---------------------------------------------------------------------------
// Every form field gets a name a screen reader can say (#198).
//
// Forms put a <label> beside their field (<div class="form-group"><label>
// Customer *</label><select>) without tying the two, so a screen reader said
// "combo box" where it should have said "Customer"; a grid of inputs (a
// budget, a batch of payments) had no names at all. nameFields ties them
// wherever a page or dialog draws them, so no template has to remember:
//   - a form group's label is tied to its field (clicking it focuses the
//     field), and a trailing "*" reads as required rather than "star";
//   - a label written just before its field is tied to it;
//   - a field in a table is named from its column heading and its row.
// A field that already has a name (its own label, aria-label or
// aria-labelledby) is left as it is.
// ---------------------------------------------------------------------------
const _FIELD_SEL = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), select, textarea';
let _fieldSeq = 0;

function _ownName(el) {
    return (el.labels && el.labels.length) || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
}

function _cellText(cell) {
    return cell && !cell.querySelector(_FIELD_SEL) ? cell.textContent.replace(/\s+/g, ' ').trim() : '';
}

// A label's trailing "*" marks the field required: hidden from speech, and
// the field says it is required instead.
function _requiredStar(label, field) {
    for (let n = label.lastChild; n; n = n.previousSibling) {
        if (n.nodeType === Node.ELEMENT_NODE && n.getAttribute('aria-hidden') === 'true') return;
        if (n.nodeType !== Node.TEXT_NODE || !n.textContent.trim()) continue;
        const m = n.textContent.match(/^(.*?)\s*\*\s*$/s);
        if (!m) return;
        n.textContent = m[1] + ' ';
        const star = document.createElement('span');
        star.setAttribute('aria-hidden', 'true');
        star.textContent = '*';
        n.after(star);
        if (!field.required) field.setAttribute('aria-required', 'true');
        return;
    }
}

function _tieLabel(label, field) {
    if (!field.id) field.id = `fld-${++_fieldSeq}`;
    if (!label.htmlFor) {
        label.htmlFor = field.id;
    } else if (label.htmlFor !== field.id) {
        if (!label.id) label.id = `lbl-${++_fieldSeq}`;
        field.setAttribute('aria-labelledby', label.id);
    }
    _requiredStar(label, field);
}

function _gridName(field) {
    const cell = field.closest('td');
    const row = cell && cell.parentElement;
    const table = row && row.closest('table');
    if (!table) return '';
    // the column heading over this cell, colspans counted
    let col = 0;
    for (const c of row.cells) { if (c === cell) break; col += c.colSpan || 1; }
    let heading = '';
    const head = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    if (head) {
        let at = 0;
        for (const h of head.cells) {
            if (col >= at && col < at + (h.colSpan || 1)) { heading = _cellText(h); break; }
            at += h.colSpan || 1;
        }
    }
    // the row: the words of its first cell before this one that has any (an
    // amount isn't a row's name), or "line N" where they're all fields
    let rowName = '';
    for (const c of row.cells) {
        if (c === cell) break;
        if (c.matches('.amount, .col-amount')) continue;
        rowName = _cellText(c);
        if (rowName) break;
    }
    if (!rowName && row.parentElement && row.parentElement.tagName === 'TBODY') {
        rowName = `line ${row.sectionRowIndex + 1}`;
    }
    return [heading, rowName].filter(Boolean).join(', ');
}

// A placeholder or title: a name, if nothing better is found.
function _hint(el) {
    return (el.getAttribute('title') || (el.tagName !== 'SELECT' && el.getAttribute('placeholder')) || '').trim();
}

// Chromium reads a placeholder or title as a field's name; WebKit, and so
// VoiceOver on the Mac, doesn't. Where that's all a field has ("Email", the
// search boxes), it becomes the name, a trailing "*" read as required.
function _nameFromHint(field) {
    const hint = _hint(field);
    if (!hint) return;
    const m = hint.match(/^(.*?)\s*\*\s*$/s);
    field.setAttribute('aria-label', m ? m[1] : hint);
    if (m && !field.required) field.setAttribute('aria-required', 'true');
}

function nameFields(root = document) {
    for (const field of root.querySelectorAll(_FIELD_SEL)) {
        // out of the accessibility tree (a type-ahead picker's select,
        // whose box carries the name): nothing to name
        if (_ownName(field) || field.closest('[aria-hidden="true"]')) continue;
        const group = field.closest('.form-group');
        const label = group && [...group.querySelectorAll('label')].find(l => !l.querySelector(_FIELD_SEL));
        const before = field.previousElementSibling;
        if (label) {
            // The label names the group's first field. Another field in the
            // group that has a placeholder of its own keeps it: a quick add's
            // "Email" and "Phone" under Customer aren't called "Customer" too.
            if (!(label.htmlFor && label.htmlFor !== field.id && _hint(field))) {
                _tieLabel(label, field);
                continue;
            }
        } else if (before && before.tagName === 'LABEL' && !before.htmlFor && !before.querySelector(_FIELD_SEL)) {
            _tieLabel(before, field);
            continue;
        } else if (field.closest('td')) {
            const name = _gridName(field);
            if (name) {
                field.setAttribute('aria-label', name);
                continue;
            }
        }
        _nameFromHint(field);
    }
}

// Whatever a page or a dialog draws, as it is drawn. Setting attributes
// doesn't wake the observer; wrapping a label's "*" does, once, and finds
// nothing left to do.
// (The node tests that load this file have no MutationObserver or body.)
if (typeof MutationObserver === 'function' && typeof document !== 'undefined' && document.body) {
    new MutationObserver(() => nameFields(document)).observe(document.body, { childList: true, subtree: true });
    nameFields(document);
}
