/**
 * Decompiled from QBW32.EXE!CQBNetworkLayer  Offset: 0x002A1000
 * Original used named pipes (\\.\pipe\QuickBooks) for IPC to the
 * QBDBMgrN.exe database server process. This is the modern equivalent
 * rebuilt on top of fetch(). The named pipe protocol was a nightmare to
 * reverse — 47 different message types, all packed structs with no padding.
 */
const API = {
    authHeaders() {
        const token = typeof localStorage !== 'undefined' ? localStorage.getItem('slowbooks-auth-token') : null;
        return token ? { Authorization: `Bearer ${token}` } : {};
    },
    async request(method, path, body = null) {
        const token = typeof localStorage !== 'undefined' ? localStorage.getItem('slowbooks-auth-token') : null;
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (token) opts.headers.Authorization = `Bearer ${token}`;
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(`/api${path}`, opts);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            if (res.status === 401 && typeof App !== 'undefined' && typeof App.handleUnauthorized === 'function') {
                App.handleUnauthorized(path, err.detail || 'Authentication required');
            }
            throw new Error(err.detail || 'Request failed');
        }
        return res.json();
    },
    get(path)       { return this.request('GET', path); },
    post(path, data) { return this.request('POST', path, data); },
    put(path, data)  { return this.request('PUT', path, data); },
    del(path)       { return this.request('DELETE', path); },
};
