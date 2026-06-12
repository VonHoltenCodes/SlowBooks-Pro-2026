/**
 * A nod to QBW32.EXE!CQBNetworkLayer  imagined offset: 0x002A1000
 * Original used named pipes (\\.\pipe\QuickBooks) for IPC to the
 * QBDBMgrN.exe database server process. This is the modern equivalent
 * rebuilt on top of fetch(). The named pipe protocol was a nightmare to
 * reverse — 47 different message types, all packed structs with no padding.
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
        const res = await fetch(`/api${path}`, opts);
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
            // FastAPI HTTPException(detail=str) → string; HTTPException(detail=dict) → object.
            // Carry both the human message and the structured body so callers can
            // introspect 409s, 422s, etc. without losing information.
            const detail = body && body.detail !== undefined ? body.detail : body;
            const message = typeof detail === 'string'
                ? detail
                : (detail && detail.message) || res.statusText || 'Request failed';
            const err = new Error(message);
            err.status = res.status;
            err.detail = detail;
            err.body = body;
            throw err;
        }
        return res.json();
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
