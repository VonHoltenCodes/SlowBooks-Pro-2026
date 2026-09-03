/**
 * Box-to-fix canvas (receipt intake v2).
 *
 * Auto-first, box-to-fix: after a scan, the operator can open this panel to
 * see the scanned image with the OCR word boxes drawn on it. Suggested field
 * boxes (total/tax/subtotal/date) are located by matching the v1 parse
 * values back to word boxes. Clicking a suggestion or dragging a rectangle
 * runs field-aware region OCR (crop + upscale + contrast + charset) on the
 * server and applies the result to the form — the low-contrast rescue.
 *
 * Two orders both work, no scrolling away from the receipt (VH308 lap,
 * 2026-09-02): box-first — drag, then tap a field button on the toolbar
 * UNDER the canvas; or arm-first — tap the field button, then drag. Every
 * step redraws immediately: a drawn box lights up amber the moment the
 * mouse lifts, and tapping a field recolors it before the server read
 * starts (the read only fills in the value).
 *
 * Coordinates: the canvas is drawn at display scale; all regions are sent
 * to the server in natural image pixels (the /intake/{id}/image space).
 * Vanilla JS + 2D canvas — no dependencies, per the design doc.
 */
const OcrCanvas = {
    _img: null,          // HTMLImageElement (natural size)
    _scale: 1,
    _words: [],          // [{text,left,top,width,height,conf}] natural px
    _suggestions: [],    // [{key,label,box:{left,top,width,height}}]
    _intakeId: null,
    _merchant: null,     // merchant string for template saves (v3)
    _applyField: null,   // (fieldKey, value, meta) => void
    _drag: null,         // {x0,y0,x1,y1} in display px while dragging
    _pendingBox: null,   // natural-px box awaiting a field-type choice
    _pendingSug: null,   // the suggestion that was clicked (null for a drag)
    _armed: null,        // field key chosen BEFORE drawing a box (arm-first)
    _values: {},         // fieldKey -> last value read, shown on the toolbar buttons
    _fieldTargets: null, // (fieldKey) => form input the value lands in, or null
    _outlined: [],       // [{el, outline, boxShadow}] to restore on close
    _winBound: false,    // window mouseup listener installed once

    FIELD_LABELS: {
        total: 'Total', tax: 'Tax', subtotal: 'Subtotal',
        date: 'Date', merchant: 'Merchant / Name', reference: 'Invoice / Ref #',
    },
    // One pastel per field so the boxes read at a glance. The legend is the
    // form itself: while the canvas is open, each destination input wears
    // its field's color as an outline (see _outlineTargets). Fills are
    // translucent so the receipt text underneath stays legible.
    // `ink`/`dark` are the picker-button colors: ink on the pale fill in
    // light mode, near-white text on the deep `dark` fill in dark mode
    // (the pastel fills washed out on SkyTech's dark theme, 2026-09-02).
    // The boxes on the image always use stroke/fill — the photo is the
    // background there, not the theme.
    FIELD_COLORS: {
        total:    { stroke: '#15803d', fill: 'rgba(134,239,172,0.38)', ink: '#166534', dark: '#14532d' },  // green
        tax:      { stroke: '#c2410c', fill: 'rgba(253,186,116,0.42)', ink: '#9a3412', dark: '#7c2d12' },  // orange
        subtotal: { stroke: '#1d4ed8', fill: 'rgba(147,197,253,0.42)', ink: '#1e40af', dark: '#1e3a8a' },  // blue
        date:     { stroke: '#6d28d9', fill: 'rgba(196,181,253,0.45)', ink: '#5b21b6', dark: '#4c1d95' },  // violet
        merchant: { stroke: '#be185d', fill: 'rgba(249,168,212,0.42)', ink: '#9d174d', dark: '#831843' },  // pink
        reference: { stroke: '#0f766e', fill: 'rgba(153,246,228,0.45)', ink: '#115e59', dark: '#134e4a' },  // teal
    },
    FIELD_OCR_TYPE: {
        total: 'amount', tax: 'amount', subtotal: 'amount',
        date: 'date', merchant: 'merchant', reference: 'reference',
    },

    panelHtml() {
        return `
        <div id="ocr-canvas-panel" style="display:none; margin-bottom:14px; border:1px solid var(--gray-300); border-radius:6px; padding:10px; background:var(--content-bg, #fff);">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                <strong style="font-size:12px;">Review scan</strong>
                <span style="font-size:11px; color:var(--gray-600);">
                    Drag a box around a value, then tap what it is below — or tap a field first, then drag.
                    Click a colored box to change or re-read it.
                </span>
            </div>
            <div style="max-height:min(420px, 55vh); overflow:auto; border:1px solid var(--gray-200);">
                <canvas id="ocr-canvas" style="display:block; cursor:crosshair;"></canvas>
            </div>
            <div id="ocr-field-picker" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; align-items:center;">
                <span id="ocr-picker-hint" style="font-size:11px; min-width:120px;">Drag a box, then tap a field:</span>
                ${['total', 'tax', 'subtotal', 'date', 'merchant'].map(k => `
                <button type="button" class="btn btn-sm ocr-pick" data-key="${k}"
                    style="--pick-stroke:${this.FIELD_COLORS[k].stroke}; --pick-fill:${this.FIELD_COLORS[k].fill}; --pick-ink:${this.FIELD_COLORS[k].ink}; --pick-dark:${this.FIELD_COLORS[k].dark};"
                    onclick="OcrCanvas.pick('${k}')">${this.FIELD_LABELS[k]}</button>`).join('')}
                <button type="button" id="ocr-picker-clear" class="btn btn-sm btn-secondary" style="display:none;"
                    onclick="OcrCanvas.cancelPending()">✕ Clear box</button>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-left:auto;"
                    onclick="OcrCanvas.close()">Close</button>
            </div>
            <div id="ocr-canvas-msg" style="font-size:11px; margin-top:6px; color:var(--gray-600);"></div>
        </div>`;
    },

    /** Open the panel for a completed scan. `result` is the v1 scan
     * response (intake_id, words, parsed fields); applyField(fieldKey,
     * value) writes into the host form. */
    async open(result, applyField, fieldTargets) {
        const panel = $('#ocr-canvas-panel');
        const canvas = $('#ocr-canvas');
        if (!panel || !canvas || !result || !result.intake_id) return;
        this._intakeId = result.intake_id;
        this._merchant = (result.merchant && result.merchant.value) || null;
        this._applyField = applyField;
        this._fieldTargets = fieldTargets || null;
        this._words = result.words || [];
        this._pendingBox = null;
        this._pendingSug = null;
        this._armed = null;
        this._drag = null;
        this._values = {};
        for (const k of ['total', 'tax', 'subtotal', 'date']) {
            if (result[k] && !(k === 'date' && result.date_is_default)) this._values[k] = result[k];
        }
        if (result.merchant && result.merchant.value) this._values.merchant = result.merchant.value;

        const img = new Image();
        img.onload = () => {
            this._img = img;
            this._suggestions = this._suggest(result);
            panel.style.display = 'block';
            this._outlineTargets();
            this._layout();
            this._bind(canvas);
            this._refreshToolbar();
            this._msg(this._suggestions.length
                ? 'Colored boxes are what the scan found. Drag a new box to fix anything it missed or got wrong.'
                : 'Drag a box around a value, then tap what it is.');
        };
        img.onerror = () => this._msg('Could not load the scan image — is SlowBooks still running?', true);
        img.src = `/api/ocr/intake/${encodeURIComponent(result.intake_id)}/image`;
    },

    close() {
        // Called from Close, from the modal's Cancel, and on modal teardown —
        // must never throw, or the host form's own close path dies with it.
        try {
            const panel = $('#ocr-canvas-panel');
            if (panel) panel.style.display = 'none';
            this._clearOutlines();
        } catch (_) { /* panel already gone */ }
        this._pendingBox = null;
        this._pendingSug = null;
        this._armed = null;
        this._drag = null;
    },

    /** Locate suggested field boxes by matching parse values to words. */
    // The form fields are the legend: outline each destination input in its
    // field's color. Two fields that land in the same input (total and
    // subtotal both feed the line rate) get a double ring — first color as
    // the outline, second as an outer shadow ring.
    _outlineTargets() {
        this._clearOutlines();
        if (!this._fieldTargets) return;
        const byEl = new Map();
        for (const key of Object.keys(this.FIELD_COLORS)) {
            let el = null;
            try { el = this._fieldTargets(key); } catch (_) { el = null; }
            if (!el || !el.offsetParent) continue;  // missing or hidden — nothing to outline
            if (!byEl.has(el)) byEl.set(el, []);
            byEl.get(el).push(key);
        }
        for (const [el, keys] of byEl) {
            this._outlined.push({ el, outline: el.style.outline, boxShadow: el.style.boxShadow });
            el.style.outline = `2px solid ${this.FIELD_COLORS[keys[0]].stroke}`;
            el.style.boxShadow = keys[1] ? `0 0 0 5px ${this.FIELD_COLORS[keys[1]].stroke}` : '';
        }
    },

    _clearOutlines() {
        for (const o of this._outlined) {
            try { o.el.style.outline = o.outline; o.el.style.boxShadow = o.boxShadow; } catch (_) { /* detached */ }
        }
        this._outlined = [];
    },

    // Which words on a value's line say it IS that field. Placement only —
    // the values come from the line-based parser; these just pick which of
    // several identical words to highlight (a line-item price and the
    // grand total both read "29.68" on the SkyTech lap, 2026-09-02).
    LINE_HINTS: {
        total: /total|amount|due|balance|payable|grand/i,
        subtotal: /sub|excl|before|net/i,
        tax: /tax|gst|vat|hst|pst|qst/i,
    },

    _suggest(result) {
        const found = [];
        const used = new Set();
        const amountKeys = [
            ['total', result.total], ['tax', result.tax], ['subtotal', result.subtotal],
        ];
        for (const [key, value] of amountKeys) {
            if (!value) continue;
            const w = this._findAmountWord(value, used, key);
            if (w) { used.add(w); found.push({ key, label: this.FIELD_LABELS[key], box: this._pad(w) }); }
        }
        if (result.date && !result.date_is_default) {
            const w = this._findDateWord(result.date, used);
            if (w) { used.add(w); found.push({ key: 'date', label: this.FIELD_LABELS.date, box: this._pad(w) }); }
        }
        if (result.merchant && result.merchant.value) {
            const run = this._findMerchantWords(result.merchant.value, used);
            if (run) {
                run.forEach(w => used.add(w));
                found.push({ key: 'merchant', label: this.FIELD_LABELS.merchant, box: this._pad(this._union(run)) });
            }
        }
        if (result.reference) {
            const w = this._findReferenceWord(result.reference, used);
            if (w) { used.add(w); found.push({ key: 'reference', label: this.FIELD_LABELS.reference, box: this._pad(w) }); }
        }
        return found;
    },

    /** Words sharing a text line with `w` (vertical centers overlap). */
    _lineOf(w) {
        const cy = w.top + w.height / 2;
        return this._words.filter(x => x.top <= cy && cy <= x.top + x.height);
    },

    /** The word to highlight for an amount: among words printing this
     *  value, the lowest one whose line carries the field's label — totals
     *  sit below the line items, and "CASH 29.68" repeats the total. */
    _findAmountWord(value, used, key) {
        const plain = String(value);                    // "49.13"
        const variants = [plain, '$' + plain,
            plain.replace(/\B(?=(\d{3})+(?!\d))/g, ','),
            '$' + plain.replace(/\B(?=(\d{3})+(?!\d))/g, ',')];
        const hits = this._words.filter(w => !used.has(w) && variants.includes(w.text));
        if (!hits.length) return null;
        const hint = key && this.LINE_HINTS[key];
        const labeled = hint
            ? hits.filter(w => this._lineOf(w).some(x => x !== w && hint.test(x.text)))
            : [];
        const pool = labeled.length ? labeled : hits;
        return pool.reduce((best, w) => (w.top > best.top ? w : best));
    },

    /** The word that prints the parsed ISO date, in whatever order/format
     *  the receipt used — not merely the first thing with a slash in it
     *  ("(81109-A)" took the Date box on the SkyTech lap). */
    _findDateWord(iso, used) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
        if (!m) return null;
        const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
        const full = [y, mo, d].sort((a, b) => a - b).join(',');
        const short = [y % 100, mo, d].sort((a, b) => a - b).join(',');
        return this._words.find(w => {
            if (used.has(w) || !/\d/.test(w.text)) return false;
            const parts = (w.text.match(/\d+/g) || []).map(Number);
            if (parts.length !== 3) return false;
            const got = parts.sort((a, b) => a - b).join(',');
            return got === full || got === short;
        }) || null;
    },

    /** The words that print the merchant name: the shortest run of
     *  consecutive same-line words whose letters/digits spell it, so the
     *  header line gets its box too ("merchant name was still
     *  unhighlighted" — SkyTech lap, 2026-09-02). Null when the name was
     *  remembered from a template rather than read off this page. */
    _findMerchantWords(name, used) {
        const norm = t => String(t).toUpperCase().replace(/[^A-Z0-9]/g, '');
        const want = norm(name);
        if (!want) return null;
        const seen = new Set();
        for (const w of this._words) {
            if (seen.has(w)) continue;
            const line = this._lineOf(w).filter(x => !used.has(x))
                .sort((a, b) => a.left - b.left);
            line.forEach(x => seen.add(x));
            for (let i = 0; i < line.length; i++) {
                let acc = '';
                for (let j = i; j < line.length; j++) {
                    acc += norm(line[j].text);
                    if (acc === want) return line.slice(i, j + 1);
                    if (acc.length >= want.length) break;
                }
            }
        }
        return null;
    },

    /** The word printing the parsed document number — exact, or with the
     *  label glued on ("#7011", "No:593101"). */
    _findReferenceWord(ref, used) {
        const want = String(ref).toUpperCase();
        const strip = t => String(t).toUpperCase().replace(/^[^A-Z0-9]+|[^A-Z0-9]+$/g, '');
        return this._words.find(w => !used.has(w) && strip(w.text) === want)
            || this._words.find(w => !used.has(w) && /\d/.test(w.text) && strip(w.text).endsWith(want))
            || null;
    },

    _union(words) {
        const left = Math.min(...words.map(w => w.left));
        const top = Math.min(...words.map(w => w.top));
        const right = Math.max(...words.map(w => w.left + w.width));
        const bottom = Math.max(...words.map(w => w.top + w.height));
        return { left, top, width: right - left, height: bottom - top };
    },

    _pad(w, p = 6) {
        return {
            left: Math.max(0, w.left - p), top: Math.max(0, w.top - p),
            width: w.width + 2 * p, height: w.height + 2 * p,
        };
    },

    _layout() {
        const canvas = $('#ocr-canvas');
        const holder = canvas.parentElement;
        const maxW = Math.max(320, holder.clientWidth - 2);
        this._scale = Math.min(1, maxW / this._img.naturalWidth);
        canvas.width = Math.round(this._img.naturalWidth * this._scale);
        canvas.height = Math.round(this._img.naturalHeight * this._scale);
        this._draw();
    },

    _draw() {
        const canvas = $('#ocr-canvas');
        if (!canvas || !this._img) return;
        const ctx = canvas.getContext('2d');
        const s = this._scale;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(this._img, 0, 0, canvas.width, canvas.height);

        // Faint word boxes — texture, not noise
        ctx.strokeStyle = 'rgba(0,51,102,0.18)';
        ctx.lineWidth = 1;
        for (const w of this._words) {
            ctx.strokeRect(w.left * s, w.top * s, w.width * s, w.height * s);
        }
        // Suggested field boxes — one pastel per field, labeled
        for (const sug of this._suggestions) {
            const b = sug.box;
            const c = this.FIELD_COLORS[sug.key] || { stroke: '#166534', fill: 'rgba(134,239,172,0.3)' };
            ctx.fillStyle = c.fill;
            ctx.fillRect(b.left * s, b.top * s, b.width * s, b.height * s);
            ctx.strokeStyle = c.stroke;
            ctx.lineWidth = 2;
            ctx.strokeRect(b.left * s, b.top * s, b.width * s, b.height * s);
            const tag = sug.reading ? `${sug.label} · reading…`
                : (sug.value ? `${sug.label} · ${sug.value}` : sug.label);
            this._label(ctx, tag, b.left * s, b.top * s, c.stroke);
        }
        // Live drag rectangle — thick amber so it reads on a photographed
        // receipt (the old thin dashed gray vanished on real paper).
        const hot = this._drag || this._pendingBox;
        if (hot) {
            let x, y, w, h;
            if (this._drag) {
                const d = this._drag;
                x = Math.min(d.x0, d.x1); y = Math.min(d.y0, d.y1);
                w = Math.abs(d.x1 - d.x0); h = Math.abs(d.y1 - d.y0);
            } else {
                const b = this._pendingBox;
                x = b.left * s; y = b.top * s; w = b.width * s; h = b.height * s;
            }
            ctx.fillStyle = 'rgba(251,191,36,0.30)';
            ctx.fillRect(x, y, w, h);
            ctx.strokeStyle = '#b45309';
            ctx.lineWidth = 3;
            ctx.setLineDash(this._drag ? [6, 4] : []);
            ctx.strokeRect(x, y, w, h);
            ctx.setLineDash([]);
            if (!this._drag) {
                const armed = this._armed ? this.FIELD_LABELS[this._armed] : null;
                this._label(ctx, armed ? `${armed}?` : 'Tap a field below ↓', x, y, '#b45309');
            }
        }
    },

    /** Label pill above (or inside, at the top edge) a box. */
    _label(ctx, text, x, y, color) {
        ctx.font = 'bold 11px sans-serif';
        const pad = 3;
        const tw = ctx.measureText(text).width;
        const ty = y - 16 < 0 ? y + 2 : y - 16;
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        ctx.fillRect(x, ty, tw + pad * 2, 14);
        ctx.fillStyle = color;
        ctx.fillText(text, x + pad, ty + 11);
    },

    _bind(canvas) {
        if (canvas._ocrBound) return;
        canvas._ocrBound = true;
        const pos = evt => {
            const r = canvas.getBoundingClientRect();
            return { x: evt.clientX - r.left, y: evt.clientY - r.top };
        };
        canvas.addEventListener('mousedown', evt => {
            const p = pos(evt);
            this._drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
        });
        canvas.addEventListener('mousemove', evt => {
            if (!this._drag) return;
            const p = pos(evt);
            this._drag.x1 = p.x; this._drag.y1 = p.y;
            this._draw();
        });
        // One window-level mouseup for the module's lifetime (the canvas is
        // re-rendered with every modal, the window isn't): a drag released
        // off the canvas still lands.
        if (this._winBound) return;
        this._winBound = true;
        window.addEventListener('mouseup', evt => {
            if (!this._drag) return;
            const canvas = $('#ocr-canvas');
            if (!canvas) { this._drag = null; return; }
            const d = this._drag;
            this._drag = null;
            const w = Math.abs(d.x1 - d.x0), h = Math.abs(d.y1 - d.y0);
            const r = canvas.getBoundingClientRect();
            if (w < 6 && h < 6) { this._click({ x: evt.clientX - r.left, y: evt.clientY - r.top }); this._draw(); return; }
            const s = this._scale;
            this._pendingBox = {
                left: Math.max(0, Math.round(Math.min(d.x0, d.x1) / s)),
                top: Math.max(0, Math.round(Math.min(d.y0, d.y1) / s)),
                width: Math.round(w / s),
                height: Math.round(h / s),
            };
            this._pendingSug = null;
            this._draw();
            if (this._armed) {
                // Arm-first: the field was chosen already — read right away.
                const key = this._armed;
                this._armed = null;
                this._assign(key);
            } else {
                this._refreshToolbar();
                this._msg('Box drawn — tap what it is on the toolbar below.');
            }
        });
    },

    _click(p) {
        const s = this._scale;
        const hit = this._suggestions.find(sug => {
            const b = sug.box;
            return p.x >= b.left * s && p.x <= (b.left + b.width) * s &&
                   p.y >= b.top * s && p.y <= (b.top + b.height) * s;
        });
        if (!hit) return;
        // Clicking a suggestion opens the picker with its current type marked,
        // so the operator can re-read it OR say "no, this box is the Total".
        this._pendingBox = { ...hit.box };
        this._pendingSug = hit;
        this._armed = null;
        this._refreshToolbar();
        this._msg(`Selected the ${hit.label} box — tap a field below to re-read it or change what it is.`);
    },

    /** Toolbar button. With a box pending: assign it. Without one: arm the
     * field so the next drag reads as it. Tapping the armed field again
     * disarms. */
    pick(fieldKey) {
        if (this._pendingBox) { this._assign(fieldKey); return; }
        this._armed = this._armed === fieldKey ? null : fieldKey;
        this._refreshToolbar();
        this._msg(this._armed
            ? `Now drag a box around the ${this.FIELD_LABELS[this._armed]} on the receipt.`
            : 'Drag a box around a value, then tap what it is.');
    },

    /** Kept for callers of the previous API (readPending == assign). */
    readPending(fieldKey) { this.pick(fieldKey); },

    _assign(fieldKey) {
        const box = this._pendingBox;
        if (!box) return;
        const sug = this._pendingSug;
        this._pendingBox = null;
        this._pendingSug = null;
        this._armed = null;
        // Whichever box was last read as this field becomes its suggestion:
        // a reassigned box drops its old label, and any box previously
        // holding this label steps aside. Keeps the canvas honest about
        // where each field value came from.
        const reassigned = sug && sug.key !== fieldKey;
        this._suggestions = this._suggestions.filter(x => x !== sug && x.key !== fieldKey);
        const entry = { key: fieldKey, label: this.FIELD_LABELS[fieldKey], box, reading: true };
        this._suggestions.push(entry);
        this._draw();            // recolor NOW — before the network round-trip
        this._refreshToolbar();
        this._read(entry, fieldKey, reassigned ? sug.key : null);
    },

    cancelPending() {
        this._pendingBox = null;
        this._pendingSug = null;
        this._armed = null;
        this._refreshToolbar();
        this._draw();
        this._msg('Drag a box around a value, then tap what it is.');
    },

    /** Toolbar reflects the mode: which field is armed / selected, the
     * value each field currently holds, and whether a box is pending. */
    _refreshToolbar() {
        const picker = $('#ocr-field-picker');
        if (!picker) return;
        const cur = this._pendingSug ? this._pendingSug.key : this._armed;
        const hint = $('#ocr-picker-hint');
        if (hint) {
            if (this._pendingSug) hint.textContent = `This box is ${this.FIELD_LABELS[cur]} — re-read it, or make it:`;
            else if (this._pendingBox) hint.textContent = 'Read this box as:';
            else if (this._armed) hint.textContent = `Drag a box around the ${this.FIELD_LABELS[this._armed]}:`;
            else hint.textContent = 'Drag a box, then tap a field:';
        }
        picker.querySelectorAll('button[data-key]').forEach(btn => {
            const k = btn.dataset.key;
            const active = k === cur;
            const v = this._values[k];
            btn.textContent = v ? `${this.FIELD_LABELS[k]} · ${String(v).slice(0, 18)}` : this.FIELD_LABELS[k];
            btn.style.fontWeight = active ? '700' : '400';
            btn.style.boxShadow = active ? `0 0 0 3px ${this.FIELD_COLORS[k].stroke}` : 'none';
            btn.style.opacity = (this._pendingBox || this._armed === k || !this._armed) ? '1' : '0.55';
        });
        const clear = $('#ocr-picker-clear');
        if (clear) clear.style.display = (this._pendingBox || this._armed) ? 'inline-block' : 'none';
    },

    async _read(entry, fieldKey, wasKey = null) {
        const box = entry.box;
        this._msg(`Reading ${this.FIELD_LABELS[fieldKey] || fieldKey}…`);
        let result;
        try {
            result = await API.post(
                `/ocr/intake/${encodeURIComponent(this._intakeId)}/region`,
                {
                    ...box,
                    field_type: this.FIELD_OCR_TYPE[fieldKey] || 'text',
                    // v3 template memory: a blessed read teaches this
                    // merchant's layout for next time.
                    merchant: this._merchant,
                    field_key: fieldKey,
                    save_template: !!(this._merchant || fieldKey === 'merchant'),
                });
        } catch (err) {
            this._drop(entry);
            this._msg(err.message, true);
            return;
        }
        entry.reading = false;
        entry.value = result.value || null;
        if (!result.value) {
            // A refused read leaves no box behind: a box over "tax + tip"
            // stayed painted across both figures on the build-42 lap and
            // read as if the tip had been taken.
            this._drop(entry);
            const saw = result.text ? ` (read "${result.text}")` : '';
            if (result.confidence === 'multiple') {
                this._msg(`That box covers more than one number${saw} — draw it around just the ${this.FIELD_LABELS[fieldKey] || fieldKey}.`, true);
            } else if (result.confidence === 'low' && result.text) {
                // Read something, but not a value the form can take (a
                // date in an order the parser doesn't know, say).
                this._msg(`Couldn't make a ${this.FIELD_LABELS[fieldKey] || fieldKey} out of "${result.text}" — type it in, or redraw the box.`, true);
            } else {
                this._msg(`Nothing readable in that box${saw} — try a slightly larger one.`, true);
            }
            return;
        }
        let applied = null;
        if (this._applyField) {
            try { applied = this._applyField(fieldKey, result.value, result); }
            catch (err) { applied = { error: `Couldn't write to the form: ${err.message}` }; }
        }
        // The host's apply hook may answer {error} (value refused — e.g. a
        // "tax" that can't be a tax on this subtotal), {note} (applied, with
        // a remark), or nothing (applied). A bare string is an error.
        if (typeof applied === 'string') applied = { error: applied };
        const raw = result.text && result.text !== result.value ? ` (read "${result.text}")` : '';
        if (applied && applied.error) {
            this._drop(entry);
            this._msg(`${this.FIELD_LABELS[fieldKey] || fieldKey}: ${result.value}${raw} — ${applied.error}`, true);
            return;
        }
        this._values[fieldKey] = result.value;
        if (fieldKey === 'merchant') this._merchant = result.value;
        this._draw();
        this._refreshToolbar();
        const note = result.confidence === 'low' ? ' (low confidence — double-check)' : '';
        const saved = result.template_saved ? ' Layout remembered for this merchant.' : '';
        // Reassigned box: the field it used to fill still holds that value.
        const moved = wasKey ? ` Was ${this.FIELD_LABELS[wasKey]} — check that field.` : '';
        const outcome = applied && applied.note ? ` ${applied.note}` : ' — applied to the form.';
        this._msg(`${this.FIELD_LABELS[fieldKey] || fieldKey}: ${result.value}${raw}${note}${outcome}${moved}${saved}`);
    },

    // Forget a box whose read was refused, so nothing lingers on the scan
    // or the field buttons.
    _drop(entry) {
        this._suggestions = this._suggestions.filter(x => x !== entry);
        this._draw();
        this._refreshToolbar();
    },

    _msg(text, isError) {
        const el = $('#ocr-canvas-msg');
        if (!el) return;
        el.textContent = text;
        el.style.color = isError ? '#c0392b' : 'var(--gray-600)';
    },
};

// Topbar data-action dispatch needs window exports (bootstrap.js callByPath).
window.OcrCanvas = OcrCanvas;
