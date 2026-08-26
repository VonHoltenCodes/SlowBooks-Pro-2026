"""SlowBooks Pro 2026 — native desktop launcher.

Entry point for the no-Docker desktop install (Windows and macOS, but runs
anywhere Python does). What it does, in order:

  1. Prepares .env (copies .env.example on first run, generates a real
     PAYROLL_ENCRYPTION_SECRET, sets APP_DEBUG=true / FORCE_HTTPS=false /
     APP_HOST=127.0.0.1 — correct for a loopback-only desktop install).
  2. Shows a company picker (like QuickBooks' "File → Open Company"):
     each company is its own SQLite file under
     the platform's per-user application-data directory, tracked in
     companies.json. Pick one or create a new one.
  3. Points DATABASE_URL at the chosen company's .db file, runs
     `alembic upgrade head` (idempotent), starts uvicorn on 127.0.0.1,
     and opens the app in a native window (pywebview → WebView2).
  4. When the window closes, the server is shut down.

To switch companies: close the window and relaunch — the picker appears
again. Flags:
  --no-window   start the server and print the URL (no native window)
  --serve-lan   Server Edition mode: serve the LAN, no window (binds
                0.0.0.0, or --bind IP for one interface). Plain HTTP —
                trusted networks only for now.
  --setup-only  prepare .env and data directories, then exit
  --smoke-test  CI self-test: create a company, boot the server, render a
                PDF, exit 0/1
  --hidden      windowless mode -- redirects output to launcher.log and
                shows a popup instead of a console on fatal startup
                errors. Automatic in the installed (frozen) build.
  --port N      override the port (default: APP_PORT from .env, else 3001)

The installed Windows build is this same file frozen by PyInstaller: the
read-only app files live in the install dir, everything writable lives
under %LOCALAPPDATA%\\SlowBooksPro, and the server child is this exe
re-executed with the internal --_serve flag (no separate Python needed).
"""

import argparse
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# PyInstaller: the read-only application files (app/, migrations/,
# alembic.ini, .env.example) live in the bundle; everything writable
# (.env, companies, uploads, backups, logs) lives in the per-user data
# area. From source, both are the repo checkout — unchanged behavior.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def get_data_dir() -> Path:
    """Same resolution as app.services.company_service.data_dir(), duplicated
    here so --setup-only works before the app's dependencies are installed."""
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SlowBooksPro" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SlowBooksPro" / "data"
    return Path.home() / ".slowbookspro" / "data"


def _config_dir() -> Path:
    """Writable per-user config root (parent of the data dir when frozen)."""
    return get_data_dir().parent if FROZEN else Path(__file__).resolve().parent


def _purge_stale_webview_cache(storage_dir: Path, current_version: str) -> None:
    """Drop WebView2's HTTP disk cache on the first launch of a new version.

    The persistent profile (private_mode=False fix from v2.1.1) also
    persists the renderer's disk cache across app updates — field report:
    a 2.4.1 update kept rendering the previous version's cached
    index.html/JS. Cookies are deliberately left alone so logins survive;
    only the cache directories go.
    """
    import shutil

    marker = storage_dir / "app-version.txt"
    try:
        if (
            marker.exists()
            and marker.read_text(encoding="utf-8").strip() == current_version
        ):
            return
    except OSError:
        pass
    for pattern in ("**/Cache", "**/Code Cache", "**/GPUCache"):
        for path in storage_dir.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    try:
        marker.write_text(current_version, encoding="utf-8")
    except OSError:
        pass  # purge again next launch; never block startup on this


ENV_FILE = _config_dir() / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def _bootstrap_frozen_runtime() -> None:
    """Make the bundled Pango/GObject DLLs and the system fonts visible to
    WeasyPrint — must run before anything imports weasyprint (both in this
    process and, because it's module-level, in the --_serve child)."""
    gtk_dir = ROOT / "gtk"
    if gtk_dir.is_dir():
        os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", str(gtk_dir))
        try:
            os.add_dll_directory(str(gtk_dir))
        except OSError:
            pass

    # MSYS2-built Pango finds fonts through fontconfig, not Windows GDI —
    # point it at C:\Windows\Fonts via a generated fonts.conf.
    try:
        conf_dir = _config_dir()
        conf_dir.mkdir(parents=True, exist_ok=True)
        win_fonts = os.path.join(
            os.environ.get("WINDIR", r"C:\Windows"), "Fonts"
        ).replace("\\", "/")
        cache_dir = str(conf_dir / "fc-cache").replace("\\", "/")
        conf_path = conf_dir / "fonts.conf"
        conf_path.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
            "<fontconfig>\n"
            f"  <dir>{win_fonts}</dir>\n"
            f"  <cachedir>{cache_dir}</cachedir>\n"
            "</fontconfig>\n",
            encoding="utf-8",
        )
        os.environ["FONTCONFIG_FILE"] = str(conf_path)
    except OSError:
        pass  # PDF rendering may still work; don't block app startup


