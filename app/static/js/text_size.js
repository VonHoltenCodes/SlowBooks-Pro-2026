/**
 * Text size (#717): 100, 115, 125 or 150 percent, kept per computer.
 *
 * One mechanism for every way of changing it: Settings → My Preferences,
 * and in the desktop app Ctrl + / Ctrl − / Ctrl 0 and Ctrl + wheel
 * (desktop_shim.js), all go through apply() below and the one stored
 * setting. The size is the CSS zoom of the whole page, set by the
 * stylesheet from the data-text-size attribute this file puts on <html>
 * (style.css, the "Text size" block after :root). The stylesheet sizes the
 * app's frame from percentages and from the viewport divided by
 * --ui-scale, so at any size the sidebar scrolls inside itself, the
 * bottom bar stays in the window and a dialog keeps its buttons on screen.
 * Zoom is screen-only: printing and the print and PDF windows, which do
 * not load this file, are never enlarged.
 *
 * Loaded from <head>, straight after boot_check.js, so the size is on the
 * page before its first paint, the sign-in screen included. ES5 like
 * boot_check.js, for the same reason: it runs before anything can say
 * the engine is too old.
 *
 * Before 2.31.8 the desktop app's zoom keys kept a free zoom level of
 * their own (slowbooks.desktop.zoom, 0.5 to 2.0 by tenths). That level is
 * carried over once to the nearest size, and the old key removed.
 */
(function () {
    'use strict';

    var KEY = 'slowbooks.textsize';
    var OLD_ZOOM_KEY = 'slowbooks.desktop.zoom';
    var SIZES = [100, 115, 125, 150];

    function storage() {
        try { return window.localStorage || null; } catch (e) { return null; }
    }

    function get(key) {
        var s = storage();
        try { return s ? s.getItem(key) : null; } catch (e) { return null; }
    }

    function set(key, value) {
        var s = storage();
        try {
            if (!s) return;
            if (value === null) s.removeItem(key);
            else s.setItem(key, value);
        } catch (e) {
            // storage refused (private mode): the size lasts this page only
        }
    }

    function valid(size) {
        for (var i = 0; i < SIZES.length; i++) if (SIZES[i] === size) return true;
        return false;
    }

    // The size nearest a zoom level (1.3 → 125); below 100 it is 100.
    function nearest(level) {
        var pct = level * 100, best = SIZES[0];
        for (var i = 1; i < SIZES.length; i++) {
            if (Math.abs(SIZES[i] - pct) < Math.abs(best - pct)) best = SIZES[i];
        }
        return best;
    }

    var current = 100;

    function read() {
        var size = parseInt(get(KEY), 10);
        return valid(size) ? size : 100;
    }

    function apply(size) {
        size = parseInt(size, 10);
        if (!valid(size)) size = 100;
        current = size;
        var root = document.documentElement;
        if (size === 100) root.removeAttribute('data-text-size');
        else root.setAttribute('data-text-size', String(size));
        set(KEY, size === 100 ? null : String(size));
        // the Settings control, should it be on the page when a key changed it
        if (typeof document.querySelectorAll === 'function') {
            var controls = document.querySelectorAll('select[data-text-size-control]');
            for (var i = 0; i < controls.length; i++) controls[i].value = String(size);
        }
        return size;
    }

    // One size up (1) or down (-1), stopping at either end.
    function step(direction) {
        var i = SIZES.indexOf(current) + (direction > 0 ? 1 : -1);
        return apply(SIZES[Math.max(0, Math.min(SIZES.length - 1, i))]);
    }

    // How many window pixels one of the page's CSS pixels covers: what a
    // script turns a measured position (getBoundingClientRect, a mouse
    // event's clientX) into before it sets a position in CSS (the
    // type-ahead's list, the calculator tape). Measured rather than taken
    // from the size, so it holds whatever an engine's zoom reports.
    function ratio() {
        var body = document.body;
        if (!body || !body.offsetWidth || !body.getBoundingClientRect) return 1;
        var r = body.getBoundingClientRect().width / body.offsetWidth;
        return r > 0 && isFinite(r) ? r : 1;
    }

    // The desktop zoom level from before 2.31.8, once.
    var old = get(OLD_ZOOM_KEY);
    if (old !== null) {
        var level = parseFloat(old);
        if (get(KEY) === null && level >= 0.5 && level <= 2) set(KEY, String(nearest(level)));
        set(OLD_ZOOM_KEY, null);
    }
    apply(read());

    window.SlowbooksTextSize = {
        SIZES: SIZES,
        KEY: KEY,
        read: function () { return current; },
        apply: apply,
        step: step,
        nearest: nearest,
        ratio: ratio,
    };
}());
