// #476: an optional short chime when a transaction posts. toast() rings it
// on a success toast when the per-user preference is on, never on an error
// or warning, and never when it's off. The sound is guarded so a browser or
// WebView without AudioContext (or one that refuses autoplay) stays silent
// instead of throwing. These stub AudioContext and watch toast() drive it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// A fake AudioContext that counts the oscillators a chime starts. Two tones
// per chime, so `starts === 2` means the sound played once.
function fakeAudioContext() {
    const counts = { contexts: 0, starts: 0 };
    function Ctx() {
        counts.contexts += 1;
        this.currentTime = 0;
        this.destination = {};
        this.createOscillator = () => ({
            type: '', frequency: {},
            connect: (node) => node,
            start: () => { counts.starts += 1; },
            stop: () => {},
        });
        this.createGain = () => ({
            gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
            connect: (node) => node,
        });
    }
    return { Ctx, counts };
}

// A minimal page: a toast container, createElement that swallows everything
// toast() does to the node, and setTimeout/clearTimeout as no-ops.
function load(audioCtor) {
    const container = { appendChild() {} };
    const document = {
        querySelector: (sel) => (sel === '#toast-container' ? container : null),
        querySelectorAll: () => [],
        createElement: () => ({
            className: '', textContent: '',
            appendChild() {}, remove() {}, addEventListener() {},
        }),
        addEventListener() {},
    };
    const window = {};
    if (audioCtor) window.AudioContext = audioCtor;
    const context = {
        window, document, App: { settings: {} },
        setTimeout: () => 0, clearTimeout: () => {},
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    vm.runInContext(
        js + '\nthis.toast = toast; this.playPostSound = playPostSound;'
           + ' this.setPostSoundEnabled = setPostSoundEnabled;',
        context,
    );
    return context;
}

test('a success toast rings the chime when the preference is on', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toast, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toast('Payment recorded');
    assert.equal(counts.starts, 2, 'two tones for one chime');
});

test('a success toast is silent when the preference is off (the default)', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toast } = load(Ctx);
    toast('Payment recorded');  // nothing turned it on
    assert.equal(counts.starts, 0);
    assert.equal(counts.contexts, 0, 'no AudioContext made while off');
});

test('an error or warning toast never rings, even with the preference on', () => {
    const { Ctx, counts } = fakeAudioContext();
    const { toast, setPostSoundEnabled } = load(Ctx);
    setPostSoundEnabled(true);
    toast('Something broke', 'error');
    toast('Heads up', 'warning');
    toast('For your information', 'info');
    assert.equal(counts.starts, 0);
});

test('a browser with no AudioContext stays silent and does not throw', () => {
    const { toast, setPostSoundEnabled } = load(undefined);  // no window.AudioContext
    setPostSoundEnabled(true);
    assert.doesNotThrow(() => toast('Entry posted'));
});