def _bootstrap_frozen_macos_runtime() -> None:
    """Point WeasyPrint at the bundled dylibs and system font directories."""
    frameworks_dir = Path(sys.executable).parent.parent / "Frameworks"
    fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    fallback_parts = [str(frameworks_dir)]
    if fallback:
        fallback_parts.append(fallback)
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(fallback_parts)

    # Homebrew's fontconfig file points back into /opt/homebrew. A signed app
    # must remain self-contained, so give the bundled library a tiny per-user
    # configuration that uses macOS fonts and a writable cache.
    try:
        from xml.sax.saxutils import escape

        config_dir = _config_dir()
        cache_dir = config_dir / "fontconfig-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        fonts_conf = config_dir / "fonts.conf"
        font_dirs = [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
        dirs_xml = "\n".join(f"  <dir>{escape(str(path))}</dir>" for path in font_dirs)
        contents = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
            "<fontconfig>\n"
            f"{dirs_xml}\n"
            f"  <cachedir>{escape(str(cache_dir))}</cachedir>\n"
            "</fontconfig>\n"
        )
        if (
            not fonts_conf.exists()
            or fonts_conf.read_text(encoding="utf-8") != contents
        ):
            fonts_conf.write_text(contents, encoding="utf-8")
        os.environ["FONTCONFIG_FILE"] = str(fonts_conf)
    except OSError:
        pass


if FROZEN:
    if sys.platform == "win32":
        _bootstrap_frozen_runtime()
    elif sys.platform == "darwin":
        _bootstrap_frozen_macos_runtime()

# Must match app/config.py's shipped placeholder — a real secret is
# generated to replace it (or an empty value) on first run.
_PLACEHOLDER_PAYROLL_KEY = "slowbooks-dev-payroll-key-change-me"


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------


def _read_env_lines() -> list[str]:
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def get_env_value(key: str) -> str | None:
    for line in _read_env_lines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped[len(key) + 1 :].strip().strip('"').strip("'")
    return None


def set_env_value(key: str, value: str) -> None:
    """Set key=value in .env, replacing an existing assignment in place."""
    lines = _read_env_lines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_env() -> None:
    """Idempotent first-run .env preparation."""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(
                ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:
            ENV_FILE.write_text("", encoding="utf-8")
        if os.name != "nt":
            ENV_FILE.chmod(0o600)
        print(f"Created {ENV_FILE}")
    elif os.name != "nt":
        try:
            ENV_FILE.chmod(0o600)
        except OSError:
            print(f"WARNING: could not restrict permissions on {ENV_FILE}")

    # Any app modules imported after this point must read the writable desktop
    # environment, never a bundled .env.
    os.environ["SLOWBOOKS_ENV_FILE"] = str(ENV_FILE)

    # APP_DEBUG=true is correct here because this deployment only ever talks
    # to 127.0.0.1: it disables the HTTPS/TLS production gates (which don't
    # apply to loopback traffic) and nothing else security-critical.
    set_env_value("APP_DEBUG", "true")
    set_env_value("FORCE_HTTPS", "false")
    # Loopback only — the desktop app is single-user, never a LAN server.
    set_env_value("APP_HOST", "127.0.0.1")

    # A real encryption secret is required regardless of database choice:
    # never leave payroll PII protected by the placeholder that ships in
    # the source tree.
    current = get_env_value("PAYROLL_ENCRYPTION_SECRET")
    if not current or current == _PLACEHOLDER_PAYROLL_KEY:
        set_env_value("PAYROLL_ENCRYPTION_SECRET", secrets.token_urlsafe(32))
        print("Generated PAYROLL_ENCRYPTION_SECRET")

    # Session-cookie signing key. Persisting it here (instead of letting
    # the app fall back to its .slowbooks-session.key file next to the
    # code) keeps the install dir read-only and sessions valid across
    # restarts.
    if not get_env_value("SESSION_SECRET_KEY"):
        set_env_value("SESSION_SECRET_KEY", secrets.token_urlsafe(48))
        print("Generated SESSION_SECRET_KEY")

    # Field-level settings encryption historically fell back to a key file
    # next to the source tree. In a frozen app that location is inside the
    # signed, read-only bundle, so persist the key in the per-user .env too.
    if not get_env_value("SETTINGS_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet

        set_env_value("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
        print("Generated SETTINGS_ENCRYPTION_KEY")

    if os.name != "nt":
        try:
            ENV_FILE.chmod(0o600)
        except OSError:
            print(f"WARNING: could not restrict permissions on {ENV_FILE}")

    data_dir = get_data_dir()
    (data_dir / "companies").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _server_env(db_url: str, port: int, bind_host: str = "127.0.0.1") -> dict:
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": db_url,
            "APP_DEBUG": "true",
            "FORCE_HTTPS": "false",
            "APP_HOST": bind_host,
            # Server Edition groundwork: anything beyond loopback flips the
            # flag the frontend uses to show the SERVER EDITION header.
            "SLOWBOOKS_SERVER_MODE": "0" if bind_host == "127.0.0.1" else "1",
            "APP_PORT": str(port),
            "SLOWBOOKS_DATA_DIR": str(get_data_dir()),
            # Where app/config.py loads .env from (the install dir is
            # read-only when frozen), and the flag the frontend uses to
            # enable desktop-only behavior like the update check.
            "SLOWBOOKS_ENV_FILE": str(ENV_FILE),
            "SLOWBOOKS_DESKTOP": "1",
        }
    )
    return env


def migrate(db_url: str, output=None) -> None:
    """Run `alembic upgrade head` against the chosen company database.

    Only meaningfully does work the first time a company file is opened
    (or after an app update ships new migrations); safe to run every time.

    `output`, when given an open file object, redirects alembic's own
    stdout/stderr there (used in --hidden mode, where there's no console
    to inherit and print to -- see launcher.log). None (the default)
    inherits the parent's console, unchanged from before.
    """
    if FROZEN:
        # No child interpreter to run `-m alembic` in — run it in-process.
        # migrations/env.py gives config.attributes["database_url"]
        # precedence, so the process-wide DATABASE_URL doesn't matter.
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        cfg.attributes["database_url"] = db_url
        os.environ["SLOWBOOKS_DATA_DIR"] = str(get_data_dir())
        # env.py imports app.database (import-time engine): make sure that
        # binds to SQLite — the bundle ships no Postgres driver.
        os.environ["DATABASE_URL"] = db_url
        command.upgrade(cfg, "head")
        return

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=_server_env(db_url, 0),
        check=True,
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
    )


