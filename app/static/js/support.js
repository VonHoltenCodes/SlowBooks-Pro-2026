/**
 * Contact support — the in-app help form.
 *
 * The app sends NOTHING: Send builds a mailto: link to support and opens the
 * user's own mail program with the message already written. The environment
 * block and a redacted tail of the log come from GET /api/support/diagnostics
 * (read-only, no outbound call). mailto can't attach files, so the form says
 * to attach screenshots or the log by hand; long logs are trimmed to keep the
 * mailto body under the length some mail clients truncate, and Copy
 * diagnostics gives the full block. Redaction is best-effort — read the body
 * before sending.
 */
const SupportPage = {
    SUPPORT_EMAIL: 'support@slowbookspro.com',
    MAILTO: 'mailto:support@slowbookspro.com',
    // Cap the body AFTER URL-encoding: encodeURIComponent inflates log text
    // and mailto limits sit near 2048; the subject takes the rest.
    MAX_ENCODED_BODY: 1500,
    TRIM_FOOTER: '\n(log trimmed — use Copy diagnostics for the full block)',

    _diag: null,  // the last diagnostics payload, for the live mailto rebuild

    // --- Pure builders (unit-tested in tests/js/support_mailto.test.cjs) ---

    shortTitle(whatHappened, limit = 60) {
        const text = (whatHappened || '').split(/\s+/).filter(Boolean).join(' ');
        if (!text) return 'support request';
        return text.slice(0, limit).trimEnd();
    },

    buildSubject(diag, whatHappened) {
        return `[SBP] v${diag.version} · ${diag.platform} ${diag.deployment}`
            + ` · ${SupportPage.shortTitle(whatHappened)}`;
    },

    // The environment the email carries: the server's block, plus the browser
    // / web-view UA whenever known — it matters most on the native window,
    // where the WKWebView / WebView2 version is the usual culprit.
    environmentText(diag) {
        let env = diag.environment || '';
        if (diag.browser) env += `\nBrowser: ${diag.browser}`;
        return env;
    },

    _encodedLen(text) { return encodeURIComponent(text).length; },

    buildBody(diag, fields) {
        const wh = (fields.whatHappened || '').trim() || '(not described)';
        const we = (fields.whatExpected || '').trim() || '(not described)';
        const parts = [
            'What happened:', wh, '',
            'What you expected:', we, '',
        ];
        if (fields.replyTo) parts.push(`Reply to: ${fields.replyTo}`, '');
        parts.push('--- Environment ---', SupportPage.environmentText(diag), '', '--- Recent log ---');
        const header = parts.join('\n') + '\n';

        if (!diag.log_available) return header + (diag.log_note || '(log file not found)');

        const max = SupportPage.MAX_ENCODED_BODY;
        const tail = diag.log_tail || '(log is empty)';
        const full = header + tail;
        if (SupportPage._encodedLen(full) <= max) return full;

        // Trim the LOG TAIL (never the user's text) until the encoded body
        // fits. Binary-search the most trailing log characters that still fit
        // (the newest lines, where the error is); drop the tail if none do.
        const base = header + SupportPage.TRIM_FOOTER + '\n';
        if (SupportPage._encodedLen(base) > max) return header.replace(/\n+$/, '');
        let lo = 0, hi = tail.length;
        while (lo < hi) {
            const mid = Math.ceil((lo + hi) / 2);
            if (SupportPage._encodedLen(base + tail.slice(-mid)) <= max) lo = mid;
            else hi = mid - 1;
        }
        return lo ? base + tail.slice(-lo) : header.replace(/\n+$/, '');
    },

    buildMailto(diag, fields) {
        const subject = SupportPage.buildSubject(diag, fields.whatHappened);
        const body = SupportPage.buildBody(diag, fields);
        return `${SupportPage.MAILTO}?subject=${encodeURIComponent(subject)}`
            + `&body=${encodeURIComponent(body)}`;
    },

    // The full environment block (no trimming) for Copy diagnostics.
    fullBlock(diag) {
        let block = SupportPage.environmentText(diag);
        if (diag.log_available) block += `\n\n--- Recent log ---\n${diag.log_tail || '(log is empty)'}`;
        else block += `\n\n${diag.log_note || '(log file not found)'}`;
        return block;
    },

    // A stand-in block when the diagnostics call fails, so the form is still
    // reachable exactly when the app is broken. Send still works with the
    // user's own text.
    _degradedDiag(err) {
        return {
            version: '?', commit: null, platform: '?', deployment: '?',
            is_native: false, reply_to: '',
            environment: `Diagnostics unavailable: ${(err && err.message) || err}`,
            log_tail: '', log_available: false,
            log_note: 'Diagnostics could not be loaded; this message still works.',
        };
    },

    // Never throws: returns real diagnostics or a degraded block.
    async loadDiag() {
        let diag;
        try {
            diag = await API.get('/support/diagnostics');
        } catch (err) {
            diag = SupportPage._degradedDiag(err);
        }
        diag.browser = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
        return diag;
    },

    // --- The dialog --------------------------------------------------------

    async open() {
        const diag = await SupportPage.loadDiag();
        SupportPage._diag = diag;

        const revealBtn = diag.is_native
            ? '<button type="button" class="btn btn-secondary" data-readonly-ok onclick="SupportPage.revealLog()">Reveal log file</button>'
            : '';
        const logNote = diag.log_note
            ? `<p style="font-size:11px; color:var(--text-muted); margin:4px 0 0;">${escapeHtml(diag.log_note)}</p>`
            : '';

        // data-readonly-ok: this form sends an email, it writes nothing to the
        // books, so a read-only or accountant sign-in keeps it usable (no lock
        // banner, fields enabled) — exactly who may need to report a problem.
        openModal('Contact support', `
            <form id="support-form" data-readonly-ok onsubmit="return false;">
                <p style="font-size:12px; margin:0 0 10px;">Redaction is best-effort — please read the message before you send it.</p>
                <div class="form-group">
                    <label for="support-what-happened">What happened ${hint("Send opens your own mail program with the message written for you — SlowBooks sends nothing itself. mailto can't attach files, so attach screenshots or the log file in your mail program if you can. Long logs are trimmed; use Copy diagnostics for the full block.", 'Contact support')}</label>
                    <textarea id="support-what-happened" name="What happened" rows="4" oninput="SupportPage.refresh()"></textarea>
                </div>
                <div class="form-group">
                    <label for="support-what-expected">What you expected</label>
                    <textarea id="support-what-expected" name="What you expected" rows="3" oninput="SupportPage.refresh()"></textarea>
                </div>
                <div class="form-group">
                    <label for="support-reply-to">Can we reply to you at this address?</label>
                    <input type="email" id="support-reply-to" name="Reply-to address" value="${escapeHtml(diag.reply_to || '')}">
                </div>
                ${logNote}
                <p style="font-size:11px; color:var(--text-muted); margin:10px 0 0;">Support emails, including any logs you attach, are stored in our private project tracker (Basecamp).</p>
                <p style="font-size:11px; color:var(--text-muted); margin:4px 0 0;">Found a security issue? Report it through <a href="https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/security/advisories/new" target="_blank" rel="noopener">GitHub Security Advisories</a>, not this form.</p>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" data-readonly-ok onclick="closeModal()">Cancel</button>
                    ${revealBtn}
                    <button type="button" class="btn btn-secondary" data-readonly-ok onclick="SupportPage.copyDiagnostics()">Copy diagnostics</button>
                    <a id="support-send" class="btn btn-primary" data-readonly-ok href="${escapeHtml(SupportPage._mailto())}">Send</a>
                </div>
            </form>`);
        SupportPage.refresh();
    },

    _fields() {
        return {
            whatHappened: (document.getElementById('support-what-happened') || {}).value || '',
            whatExpected: (document.getElementById('support-what-expected') || {}).value || '',
            replyTo: (document.getElementById('support-reply-to') || {}).value || '',
        };
    },

    _mailto() {
        if (!SupportPage._diag) return SupportPage.MAILTO;
        return SupportPage.buildMailto(SupportPage._diag, SupportPage._fields());
    },

    // Rebuild the Send link's href from the live field values.
    refresh() {
        const send = document.getElementById('support-send');
        if (send) send.setAttribute('href', SupportPage._mailto());
    },

    copyDiagnostics() {
        if (!SupportPage._diag) return;
        copyToClipboard(SupportPage.fullBlock(SupportPage._diag), 'Diagnostics');
    },

    async revealLog() {
        try {
            const res = await API.post('/support/reveal-log', {});
            if (res && res.revealed === false) toast(res.reason || "There's no log file to show yet.", 'error');
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};

window.SupportPage = SupportPage;
