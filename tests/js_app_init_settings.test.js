const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = fs.readFileSync('app/static/js/app.js', 'utf8');
const events = [];
const settings = { locale: 'en_NZ', currency: 'NZD' };
const context = {
    API: {
        get: async path => {
            events.push(`api:${path}`);
            return settings;
        },
    },
    $: selector => (
        selector === '#topbar-clock' || selector === '#status-date' || selector === '#status-company'
            ? { textContent: '' }
            : null
    ),
    $$: () => [],
    console,
    document: {
        documentElement: { getAttribute: () => 'light', setAttribute() {} },
        addEventListener(event) {
            events.push(`document:${event}`);
        },
        querySelector: () => null,
        querySelectorAll: () => [],
    },
    escapeHtml: value => String(value),
    location: { hash: '#/invoices' },
    localStorage: { getItem: () => null, setItem() {} },
    setInterval: () => 1,
    window: {
        addEventListener(event) {
            events.push(`window:${event}`);
        },
    },
};

vm.createContext(context);
vm.runInContext(`${code}\nthis.App = App;`, context);

const order = [];
context.App.navigate = hash => {
    order.push(`navigate:${hash}:${context.App.settings.locale || 'missing'}:${context.App.settings.currency || 'missing'}`);
};

(async () => {
    await context.App.init();
    assert.deepStrictEqual(context.App.settings, settings);
    assert.ok(events.indexOf('api:/settings') !== -1);
    assert.deepStrictEqual(order, [
        'navigate:#/invoices:en_NZ:NZD',
    ]);
})();