def start_server(
    db_url: str, port: int, output=None, bind_host: str = "127.0.0.1"
) -> subprocess.Popen:
    if FROZEN:
        # Re-exec this same bundled exe; --_serve (handled at the top of
        # main()) turns the child into the uvicorn server. cwd must be
        # writable — the install dir is not.
        cmd = [sys.executable, "--_serve"]
        cwd = get_data_dir()
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            bind_host,
            "--port",
            str(port),
            # cmd.exe consoles don't render ANSI colors by default; without
            # this the logs are full of "<-[32m" escape-code garbage.
            "--no-use-colors",
        ]
        cwd = ROOT
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_server_env(db_url, port, bind_host),
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
    )


def wait_for_health(
    proc: subprocess.Popen,
    port: int,
    timeout: float = 120,
    host: str = "127.0.0.1",
) -> bool:
    url = f"http://{host}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # server process died
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _server_already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as resp:
            return resp.status == 200
    except OSError:
        return False


def launch_company(
    filename: str, port: int, output=None, bind_host: str = "127.0.0.1"
) -> subprocess.Popen:
    """Point the app at a company file, migrate it, and start the server."""
    from app.services import company_service

    # A second launch while the app is already open would lose the fight
    # for the port and fail confusingly -- say what's actually wrong.
    if _server_already_running(port):
        raise RuntimeError(
            "SlowBooks Pro is already running (another window is open). "
            "Close it first -- or end the SlowBooksPro process in Task "
            "Manager if no window is visible -- then try again."
        )

    db_path = company_service.company_db_path(filename)
    if db_path is None:
        raise ValueError(f"Invalid company file name: {filename!r}")

    db_url = "sqlite:///" + db_path.as_posix()
    set_env_value("DATABASE_URL", db_url)
    company_service.set_last_opened(filename)

    migrate(db_url, output=output)

    proc = start_server(db_url, port, output=output, bind_host=bind_host)
    # 0.0.0.0 includes loopback; a specific interface bind does not.
    health_host = "127.0.0.1" if bind_host in ("127.0.0.1", "0.0.0.0") else bind_host
    if not wait_for_health(proc, port, host=health_host):
        stop_server(proc)
        raise RuntimeError(
            f"Server did not become healthy on port {port}. "
            "Check for another app using the port, then try again."
        )
    return proc


# ---------------------------------------------------------------------------
# Company picker (pywebview window, later reused for the app itself)
# ---------------------------------------------------------------------------

