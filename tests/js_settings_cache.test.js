const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const settingsCode = fs.readFileSync('app/static/js/settings.js', 'utf8');
const code = `${settingsCode}\nthis.SettingsPage = SettingsPage;`;
const updatedSettings = { locale: 'en-NZ', currency: 'NZD', company_name: 'SlowBooks NZ' };
const posts = [];
const context = {
    App: { settings: { locale: 'en-US', currency: 'USD' } },
    API: {
        get: async () => ({ payroll_contact_name: '', payroll_contact_phone: '', payroll_contact_email: '' }),
        put: async () => updatedSettings,
        post: async (path) => {
            posts.push(path);
            return { status: 'loaded' };
        },
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
    context.SettingsPage.loadBackups = () => {};
    const sampleHtml = await context.SettingsPage.render();
    assert.ok(sampleHtml.includes('Employment Information and employee filing exports'));
    assert.ok(!sampleHtml.includes('payday filing'));
    assert.ok(sampleHtml.includes('Load NZ Demo Data'));
    assert.ok(sampleHtml.includes('Load Xero Sample Default Chart'));
    assert.ok(sampleHtml.includes('Load MAS Chart of Accounts'));

    await context.SettingsPage.save({
        preventDefault() {},
        target: { data: { locale: 'en-NZ', currency: 'NZD' } },
    });

    assert.deepStrictEqual(context.App.settings, updatedSettings);

    await context.SettingsPage.loadDemoData();
    await context.SettingsPage.loadChartTemplate('xero');
    await context.SettingsPage.loadChartTemplate('mas');
    assert.deepStrictEqual(posts, ['/settings/load-demo-data', '/settings/load-chart-template/xero', '/settings/load-chart-template/mas']);
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
