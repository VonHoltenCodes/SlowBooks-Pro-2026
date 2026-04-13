const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const appCode = fs.readFileSync('app/static/js/app.js', 'utf8');
const indexHtml = fs.readFileSync('index.html', 'utf8');

const context = {
    API: { get: async () => ({}) },
    $: () => null,
    $$: () => [],
    console,
    document: {
        documentElement: { getAttribute: () => 'light', setAttribute() {} },
        addEventListener() {},
        querySelector: () => null,
        querySelectorAll: () => [],
    },
    escapeHtml: value => String(value),
    location: { hash: '#/' },
    localStorage: { getItem: () => null, setItem() {} },
    setInterval: () => 1,
    window: { addEventListener() {} },
};

vm.createContext(context);
vm.runInContext(`${appCode}\nthis.App = App;`, context);

assert.ok(!('/tax' in context.App.routes), 'App.routes should not expose /tax');
assert.ok(!indexHtml.includes('href="#/tax"'), 'index nav should not expose #/tax');
assert.ok(!indexHtml.includes('Tax Reports'), 'index nav should not advertise Tax Reports');
