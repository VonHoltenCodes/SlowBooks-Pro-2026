/**
 * Receipt / Document Intake — shared Scan helper (Tier 2).
 * Used by the Enter Sales Receipt and Enter Bill modals:
 *
 *   1. Gating — GET /api/ocr/status; disable the Scan button with guidance
 *      when Tesseract isn't installed (the app runs exactly as before).
 *   2. Upload — POST /api/ocr/receipt (multipart); hand the extracted
 *      fields to the form's apply callback, which pre-fills and recalculates.
 *   3. Attach — after the document is saved, POST
 *      /api/ocr/intake/{id}/attach moves the stored scan into the
 *      attachments store (spec §6.5).
 *   4. Discard — when the modal closes without saving, DELETE the pending
 *      scan; the 24h intake TTL is the safety net if the tab dies mid-flow.
 *
 * The scan row markup comes from scanRowHtml(); pages call wire() right
 * after openModal(). Only one modal is open at a time, so the intake id
 * is module state.
 */
const ScanHelper = {
    _intakeId: null,
    _lastResult: null,
    _applyField: null,
    _fieldTargets: null,
    _status: null,
    _statusAt: 0,
    _STATUS_TTL: 120000, // 2 minutes

    async status() {
        const now = Date.now();
        if (this._status && now - this._statusAt < this._STATUS_TTL) return this._status;
        try {
            this._status = await API.get('/ocr/status');
        } catch (err) {
            // Unknown — treat as unavailable so the button never promises
            // a scan the server can't do.
            this._status = { available: false, version: null, languages: null };
        }
        this._statusAt = now;
        return this._status;
    },

    scanRowHtml() {
        return `
        <div id="scan-row" style="display:flex; align-items:center; gap:10px; margin-bottom:14px;
             padding:10px 12px; border:1px dashed var(--gray-300); border-radius:6px; background:var(--primary-light);">
            <button type="button" id="scan-btn" class="btn btn-secondary" onclick="ScanHelper.pick()">📄 Scan Receipt</button>
            <input type="file" id="scan-file" accept="image/png,image/jpeg,image/webp,application/pdf" style="display:none;">
            <button type="button" id="scan-review-btn" class="btn btn-sm btn-secondary" style="display:none;"
                onclick="ScanHelper.reviewBoxes()">🔍 Review boxes</button>
            <span id="scan-status" style="font-size:12px; color:var(--gray-600);"></span>
        </div>
        ${OcrCanvas.panelHtml()}`;
    },

    pick() {
        const input = $('#scan-file');
        if (input) input.click();
    },

    reviewBoxes() {
        if (this._lastResult && this._applyField) {
            OcrCanvas.open(this._lastResult, this._applyField, this._fieldTargets);
        }
    },

    async wire(applyCallback, applyField, fieldTargets) {
        this._applyField = applyField || null;
        this._fieldTargets = fieldTargets || null;
        const btn = $('#scan-btn');
        const input = $('#scan-file');
        if (!btn || !input) return;
        const s = await this.status();
        if (!s.available) {
            btn.disabled = true;
            btn.title = "Tesseract OCR isn't installed — see Settings for install instructions.";
            const statusEl = $('#scan-status');
            if (statusEl) statusEl.textContent = "Scanning disabled — Tesseract isn't installed (see Settings).";
            return;
        }
        input.addEventListener('change', () => {
            const file = input.files[0];
            if (!file) return;
            this.scan(file, applyCallback);
            input.value = '';
        });
    },

    async scan(file, applyCallback) {
        const btn = $('#scan-btn');
        const statusEl = $('#scan-status');
        const fd = new FormData();
        fd.append('file', file);
        if (btn) btn.disabled = true;
        if (statusEl) { statusEl.textContent = 'Scanning…'; statusEl.style.color = 'var(--gray-600)'; }
        try {
            let resp;
            try { resp = await fetch('/api/ocr/receipt', { method: 'POST', body: fd }); }
            catch (err) { throw new Error("SlowBooks isn't responding (network error) — if this keeps happening, close and relaunch SlowBooks Pro."); }
            if (!resp.ok) {
                const d = await resp.json().catch(() => ({}));
                throw new Error(d.detail || `Scan failed (HTTP ${resp.status})`);
            }
            const result = await resp.json();
            if (!result.ocr_available) {
                if (statusEl) statusEl.textContent = result.message || 'Scanning unavailable.';
                return;
            }
            this._intakeId = result.intake_id || null;
            this._lastResult = result;
            applyCallback(result);
            const reviewBtn = $('#scan-review-btn');
            if (reviewBtn && result.intake_id) reviewBtn.style.display = 'inline-block';
            // Partial scans open the canvas automatically — the box-to-fix
            // moment is exactly when auto-parse fell short.
            if (result.partial && result.intake_id && this._applyField) {
                OcrCanvas.open(result, this._applyField, this._fieldTargets);
            }
            if (statusEl) {
                statusEl.textContent = this.summary(result);
                statusEl.style.color = result.partial ? '#b45309' : '#166534';
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = err.message; statusEl.style.color = '#c0392b'; }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    summary(result) {
        let msg = 'Scan complete — review before saving.';
        if (result.template_applied) {
            // v3 template memory: this merchant's saved layout filled the
            // amounts and the arithmetic checks out.
            msg = 'Saved layout for this merchant filled the fields — review before saving.';
        }
        if (result.partial && result.partial_reasons && result.partial_reasons.length) {
            msg = 'Partial: ' + result.partial_reasons.join('; ');
        }
        if (result.multi_page) msg += ' First page scanned.';
        return msg;
    },

    async attachAfterSave(entityType, entityId) {
        if (!this._intakeId) return;
        const intakeId = this._intakeId;
        try {
            await API.post(`/ocr/intake/${intakeId}/attach`, { entity_type: entityType, entity_id: entityId });
            this._intakeId = null;
        } catch (err) {
            // The document saved fine; only the evidence link failed. The
            // intake TTL reaps the file; the operator can attach manually.
            toast('Saved, but the scan image couldn\'t be attached — add it manually.', 'error');
        }
    },

    async discard() {
        // Runs from the modal's Cancel button ahead of closeModal(): nothing
        // in here may throw or block, or the modal never closes.
        try { if (window.OcrCanvas) OcrCanvas.close(); } catch (_) { /* never block Cancel */ }
        this._lastResult = null;
        if (!this._intakeId) return;
        const intakeId = this._intakeId;
        this._intakeId = null;
        try {
            await API.del(`/ocr/intake/${intakeId}`);
        } catch (err) {
            // Idempotent on the server; the TTL sweep is the backstop.
        }
    },

    /**
     * Turn a boxed tax read into a percent for a tax-rate input, or explain
     * why it can't be one. `raw` is the region's raw text: a read that
     * carried a "%" IS the rate ("8.25%"). Otherwise value is an amount and
     * the rate is amount / subtotal — refused when the amount is as big as
     * the subtotal (the box caught the total, or several numbers) or the
     * rate lands past 50%, so one wrong drag can't write 1204.17% into the
     * form (VH308 lap, 2026-09-02).
     * Returns { pct } or { error }.
     */
    taxPercent(value, subtotal, raw) {
        const text = String(raw || '');
        const num = parseFloat(String(value).replace(/[^0-9.]/g, ''));
        if (!(num > 0)) return { error: `Tax read "${value}" isn't a number — not applied.` };
        if (text.includes('%') || String(value).includes('%')) {
            if (num > 50) return { error: `Tax rate ${num}% is not plausible — not applied.` };
            return { pct: num };
        }
        const sub = parseFloat(subtotal);
        if (!(sub > 0)) return { error: `Tax ${num.toFixed(2)} read, but there's no subtotal yet — read the Total or Subtotal first.` };
        if (num >= sub) {
            return { error: `Tax ${num.toFixed(2)} is at least the subtotal (${sub.toFixed(2)}) — not applied. Draw the box around just the tax amount.` };
        }
        const pct = (num / sub) * 100;
        if (pct > 50) return { error: `Tax ${num.toFixed(2)} on ${sub.toFixed(2)} would be ${pct.toFixed(2)}% — not applied. Draw the box around just the tax amount.` };
        return { pct: Math.round(pct * 100) / 100 };
    },
};
