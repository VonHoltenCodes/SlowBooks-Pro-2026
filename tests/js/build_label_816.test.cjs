// About names the build (#816): App.buildLabel turns /api/system's answer into
// "Version <version>+<sha>", or the bare version when the build does not know
// its commit. Pulled out of app.js on its own; it touches nothing else.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

function buildLabel() {
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const m = src.match(/\n    buildLabel\(info\) \{\n([\s\S]*?)\n    \},\n/);
    assert.ok(m, 'App.buildLabel is in app.js');
    return new Function('info', m[1]);
}

test('a build with a commit shows <version>+<sha>', () => {
    const label = buildLabel()({ version: '2.32.0', commit: '0123456789ab', build_version: '2.32.0+0123456789ab' });
    assert.equal(label, 'Version 2.32.0+0123456789ab');
});

test('a build without a commit shows the bare version', () => {
    assert.equal(buildLabel()({ version: '2.32.0', commit: null, build_version: '2.32.0' }), 'Version 2.32.0');
    // an older server that sends no build_version at all
    assert.equal(buildLabel()({ version: '2.32.0' }), 'Version 2.32.0');
});

test('no version (a refused /api/system) shows nothing', () => {
    assert.equal(buildLabel()({}), null);
    assert.equal(buildLabel()(null), null);
});
