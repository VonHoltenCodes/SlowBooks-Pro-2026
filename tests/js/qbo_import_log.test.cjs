// Exercise the real QBO page's HTTP failures without browser or network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fixture(fetch) {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            innerHTML: '', textContent: '', hidden: false, disabled: false,
            scrollHeight: 0, scrollTop: 0, clientHeight: 320,
            insertAdjacentHTML(_, html) { this.innerHTML += html; },
            attributes: {},
            setAttribute(name, value) { this.attributes[name] = value; },
            classList: { toggle(name, active) { this[name] = active; } },
            querySelector(selector) { return selector === '.qbo-log-error' && /class="qbo-log-error"/.test(this.innerHTML) ? {} : null; },
        });
        return elements.get(id);
    };
    const buttons = [element('all'), element('selected')];
    const timers = new Map();
    let timerId = 0, authPrompts = 0;
    const context = {
        fetch, AbortController, Date, console,
        setTimeout(callback, ms) { timers.set(++timerId, { callback, ms }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() { return ++timerId; }, clearInterval() {},
        localStorage: { getItem: () => null },
        window: { SlowbooksAuth: { promptAuth() { authPrompts++; } } },
        App: { setStatus(text) { element('status').textContent = text; } },
        API: { get: async () => ({ connected: true }) },
        $: selector => element(selector), $$: () => buttons,
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        toast() { throw new Error('Import failures should be recorded in the log, not only in a toast'); },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/qbo.js'), 'utf8') + '\nthis.QBOPage = QBOPage;', context);
    const page = context.QBOPage;
    Object.assign(page, {
        _mounted: true, _status: { connected: true }, _monitorReady: true,
        _monitorBlocked: false, _monitorError: null, _startError: null,
        _enteredAt: Date.now(), _lastContact: Date.now(), _diagnosticKey: '',
    });
    return { page, element, buttons, timers, authPrompts: () => authPrompts };
}

const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const snapshot = (run = null, events = []) => ({ run, events, has_more: false, server_time: new Date().toISOString() });
const running = () => ({ run_id: 'run-1', status: 'running', started_at: new Date().toISOString(),
    last_progress_at: new Date().toISOString(), current_step: 'Fetching JournalEntry page 2',
    counters: { fetched: 100, processed: 0, imported: 0, pending: 0, skipped: 0, errors: 0 } });
const progress = sequence => ({ sequence, timestamp: new Date().toISOString(), level: 'info', code: '',
    entity: 'journal_entries', item_id: '17', item_label: 'Adjustment', action: 'query', message: 'Fetching JournalEntry page 2' });

test('Errors filter preserves the event cursor and reports newly arriving errors', () => {
    const f = fixture(async () => response(200, snapshot()));
    f.page._appendEvents([progress(1)]);
    f.page.toggleErrors();
    assert.equal(f.element('#qbo-import-log').classList['qbo-errors-only'], true);
    assert.equal(f.element('#qbo-errors-filter').attributes['aria-pressed'], 'true');
    assert.equal(f.element('#qbo-log-empty').hidden, false);
    assert.equal(f.element('#qbo-log-empty').textContent, 'No errors recorded.');
    f.page._diagnostic('info', 'MONITOR_RETRY', 'monitor', 'Retrying import monitoring');
    assert.equal(f.element('#qbo-log-empty').hidden, false);
    f.page._appendEvents([{ ...progress(2), level: 'error', code: 'IMPORT_POSTING_MISMATCH',
        item_id: '11063', item_label: '9/1 Payroll', message: 'Account QBO #115 / local #136: local 0.00, QBO 5849.46' }]);
    assert.equal(f.element('#qbo-log-empty').hidden, true);
    assert.equal(f.page._after, 2);
    const html = f.element('#qbo-log-rows').innerHTML;
    assert.match(html, /data-sequence="1"/);
    assert.match(html, /#11063/);
    assert.match(html, /9\/1 Payroll/);
    assert.match(html, /Account QBO #115 \/ local #136/);
    f.page.toggleErrors();
    assert.equal(f.element('#qbo-errors-filter').attributes['aria-pressed'], 'false');
    assert.equal(f.element('#qbo-log-rows').innerHTML, html);
});

test('GET 404 is timestamped, identifies the missing route, and stops misleading reconnects', async () => {
    const f = fixture(async () => response(404, { detail: 'Not Found' }));
    f.page._enteredAt -= 30000;
    await f.page._pollLog();
    assert.equal(f.element('#qbo-run-status').textContent, 'Server update required');
    assert.match(f.element('#qbo-run-detail').textContent, /HTTP 404.*Restart the server/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /HTTP_<wbr>404/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /GET \/api\/qbo\/import-runs\/latest/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /title="\d{4}-\d\d-\d\dT/);
    assert.equal(f.element('#qbo-log-empty').hidden, true);
    assert.equal(f.element('#qbo-monitor-retry').hidden, false);
    assert.ok(f.buttons.every(button => button.disabled));
    assert.equal(f.timers.size, 0);
    f.page._lastContact -= 60000;
    f.page._updateActivity();
    assert.equal(f.element('#qbo-run-status').textContent, 'Server update required');
    assert.doesNotMatch(f.element('#qbo-run-activity').textContent, /reconnecting/i);
    assert.equal(f.page._after, 0);
});

test('import start is gated until monitoring is available', async () => {
    let requests = 0;
    const f = fixture(async () => { requests++; return response(404, {}); });
    f.page._monitorReady = false;
    await f.page._startImport(['journal_entries']);
    assert.equal(requests, 0);
    assert.match(f.element('#qbo-log-rows').innerHTML, /MONITOR_<wbr>NOT_<wbr>READY/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /Import was not started/);
});

test('POST 404 is recorded in the log and keeps the previous import rows', async () => {
    const f = fixture(async () => response(404, { detail: 'Not Found' }));
    f.page._appendEvents([progress(7)]);
    await f.page._startImport(['journal_entries']);
    const html = f.element('#qbo-log-rows').innerHTML;
    assert.match(html, /data-sequence="7"/);
    assert.match(html, /Requesting import: journal_entries/);
    assert.match(html, /POST \/api\/qbo\/import-runs/);
    assert.match(html, /HTTP_<wbr>404/);
    assert.equal(f.element('#qbo-run-status').textContent, 'Server update required');
    assert.equal(f.page._after, 7);
    assert.equal(f.timers.size, 0);
});

test('server errors identify HTTP status and retain progress while retrying', async () => {
    const f = fixture(async () => response(503, { detail: 'Service temporarily unavailable' }));
    f.page._run = running();
    f.page._appendEvents([progress(8)]);
    await f.page._pollLog();
    await f.page._pollLog();
    const html = f.element('#qbo-log-rows').innerHTML;
    assert.match(html, /data-sequence="8"/);
    assert.equal((html.match(/HTTP_<wbr>503/g) || []).length, 1);
    assert.equal(f.element('#qbo-run-status').textContent, 'Monitor unavailable');
    assert.match(f.element('#qbo-run-activity').textContent, /Server responded HTTP 503/);
    assert.equal(f.page._after, 8);
    assert.ok([...f.timers.values()].some(timer => timer.ms === 2000));
});

test('network loss is logged, reconnects, and does not advance the server event cursor', async () => {
    let disconnected = true;
    const run = running();
    const f = fixture(async () => {
        if (disconnected) throw new TypeError('Failed to fetch');
        return response(200, snapshot(run, [progress(9)]));
    });
    f.page._run = run;
    f.page._appendEvents([progress(8)]);
    f.page._lastContact -= 20000;
    await f.page._pollLog();
    assert.equal(f.element('#qbo-run-status').textContent, 'Connection interrupted');
    assert.match(f.element('#qbo-log-rows').innerHTML, /NETWORK_<wbr>ERROR/);
    assert.equal(f.page._after, 8);
    disconnected = false;
    await f.page._pollLog();
    assert.equal(f.element('#qbo-run-status').textContent, 'Running');
    assert.match(f.element('#qbo-log-rows').innerHTML, /MONITOR_<wbr>RECONNECTED/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /data-sequence="9"/);
    assert.equal(f.page._after, 9);
});

test('HTTP 401 prompts login, records the failure, and pauses monitoring', async () => {
    const f = fixture(async () => response(401, { detail: 'Not authenticated' }));
    await f.page._pollLog();
    assert.equal(f.authPrompts(), 1);
    assert.match(f.element('#qbo-log-rows').innerHTML, /HTTP_<wbr>401/);
    assert.match(f.element('#qbo-run-detail').textContent, /session expired/);
    assert.equal(f.timers.size, 0);
});

test('retry after server update restores monitoring and enables import controls', async () => {
    let updated = false;
    const f = fixture(async () => updated ? response(200, snapshot()) : response(404, { detail: 'Not Found' }));
    await f.page._pollLog();
    updated = true;
    f.page.retryMonitor();
    // retryMonitor intentionally starts the asynchronous read without waiting.
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.element('#qbo-run-status').textContent, 'Ready');
    assert.equal(f.element('#qbo-monitor-retry').hidden, true);
    assert.ok(f.buttons.every(button => !button.disabled));
    assert.match(f.element('#qbo-log-rows').innerHTML, /HTTP_<wbr>404/);
    assert.match(f.element('#qbo-log-rows').innerHTML, /MONITOR_<wbr>RETRY/);
    assert.equal(f.page._monitorReady, true);
});

test('invalid successful response is a visible error, not silent reconnecting', async () => {
    const f = fixture(async () => response(200, { arbitrary: 'payload' }));
    await f.page._pollLog();
    assert.match(f.element('#qbo-log-rows').innerHTML, /IMPORT_<wbr>INVALID_<wbr>RESPONSE/);
    assert.equal(f.element('#qbo-run-status').textContent, 'Monitor unavailable');
    assert.equal(f.timers.size, 0);
});

test('a slow import request shows its action before the server accepts it', async () => {
    let accept;
    const f = fixture(async (_, options) => options?.method === 'POST'
        ? new Promise(resolve => { accept = resolve; }) : response(200, snapshot(running())));
    const started = f.page._startImport(['journal_entries']);
    assert.equal(f.element('#qbo-run-status').textContent, 'Starting');
    assert.match(f.element('#qbo-log-rows').innerHTML, /Requesting import: journal_entries/);
    assert.ok(f.buttons.every(button => button.disabled));
    accept(response(202, { run_id: 'run-1', status: 'queued' }));
    await started;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.element('#qbo-run-status').textContent, 'Running');
});

test('aborting an older poll cannot overwrite a POST 404 with a network failure', async () => {
    const f = fixture(async (_, options) => {
        if (options?.method === 'POST') return response(404, { detail: 'Not Found' });
        return new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), { name: 'AbortError' })));
        });
    });
    const previousPoll = f.page._pollLog();
    await f.page._startImport(['journal_entries']);
    await previousPoll;
    assert.equal(f.element('#qbo-run-status').textContent, 'Server update required');
    assert.equal(f.page._monitorError.code, 'HTTP_404');
    assert.equal(f.page._monitorBlocked, true);
    assert.equal(f.timers.size, 0);
    assert.doesNotMatch(f.element('#qbo-log-rows').innerHTML, /MONITOR_<wbr>TIMEOUT/);
});

test('a non-JSON error response still logs its HTTP status', async () => {
    const f = fixture(async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError('HTML error page'); } }));
    await f.page._pollLog();
    assert.equal(f.element('#qbo-run-status').textContent, 'Monitor unavailable');
    assert.match(f.element('#qbo-log-rows').innerHTML, /HTTP_<wbr>502/);
    assert.match(f.element('#qbo-run-activity').textContent, /Server responded HTTP 502/);
});
