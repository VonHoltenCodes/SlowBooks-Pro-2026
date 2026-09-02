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

    FIELD_LABELS: {
        total: 'Total', tax: 'Tax', subtotal: 'Subtotal',
        date: 'Date', merchant: 'Merchant / Name',
    },
    FIELD_OCR_TYPE: {
        total: 'amount', tax: 'amount', subtotal: 'amount',
        date: 'date', merchant: 'merchant',
    },

    panelHtml() {
        return `
        <div id="ocr-canvas-panel" style="display:none; margin-bottom:14px; border:1px solid var(--gray-300); border-radius:6px; padding:10px; background:var(--content-bg, #fff);">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                <strong style="font-size:12px;">Review scan</strong>
                <span id="ocr-canvas-hint" style="font-size:11px; color:var(--gray-600);">
                    Click a highlighted box to re-read it, or drag a rectangle around anything the scan missed.
                </span>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-left:auto;"
                    onclick="OcrCanvas.close()">Close</button>
            </div>
            <div id="ocr-field-picker" style="display:none; gap:6px; margin-bottom:8px; align-items:center;">
                <span style="font-size:11px;">Read this box as:</span>
                <button type="button" class="btn btn-sm btn-secondary" onclick="OcrCanvas.readPending('total')">Total</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="OcrCanvas.readPending('tax')">Tax</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="OcrCanvas.readPending('subtotal')">Subtotal</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="OcrCanvas.readPending('date')">Date</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="OcrCanvas.readPending('merchant')">Merchant</button>
                <button type="button" class="btn btn-sm" onclick="OcrCanvas.cancelPending()">✕</button>
            </div>
            <div style="max-height:420px; overflow:auto; border:1px solid var(--gray-200);">
                <canvas id="ocr-canvas" style="display:block; cursor:crosshair;"></canvas>
            </div>
            <div id="ocr-canvas-msg" style="font-size:11px; margin-top:6px; color:var(--gray-600);"></div>
        </div>`;
    },

    /** Open the panel for a completed scan. `result` is the v1 scan
     * response (intake_id, words, parsed fields); applyField(fieldKey,
     * value) writes into the host form. */
    async open(result, applyField) {
        const panel = $('#ocr-canvas-panel');
        const canvas = $('#ocr-canvas');
        if (!panel || !canvas || !result || !result.intake_id) return;
        this._intakeId = result.intake_id;
        this._merchant = (result.merchant && result.merchant.value) || null;
        this._applyField = applyField;
        this._words = result.words || [];
        this._pendingBox = null;
        this._drag = null;

        const img = new Image();
        img.onload = () => {
            this._img = img;
            this._suggestions = this._suggest(result);
            panel.style.display = 'block';
            this._layout();
            this._bind(canvas);
            this._msg(this._suggestions.length
                ? 'Highlighted: what the scan thinks it found. Click one to re-read just that box.'
                : 'Drag a rectangle around a value to read it precisely.');
        };
        img.onerror = () => this._msg('Could not load the scan image.', true);
        img.src = `/api/ocr/intake/${encodeURIComponent(result.intake_id)}/image`;
    },

    close() {
        const panel = $('#ocr-canvas-panel');
        if (panel) panel.style.display = 'none';
        this._pendingBox = null;
        const picker = $('#ocr-field-picker');
        if (picker) picker.style.display = 'none';
    },

    /** Locate suggested field boxes by matching parse values to words. */
    _suggest(result) {
        const found = [];
        const used = new Set();
        const amountKeys = [
            ['total', result.total], ['tax', result.tax], ['subtotal', result.subtotal],
        ];
        for (const [key, value] of amountKeys) {
            if (!value) continue;
            const w = this._findAmountWord(value, used);
            if (w) { used.add(w); found.push({ key, label: this.FIELD_LABELS[key], box: this._pad(w) }); }
        }
        if (result.date && !result.date_is_default) {
            const w = this._words.find(x => !used.has(x) && /\d/.test(x.text) &&
                (x.text.includes('/') || x.text.includes('-')));
            if (w) { used.add(w); found.push({ key: 'date', label: this.FIELD_LABELS.date, box: this._pad(w) }); }
        }
        return found;
    },

    _findAmountWord(value, used) {
        const plain = String(value);                    // "49.13"
        const variants = [plain, '$' + plain,
            plain.replace(/\B(?=(\d{3})+(?!\d))/g, ','),
            '$' + plain.replace(/\B(?=(\d{3})+(?!\d))/g, ',')];
        return this._words.find(w => !used.has(w) && variants.includes(w.text));
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
        // Suggested field boxes — labeled
        for (const sug of this._suggestions) {
            const b = sug.box;
            ctx.strokeStyle = '#166534';
            ctx.lineWidth = 2;
            ctx.strokeRect(b.left * s, b.top * s, b.width * s, b.height * s);
            ctx.fillStyle = '#166534';
            ctx.font = '11px sans-serif';
            ctx.fillText(sug.label, b.left * s, Math.max(10, b.top * s - 3));
        }
        // Live drag rectangle
        if (this._drag) {
            const d = this._drag;
            ctx.strokeStyle = '#b45309';
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 3]);
            ctx.strokeRect(Math.min(d.x0, d.x1), Math.min(d.y0, d.y1),
                Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0));
            ctx.setLineDash([]);
        }
        // Pending (awaiting field choice)
        if (this._pendingBox) {
            const b = this._pendingBox;
            ctx.strokeStyle = '#b45309';
            ctx.lineWidth = 2;
            ctx.strokeRect(b.left * s, b.top * s, b.width * s, b.height * s);
        }
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
        canvas.addEventListener('mouseup', evt => {
            if (!this._drag) return;
            const d = this._drag;
            this._drag = null;
            const w = Math.abs(d.x1 - d.x0), h = Math.abs(d.y1 - d.y0);
            if (w < 6 && h < 6) { this._click(pos(evt)); this._draw(); return; }
            const s = this._scale;
            this._pendingBox = {
                left: Math.round(Math.min(d.x0, d.x1) / s),
                top: Math.round(Math.min(d.y0, d.y1) / s),
                width: Math.round(w / s),
                height: Math.round(h / s),
            };
            const picker = $('#ocr-field-picker');
            if (picker) picker.style.display = 'flex';
            this._draw();
        });
    },

    _click(p) {
        const s = this._scale;
        const hit = this._suggestions.find(sug => {
            const b = sug.box;
            return p.x >= b.left * s && p.x <= (b.left + b.width) * s &&
                   p.y >= b.top * s && p.y <= (b.top + b.height) * s;
        });
        if (hit) this._read(hit.box, hit.key);
    },

    readPending(fieldKey) {
        if (!this._pendingBox) return;
        const box = this._pendingBox;
        this.cancelPending();
        this._read(box, fieldKey);
    },

    cancelPending() {
        this._pendingBox = null;
        const picker = $('#ocr-field-picker');
        if (picker) picker.style.display = 'none';
        this._draw();
    },

    async _read(box, fieldKey) {
        this._msg(`Reading ${this.FIELD_LABELS[fieldKey] || fieldKey}…`);
        try {
            const result = await API.post(
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
            if (!result.value) {
                this._msg('Nothing readable in that box — try a slightly larger one.', true);
                return;
            }
            if (fieldKey === 'merchant') this._merchant = result.value;
            if (this._applyField) this._applyField(fieldKey, result.value, result);
            const note = result.confidence === 'low' ? ' (low confidence — double-check)' : '';
            const saved = result.template_saved ? ' Layout remembered for this merchant.' : '';
            this._msg(`${this.FIELD_LABELS[fieldKey] || fieldKey}: ${result.value}${note} — applied to the form.${saved}`);
        } catch (err) {
            this._msg(err.message, true);
        }
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