PICKER_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>SlowBooks Pro 2026</title>
<style>
  body { font-family: "Segoe UI", system-ui, sans-serif; background: #f4f6f8;
         margin: 0; display: flex; justify-content: center; }
  .wrap { max-width: 460px; width: 100%; padding: 40px 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; color: #1a2b3c; }
  .sub { color: #667; font-size: 13px; margin-bottom: 24px; }
  .company { background: #fff; border: 1px solid #d8dee4; border-radius: 8px;
             padding: 14px 16px; margin-bottom: 10px; cursor: pointer;
             display: flex; justify-content: space-between; align-items: center; }
  .company:hover { border-color: #2f6fed; box-shadow: 0 1px 4px rgba(47,111,237,.15); }
  .company .name { font-weight: 600; color: #1a2b3c; }
  .company .file { font-size: 11px; color: #99a; }
  .open { color: #2f6fed; font-size: 12px; font-weight: 600; }
  .newco { margin-top: 20px; background: #fff; border: 1px dashed #b9c2cc;
           border-radius: 8px; padding: 16px; }
  .newco input { width: 100%; box-sizing: border-box; padding: 8px 10px;
                 border: 1px solid #c6ccd4; border-radius: 6px; font-size: 14px; }
  .newco button { margin-top: 10px; width: 100%; padding: 9px; border: 0;
                  border-radius: 6px; background: #2f6fed; color: #fff;
                  font-size: 14px; font-weight: 600; cursor: pointer; }
  .newco button:disabled { background: #a9bce0; cursor: default; }
  #status { margin-top: 16px; font-size: 13px; color: #556; min-height: 18px; }
  #status.error { color: #c0392b; }
  .empty { color: #778; font-size: 13px; margin-bottom: 8px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>SlowBooks Pro 2026</h1>
  <div class="sub">Choose a company to open, or create a new one.</div>
  <div id="list"></div>
  <div class="newco">
    <input id="newname" placeholder="New company name (e.g. Acme Consulting)">
    <button id="createbtn" onclick="createCompany()">+ Create New Company</button>
  </div>
  <div id="status"></div>
</div>
<script>
function setStatus(msg, isError) {
  const el = document.getElementById('status');
  el.textContent = msg || '';
  el.className = isError ? 'error' : '';
}
function setBusy(busy) {
  document.getElementById('createbtn').disabled = busy;
  document.querySelectorAll('.company').forEach(function (el) {
    el.style.pointerEvents = busy ? 'none' : 'auto';
    el.style.opacity = busy ? '0.6' : '1';
  });
}
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
async function refresh() {
  const info = await window.pywebview.api.list_companies();
  const list = document.getElementById('list');
  if (!info.companies.length) {
    list.innerHTML = '<div class="empty">No companies yet — create your first one below.</div>';
    return;
  }
  list.innerHTML = info.companies.map(function (c) {
    const last = c.file === info.last_opened ? ' <span class="open">last opened</span>' : '';
    return '<div class="company" data-file="' + esc(c.file) + '">' +
      '<div><div class="name">' + esc(c.name) + last + '</div>' +
      '<div class="file">' + esc(c.file) + '</div></div>' +
      '<div class="open">Open &rsaquo;</div></div>';
  }).join('');
  list.querySelectorAll('.company').forEach(function (el) {
    el.onclick = function () { openCompany(el.getAttribute('data-file')); };
  });
}
async function openCompany(file) {
  setBusy(true);
  setStatus('Opening company… first open can take a minute.');
  const result = await window.pywebview.api.open_company(file);
  if (result && result.success) {
    // Navigate from JS, only AFTER the call above has resolved -- doing
    // this from Python instead (mid-call) would navigate the window
    // away before pywebview can deliver the return value to this very
    // page, throwing 'window.pywebview._returnValuesCallbacks... is
    // not a function' in a background thread.
    window.location.href = result.url;
  } else {
    setStatus((result && result.error) || 'Could not open company.', true);
    setBusy(false);
  }
}
async function createCompany() {
  const name = document.getElementById('newname').value.trim();
  if (!name) { setStatus('Enter a company name first.', true); return; }
  setBusy(true);
  setStatus('Creating "' + name + '"… this takes a moment.');
  const result = await window.pywebview.api.create_company(name);
  if (!result.success) {
    setStatus(result.error || 'Could not create company.', true);
    setBusy(false);
    return;
  }
  setStatus('Created. Opening…');
  await openCompany(result.file);
}
window.addEventListener('pywebviewready', refresh);
</script>
</body>
</html>
"""


def _safe_temp_filename(title: str, suffix: str) -> str:
    """Derive a safe filename for a transient viewer file from a title
    string the caller does not fully control (a document's own title,
    e.g. an invoice number). No path separators, no traversal, ASCII
    letters/digits/dash/underscore only, bounded length."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title or "document").strip("-")
    if not slug:
        slug = "document"
    return slug[:80] + suffix


class PickerApi:
    """js_api bridge, available as window.pywebview.api on both the company
    picker AND the main app window (the same Window object is reused --
    the picker's own JS navigates it to the running app on success).

    IMPORTANT: all state on this object MUST be underscore-private.
    pywebview recursively serializes every PUBLIC attribute of the js_api
    object to expose it to JavaScript -- storing the pywebview Window here
    as `self.window` sent that walk into the native WinForms object graph,
    producing endless console spam ('AccessibilityObject.Bounds.Empty...
    maximum recursion depth exceeded', 'CoreWebView2 can only be accessed
    from the UI thread') on every page load. Underscore names are skipped
    by pywebview's get_functions(), so only the methods below are exposed.
    """

    def __init__(self, port: int, log_fh=None):
        self._port = port
        self._server: subprocess.Popen | None = None
        self._log_fh = log_fh

    def open_document_html(self, title: str, html: str) -> dict:
        """Show already-fetched, already-authenticated HTML (an invoice/
        estimate print-preview page) in a new native window.

        Why the caller fetches the content itself rather than handing over
        a URL for a fresh window to load: a fresh pywebview window is a
        SEPARATE top-level browsing context, and field testing showed it
        does NOT reliably carry the main window's session cookie (still
        got {"detail":"Not authenticated"} even though windows in this
        process nominally share one WebView2 profile). desktop_shim.js
        instead fetches the URL from the already-authenticated page's own
        JavaScript -- exactly like the app's normal API calls, which is
        why those always work -- and hands the finished content over here
        to display. No further authenticated network request is needed.

        An <iframe> overlay was tried before this and rejected outright:
        the app sends Content-Security-Policy: frame-ancestors 'none'
        (deliberate anti-clickjacking), so framing renders "This content
        is blocked" even same-origin, even from the app itself. A new
        top-level window sidesteps that; it isn't a frame.
        """
        try:
            import webview

            webview.create_window(title or "SlowBooks Pro 2026", html=html)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def open_document_pdf(self, title: str, base64_data: str) -> dict:
        """Show an already-fetched PDF (base64-encoded by the caller) in a
        new native window, via a local temp file. Chromium's built-in PDF
        viewer renders file:// URLs with its own print/save/zoom controls,
        and a local file needs no authentication at all -- sidestepping
        the same cross-window-cookie problem open_document_html's
        docstring describes.
        """
        import base64
        import tempfile

        try:
            import webview

            data = base64.b64decode(base64_data)
            temp_dir = Path(tempfile.gettempdir()) / "SlowBooksProDocs"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = temp_dir / _safe_temp_filename(title, ".pdf")
            temp_path.write_bytes(data)
            webview.create_window(title or "SlowBooks Pro 2026", temp_path.as_uri())
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def save_backup_file(self, filename: str) -> dict:
        """Copy a backup file straight from disk into the user's Downloads
        folder -- no HTTP request at all.

        Field test: fetching /api/backups/download/<file> from this page's
        own JS (the same pattern open_document_pdf/_html use) came back
        "Failed to fetch" even though the server logged a 200 for the exact
        same request. Root cause: that endpoint serves
        media_type="application/octet-stream" with Content-Disposition:
        attachment, and WebView2 (with ALLOW_DOWNLOADS on, needed elsewhere
        for the Save-As dialog on this same window) intercepts that at the
        network layer as a native download -- even when the request was
        made via fetch() from a page's own script, not a real click or
        navigation. The response never reaches the page's fetch() promise.
        A CSV export sidesteps this by switching to Content-Disposition:
        inline (browser-renderable text, so it's no longer download-
        flagged); an octet-stream binary has no such option. Since the
        desktop app and the backup file are on the same machine, this
        skips HTTP for the backup case entirely.
        """
        try:
            import shutil

            from app.services.backup_service import BACKUP_DIR, _safe_backup_filename

            safe_name = _safe_backup_filename(filename)
            if safe_name is None:
                return {"success": False, "error": "Invalid backup filename"}
            src = (BACKUP_DIR / safe_name).resolve()
            if not src.is_relative_to(BACKUP_DIR.resolve()) or not src.exists():
                return {"success": False, "error": "Backup file not found"}

            downloads = Path.home() / "Downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            dest = downloads / src.name
            stem, suffix = src.stem, src.suffix
            n = 1
            while dest.exists():
                dest = downloads / f"{stem} ({n}){suffix}"
                n += 1
            shutil.copy2(src, dest)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "path": str(dest)}

    def list_companies(self) -> dict:
        from app.services import company_service

        return {
            "companies": company_service.manifest_list_companies(),
            "last_opened": company_service.get_last_opened(),
        }

    def create_company(self, name: str) -> dict:
        from app.services import company_service

        try:
            return company_service.manifest_create_company(name)
        except Exception as exc:  # surfaced in the picker, not a traceback
            return {"success": False, "error": str(exc)}

    def open_company(self, filename: str) -> dict:
        """Launch the company's server and hand the URL back to the picker
        page's own JS to navigate to, once this call has resolved.

        Previously this method navigated the window itself
        (self._window.load_url(...)) before returning. That's a race:
        pywebview delivers a JS promise's return value by finding the
        call still pending on the (now-navigated-away) page, so on a
        fast launch the picker page was already gone by the time
        pywebview tried to resolve it -- 'window.pywebview.
        _returnValuesCallbacks.open_company... is not a function' in a
        background thread. Returning the url and letting the caller
        navigate AFTER the call resolves avoids the race entirely.
        """
        try:
            self._server = launch_company(filename, self._port, output=self._log_fh)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "url": f"http://127.0.0.1:{self._port}"}


def _webview2_installed() -> bool:
    """Is the Microsoft WebView2 runtime present? (Windows only.)

    Same detection Microsoft documents (and pywebview itself uses): the
    Evergreen runtime registers a 'pv' version under an EdgeUpdate key.
    This must be checked HERE, before opening the window: pywebview does
    NOT fail when WebView2 is missing -- even with gui='edgechromium'
    forced it silently falls back to the legacy IE/MSHTML control, where
    neither the app's JavaScript nor the js_api bridge works (endless
    "SyncRoot ... maximum recursion depth exceeded" spam, dead buttons).
    """
    if sys.platform != "win32":
        return True
    import winreg

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    key_paths = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}",
        ),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for root, path in key_paths:
        try:
            with winreg.OpenKey(root, path) as key:
                pv, _ = winreg.QueryValueEx(key, "pv")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _show_error_box(message: str) -> None:
    """Best-effort native popup for fatal errors -- used in --hidden mode,
    where there's no visible console to print to. No-op on unsupported
    platforms, or if it fails for any reason."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    "on run argv",
                    "-e",
                    'display alert "SlowBooks Pro 2026" message (item 1 of argv) as critical',
                    "-e",
                    "end run",
                    "--",
                    message,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return
        if sys.platform == "win32":
            import ctypes

            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(
                0, message, "SlowBooks Pro 2026", MB_ICONERROR
            )
    except Exception:
        pass


def _compose_serve_banner(port: int, addresses: list[str]) -> str:
    """The 'your team connects at ...' text — shared by the console print,
    the connect-urls.txt drop, and the frozen build's popup."""
    lines = ["SlowBooks Pro — SERVER EDITION mode is running.", ""]
    lines.append("Your team connects at:")
    for addr in addresses or ["<this machine's IP>"]:
        lines.append(f"    http://{addr}:{port}")
    lines += [
        "",
        "Traffic is plain HTTP — trusted networks only.",
        "This server stops when this process is closed.",
    ]
    return "\n".join(lines)


def _announce_serve_lan(port: int) -> None:
    """Make the connect URLs impossible to miss.

    The frozen exe has no console (field report: 'I ran the command and
    nothing happened'), so print alone is useless there. Always write
    connect-urls.txt next to the data, and on frozen Windows also raise a
    non-blocking popup from a daemon thread (the server keeps serving
    behind it)."""
    banner = _compose_serve_banner(port, _lan_addresses())
    print(banner)
    try:
        (get_data_dir() / "connect-urls.txt").write_text(banner, encoding="utf-8")
    except OSError:
        pass
    if FROZEN and sys.platform == "win32":
        import threading

        def _popup():
            try:
                import ctypes

                MB_ICONINFORMATION = 0x40
                ctypes.windll.user32.MessageBoxW(
                    0, banner, "SlowBooks Pro — Server Edition", MB_ICONINFORMATION
                )
            except Exception:
                pass

        threading.Thread(target=_popup, daemon=True).start()


def run_window(port: int, log_fh=None) -> int:
    try:
        import webview
    except ImportError:
        msg = (
            "pywebview is not installed. Install it with:\n"
            "    pip install -r requirements-desktop.txt\n"
            "or start without a native window:\n"
            "    python desktop_launcher.py --no-window"
        )
        print(msg)
        _show_error_box(msg)
        return 1

    if not _webview2_installed():
        msg = (
            "The Microsoft WebView2 runtime is not installed, so the app\n"
            "window cannot open properly.\n"
            "Install it from:\n"
            "    https://developer.microsoft.com/microsoft-edge/webview2/\n"
            "You can also start without a native window:\n"
            "    python desktop_launcher.py --no-window"
        )
        print(msg)
        _show_error_box(msg)
        return 1

    # pywebview CANCELS downloads by default -- without this, saving a PDF,
    # a CSV export, or a backup from inside the app silently does nothing.
    # With it, WebView2 shows a normal "Save As" dialog.
    webview.settings["ALLOW_DOWNLOADS"] = True

    # pywebview defaults to private_mode=True, which partitions cookie
    # storage: the session cookie never reached the second native window
    # (print preview / PDF -> "Not authenticated") or WebView2's download
    # requests (CSV export -> "Needs authorization"). A persistent shared
    # profile under our data dir fixes both, and logins now survive app
    # restarts as a bonus.
    storage_dir = get_data_dir() / "webview"
    storage_dir.mkdir(parents=True, exist_ok=True)
    from app import __version__ as app_version

    _purge_stale_webview_cache(storage_dir, app_version)

    api = PickerApi(port, log_fh)
    webview.create_window(
        "SlowBooks Pro 2026",
        html=PICKER_HTML,
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    try:
        # Require the WebView2 (Chromium) renderer on Windows. Without this,
        # pywebview silently falls back to the legacy IE/MSHTML control on
        # machines missing the WebView2 runtime -- where neither the app's
        # JavaScript nor pywebview's own js_api bridge works (field-observed
        # as endless "SyncRoot ... maximum recursion depth exceeded" spam and
        # picker buttons that do nothing). Better to fail with instructions.
        gui = "edgechromium" if sys.platform == "win32" else None
        # Blocks until the window is closed. private_mode/storage_path:
        # see the comment above where storage_dir is created.
        webview.start(gui=gui, private_mode=False, storage_path=str(storage_dir))
    except Exception as exc:
        if sys.platform == "win32":
            guidance = (
                "This usually means the Microsoft WebView2 runtime is missing.\n"
                "Install it from:\n"
                "    https://developer.microsoft.com/microsoft-edge/webview2/"
            )
        elif sys.platform == "darwin":
            guidance = (
                "The macOS Cocoa/WebKit window could not start. Review the "
                "launcher log for the packaged native dependency error."
            )
        else:
            guidance = "The native webview backend could not start."
        msg = (
            f"Could not open the native window: {exc}\n"
            f"{guidance}\n"
            "You can also start without a native window:\n"
            "    python desktop_launcher.py --no-window"
        )
        print(msg)
        _show_error_box(msg)
        return 1
    finally:
        stop_server(api._server)
    return 0


# ---------------------------------------------------------------------------
# Headless mode (--no-window): for scripting and debugging
# ---------------------------------------------------------------------------


def _lan_addresses() -> list[str]:
    """Best-effort list of this machine's reachable names/IPs for the
    'your team connects at ...' banner. Never raises."""
    import socket

    seen: list[str] = []
    try:
        hostname = socket.gethostname()
        if hostname:
            seen.append(hostname)
    except OSError:
        pass
    try:
        # UDP "connect" to a public address picks the primary interface
        # without sending a packet.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and ip not in seen:
                seen.append(ip)
        finally:
            s.close()
    except OSError:
        pass
    return seen


def run_headless(port: int, bind_host: str = "127.0.0.1") -> int:
    from app.services import company_service

    filename = company_service.get_last_opened()
    if filename is None:
        companies = company_service.manifest_list_companies()
        if companies:
            filename = companies[0]["file"]
        else:
            print("No companies yet -- creating 'My Company'.")
            result = company_service.manifest_create_company("My Company")
            if not result["success"]:
                print(f"ERROR: {result['error']}")
                return 1
            filename = result["file"]

    print(f"Opening company file: {filename}")
    try:
        proc = launch_company(filename, port, bind_host=bind_host)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1

    if bind_host == "127.0.0.1":
        print(f"SlowBooks Pro is running at http://127.0.0.1:{port}")
    else:
        _announce_serve_lan(port)
    print("Press Ctrl+C to stop.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server(proc)
    return 0


def _watch_parent(parent_pid: int, poll_seconds: float = 2.0) -> None:
    """Daemon thread: exit this server process when its launcher dies.

    Issue #52: on macOS, Command-Q terminates the parent app process
    without unwinding Python cleanup, orphaning the --_serve child on
    the port (invisible server, "already running" on relaunch). The
    child watching its parent fixes every abnormal-parent-death path —
    including launcher crashes — with one mechanism.

    Server Edition is preserved BY DESIGN, not by accident: in
    --serve-lan / scheduled-task mode the launcher parent stays alive
    holding proc.wait(), so the watcher never fires and the server runs
    perpetually. POSIX only (macOS + Linux); on Windows every quit path
    goes through the window-close cleanup that already works.

    (Deliberate "keep serving after the window closes" is a designed
    v2.6 feature — tray indicator + relaunch adoption — not an accident
    of orphaning.)
    """
    import threading
    import time as _t

    def _poll():
        while True:
            _t.sleep(poll_seconds)
            try:
                os.kill(parent_pid, 0)  # signal 0 = existence check
            except OSError:
                os._exit(0)  # parent gone: take the port down with us
            if os.getppid() != parent_pid:
                os._exit(0)  # reparented (launchd/init adopted us)

    threading.Thread(target=_poll, daemon=True, name="parent-watch").start()


def _serve() -> int:
    """Internal: run the uvicorn server in this process. The frozen build
    has no child interpreter for `-m uvicorn`, so start_server() re-execs
    the bundled exe with --_serve instead; all configuration (DATABASE_URL,
    APP_PORT, SLOWBOOKS_*) arrives via the environment from _server_env()."""
    import uvicorn

    if sys.platform != "win32":
        _watch_parent(os.getppid())

    # Import the app OURSELVES rather than passing "app.main:app": when the
    # import fails, uvicorn's string loader reports only "Could not import
    # module" and hides the actual traceback.
    try:
        import app.main
    except BaseException:
        import traceback

        traceback.print_exc(file=sys.stderr)
        raise

    port = int(os.environ.get("APP_PORT", "3001"))
    host = os.environ.get("APP_HOST", "127.0.0.1")
    uvicorn.run(app.main.app, host=host, port=port, use_colors=False)
    return 0


def run_smoke_test(port: int = 3999) -> int:
    """Headless self-test for CI: prove the frozen bundle can prepare an
    environment, create + migrate a company database, serve the app, and —
    the riskiest part on Windows — render a PDF through WeasyPrint's
    bundled Pango/GObject DLLs. Exit code is the verdict."""
    import logging

    # Service-layer failures are logger.exception'd and swallowed into
    # generic user-facing errors; in a smoke test we want the traceback.
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)

    if sys.platform == "darwin":
        print("smoke: importing the Cocoa webview backend...")
        import webview.platforms.cocoa  # noqa: F401

    prepare_env()
    os.environ["SLOWBOOKS_DATA_DIR"] = str(get_data_dir())
    from app.services import company_service

    print("smoke: creating company...")
    result = company_service.manifest_create_company("Smoke Test Co")
    if result["success"]:
        filename = result["file"]
    else:
        existing = company_service.manifest_list_companies()
        if not existing:
            print(f"smoke: FAIL create_company: {result.get('error')}")
            return 1
        filename = existing[0]["file"]

    print("smoke: launching server...")
    # Pipe the server child's output into our own (the log file when
    # frozen) — a crashing uvicorn child is otherwise completely silent.
    child_out = sys.stdout if hasattr(sys.stdout, "fileno") else None
    proc = launch_company(filename, port, output=child_out)
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=5
        ) as resp:
            if resp.status != 200:
                print(f"smoke: FAIL /health returned {resp.status}")
                return 1
    finally:
        stop_server(proc)

    print("smoke: rendering a PDF via WeasyPrint...")
    import weasyprint

    pdf = weasyprint.HTML(
        string="<h1>SlowBooks Pro</h1><p>PDF rendering works.</p>"
    ).write_pdf()
    if not pdf or not pdf.startswith(b"%PDF"):
        print("smoke: FAIL WeasyPrint did not produce a PDF")
        return 1

    print("smoke: PASS")
    return 0


def main() -> int:
    # Make every stdio write total BEFORE argparse can print anything.
    # A frozen console=False build launched with redirected stdio (any
    # pipe: `SlowBooksPro.exe --help | ...`, CI, an agent) gets whatever
    # encoding the handle reports — cp1252 on US Windows — and this
    # module docstring, which argparse prints for --help, contains
    # characters cp1252 cannot encode. print_help() then raised
    # UnicodeEncodeError; unhandled in a windowed build, the bootloader
    # parked the process on an error dialog nobody can see. Field
    # report: one such process survived ~7 hours. With errors="replace"
    # the worst case is a "?" instead of an arrow.
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None:
            try:
                _stream.reconfigure(errors="replace")
            except (AttributeError, OSError):
                pass  # not a TextIOWrapper (test harness, log redirect)

    # Not a user flag — the frozen server child (see start_server) and
    # argparse must never meet, so handle it before parsing.
    if "--_serve" in sys.argv:
        return _serve()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="prepare .env and data directories, then exit",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="CI self-test: create a company, boot the server, render a "
        "PDF, exit 0/1. Uses SLOWBOOKS_DATA_DIR for isolation.",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="start the server and print the URL instead of opening a window",
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help=(
            "windowless mode -- redirects all output to launcher.log and "
            "shows a popup instead of a console on fatal startup errors, "
            "since there's no visible console to print to. For live "
            "console output while troubleshooting, run from a terminal "
            "without this flag."
        ),
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--serve-lan",
        action="store_true",
        help="Server Edition mode: serve to the local network (no window). "
        "Binds 0.0.0.0 unless --bind is given.",
    )
    parser.add_argument(
        "--bind",
        default=None,
        metavar="IP",
        help="with --serve-lan: bind a specific interface instead of all",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        metavar="PATH",
        help="override the data directory (sets SLOWBOOKS_DATA_DIR). Used "
        "by the Server Edition scheduled task so books live in a "
        "machine-wide location instead of one user's profile.",
    )
    args = parser.parse_args()
    if args.data_dir:
        os.environ["SLOWBOOKS_DATA_DIR"] = str(Path(args.data_dir).resolve())

    # A frozen console=False exe has no console at all — always behave as
    # --hidden there so output lands in launcher.log instead of vanishing.
    hidden = args.hidden or FROZEN

    log_fh = None
    log_path = None
    if hidden:
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path = data_dir / "launcher.log"
        try:
            log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
            log_fh.write(
                f"\n==== {datetime.now().isoformat(timespec='seconds')} ====\n"
            )
            sys.stdout = log_fh
            sys.stderr = log_fh
        except OSError:
            log_fh = None  # best effort; proceed without file logging

    try:
        prepare_env()
        # Pin the data dir for this process and every child (uvicorn,
        # alembic), so the app's company_service resolves the same
        # location.
        os.environ["SLOWBOOKS_DATA_DIR"] = str(get_data_dir())
        os.environ.setdefault("SLOWBOOKS_ENV_FILE", str(ENV_FILE))
        if FROZEN:
            # app.config bakes DATABASE_URL into a constant at import time
            # and app.database builds its engine from it. This launcher
            # process never uses that engine, but the modules DO get
            # imported here (company picker, in-process alembic), and the
            # Postgres fallback would import psycopg2 — which the desktop
            # bundle deliberately doesn't ship. Bind it to a scratch
            # SQLite URL BEFORE the first app import; the server child
            # gets the real company URL via _server_env().
            os.environ.setdefault(
                "DATABASE_URL",
                "sqlite:///" + (get_data_dir() / "launcher-scratch.db").as_posix(),
            )

        if args.setup_only:
            print("Setup complete.")
            return 0

        if args.smoke_test:
            # Never fall through to the generic handler below: its
            # MessageBox would block a headless CI runner forever.
            try:
                return run_smoke_test()
            except Exception:
                import traceback

                traceback.print_exc(file=sys.stdout)
                print("smoke: FAIL (unhandled exception above)")
                return 1

        port = args.port or int(get_env_value("APP_PORT") or "3001")

        if args.serve_lan:
            return run_headless(port, bind_host=args.bind or "0.0.0.0")
        if args.no_window:
            return run_headless(port)
        return run_window(port, log_fh)
    except Exception:
        if not hidden:
            raise  # unchanged behavior: let the traceback print normally
        import traceback

        tb = traceback.format_exc()
        if log_fh:
            log_fh.write(tb + "\n")
        _show_error_box(
            "SlowBooks Pro hit an unexpected error and could not start.\n\n"
            f"Details were written to:\n{log_path}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
