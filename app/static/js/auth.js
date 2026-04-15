/**
 * Slowbooks Pro 2026 — Auth overlay (Phase 9.7)
 *
 * Injects a full-screen login/setup modal when the API returns 401.
 * Runs BEFORE app.js so we can block rendering until auth is settled.
 */
(function () {
    "use strict";

    const AUTH_STATUS_URL = "/api/auth/status";
    const AUTH_SETUP_URL = "/api/auth/setup";
    const AUTH_LOGIN_URL = "/api/auth/login";

    function buildOverlay(mode) {
        const title = mode === "setup" ? "Set your password" : "Unlock Slowbooks";
        const subtitle =
            mode === "setup"
                ? "First-time setup — pick a password (min 8 chars). You'll need it to sign in next time."
                : "Enter your password to continue.";
        const btnLabel = mode === "setup" ? "Set password & continue" : "Unlock";

        const root = document.createElement("div");
        root.id = "auth-overlay";
        root.setAttribute(
            "style",
            "position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.85);" +
                "display:flex;align-items:center;justify-content:center;" +
                "font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        );
        root.innerHTML =
            '<form id="auth-form" ' +
            'style="background:#fff;color:#111;padding:32px 28px;border-radius:8px;' +
            'min-width:340px;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,0.4);">' +
            '<h2 style="margin:0 0 8px;font-size:20px;">' +
            title +
            "</h2>" +
            '<p style="margin:0 0 20px;color:#555;font-size:13px;line-height:1.5;">' +
            subtitle +
            "</p>" +
            '<input id="auth-password" type="password" autocomplete="current-password" ' +
            'required minlength="' +
            (mode === "setup" ? 8 : 1) +
            '" ' +
            'style="width:100%;padding:10px 12px;font-size:15px;border:1px solid #ccc;' +
            'border-radius:4px;box-sizing:border-box;margin-bottom:12px;">' +
            '<button type="submit" id="auth-submit" ' +
            'style="width:100%;padding:11px;font-size:15px;font-weight:600;' +
            "background:#0066cc;color:#fff;border:0;border-radius:4px;cursor:pointer;\">" +
            btnLabel +
            "</button>" +
            '<div id="auth-error" style="color:#c00;font-size:13px;margin-top:10px;min-height:18px;"></div>' +
            "</form>";
        return root;
    }

    async function doAuth(mode, password) {
        const url = mode === "setup" ? AUTH_SETUP_URL : AUTH_LOGIN_URL;
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ password: password }),
        });
        if (!res.ok) {
            let detail = "Authentication failed";
            try {
                const body = await res.json();
                detail = body.detail || detail;
            } catch (e) {}
            throw new Error(detail);
        }
        return res.json();
    }

    function showOverlay(mode, onSuccess) {
        const existing = document.getElementById("auth-overlay");
        if (existing) existing.remove();
        const overlay = buildOverlay(mode);
        document.body.appendChild(overlay);
        const form = overlay.querySelector("#auth-form");
        const input = overlay.querySelector("#auth-password");
        const errBox = overlay.querySelector("#auth-error");
        const btn = overlay.querySelector("#auth-submit");
        input.focus();
        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.textContent = "";
            btn.disabled = true;
            btn.textContent = "...";
            try {
                await doAuth(mode, input.value);
                overlay.remove();
                if (onSuccess) onSuccess();
                else window.location.reload();
            } catch (err) {
                errBox.textContent = err.message;
                btn.disabled = false;
                btn.textContent =
                    mode === "setup" ? "Set password & continue" : "Unlock";
            }
        });
    }

    async function checkStatus() {
        try {
            const res = await fetch(AUTH_STATUS_URL, {
                credentials: "same-origin",
            });
            if (!res.ok) return { authenticated: false, setup_needed: false };
            return await res.json();
        } catch (e) {
            return { authenticated: false, setup_needed: false };
        }
    }

    // Expose globals so api.js can prompt on 401
    window.SlowbooksAuth = {
        promptLogin: function () {
            showOverlay("login");
        },
        promptSetup: function () {
            showOverlay("setup");
        },
    };

    // On page load, decide whether to block rendering with the overlay
    document.addEventListener("DOMContentLoaded", async function () {
        const status = await checkStatus();
        if (status.setup_needed) {
            showOverlay("setup");
        } else if (!status.authenticated) {
            showOverlay("login");
        }
    });
})();
