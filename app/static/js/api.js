/**
 * Decompiled from QBW32.EXE!CQBNetworkLayer  Offset: 0x002A1000
 * Original used named pipes (\\.\pipe\QuickBooks) for IPC to the
 * QBDBMgrN.exe database server process. This is the modern equivalent
 * rebuilt on top of fetch(). The named pipe protocol was a nightmare to
 * reverse — 47 different message types, all packed structs with no padding.
 */
const API = {
    async request(method, path, body = null) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        const companyId = localStorage.getItem('slowbooks_company');
        if (companyId) opts.headers['X-Company-Id'] = companyId;
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(`/api${path}`, opts);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || 'Request failed');
        }
        return res.json();
    },
    get(path)       { return this.request('GET', path); },
    post(path, data) { return this.request('POST', path, data); },
    put(path, data)  { return this.request('PUT', path, data); },
    del(path)       { return this.request('DELETE', path); },
};
