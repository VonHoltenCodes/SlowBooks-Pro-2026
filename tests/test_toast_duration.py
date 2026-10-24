"""A toast stays long enough to be read (2.18.0 gate, macbase1 NEW-13).

Every toast was removed after three seconds whatever it said, so a two-line
refusal (the closing-date lock) was gone before it was read. A toast now
stays three seconds for a few words and longer for more, at least six for
an error and never more than fifteen. It takes no clicks: the container has
pointer-events: none, so a click where a toast sits reaches what is under it
("Settings saved" over Save Settings swallowed the next save, 2.29.0 gate,
skytech W-35); with no pointer events there is no hover to hold it, so its
whole stay is the timer."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
const fs = require('fs'), vm = require('vm');
const timers = [];
function node() {
  const listeners = {};
  return { className: '', textContent: '', removed: false, listeners,
    remove() { this.removed = true; },
    addEventListener(t, fn) { listeners[t] = fn; } };
}
const shown = [];
const container = { appendChild: (el) => shown.push(el) };
const ctx = { console, window: {},
  document: { addEventListener: () => {}, createElement: () => node(),
              querySelector: (s) => (s === '#toast-container' ? container : null) },
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\nthis.toast = toast; this.toastMs = toastMs;', ctx);
const lock = 'Transaction date 2026-08-17 is on or before the closing date (2026-08-27). '
  + 'Modifications to closed periods are not allowed. The closing-date password you entered '
  + 'is not correct. Too many wrong closing-date passwords were entered; try again in 10 minutes.';
const ms = {
  short: ctx.toastMs('Invoice saved', 'success'),
  shortError: ctx.toastMs('Not found', 'error'),
  lock: ctx.toastMs(lock, 'error'),
  huge: ctx.toastMs('x'.repeat(2000), 'success'),
};
ctx.toast(lock, 'error');
const el = shown[0];
const first = timers[0].ms;
timers[0].fn();
console.log(JSON.stringify({ ms, first, listeners: Object.keys(el.listeners),
  timers: timers.length, removed: el.removed }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_toast_stays_long_enough_to_read():
    out = subprocess.run(
        ["node", "-e", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["ms"]["short"] == 3000
    assert got["ms"]["shortError"] == 6000
    assert 12000 <= got["ms"]["lock"] <= 15000  # the two-line lock refusal
    assert got["ms"]["huge"] == 15000
    assert got["first"] == got["ms"]["lock"]
    # no mouse handlers: the toast is not in the pointer's way at all
    assert got["listeners"] == [] and got["timers"] == 1
    assert got["removed"] is True


def test_a_toast_takes_no_clicks():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    container = css[css.index("#toast-container {") :]
    container = container[: container.index("}")]
    assert "pointer-events: none;" in container
    # a toast question's buttons (toastAction) take their own
    assert ".toast .btn { pointer-events: auto; }" in css
