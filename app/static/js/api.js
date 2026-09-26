/**
 * API wrapper — every page talks to the backend through this thin
 * fetch() layer.
 */
const API = {
    async request(method, path, body = null) {
        // Set when a change refused for the closing date is sent again with
        // the override password the person typed. Used for that one change
        // only: never kept, never logged.
        let closingPassword = null;
        for (;;) {
            const opts = {
                method,
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
            };
            const companyId = localStorage.getItem('slowbooks_company');
            if (companyId) opts.headers['X-Company-Id'] = companyId;
            // Percent-encoded: a header value cannot carry every character
            // a password can.
            if (closingPassword) opts.headers['X-Closing-Date-Password'] = encodeURIComponent(closingPassword);
            if (body) opts.body = JSON.stringify(body);
            let res;
            try {
                res = await fetch(`/api${path}`, opts);
            } catch (err) {
                // The browser's bare "Failed to fetch" means the local server is
                // gone (desktop shell still showing the page). Say so.
                throw new Error("SlowBooks isn't responding (network error) — if this keeps happening, close and relaunch SlowBooks Pro.");
            }
            if (res.status === 401 && window.SlowbooksAuth) {
                // Session expired, never authed, or fresh install -- let auth.js
                // re-check status and pick setup vs. login. Hardcoding promptLogin
                // here races with the DOMContentLoaded check on first install.
                // While its dialog is up, this request waits: the page reloads
                // once the person is in, so nothing is lost by not answering,
                // and nothing behind the dialog reports a failure that is not
                // one ("Error loading page" behind a new company's setup —
                // explore 2.17.3, M18).
                const waiting = await window.SlowbooksAuth.promptAuth();
                if (waiting === true) return new Promise(() => {});
                const err = new Error('Not authenticated');
                err.status = 401;
                throw err;
            }
            if (res.status === 429) {
                throw new Error('Rate limit exceeded -- slow down and try again');
            }
            if (!res.ok) {
                const errBody = await res.json().catch(() => ({ detail: res.statusText }));
                // FastAPI HTTPException(detail=str) → string; HTTPException(detail=dict) → object;
                // pydantic validation (422) → array of {loc, msg, message} entries.
                // Carry both the human message and the structured body so callers can
                // introspect 409s, 422s, etc. without losing information.
                const detail = errBody && errBody.detail !== undefined ? errBody.detail : errBody;
                const message = API.errorMessage(detail, res.statusText);
                // A change inside the closed period, on a company with a
                // closing-date password: the server says so in a header. Ask
                // for the password here and send the same change again; the
                // "Password (optional)" setting did nothing before (explore
                // 2.17.3, M7). Cancel keeps the refusal as it was. With no
                // password set, no header comes back and nothing is asked.
                const override = res.status === 403 && res.headers && typeof res.headers.get === 'function'
                    ? res.headers.get('X-Closing-Date-Override') : null;
                // "locked": too many wrong passwords — show the refusal, don't ask again
                if (override === 'password' || override === 'wrong-password') {
                    const typed = await API.askClosingDatePassword(message, override === 'wrong-password');
                    if (typed) { closingPassword = typed; continue; }
                }
                const err = new Error(message);
                err.status = res.status;
                err.detail = detail;
                err.body = errBody;
                throw err;
            }
            return res.json();
        }
    },
    // One dialog at a time: a second refused change waits for the first.
    _closingPromptChain: Promise.resolve(),
    // Asks for the closing-date password over whatever is open (the form
    // being saved stays underneath, untouched). Resolves to what was typed,
    // or null for Cancel / Escape.
    askClosingDatePassword(message, wrong) {
        const ask = () => new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.setAttribute('style', 'position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.45);'
                + 'display:flex;align-items:center;justify-content:center;padding:16px;');
            const box = document.createElement('form');
            box.setAttribute('role', 'dialog');
            box.setAttribute('aria-modal', 'true');
            box.setAttribute('aria-labelledby', 'closing-pw-title');
            box.setAttribute('style', 'background:var(--panel-bg,#fff);color:var(--text-primary,#111);'
                + 'border:1px solid var(--panel-border,#c0c8d0);border-radius:6px;padding:18px 20px;'
                + 'max-width:440px;width:100%;box-shadow:0 12px 40px rgba(0,0,0,0.35);font-size:13px;');
            const title = document.createElement('h3');
            title.id = 'closing-pw-title';
            title.textContent = 'This date is in a closed period';
            title.setAttribute('style', 'margin:0 0 8px;font-size:15px;');
            const text = document.createElement('p');
            text.textContent = message;
            text.setAttribute('style', 'margin:0 0 12px;line-height:1.45;');
            const label = document.createElement('label');
            label.textContent = 'Closing-date password';
            label.setAttribute('style', 'display:block;font-weight:600;margin-bottom:4px;');
            const input = document.createElement('input');
            input.type = 'password';
            input.autocomplete = 'off';
            input.required = true;
            input.setAttribute('aria-label', 'Closing-date password');
            input.setAttribute('style', 'width:100%;box-sizing:border-box;padding:6px 8px;');
            label.appendChild(input);
            const note = document.createElement('div');
            note.setAttribute('role', 'alert');
            note.setAttribute('style', 'color:var(--danger,#c33);min-height:16px;margin:6px 0 0;');
            note.textContent = wrong ? 'That password is not correct. Try again, or cancel.' : '';
            const buttons = document.createElement('div');
            buttons.setAttribute('style', 'display:flex;justify-content:flex-end;gap:6px;margin-top:12px;');
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn-secondary';
            cancel.textContent = 'Cancel';
            const ok = document.createElement('button');
            ok.type = 'submit';
            ok.className = 'btn btn-primary';
            ok.textContent = 'Make this change';
            buttons.append(cancel, ok);
            box.append(title, text, label, note, buttons);
            overlay.appendChild(box);
            const opener = document.activeElement;
            const finish = value => {
                document.removeEventListener('keydown', onKey, true);
                overlay.remove();
                if (opener && document.contains(opener)) { try { opener.focus(); } catch (e) { /* gone */ } }
                resolve(value);
            };
            // Capture phase, so Escape closes this dialog and not the form
            // underneath it.
            const onKey = e => {
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(null); }
            };
            document.addEventListener('keydown', onKey, true);
            cancel.addEventListener('click', () => finish(null));
            box.addEventListener('submit', e => { e.preventDefault(); finish(input.value || null); });
            document.body.appendChild(overlay);
            input.focus();
        });
        const next = API._closingPromptChain.then(ask, ask);
        API._closingPromptChain = next.catch(() => null);
        return next;
    },
    // The sentence to show for an error body's `detail`. A 422 is a list of
    // entries, each with a plain `message` from the server ("Name is
    // required.") — the page used to show validator text instead: "name:
    // String should have at least 1 character" (explore 2.17.3, L5). An
    // entry without one still names its field, never a bare "Unprocessable
    // Entity" (#64).
    errorMessage(detail, fallback) {
        if (typeof detail === 'string') return detail;
        if (Array.isArray(detail)) {
            return detail.map(d => {
                if (!d) return '';
                if (d.message) return d.message;
                const field = (d.loc || []).filter(p => p !== 'body').join('.');
                return field ? `${field}: ${d.msg}` : d.msg;
            }).filter(Boolean).join(' ') || fallback || 'Request failed';
        }
        return (detail && detail.message) || fallback || 'Request failed';
    },
    // post/put accept an optional opts.query → appended as a query string.
    // Used e.g. by vendors/customers to retry with ?force=true after a
    // duplicate-warning 409.
    get(path)       { return this.request('GET', path); },
    post(path, data, opts) { return this.request('POST', path + _qs(opts), data); },
    put(path, data, opts)  { return this.request('PUT', path + _qs(opts), data); },
    del(path)       { return this.request('DELETE', path); },
};

function _qs(opts) {
    if (!opts || !opts.query) return '';
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) {
        if (v !== undefined && v !== null) params.append(k, String(v));
    }
    const s = params.toString();
    return s ? '?' + s : '';
}
