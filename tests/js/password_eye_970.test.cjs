// The show-password eye (#970): the toggle on its own, with a stub input and button.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const eye = require(path.join(__dirname, '..', '..', 'app', 'static', 'js', 'password_eye.js'));

function stub() {
    const attrs = {};
    return {
        type: 'password',
        innerHTML: '',
        setAttribute(k, v) { attrs[k] = v; },
        getAttribute(k) { return attrs[k]; },
        attrs,
    };
}

test('the toggle shows, then hides, and names itself each way', () => {
    const input = stub(); const button = stub();
    assert.strictEqual(eye.toggle(input, button), true);
    assert.strictEqual(input.type, 'text');
    assert.strictEqual(button.attrs['aria-pressed'], 'true');
    assert.strictEqual(button.attrs['aria-label'], 'Hide password');
    assert.strictEqual(button.attrs['title'], 'Hide password');
    assert.ok(button.innerHTML.includes('<svg'));
    assert.strictEqual(eye.toggle(input, button), false);
    assert.strictEqual(input.type, 'password');
    assert.strictEqual(button.attrs['aria-pressed'], 'false');
    assert.strictEqual(button.attrs['aria-label'], 'Show password');
});

test('the icon is hidden from assistive tech and the value is never touched', () => {
    const input = stub(); input.value = 'hunter2';
    const button = stub();
    eye.toggle(input, button);
    assert.ok(button.innerHTML.includes('aria-hidden="true"'));
    assert.strictEqual(input.value, 'hunter2');
});
