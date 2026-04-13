const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/settings.js', 'utf8')}\nthis.SettingsPage = SettingsPage;`;
const updatedSettings = { locale: 'en-NZ', currency: 'NZD', company_name: 'SlowBooks NZ' };
const context = {
    App: { settings: { locale: 'en-US', currency: 'USD' } },
    API: {
        put: async () => updatedSettings,
    },
    FormData: class {
        constructor(target) {
            this.target = target;
        }
        entries() {
            return Object.entries(this.target.data);
        }
    },
    Object,
    toast: () => {},
    escapeHtml: (value) => value || '',
    setTimeout,
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    await context.SettingsPage.save({
        preventDefault() {},
        target: { data: { locale: 'en-NZ', currency: 'NZD' } },
    });

    assert.deepStrictEqual(context.App.settings, updatedSettings);
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
