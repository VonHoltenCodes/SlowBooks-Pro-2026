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

    async _parseError(res) {
        const err = await res.json().catch(async () => ({ detail: await res.text().catch(() => res.statusText) }));
        return err.detail || 'Request failed';
    },

    async raw(method, path, { body = null, headers = {} } = {}) {
        const opts = {
            method,
            headers: { ...this.authHeaders(), ...headers },
        };
        if (body !== null) {
            if (typeof FormData !== 'undefined' && body instanceof FormData) {
                opts.body = body;
            } else {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
        }
        const res = await fetch(`/api${path}`, opts);
        if (!res.ok) {
            const detail = await this._parseError(res);
            if (res.status === 401 && typeof App !== 'undefined' && typeof App.handleUnauthorized === 'function') {
                App.handleUnauthorized(path, detail || 'Authentication required');
            }
            throw new Error(detail || 'Request failed');
        }
        return res;
    },

    async request(method, path, body = null) {
        const res = await this.raw(method, path, { body });
        return res.json();
    },

    get(path) { return this.request('GET', path); },
    post(path, data) { return this.request('POST', path, data); },
    put(path, data) { return this.request('PUT', path, data); },
    del(path) { return this.request('DELETE', path); },
    async postForm(path, formData) {
        const res = await this.raw('POST', path, { body: formData });
        return res.json();
    },
    async download(path, fallbackName = 'download.bin') {
        const res = await this.raw('GET', path);
        const disposition = res.headers.get('Content-Disposition');
        let filename = fallbackName;
        if (disposition) {
            const match = disposition.match(/filename="?([^\"]+)"?/);
            if (match) filename = match[1];
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
    },
    async open(path, fallbackName = 'document.bin') {
        const res = await this.raw('GET', path);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const opened = typeof window !== 'undefined' && typeof window.open === 'function' ? window.open(url, '_blank') : null;
        if (!opened) {
            const link = document.createElement('a');
            link.href = url;
            link.download = fallbackName;
            link.click();
        }
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
};
