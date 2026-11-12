// Print windows at a real address (#823). In the desktop shell a print or
// print-preview sheet opens at http://127.0.0.1:<port>/print/<token>: the
// shim asks POST /api/print-tokens for a one-time token under the page's own
// sign-in and hands the address to the launcher's open_document_url, so the
// print footer carries a short localhost address instead of the html=
// window's data: string. PDFs and CSVs keep the fetch-and-save path; a sheet
// whose address cannot be made opens from its HTML as before.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const ORIGIN = 'http://127.0.0.1:3001';
const SHEETS = {
    '/api/invoices/7/print-preview': { type: 'text/html; charset=utf-8', body: '<html>Invoice</html>' },
    '/api/reports/profit-loss/print': { type: 'text/html; charset=utf-8', body: '<html>P&L</html>' },
    '/api/budgets/print': { type: 'text/html; charset=utf-8', body: '<html>Budget</html>' },
    '/api/banking/reconciliations/3/print': { type: 'text/html; charset=utf-8', body: '<html>Recon</html>' },
    '/api/invoices/7/pdf': { type: 'application/pdf', disposition: 'inline; filename=Invoice_1001.pdf', body: '%PDF-1.7' },
    '/api/checks/print': { type: 'application/pdf', disposition: 'inline; filename=Check_1.pdf', body: '%PDF-1.7' },
    '/api/csv/export/customers': { type: 'text/csv; charset=utf-8', disposition: 'inline; filename=customers.csv', body: 'Name\nAcme' },
};

function answer(status, type, body, disposition) {
    const headers = { 'content-type': type, 'content-disposition': disposition || '' };
    const bytes = Buffer.from(body);
    const blob = { size: bytes.length, type, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) };
    return {
        ok: status < 400, status,
        headers: { get: (k) => headers[k.toLowerCase()] || null },
        blob: async () => blob,
        arrayBuffer: async () => blob.arrayBuffer(),
        text: async () => body,
        json: async () => JSON.parse(body),
    };
}

