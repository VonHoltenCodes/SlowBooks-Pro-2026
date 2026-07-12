/**
 * Desktop (pywebview) shim — only active inside the native desktop window.
 *
 * Problem this solves: the app opens print previews, PDFs, CSV exports and
 * attachments with window.open()/target="_blank". In a browser install the
 * new tab shares the session cookie and everything works. In the desktop
 * shell, pywebview routes "new window" requests to the SYSTEM browser,
 * which has a separate cookie jar — so those URLs come back
 * {"detail":"Not authenticated"}.
 *
 * Fix: when running inside pywebview, same-origin popups are handed to
 * Python (window.pywebview.api.open_document), which opens a second
 * native pywebview window instead. That's a real top-level navigation, so
 * it isn't blocked by this app's frame-ancestors 'none' CSP (an <iframe>
 * overlay was tried first and hit exactly that wall — "This content is
 * blocked"), and every pywebview window in this process shares one
 * WebView2 cookie store, so the new window is already authenticated.
 * Genuinely external links (developer portals, state lookup sites) still
 * open in the real browser, unchanged.
 *
 * In a normal browser this file is a no-op.
 */
(function () {
    'use strict';

    // pywebview injects window.pywebview once the window is ready; by the
    // time a user can click anything it's present, so no readiness gating
    // is needed at call time -- only checked defensively below.
    function inDesktopShell() {
        return typeof window.pywebview !== 'undefined'
            || navigator.userAgent.includes('WebView2');
    }

    function isSameOrigin(url) {
        try {
            const u = new URL(url, window.location.href);
            return u.origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    function openInNativeWindow(url) {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_document) {
            window.pywebview.api.open_document(url);
        }
    }

    const realOpen = window.open.bind(window);
    window.open = function (url, target, features) {
        if (url && inDesktopShell() && isSameOrigin(url)) {
            openInNativeWindow(url);
            return null;
        }
        return realOpen(url, target, features);
    };

    // Same treatment for <a target="_blank"> anchors (attachment downloads,
    // employee documents, print-preview links). Plain download links
    // (no target="_blank") are left alone -- WebView2's native download
    // flow already handles those correctly in the same window.
    document.addEventListener('click', function (e) {
        if (!inDesktopShell()) return;
        const a = e.target && e.target.closest ? e.target.closest('a[target="_blank"]') : null;
        if (!a || !a.href || !isSameOrigin(a.href)) return;
        e.preventDefault();
        openInNativeWindow(a.href);
    }, true);
})();
