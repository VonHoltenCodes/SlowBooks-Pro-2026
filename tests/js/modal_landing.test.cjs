// Where a dialog opens (_modalLanding, NEW-32 and #1095): its first control,
// passing over a form's skip link, a field's tortoise ([data-hint]) and the
// Activity panel's toggle ([data-activity-toggle]); the dialog itself when
// that control undoes something (Void, Delete: [data-destructive]). The
// payment, credit memo and deposit views put Activity ahead of Void, and
// opened on the toggle; they open on the dialog now.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const JS = path.join(__dirname, '../../app/static/js');

// A control: its classes and attributes, answering the simple selectors
// _modalLanding and _tabStops ask about (a comma list of .class, [attr],
// :disabled), and drawn.
function control(name, { cls = [], attrs = [] } = {}) {
    const has = sel => {
        sel = sel.trim();
        if (sel === ':disabled') return false;
        if (sel.startsWith('.')) return cls.includes(sel.slice(1));
        const m = /^\[([\w-]+)\]$/.exec(sel);
        if (m) return attrs.includes(m[1]);
        throw new Error(`selector this fake does not know: ${sel}`);
    };
    return {
        name, tagName: 'BUTTON', tabIndex: 0,
        matches: sel => sel.split(',').some(has),
        checkVisibility: () => true,
    };
}

function landingOver(stops) {
    const modal = { name: 'modal' };
    const body = { querySelectorAll: () => stops };
    const ctx = {
        window: {}, console,
        document: {
            activeElement: null, contains: () => false, addEventListener() {},
            getElementById: () => null, body: {},
            querySelector: sel => (sel === '#modal' ? modal : sel === '#modal-body' ? body : null),
            querySelectorAll: () => [],
        },
        setTimeout: () => 0, clearTimeout() {}, location: { hash: '#/' },
    };
    ctx.window.document = ctx.document;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'utils.js'), 'utf8') + '\nthis._modalLanding = _modalLanding;', ctx);
    return ctx._modalLanding().name;
}

const ACTIVITY = () => control('activity', { attrs: ['data-activity-toggle'] });
const VOID = () => control('void', { attrs: ['data-destructive'] });

test('a view with Activity ahead of Void opens on the dialog itself', () => {
    // the payment, credit memo and deposit views: Activity, then Void
    assert.equal(landingOver([ACTIVITY(), VOID(), control('print')]), 'modal');
});

test('a view with Activity ahead of a harmless control opens on that control', () => {
    assert.equal(landingOver([ACTIVITY(), control('save-pdf'), VOID()]), 'save-pdf');
});

test('a view whose only control is the Activity toggle opens on the dialog', () => {
    assert.equal(landingOver([ACTIVITY()]), 'modal');
});

test('skip links and tortoises are still passed over, as before', () => {
    const skip = control('skip', { cls: ['skip-link'] });
    const hint = control('hint', { attrs: ['data-hint'] });
    assert.equal(landingOver([skip, hint, control('date')]), 'date');
    assert.equal(landingOver([skip, hint, ACTIVITY(), VOID()]), 'modal');
    // a dialog that has nothing but its skip link lands on the link
    assert.equal(landingOver([skip, hint, ACTIVITY()]), 'skip');
});

test('a first control that undoes is never the landing', () => {
    assert.equal(landingOver([VOID(), control('close')]), 'modal');
});

test('the Activity panel marks its toggle for the landing rule', () => {
    const src = fs.readFileSync(path.join(JS, 'utils.js'), 'utf8');
    assert.match(src, /<summary data-activity-toggle\b/);
});
