/**
 * API wrapper — every page talks to the backend through this thin
 * fetch() layer.
 */
const API = {
    async request(method, path, body = null) {
        const opts = {
            method,
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
        };
        const companyId = localStorage.getItem('slowbooks_company');
        if (companyId) opts.headers['X-Company-Id'] = companyId;
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
            window.SlowbooksAuth.promptAuth();
            throw new Error('Not authenticated');
        }
        if (res.status === 429) {
            throw new Error('Rate limit exceeded -- slow down and try again');
        }
        if (!res.ok) {
            const body = await res.json().catch(() => ({ detail: res.statusText }));
            // FastAPI HTTPException(detail=str) → string; HTTPException(detail=dict) → object;
            // pydantic validation (422) → array of {loc, msg, message} entries.
            // Carry both the human message and the structured body so callers can
            // introspect 409s, 422s, etc. without losing information.
            const detail = body && body.detail !== undefined ? body.detail : body;
            const message = API.errorMessage(detail, res.statusText);
            const err = new Error(message);
            err.status = res.status;
            err.detail = detail;
            err.body = body;
            throw err;
        }
        return res.json();
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
