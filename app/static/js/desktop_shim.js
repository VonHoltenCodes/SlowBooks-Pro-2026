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
 * Fix: when running inside pywebview, same-origin popups are rendered in an
 * in-app overlay <iframe> instead (same window = same session; Chromium's
 * built-in viewer renders inline PDFs, and print-preview pages can call
 * window.print() as designed). Genuinely external links (developer portals,
 * state lookup sites) still open in the real browser, unchanged.
 *
 * In a normal browser this file is a no-op.
 */
(function () {
    'use strict';

    // pywebview injects window.pywebview; it may appear slightly after our
    // scripts run, so also allow an explicit opt-in via the user agent the
    // shim can't miss: WebView2 always contains "WebView2" in the UA... but
    // keep it simple and robust: check pywebview now AND at first use.
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

    let overlay = null;

    function closeOverlay() {
        if (overlay) {
            overlay.remove();
            overlay = null;
        }
    }

    function openInOverlay(url) {
        closeOverlay();
        overlay = document.createElement('div');
        overlay.setAttribute('style',
            'position:fixed;inset:0;z-index:99999;background:rgba(20,26,34,.72);' +
            'display:flex;flex-direction:column;');

        const bar = document.createElement('div');
        bar.setAttribute('style',
            'display:flex;justify-content:flex-end;gap:8px;padding:10px 14px;');
        const mkBtn = function (label) {
            const b = document.createElement('button');
            b.textContent = label;
            b.setAttribute('style',
                'padding:7px 16px;border:0;border-radius:6px;font-size:13px;' +
                'font-weight:600;cursor:pointer;background:#fff;color:#1a2b3c;');
            return b;
        };
        const printBtn = mkBtn('Print');
        const closeBtn = mkBtn('Close');

        const frame = document.createElement('iframe');
        frame.src = url;
        frame.setAttribute('style',
            'flex:1;border:0;margin:0 14px 14px;border-radius:8px;background:#fff;');

        printBtn.onclick = function () {
            try {
                frame.contentWindow.focus();
                frame.contentWindow.print();
            } catch (e) { /* cross-state hiccup; the page may self-print */ }
        };
        closeBtn.onclick = closeOverlay;
        overlay.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeOverlay();
        });

        bar.appendChild(printBtn);
        bar.appendChild(closeBtn);
        overlay.appendChild(bar);
        overlay.appendChild(frame);
        document.body.appendChild(overlay);
    }

    const realOpen = window.open.bind(window);
    window.open = function (url, target, features) {
        if (url && inDesktopShell() && isSameOrigin(url)) {
            openInOverlay(url);
            return null;
        }
        return realOpen(url, target, features);
    };

    // Same treatment for <a target="_blank"> anchors (attachment downloads,
    // employee documents): same-origin ones go through the overlay, where a
    // Content-Disposition: attachment response triggers WebView2's download
    // flow instead of a stranded, unauthenticated browser tab.
    document.addEventListener('click', function (e) {
        if (!inDesktopShell()) return;
        const a = e.target && e.target.closest ? e.target.closest('a[target="_blank"]') : null;
        if (!a || !a.href || !isSameOrigin(a.href)) return;
        e.preventDefault();
        openInOverlay(a.href);
    }, true);
})();
