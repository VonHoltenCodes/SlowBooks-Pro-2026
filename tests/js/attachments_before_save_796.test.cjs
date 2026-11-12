// Attachments on a new bill, expense or card charge before its first save
// (#796): PendingFiles in utils.js holds the files the form attached, puts
// their ids on the create call, drops one taken off the form, and discards
// the rest when the form closes without saving. Pulled out of utils.js on
// its own, with the page's helpers stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const JS = path.join(__dirname, '../../app/static/js');
const UTILS = fs.readFileSync(path.join(JS, 'utils.js'), 'utf8');

function source(name) {
    const start = UTILS.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `${name} is in utils.js`);
    const end = UTILS.indexOf('\n}\n', start);
    return UTILS.slice(start, end + 2);
}

function load({ upload } = {}) {
    const start = UTILS.indexOf('const PendingFiles = {');
    assert.ok(start >= 0, 'PendingFiles is in utils.js');
    const end = UTILS.indexOf('\n};\n', start);
    const body = UTILS.slice(start, end + 3);
    const calls = { fetch: [], del: [], toast: [] };
    const elements = {
        '#pending-files-list': { innerHTML: '' },
        '#pending-attach-file': { files: [], value: '' },
    };
    const $ = sel => elements[sel] || null;
    const API = {
        del: async p => { calls.del.push(p); return { status: 'deleted' }; },
        responseError: async (resp, fallback) => (await resp.json()).detail || fallback,
    };
    const fetchStub = async (url, opts) => {
        calls.fetch.push([url, opts.method]);
        return upload();
    };
    class FormData { append(k, v) { this[k] = v; } }
    const make = new Function('$', 'API', 'fetch', 'FormData', 'toast',
        `${source('escapeHtml')}\n${source('formatFileSize')}\n${body}\nreturn PendingFiles;`);
    const PendingFiles = make($, API, fetchStub, FormData, (m, kind) => calls.toast.push([m, kind]));
    return { PendingFiles, calls, elements };
}

const held = (id, filename, size = 2048) => ({
    ok: true, json: async () => ({ id, filename, file_size: size, mime_type: 'application/pdf' }),
});

test('an attached file is listed and its id goes on the save', async () => {
    let n = 0;
    const ids = ['a'.repeat(32), 'b'.repeat(32)];
    const { PendingFiles, calls, elements } = load({ upload: () => held(ids[n], `r${n++}.pdf`) });
    PendingFiles.reset();
    assert.deepEqual(PendingFiles.ids(), []);
    assert.match(PendingFiles.stripHtml(), /No attachments/);
    for (const _ of ids) {
        elements['#pending-attach-file'].files = [{ name: 'x.pdf' }];
        await PendingFiles.add();
    }
    assert.deepEqual(calls.fetch, [['/api/attachments/pending', 'POST'], ['/api/attachments/pending', 'POST']]);
    assert.deepEqual(PendingFiles.ids(), ids);
    const list = elements['#pending-files-list'].innerHTML;
    assert.match(list, /r0\.pdf/);
    assert.match(list, /r1\.pdf/);
    assert.match(list, /\(2\.0 KB\)/);
    assert.equal(elements['#pending-attach-file'].value, '');
});

test('a refused file says the server\'s sentence and is not listed', async () => {
    const { PendingFiles, calls, elements } = load({
        upload: () => ({ ok: false, json: async () => ({ detail: "File extension '.exe' not allowed" }) }),
    });
    PendingFiles.reset();
    elements['#pending-attach-file'].files = [{ name: 'tool.exe' }];
    await PendingFiles.add();
    assert.deepEqual(calls.toast, [["File extension '.exe' not allowed", 'error']]);
    assert.deepEqual(PendingFiles.ids(), []);
});

test('no file chosen asks for one and sends nothing', async () => {
    const { PendingFiles, calls } = load({ upload: () => assert.fail('no upload') });
    await PendingFiles.add();
    assert.deepEqual(calls.toast, [['Select a file first', 'error']]);
    assert.deepEqual(calls.fetch, []);
});

test('a file taken off the form is dropped here and on the server', async () => {
    const id = 'c'.repeat(32);
    const { PendingFiles, calls, elements } = load({ upload: () => held(id, 'receipt.pdf') });
    elements['#pending-attach-file'].files = [{}];
    await PendingFiles.add();
    await PendingFiles.remove(id);
    assert.deepEqual(PendingFiles.ids(), []);
    assert.deepEqual(calls.del, [`/attachments/pending/${id}`]);
    assert.match(elements['#pending-files-list'].innerHTML, /No attachments/);
});

test('closing the form without saving discards every file', async () => {
    let n = 0;
    const { PendingFiles, calls, elements } = load({ upload: () => held(String(n++).repeat(32), 'r.pdf') });
    for (let i = 0; i < 2; i++) { elements['#pending-attach-file'].files = [{}]; await PendingFiles.add(); }
    PendingFiles.discard();
    assert.deepEqual(calls.del, ['/attachments/pending/' + '0'.repeat(32), '/attachments/pending/' + '1'.repeat(32)]);
    assert.deepEqual(PendingFiles.ids(), []);
});

test('after a save the files are the document\'s: closing discards nothing', async () => {
    const { PendingFiles, calls, elements } = load({ upload: () => held('d'.repeat(32), 'r.pdf') });
    elements['#pending-attach-file'].files = [{}];
    await PendingFiles.add();
    PendingFiles.saved();
    PendingFiles.discard();
    assert.deepEqual(calls.del, []);
});

test('a file name is shown as text, never as markup', () => {
    const { PendingFiles } = load({});
    const html = PendingFiles.listHtml([{ id: 'e'.repeat(32), filename: '<img src=x onerror=alert(1)>.pdf', file_size: 5 }]);
    assert.ok(!html.includes('<img'));
    assert.match(html, /&lt;img/);
    assert.match(html, /\(5 bytes\)/);
});

test('each new-document form shows the strip and sends the ids', () => {
    for (const [file, create] of [['bills.js', "API.post('/bills'"], ['expenses.js', "API.post('/expenses'"], ['cc_charges.js', "API.post('/cc-charges'"]]) {
        const js = fs.readFileSync(path.join(JS, file), 'utf8');
        assert.ok(js.includes('PendingFiles.reset();'), file);
        assert.ok(js.includes('PendingFiles.stripHtml()'), file);
        assert.ok(js.includes('pending_attachments: PendingFiles.ids()'), file);
        assert.ok(js.includes('onClose: () => PendingFiles.discard()'), file);
        // the ids are cleared only after the create answers
        assert.ok(js.indexOf('PendingFiles.saved();') > js.indexOf(create), file);
    }
    // an edit of a saved bill keeps its own Attachments panel (on its view)
    const bills = fs.readFileSync(path.join(JS, 'bills.js'), 'utf8');
    assert.ok(bills.includes("${bill ? '' : PendingFiles.stripHtml()}"));
});