function load({ urlBridge = true, mint = 'ok', openResult = { success: true } } = {}) {
    const calls = [];
    const fetched = [];
    const toasts = [];
    let clickHandler = null;
    let n = 0;
    const api = {
        open_document_html: async (title, html) => { calls.push(['open_document_html', html]); return { success: true }; },
        open_document_pdf: async (name) => { calls.push(['open_document_pdf', name]); return { success: true, path: '/Documents/' + name }; },
        save_document_file: async (name) => { calls.push(['save_document_file', name]); return { success: true, path: '/Documents/' + name }; },
        reveal_path: async () => ({ success: true }),
    };
    if (urlBridge) {
        api.open_document_url = async (title, url) => { calls.push(['open_document_url', title, url]); return openResult; };
    }
    const win = {
        location: { href: ORIGIN + '/#/', origin: ORIGIN },
        open: () => null,
        addEventListener() {},
        pywebview: { api },
    };
    const ctx = {
        console, URL, Promise, Buffer, Uint8Array, String, JSON,
        btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
        window: win,
        navigator: { userAgent: 'node' },
        setTimeout: (fn, ms) => (ms > 1000 ? 0 : setTimeout(fn, ms)),
        document: { addEventListener: (type, fn) => { if (type === 'click') clickHandler = fn; } },
        fetch: async (url, opts) => {
            fetched.push([url, opts || {}]);
            if (url === '/api/print-tokens') {
                if (mint === 'throw') throw new Error('Failed to fetch');
                if (mint !== 'ok') return answer(mint, 'application/json', JSON.stringify({ detail: 'refused' }));
                n += 1;
                return answer(201, 'application/json', JSON.stringify({ token: 'tok' + n + '_abcdefghijklmnop', url: ORIGIN + '/print/tok' + n, expires_in: 60 }));
            }
            const sheet = SHEETS[new URL(url, ORIGIN).pathname];
            return answer(200, sheet.type, sheet.body, sheet.disposition);
        },
        toast: (m, kind) => toasts.push([m, kind]),
        toastAction() {},
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/desktop_shim.js'), 'utf8'), ctx);
    const settle = () => new Promise((r) => setTimeout(r, 20));
    return {
        calls, fetched, toasts, win, settle,
        open: async (url) => { win.open(url, '_blank'); await settle(); },
        click: async (href) => {
            const a = { href, target: '_blank' };
            a.closest = (sel) => (sel === 'a[target="_blank"], a[download]' ? a : null);
            const e = { target: a, prevented: false, preventDefault() { this.prevented = true; } };
            clickHandler(e);
            await settle();
            return e;
        },
    };
}

test('a print-preview opens at its one-time localhost address, not from HTML', async () => {
    const shell = load();
    await shell.open('/api/invoices/7/print-preview');
    assert.deepEqual(shell.calls, [
        ['open_document_url', 'SlowBooks Pro 2026', ORIGIN + '/print/tok1_abcdefghijklmnop'],
    ]);
    // one request: the token; the sheet itself is drawn by the server
    assert.equal(shell.fetched.length, 1);
    const [url, opts] = shell.fetched[0];
    assert.equal(url, '/api/print-tokens');
    assert.equal(opts.method, 'POST');
    assert.equal(opts.credentials, 'same-origin');
    assert.equal(opts.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(opts.body), { path: '/api/invoices/7/print-preview', query: '' });
});

test("a report's Print sends its query with its path", async () => {
    const shell = load();
    await shell.open('/api/reports/profit-loss/print?start_date=2026-01-01&end_date=2026-03-31');
    assert.deepEqual(JSON.parse(shell.fetched[0][1].body), {
        path: '/api/reports/profit-loss/print',
        query: 'start_date=2026-01-01&end_date=2026-03-31',
    });
    assert.equal(shell.calls[0][0], 'open_document_url');
});

test('a Print link clicked (target=_blank) takes the same address', async () => {
    const shell = load();
    const e = await shell.click(ORIGIN + '/api/banking/reconciliations/3/print');
    assert.equal(e.prevented, true);
    assert.deepEqual(shell.calls, [
        ['open_document_url', 'SlowBooks Pro 2026', ORIGIN + '/print/tok1_abcdefghijklmnop'],
    ]);
    assert.deepEqual(JSON.parse(shell.fetched[0][1].body), {
        path: '/api/banking/reconciliations/3/print', query: '',
    });
});

test('PDFs, the check and CSVs keep the download path and never ask for a token', async () => {
    const shell = load();
    await shell.open('/api/invoices/7/pdf');
    await shell.open('/api/checks/print?transaction_id=4');
    await shell.open('/api/csv/export/customers');
    assert.deepEqual(shell.calls, [
        ['open_document_pdf', 'Invoice_1001.pdf'],
        ['open_document_pdf', 'Check_1.pdf'],
        ['save_document_file', 'customers.csv'],
    ]);
    assert.ok(shell.fetched.every(([url]) => url !== '/api/print-tokens'));
});

test('a token refused or unreachable falls back to the sheet opened from its HTML', async () => {
    for (const mint of [500, 403, 'throw']) {
        const shell = load({ mint });
        await shell.open('/api/budgets/print?year=2026');
        assert.deepEqual(shell.calls, [['open_document_html', '<html>Budget</html>']], String(mint));
        assert.deepEqual(shell.fetched.map(([url]) => url), ['/api/print-tokens', '/api/budgets/print?year=2026']);
    }
});

test('a window that would not open falls back too', async () => {
    const shell = load({ openResult: { success: false, error: 'no' } });
    await shell.open('/api/invoices/7/print-preview');
    assert.deepEqual(shell.calls.map((c) => c[0]), ['open_document_url', 'open_document_html']);
});

test('a launcher without open_document_url opens from HTML and asks for no token', async () => {
    const shell = load({ urlBridge: false });
    await shell.open('/api/invoices/7/print-preview');
    assert.deepEqual(shell.calls, [['open_document_html', '<html>Invoice</html>']]);
    assert.deepEqual(shell.fetched.map(([url]) => url), ['/api/invoices/7/print-preview']);
});

test('printSheet names print sheets of this app and nothing else', () => {
    const { win } = load();
    const sheet = win.SlowbooksDesktop.printSheet;
    assert.deepEqual(JSON.parse(JSON.stringify(sheet('/api/estimates/2/print-preview'))), { path: '/api/estimates/2/print-preview', query: '' });
    assert.deepEqual(JSON.parse(JSON.stringify(sheet(ORIGIN + '/api/budgets/variance/print?year=2026'))), { path: '/api/budgets/variance/print', query: 'year=2026' });
    for (const url of [
        '/api/invoices/7',
        '/api/invoices/7/pdf',
        '/api/print-tokens',
        '/print/abc',
        '/api/checks/print',
        '/api/checks/test-print',
        '/api/donors/giving-statement/preview',
        '/portal/print',
    ]) {
        assert.equal(sheet(url), null, url);
    }
});
