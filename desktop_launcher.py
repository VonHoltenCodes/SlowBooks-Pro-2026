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
  --mcp         run the local MCP server over stdio against the running app
                (see docs/agents.md)
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
from html import escape as _html_escape
from pathlib import Path
from string import Template

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


ENV_EXAMPLE = ROOT / ".env.example"


def env_file() -> Path:
    """Writable per-user .env. Resolved on every call (not at import) so
    --data-dir / SLOWBOOKS_DATA_DIR, which are applied after this module
    loads, move the .env along with the rest of the data directory."""
    return _config_dir() / ".env"


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
    if not env_file().exists():
        return []
    return env_file().read_text(encoding="utf-8").splitlines()


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
    env_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_env() -> None:
    """Idempotent first-run .env preparation."""
    env_file().parent.mkdir(parents=True, exist_ok=True)
    if not env_file().exists():
        if ENV_EXAMPLE.exists():
            env_file().write_text(
                ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:
            env_file().write_text("", encoding="utf-8")
        if os.name != "nt":
            env_file().chmod(0o600)
        print(f"Created {env_file()}")
    elif os.name != "nt":
        try:
            env_file().chmod(0o600)
        except OSError:
            print(f"WARNING: could not restrict permissions on {env_file()}")

    # Any app modules imported after this point must read the writable desktop
    # environment, never a bundled .env.
    os.environ["SLOWBOOKS_ENV_FILE"] = str(env_file())

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
            env_file().chmod(0o600)
        except OSError:
            print(f"WARNING: could not restrict permissions on {env_file()}")

    data_dir = get_data_dir()
    (data_dir / "companies").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _server_env(
    db_url: str, port: int, bind_host: str = "127.0.0.1", key: bytes | None = None
) -> dict:
    env = dict(os.environ)
    # The company file's key (#290), when the file is encrypted: the server
    # child and a migration child take it from here, so neither opens the
    # file without it. A value inherited from the shell never leaks through.
    env.pop("SLOWBOOKS_COMPANY_FILE_KEY", None)
    if key is not None:
        env["SLOWBOOKS_COMPANY_FILE_KEY"] = key.hex()
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
            "SLOWBOOKS_ENV_FILE": str(env_file()),
            "SLOWBOOKS_DESKTOP": "1",
        }
    )
    return env


def migrate(db_url: str, output=None, key: bytes | None = None) -> None:
    """Run `alembic upgrade head` against the chosen company database.

    ``key`` is the company file's key when it is encrypted (#290): in hand
    before the migration opens the file, handed over in-process through
    the alembic config or to a child through its environment.

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
        cfg.attributes["company_file_key"] = key
        os.environ["SLOWBOOKS_DATA_DIR"] = str(get_data_dir())
        # env.py imports app.database (import-time engine): make sure that
        # binds to SQLite — the bundle ships no Postgres driver.
        os.environ["DATABASE_URL"] = db_url
        command.upgrade(cfg, "head")
        return

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=_server_env(db_url, 0, key=key),
        check=True,
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
    )


def start_server(
    db_url: str,
    port: int,
    output=None,
    bind_host: str = "127.0.0.1",
    key: bytes | None = None,
) -> subprocess.Popen:
    if FROZEN:
        # Re-exec this same bundled exe; --_serve (handled at the top of
        # main()) turns the child into the uvicorn server. cwd must be
        # writable — the install dir is not.
        cmd = [sys.executable, "--_serve"]
        cwd = get_data_dir()
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        # The same --_serve child as the frozen build, so a source run gets
        # the parent watcher too (#52). A bare `-m uvicorn` child had none:
        # Command-Q left it on the port, and the next launch was refused as
        # "already running". Host and port reach it through _server_env().
        cmd = [sys.executable, str(Path(__file__).resolve()), "--_serve"]
        cwd = ROOT
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_server_env(db_url, port, bind_host, key=key),
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


class LockedCompany(RuntimeError):
    """An encrypted company file whose key is in its keyring (#290): the
    picker unlocks it with an administrator's sign-in or the recovery key.
    ``str(exc)`` is the sentence to show."""


def launch_company(
    filename: str,
    port: int,
    output=None,
    bind_host: str = "127.0.0.1",
    persist: bool = True,
    key: bytes | None = None,
) -> subprocess.Popen:
    """Point the app at a company file, migrate it, and start the server.

    ``key`` is the company file's key when the picker unlocked it (#290:
    an administrator's sign-in or the recovery key); otherwise the key is
    resolved from the environment as before.

    ``persist`` records the choice as the desktop app's state (.env
    DATABASE_URL + last-opened) — right for the windowed picker, wrong for
    a headless --serve-lan / --no-window run, which would otherwise repoint
    the next windowed launch (issue #110). The server itself takes its
    DATABASE_URL from the environment start_server() builds, so nothing
    needs the file."""
    from app.services import company_file, company_service

    db_path = company_service.company_db_path(filename)
    if db_path is None:
        raise ValueError(f"Invalid company file name: {filename!r}")

    # The key is in hand before anything opens the file (#290): before the
    # already-running probe, before the migrations, before the server
    # child, which takes it from the environment start_server() builds. An
    # encrypted file with no key stops here, with one sentence.
    if key is None:
        key = company_file.resolve_key()
    company_file.sweep_stale_conversions(db_path)
    if company_file.is_encrypted_file(db_path):
        if key is None:
            from app.services import company_keyring

            if company_keyring.exists(db_path):
                # the desktop's own key: the picker asks for a sign-in
                raise LockedCompany(company_keyring.LOCKED_MESSAGE)
            raise RuntimeError(company_file.NO_KEY_MESSAGE)
        # A wrong key is refused before the migration opens the file
        # writable, so it writes nothing (NEW-109).
        try:
            company_file.verify_key(db_path, key)
        except company_file.CompanyFileLocked as exc:
            raise RuntimeError(str(exc)) from exc

    # A second launch while the app is already open would lose the fight
    # for the port and fail confusingly -- say what's actually wrong.
    if _server_already_running(port):
        raise RuntimeError(
            "SlowBooks Pro is already running (another window is open). "
            "Close it first -- or end the SlowBooksPro process in Task "
            "Manager if no window is visible -- then try again."
        )

    db_url = "sqlite:///" + db_path.as_posix()
    if persist:
        set_env_value("DATABASE_URL", db_url)
        company_service.set_last_opened(filename)

    migrate(db_url, output=output, key=key)

    proc = start_server(db_url, port, output=output, bind_host=bind_host, key=key)
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
  #unlock { margin-top: 16px; background: #fff; border: 1px solid #d8dee4; border-radius: 8px; padding: 16px; }
  #unlock h2 { font-size: 15px; margin: 0 0 4px; color: #1a2b3c; }
  #unlock p { font-size: 12px; color: #556; margin: 0 0 10px; line-height: 1.5; }
  #unlock label { display: block; font-size: 12px; color: #334; margin: 8px 0 3px; }
  #unlock input { width: 100%; box-sizing: border-box; padding: 8px 10px;
                  border: 1px solid #c6ccd4; border-radius: 6px; font-size: 14px; }
  #unlock .row { display: flex; gap: 8px; margin-top: 12px; }
  #unlock button { flex: 1; padding: 9px; border: 0; border-radius: 6px; background: #2f6fed;
                   color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; }
  #unlock button.alt { background: #e8ecf1; color: #1a2b3c; }
  #unlock button:disabled { background: #a9bce0; cursor: default; }
  #unlock .switch { font-size: 12px; color: #2f6fed; cursor: pointer; background: none; border: 0;
                    padding: 0; margin-top: 10px; text-decoration: underline; flex: none; font-weight: 400; }
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
  <div id="unlock" hidden>
    <h2 id="unlock-title">Unlock the company file</h2>
    <p>This company file is encrypted. Sign in as an administrator to open it, or enter its recovery key.</p>
    <div id="unlock-signin">
      <label for="unlock-user">Username</label>
      <input id="unlock-user" autocomplete="username" list="unlock-admins">
      <datalist id="unlock-admins"></datalist>
      <label for="unlock-pass">Password</label>
      <input id="unlock-pass" type="password" autocomplete="current-password">
    </div>
    <div id="unlock-recovery" hidden>
      <label for="unlock-key">Recovery key</label>
      <input id="unlock-key" autocomplete="off" placeholder="ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567" spellcheck="false">
    </div>
    <div class="row">
      <button id="unlock-btn" onclick="unlock()">Unlock and open</button>
      <button class="alt" onclick="hideUnlock()">Cancel</button>
    </div>
    <button class="switch" id="unlock-switch" onclick="switchUnlock()">Use the recovery key instead</button>
  </div>
  <div id="status"></div>
</div>
<script>
var unlocking = null;  // the file waiting for its key
function showUnlock(file, name, admins) {
  unlocking = file;
  document.getElementById('unlock-title').textContent = 'Unlock ' + (name || 'the company file');
  document.getElementById('unlock-admins').innerHTML = (admins || []).map(function (a) { return '<option value="' + esc(a) + '">'; }).join('');
  if ((admins || []).length === 1) document.getElementById('unlock-user').value = admins[0];
  document.getElementById('unlock-pass').value = '';
  document.getElementById('unlock-key').value = '';
  document.getElementById('unlock').hidden = false;
  setStatus('');
  (document.getElementById('unlock-user').value ? document.getElementById('unlock-pass') : document.getElementById('unlock-user')).focus();
}
function hideUnlock() {
  unlocking = null;
  document.getElementById('unlock').hidden = true;
  setBusy(false);
}
function switchUnlock() {
  var signin = document.getElementById('unlock-signin'), recovery = document.getElementById('unlock-recovery');
  var toRecovery = recovery.hidden;
  signin.hidden = toRecovery; recovery.hidden = !toRecovery;
  document.getElementById('unlock-switch').textContent = toRecovery ? 'Sign in as an administrator instead' : 'Use the recovery key instead';
  (toRecovery ? document.getElementById('unlock-key') : document.getElementById('unlock-user')).focus();
}
async function unlock() {
  if (!unlocking) return;
  var recovery = document.getElementById('unlock-recovery').hidden ? '' : document.getElementById('unlock-key').value;
  var user = document.getElementById('unlock-user').value, pass = document.getElementById('unlock-pass').value;
  if (!recovery && !(user && pass)) { setStatus('Enter the username and password, or the recovery key.', true); return; }
  var btn = document.getElementById('unlock-btn');
  btn.disabled = true;
  setStatus('Unlocking… this takes a moment.');
  var result = await window.pywebview.api.unlock_company(unlocking, user, pass, recovery);
  if (result && result.success) {
    window.location.href = result.url;
    return;
  }
  setStatus((result && result.error) || 'Could not unlock the company file.', true);
  btn.disabled = false;
  document.getElementById('unlock-pass').value = '';
  (recovery ? document.getElementById('unlock-key') : document.getElementById('unlock-pass')).focus();
}
document.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !document.getElementById('unlock').hidden && e.target.closest('#unlock')) unlock();
});
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
    el.onclick = function () { openCompany(el.getAttribute('data-file'), el.querySelector('.name').firstChild.textContent); };
  });
  // First launch of the window: straight into the last company. The
  // sign-in screen it lands on offers "Choose a different company", and
  // Sign out comes back here with auto_open off.
  const last = info.companies.find(function (c) { return c.file === info.last_opened; });
  if (info.auto_open && last) {
    openCompany(last.file, last.name);
  }
}
async function openCompany(file, name) {
  setBusy(true);
  // Name the company: the list greys out while this runs, and a greyed list
  // with a generic line read as "all companies unavailable" (2.14.0 gate).
  setStatus('Opening ' + (name || 'company') + '… first open can take a minute.');
  const result = await window.pywebview.api.open_company(file);
  if (result && result.success) {
    // Navigate from JS, only AFTER the call above has resolved -- doing
    // this from Python instead (mid-call) would navigate the window
    // away before pywebview can deliver the return value to this very
    // page, throwing 'window.pywebview._returnValuesCallbacks... is
    // not a function' in a background thread.
    window.location.href = result.url;
  } else if (result && result.locked) {
    // the desktop's own key (#290): an administrator's sign-in, or the
    // recovery key, unlocks it here
    showUnlock(file, name, result.admins);
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


def _documents_dir() -> Path:
    """The user's Documents folder, or home when the account has none.

    On Windows the real folder is asked of the shell: with OneDrive
    "Known Folder Move" it lives under OneDrive, and ~/Documents may be
    absent or an empty leftover. Elsewhere ~/Documents is the convention.
    This is only a location; whether the app may WRITE there is a
    separate question that only a write can answer (see _save_report).
    """
    if sys.platform == "win32":
        try:
            import ctypes

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            fid = (ctypes.c_byte * 16)(
                *bytes.fromhex("D09AD3FD8F23AF46ADB46C85480369C7")
            )
            out = ctypes.c_wchar_p()
            if (
                ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(out)
                )
                == 0
                and out.value
            ):
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return path
        except Exception:
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def _fallback_reports_dir(folder: str = "Reports") -> Path:
    """Where Save PDF lands when the Documents folder refuses the write:
    the app's own data directory, which no folder-protection feature
    guards (Application Support on macOS, LOCALAPPDATA on Windows)."""
    return get_data_dir() / folder


# What a customer, vendor or donor is sent — as against a report about the
# books — known by the name the server gives the PDF ("Invoice_1002.pdf",
# "Statement_Acme Diner.pdf"). Invoices and statements used to land in
# .../Reports beside the P&L (macbase1, F24); they go to a Documents folder
# beside it.
_DOCUMENT_KINDS = frozenset(
    {
        "invoice",
        "salesreceipt",
        "estimate",
        "statement",
        "creditmemo",
        "pledge",
        "donationreceipt",
        "acknowledgment",
        "givingstatement",
        "givingstatements",
        "check",
        "purchaseorder",
        "po",
        "bill",
        "vendorcredit",
    }
)


def _given_folder(folder: str) -> str:
    """The folder a page asked for, when it is one of the two the app
    saves into: an attachment (a receipt, a W-4) is a document, whatever
    its own name."""
    return folder if folder in ("Documents", "Reports") else ""


def _folder_for(filename: str) -> str:
    """The folder for a PDF: Documents for a document someone is sent,
    Reports for everything else."""
    # the kind is the name's first word, read off the stem: "Check.pdf" (a
    # check with no number) is a check too (2.24.1 gate, NEW-168)
    stem = Path(str(filename or "")).stem
    kind = stem.split("_", 1)[0].replace("-", "").lower()
    return "Documents" if kind in _DOCUMENT_KINDS else "Reports"


def _write_unique(folder: Path, name: str, data: bytes) -> Path:
    """Write ``data`` as ``name`` under ``folder`` without overwriting:
    a " (2)", " (3)" suffix when the name is taken."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while dest.exists():
        dest = folder / f"{stem} ({n}){suffix}"
        n += 1
    dest.write_bytes(data)
    return dest


def _folder_permission_remedy(folder: Path) -> str:
    """One sentence telling the user how to let the app into ``folder``.
    macOS: TCC "Files and Folders" consent (per app, remembered after the
    first prompt). Windows: Defender's Controlled Folder Access is the
    usual cause. Linux: a plain permission problem."""
    if sys.platform == "darwin":
        return (
            "To allow it, open System Settings > Privacy & Security > "
            f"Files and Folders > SlowBooks Pro and turn on {folder.name} Folder."
        )
    if sys.platform == "win32":
        return (
            "If Controlled Folder Access is on, allow SlowBooks Pro under "
            "Windows Security > Virus & threat protection > Ransomware protection."
        )
    return f"Check the permissions on {folder}."


def _save_report(
    name: str, data: bytes, folder: str = "Reports"
) -> tuple[Path, str | None]:
    """Save a report PDF where the user can find it.

    Documents/SlowBooks Pro/<folder> first — Reports, or Documents for an
    invoice or statement (_folder_for). Existence of the Documents
    folder says nothing about permission -- on macOS the system decides
    per application (a consent prompt on first use; a denial is
    remembered), on Windows Controlled Folder Access can block it -- so
    the write itself is the test. When it is refused the file goes to
    the app's data directory instead and the second value is a note for
    the user saying so, naming both folders and the remedy. Any other
    failure propagates.
    """
    preferred = _documents_dir() / "SlowBooks Pro" / folder
    try:
        return _write_unique(preferred, name, data), None
    except PermissionError:
        dest = _write_unique(_fallback_reports_dir(folder), name, data)
        note = (
            f"SlowBooks Pro was not allowed to write to {preferred}, so the "
            f"file was saved to {dest.parent} instead. "
            + _folder_permission_remedy(_documents_dir())
        )
        return dest, note


def _safe_temp_filename(title: str, suffix: str) -> str:
    """Derive a safe filename for a transient viewer file from a title
    string the caller does not fully control (a document's own title,
    e.g. an invoice number). No path separators, no traversal, ASCII
    letters/digits/dash/underscore only, bounded length."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title or "document").strip("-")
    if not slug:
        slug = "document"
    return slug[:80] + suffix


def _saved_document_roots() -> tuple[Path, ...]:
    """The folders the app saves into when asked to: Documents/SlowBooks Pro
    (its Reports and Documents), and the app-data folders a refused
    Documents write falls back to."""
    return (
        (_documents_dir() / "SlowBooks Pro").resolve(),
        _fallback_reports_dir().resolve(),
        _fallback_reports_dir("Documents").resolve(),
    )


def _openable_pdf(path) -> Path | None:
    """``path`` as a PDF this app saved, or None: the guard in front of
    handing a file to another program. Only a .pdf that exists, resolved
    (links and '..' followed) inside a saved-documents folder — anything
    else there (an export named by the page, an attachment) could be run
    rather than shown by the program the system picks."""
    try:
        target = Path(str(path or "")).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if target.suffix.lower() != ".pdf" or not target.is_file():
        return None
    if not any(target.is_relative_to(root) for root in _saved_document_roots()):
        return None
    return target


def _open_with_default_app(target: Path) -> None:
    """Hand a file to the program the system opens that kind of file with."""
    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def _default_pdf_app(pdf: Path) -> str | None:
    """The name of the program the system opens a PDF with ("Preview",
    "Microsoft Edge"), for the viewer's button; None when it can't be told,
    and the button says "your PDF app"."""
    try:
        if sys.platform == "darwin":
            # pyobjc, which pywebview's macOS backend brings along
            from AppKit import NSWorkspace
            from Foundation import NSURL

            app = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(
                NSURL.fileURLWithPath_(str(pdf))
            )
            if app is None:
                return None
            return Path(str(app.path())).stem or None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            # ASSOCF_NONE, ASSOCSTR_FRIENDLYAPPNAME
            found = ctypes.windll.shlwapi.AssocQueryStringW(
                0, 4, ".pdf", None, buf, ctypes.byref(size)
            )
            return (buf.value or None) if found == 0 else None
    except Exception:
        return None
    return None


def _reveal(path) -> dict:
    """Open the folder that holds a file this app saved (Explorer / Finder /
    the desktop's file manager), with the file selected where the system
    can. Only paths under the app's saved-documents folders and the user's
    Downloads folder are accepted."""
    try:
        target = Path(str(path or "")).resolve()
        allowed = (*_saved_document_roots(), (Path.home() / "Downloads").resolve())
        if not any(target.is_relative_to(base) for base in allowed):
            return {"success": False, "error": "Not a file this app saved"}
        folder = target if target.is_dir() else target.parent
        if sys.platform == "win32":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            args = (
                ["open", "-R", str(target)]
                if target.is_file()
                else ["open", str(folder)]
            )
            subprocess.Popen(args)
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True}


# The page a Save PDF window shows: the PDF under a toolbar. The platform
# viewers offered no Save or Print on macOS, and Cmd+S did nothing (F24);
# the program the system opens a PDF with has both, so the toolbar opens
# the file there, or shows it in its folder. Cmd/Ctrl+P and Cmd/Ctrl+S do
# the first.
_VIEWER_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>$title</title>
<style>
  :root { color-scheme: light dark; }
  html, body { margin: 0; height: 100%; }
  body { display: flex; flex-direction: column; background: #525659;
         font: 13px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .bar { display: flex; align-items: center; gap: 8px; padding: 6px 10px;
         background: #f3f4f6; color: #1f2937; border-bottom: 1px solid #c9ced6; }
  .name { font-weight: 600; white-space: nowrap; }
  /* where the file was saved: #6b7280 was 4.39:1 on the light bar */
  .where { flex: 1; min-width: 0; color: #686f7d; white-space: nowrap;
           overflow: hidden; text-overflow: ellipsis; }
  .note { color: #b91c1c; }
  button { font: inherit; padding: 4px 10px; border: 1px solid #9aa3af;
           border-radius: 4px; background: #fff; color: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  iframe { flex: 1; width: 100%; border: 0; background: #fff; }
  @media (prefers-color-scheme: dark) {
    .bar { background: #1f2937; color: #e5e7eb; border-color: #374151; }
    .where { color: #9ca3af; }
    .note { color: #fca5a5; }
    button { background: #374151; border-color: #4b5563; }
  }
</style>
</head>
<body>
<div class="bar" role="toolbar" aria-label="$name">
  <span class="name">$name</span>
  <span class="where" title="$path">Saved in $where</span>
  <span class="note" id="note" role="status"></span>
  <button type="button" id="open-in-app" disabled
          title="Print it, or save a copy somewhere else, from there">$open_label</button>
  <button type="button" id="show-in-folder" disabled>Show in folder</button>
</div>
<iframe src="$src" title="$name"></iframe>
<script>
(function () {
  var note = document.getElementById('note');
  function call(name) {
    var api = window.pywebview && window.pywebview.api;
    if (!api || !api[name]) { note.textContent = 'Not available in this window.'; return; }
    note.textContent = '';
    api[name]().then(function (r) {
      if (!r || !r.success) note.textContent = (r && r.error) || 'That did not work.';
    });
  }
  function ready() {
    document.getElementById('open-in-app').disabled = false;
    document.getElementById('show-in-folder').disabled = false;
  }
  document.getElementById('open-in-app').addEventListener('click', function () {
    call('open_in_default_app');
  });
  document.getElementById('show-in-folder').addEventListener('click', function () {
    call('show_in_folder');
  });
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === 'p' || e.key === 's')) {
      e.preventDefault();
      call('open_in_default_app');
    }
  });
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_in_default_app) ready();
  else window.addEventListener('pywebviewready', ready);
})();
</script>
</body>
</html>
""")


def _write_viewer(pdf: Path, title: str) -> Path:
    """Write the viewer page for a saved PDF and return it. It goes in the
    app's own data folder, never beside the user's files; pages older than
    a day are cleared out as a new one is written."""
    folder = get_data_dir() / "viewer"
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 86400
    for old in folder.glob("viewer-*.html"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    app = _default_pdf_app(pdf)
    page = _VIEWER_PAGE.substitute(
        title=_html_escape(title or pdf.name),
        name=_html_escape(pdf.name),
        path=_html_escape(str(pdf)),
        where=_html_escape(str(pdf.parent)),
        src=_html_escape(pdf.as_uri()),
        open_label=_html_escape(f"Open in {app}" if app else "Open in your PDF app"),
    )
    dest = folder / f"viewer-{secrets.token_hex(8)}.html"
    dest.write_text(page, encoding="utf-8")
    return dest


class DocumentViewerApi:
    """js_api of a Save PDF window: its toolbar's two buttons. Bound to the
    one file the window shows — no method takes a path, so the page can't
    name another file — with the path guard behind that. State stays
    underscore-private (see PickerApi)."""

    def __init__(self, path):
        self._path = str(path)

    def open_in_default_app(self) -> dict:
        target = _openable_pdf(self._path)
        if target is None:
            return {
                "success": False,
                "error": "This PDF has been moved or deleted. Use Show in folder.",
            }
        try:
            _open_with_default_app(target)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def show_in_folder(self) -> dict:
        return _reveal(self._path)


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
        self._window = None  # set by run_window once the window exists
        # First load of the picker opens the last company straight to
        # sign-in (owner, 2026-09-13: "when there are companies it loads to
        # companies now"); a picker reached by choice — sign out, Switch
        # company — must not, or you could never leave.
        self._auto_open = True
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

            webview.create_window(
                title or "SlowBooks Pro 2026", html=html, zoomable=True
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def open_external(self, url: str) -> dict:
        """Open a URL in the user's default browser (employee portal links —
        a separate cookie-based site that must NOT be rendered in a
        document window, where its own links have no origin to resolve
        against), or a mailto: link in their mail program (Contact
        support's Send, which WKWebView drops on its own: 2.30.0 gate
        NEW-185). http(s) and mailto only."""
        try:
            import webbrowser

            if not re.match(r"^(https?://|mailto:)", str(url or ""), re.IGNORECASE):
                return {
                    "success": False,
                    "error": "Only http(s) and mailto links can be opened",
                }
            webbrowser.open(str(url))
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True}

    def open_document_pdf(self, title: str, base64_data: str, folder: str = "") -> dict:
        """Save an already-fetched PDF (base64-encoded by the caller) under
        Documents/SlowBooks Pro/Reports — or .../Documents for an invoice,
        estimate, statement or other document someone is sent — and show it
        in a new native window.
        The platform web view renders a file:// PDF with its own viewer
        (WebView2/Chromium on Windows, WKWebView on macOS, WebKitGTK on
        Linux), and a local file needs no authentication at all --
        sidestepping the same cross-window-cookie problem
        open_document_html's docstring describes.

        Field note (v2.9 lap): the file used to land in a temp folder, so
        "Save PDF" produced a window and nothing the user could find
        afterwards. Now it is a real file in a predictable place, never
        overwritten (a " (2)" suffix when the name is taken), and the path
        goes back to the page so it can say where. If the Documents folder
        refuses the write (macOS consent denied, Controlled Folder Access)
        the file goes to the app's data directory and ``note`` explains,
        so a denial never looks like a crash.

        The window shows the PDF under a toolbar (_VIEWER_PAGE): Open in
        <the PDF app>, which has Print and Save — the platform viewer had
        neither on macOS (F24) — and Show in folder.
        """
        import base64

        try:
            import webview

            data = base64.b64decode(base64_data)
            # The title is the server's filename, "Invoice_1002.pdf": take
            # the extension off before the name is made safe, or the dot
            # became a dash and the file was "Invoice_1002-pdf.pdf" (F24).
            stem = re.sub(r"\.pdf$", "", str(title or ""), flags=re.IGNORECASE)
            name = _safe_temp_filename(stem, ".pdf")
            # an attachment says it is a document (the page knows); anything
            # else is sorted by the name the server gave it
            dest, note = _save_report(
                name, data, _given_folder(folder) or _folder_for(name)
            )
            # The PDF under a toolbar (Open in the PDF app, Show in folder);
            # the bare PDF if the page can't be written.
            try:
                url, api = _write_viewer(dest, title).as_uri(), DocumentViewerApi(dest)
            except OSError:
                url, api = dest.as_uri(), None
            webview.create_window(
                title or "SlowBooks Pro 2026", url, js_api=api, zoomable=True
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        result = {"success": True, "path": str(dest)}
        if note:
            result["note"] = note
        return result

    def save_document_file(
        self, title: str, base64_data: str, folder: str = ""
    ) -> dict:
        """Save an already-fetched export (a CSV, or anything the page would
        otherwise hand to a download link) under Documents/SlowBooks Pro/
        Reports, with the same fallback and "note" contract as
        open_document_pdf, and without opening a viewer. The shell prefers
        this over a blob <a download>: both WebView2 and WKWebView route a
        page-initiated download through native plumbing that the page
        cannot observe, so the user never learns whether or where the
        file landed."""
        import base64

        try:
            data = base64.b64decode(base64_data)
            given = Path(str(title or ""))
            suffix = given.suffix if given.suffix else ".txt"
            dest, note = _save_report(
                _safe_temp_filename(given.stem, suffix),
                data,
                _given_folder(folder) or "Reports",
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        result = {"success": True, "path": str(dest)}
        if note:
            result["note"] = note
        return result

    def save_secret_file(self, name: str, text: str) -> dict:
        """Save a text the person must keep (the company file's recovery
        key, #290) where they choose, through a native Save panel. Not
        save_document_file: that lands in Documents/SlowBooks Pro/Reports
        with every export, which iCloud or OneDrive sync on many machines
        (2.24.0 gate: both agents saw the key go to a cloud folder).
        Returns {"success", "path"}, {"cancelled": True} when the panel is
        dismissed, or {"success": False, "error"}."""
        try:
            import webview

            window = self._window
            if window is None:
                return {"success": False, "error": "no window"}
            safe = _safe_temp_filename(
                Path(str(name or "recovery key.txt")).stem, ".txt"
            )
            chosen = window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=str(Path.home()),
                save_filename=safe,
            )
            if not chosen:
                return {"success": False, "cancelled": True}
            dest = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
            if not dest.suffix:
                dest = dest.with_suffix(".txt")
            dest.write_text(str(text or ""), encoding="utf-8")
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "path": str(dest)}

    def reveal_path(self, path: str) -> dict:
        """Open the folder that holds a file this app saved (Explorer /
        Finder / the desktop's file manager). Only paths under the app's
        own Reports and the user's Downloads folders are accepted."""
        return _reveal(path)

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
            try:
                downloads.mkdir(parents=True, exist_ok=True)
                dest = downloads / src.name
                stem, suffix = src.stem, src.suffix
                n = 1
                while dest.exists():
                    dest = downloads / f"{stem} ({n}){suffix}"
                    n += 1
                shutil.copy2(src, dest)
            except PermissionError:
                # A refused folder is not a crash: say which folder, where
                # the backup already is, and how to allow it next time.
                return {
                    "success": False,
                    "error": (
                        f"SlowBooks Pro was not allowed to write to {downloads}. "
                        f"The backup is still at {src}. "
                        + _folder_permission_remedy(downloads)
                    ),
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "path": str(dest)}

    def list_companies(self) -> dict:
        from app.services import company_service

        return {
            "companies": company_service.manifest_list_companies(),
            "last_opened": company_service.get_last_opened(),
            "auto_open": self._auto_open,
        }

    def create_company(self, name: str) -> dict:
        from app.services import company_service

        try:
            return company_service.manifest_create_company(name)
        except Exception as exc:  # surfaced in the picker, not a traceback
            return {"success": False, "error": str(exc)}

    def show_picker(self) -> dict:
        """Back to the company picker without closing the app.

        Signing out used to reload the same company's login screen; the
        only way to another company, or to see who the users are, was to
        quit and relaunch — and the Companies page said exactly that. The
        server for the open company is stopped and the picker page is
        loaded back into this window. The load is scheduled rather than
        done inside this call, for the same reason open_company hands its
        URL back instead of navigating: pywebview resolves a JS promise on
        the page that made the call, and that page is about to be gone.
        """
        import threading

        stop_server(self._server)
        self._server = None
        self._auto_open = False  # the person asked for the picker; show it
        window = self._window

        def _load():
            time.sleep(0.15)
            try:
                if window is not None:
                    window.load_html(PICKER_HTML)
            except Exception:
                pass  # the picker is a convenience; the app is still running

        threading.Thread(target=_load, daemon=True).start()
        return {"success": True}

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
        except LockedCompany as exc:
            # the desktop's own key (#290): the picker asks for an
            # administrator's sign-in, or the recovery key
            from app.services import company_keyring, company_service

            ring = company_keyring.load(company_service.company_db_path(filename))
            return {
                "success": False,
                "locked": True,
                "error": str(exc),
                "admins": company_keyring.admins(ring),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "url": f"http://127.0.0.1:{self._port}"}

    def unlock_company(
        self,
        filename: str,
        username: str = "",
        password: str = "",
        recovery_key: str = "",
    ) -> dict:
        """Unlock an encrypted company file with an administrator's sign-in
        or its recovery key (#290), then launch it. The key goes to the
        server child through its environment, as a launcher-resolved key
        does; nothing is written anywhere."""
        from app.services import company_keyring, company_service

        db_path = company_service.company_db_path(filename)
        if db_path is None:
            return {
                "success": False,
                "error": f"Invalid company file name: {filename!r}",
            }
        try:
            key = company_keyring.unlock(
                db_path, username=username, password=password, recovery_key=recovery_key
            )
        except company_keyring.Locked as exc:
            return {"success": False, "locked": True, "error": str(exc)}
        try:
            self._server = launch_company(
                filename, self._port, output=self._log_fh, key=key
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": True, "url": f"http://127.0.0.1:{self._port}"}


# The lowest Microsoft Edge WebView2 runtime the app's window works on. Two
# floors meet here, and this is the higher one:
#   * The window. pywebview 6.2.1 always sets the WinForms control's
#     CreationProperties.IsInPrivateModeEnabled, and with it set the control
#     asks the runtime for ICoreWebView2Environment10 (multiple profiles),
#     which Microsoft made stable in SDK 1.0.1210.39, "for Runtime
#     101.0.1210.39 or later". On an older runtime the control fails to
#     start, pywebview only logs it, and the window stays white with nothing
#     in it (a support email on 2.29.2: "Full white screen on startup ... no
#     menus or sidebars").
#   * The page. The app's JavaScript needs Chromium 93 (Object.hasOwn);
#     tests/test_webview2_floor.py keeps this constant at or above every
#     browser feature the code uses.
# pywebview's own check asks only for 86 (winforms._is_chromium), so any
# runtime from 86 up to this one opened the white window. The installer's
# IsWebView2Current compares against the same number
# (tests/test_windows_packaging.py keeps the two equal).
MIN_WEBVIEW2 = "101.0.1210.39"

# Where a WebView2 runtime registers its version (a 'pv' value under
# EdgeUpdate\Clients\{guid}): the Evergreen runtime, then the Microsoft Edge
# preview channels a WebView2 app also runs on, in the order the WebView2
# loader tries them by default, most stable first (Microsoft's
# ChannelSearchKind). pywebview's own check reads the same four.
WEBVIEW2_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # WebView2 Runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Microsoft Edge Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Microsoft Edge Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Microsoft Edge Canary
)


def _version_parts(text) -> tuple[int, ...] | None:
    """'130.0.2849.80' -> (130, 0, 2849, 80), padded to four numbers. None
    for anything that is not a version, and for 0.0.0.0, which is what an
    uninstalled runtime leaves behind (Microsoft's docs)."""
    try:
        parts = tuple(int(p) for p in str(text).strip().split("."))
    except ValueError:
        return None
    if not any(parts):
        return None
    return parts + (0,) * (4 - len(parts))


def _webview2_version() -> str | None:
    """The version of the WebView2 runtime the window would run on, or None
    when there is none. (Windows only; None anywhere else.)

    Same detection Microsoft documents (and pywebview itself uses): a
    runtime registers a 'pv' version under an EdgeUpdate key, per machine
    (HKLM) or per user (HKCU). The first channel installed is the one the
    WebView2 loader uses, so it is the one that counts; within it, the
    highest version its keys name.
    """
    if sys.platform != "win32":
        return None
    import winreg

    for guid in WEBVIEW2_CLIENTS:
        key_paths = [
            (
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}",
            ),
            (
                winreg.HKEY_CURRENT_USER,
                rf"Software\Microsoft\EdgeUpdate\Clients\{guid}",
            ),
        ]
        found = []
        for root, path in key_paths:
            try:
                with winreg.OpenKey(root, path) as key:
                    pv, _ = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            if _version_parts(pv):
                found.append(str(pv).strip())
        if found:
            return max(found, key=_version_parts)
    return None


def _webview2_status() -> tuple[str, str | None]:
    """("missing" | "old" | "ok", the version found). Off Windows there is
    no WebView2 to check, and the answer is ("ok", None).

    This must be checked HERE, before opening the window: pywebview does
    NOT fail when WebView2 is missing -- even with gui='edgechromium'
    forced it silently falls back to the legacy IE/MSHTML control, where
    neither the app's JavaScript nor the js_api bridge works (endless
    "SyncRoot ... maximum recursion depth exceeded" spam, dead buttons).
    An old runtime fails as quietly: below MIN_WEBVIEW2 the window opens
    white and stays white.
    """
    if sys.platform != "win32":
        return "ok", None
    version = _webview2_version()
    if version is None:
        return "missing", None
    if _version_parts(version) < _version_parts(MIN_WEBVIEW2):
        return "old", version
    return "ok", version


def _webview2_installed() -> bool:
    """Is a Microsoft WebView2 runtime present, whatever its version?
    (Windows only; True anywhere else.) _webview2_status says whether it is
    new enough."""
    return _webview2_status()[0] != "missing"


WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
WINDOWS_INSTALLER = "SlowBooksPro-Setup-x64.exe"


def _no_webview2_message() -> str:
    """What to tell someone whose machine has no WebView2 runtime.

    Issue #149: the portable zip carries no bootstrapper (the installer
    does), so this is the first thing a user of it sees on a fresh
    Windows image. The old text ended "python desktop_launcher.py
    --no-window" — an instruction the installed build cannot follow,
    because it has no Python and no such file. Sixth appearance of that
    class. The frozen build is told the two things it CAN do.
    """
    if FROZEN:
        return (
            "SlowBooks Pro needs the Microsoft Edge WebView2 runtime to show "
            "its window, and this computer does not have it.\n"
            "\n"
            "Two ways to fix that:\n"
            f"  1. Install the runtime from Microsoft: {WEBVIEW2_URL}\n"
            f"  2. Or install SlowBooks Pro with its installer, {WINDOWS_INSTALLER}, "
            "which sets the runtime up for you.\n"
        )
    return (
        "The Microsoft WebView2 runtime is not installed, so the app window "
        "cannot open properly.\n"
        f"Install it from: {WEBVIEW2_URL}\n"
        "You can also start without a native window:\n"
        "    python desktop_launcher.py --no-window"
    )


def _ask_yes_no(message: str, title: str = "SlowBooks Pro 2026") -> bool:
    """A native Yes/No box (Windows only; False anywhere it cannot ask)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        MB_YESNO, MB_ICONWARNING, IDYES = 0x4, 0x30, 6
        return (
            ctypes.windll.user32.MessageBoxW(
                0, message, title, MB_YESNO | MB_ICONWARNING
            )
            == IDYES
        )
    except Exception:
        return False


def _hold_until_dismissed(message: str, title: str = "SlowBooks Pro 2026") -> None:
    """Block on a native OK box (Windows); elsewhere, wait for Ctrl+C."""
    if sys.platform == "win32":
        try:
            import ctypes

            MB_ICONINFORMATION = 0x40
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_ICONINFORMATION)
            return
        except Exception:
            pass
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _old_webview2_message(version: str) -> str:
    """What to tell someone whose WebView2 runtime is older than
    MIN_WEBVIEW2: the version found, the version needed, and the same ways
    out a missing runtime has. A reinstall is one of them now that the
    installer updates an old runtime; before, it ran Microsoft's
    bootstrapper only when there was none ("I have tried reinstalling as
    well", the support email)."""
    why = (
        "SlowBooks Pro needs a newer Microsoft Edge WebView2 runtime to show "
        f"its window. This computer has version {version}; SlowBooks needs "
        f"{MIN_WEBVIEW2} or later.\n"
    )
    if FROZEN:
        return why + (
            "\n"
            f"Update it from Microsoft: {WEBVIEW2_URL}\n"
            f"or run the SlowBooks Pro installer, {WINDOWS_INSTALLER}, again, "
            "which updates it.\n"
        )
    return why + (
        f"Update it from Microsoft: {WEBVIEW2_URL}\n"
        "You can also start without a native window:\n"
        "    python desktop_launcher.py --no-window"
    )


def _run_without_webview2(port: int, log_fh=None, msg: str | None = None) -> int:
    """The window cannot open: no WebView2 runtime, or (with ``msg``, the
    old-runtime message) one too old to draw it. Say why in terms the
    reader can act on and, on Windows, offer the one thing that works
    without the runtime: the app in the system browser, held open by a box
    the user closes to stop it. Verified by unit test with a fake registry;
    the end-to-end on a machine with the runtime genuinely absent needs a
    scratch VM (issue #149) and is recorded as uncovered, not as passed."""
    msg = msg or _no_webview2_message()
    print(msg)
    if not _ask_yes_no(msg + "\nOpen SlowBooks Pro in your web browser instead?"):
        _show_error_box(msg)
        return 1
    return run_in_browser(port, log_fh)


def run_in_browser(port: int, log_fh=None) -> int:
    """Serve on loopback and open the system browser on it."""
    import webbrowser

    proc = _start_default_company(port, output=log_fh)
    if proc is None:
        return 1
    url = f"http://127.0.0.1:{port}"
    print(f"SlowBooks Pro is running at {url}")
    webbrowser.open(url)
    try:
        _hold_until_dismissed(
            f"SlowBooks Pro is open in your web browser at {url}\n"
            "\n"
            "Keep this box open while you work. Click OK to stop SlowBooks Pro."
        )
    finally:
        stop_server(proc)
    return 0


def _show_error_box(message: str, mailto: str | None = None) -> None:
    """Best-effort native popup for fatal errors -- used in --hidden mode,
    where there's no visible console to print to. No-op on unsupported
    platforms, or if it fails for any reason.

    When ``mailto`` is given the dialog offers an "Email support" choice that
    opens the user's mail program with the error already written (the same
    in-app Contact support feature, from the one place the app never started)."""
    try:
        if sys.platform == "darwin":
            buttons = (
                ' buttons {"Email support", "OK"} default button "OK"' if mailto else ""
            )
            result = subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    "on run argv",
                    "-e",
                    'display alert "SlowBooks Pro 2026" message (item 1 of argv) '
                    "as critical" + buttons,
                    "-e",
                    "end run",
                    "--",
                    message,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if mailto and "Email support" in (result.stdout or ""):
                subprocess.run(["open", mailto], check=False, timeout=15)
            return
        if sys.platform == "win32":
            import ctypes

            if mailto:
                MB_YESNO = 0x4
                MB_ICONERROR = 0x10
                IDYES = 6
                clicked = ctypes.windll.user32.MessageBoxW(
                    0,
                    message + "\n\nEmail support about this now?",
                    "SlowBooks Pro 2026",
                    MB_YESNO | MB_ICONERROR,
                )
                if clicked == IDYES:
                    os.startfile(mailto)  # noqa: S606 — a mailto we built
                return
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(
                0, message, "SlowBooks Pro 2026", MB_ICONERROR
            )
    except Exception:
        pass


def _support_mailto(log_path, deployment="native") -> str | None:
    """A mailto: to support for the fatal-error dialog, built with the same
    subject/body as the in-app form. No DB is open (the app never started), so
    the log tail is redacted with no name list. Best-effort: None on failure.

    ``deployment`` is passed EXPLICITLY: sd.detect_deployment() reads
    SLOWBOOKS_DESKTOP / SLOWBOOKS_SERVER_MODE, which this launcher only ever
    sets in the SERVER CHILD's env (see _server_env), never its own — so
    detecting it here would mislabel every crashed native install as
    self-managed."""
    try:
        from urllib.parse import quote

        from app import __version__
        from app.services import support_diagnostics as sd

        platform_name = sd.detect_platform()
        env = sd.environment_block(
            __version__, sd.app_commit(), platform_name, sd.python_version(), deployment
        )
        tail, available = sd.read_log_tail(log_path) if log_path else ("", False)
        tail = sd.redact(tail, []) if available else ""
        subject = sd.build_subject(
            __version__, platform_name, deployment, "App failed to start"
        )
        body = sd.build_body(
            "SlowBooks Pro could not start.",
            "The app to open normally.",
            "",
            env,
            tail,
            log_available=available,
        )
        return f"mailto:{sd.SUPPORT_EMAIL}?subject={quote(subject)}&body={quote(body)}"
    except Exception:
        return None


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

    status, version = _webview2_status()
    if sys.platform == "win32":
        # one line in launcher.log, for whoever reads it next
        print(f"WebView2 runtime: {version or 'none'} (needs {MIN_WEBVIEW2} or later)")
    if status == "missing":
        return _run_without_webview2(port, log_fh)
    if status == "old":
        return _run_without_webview2(port, log_fh, _old_webview2_message(version))

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
    api._window = webview.create_window(
        "SlowBooks Pro 2026",
        html=PICKER_HTML,
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
        zoomable=True,
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
                "This usually means the Microsoft WebView2 runtime is missing "
                "or out of date.\n"
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


def _migrate_other_companies(started: str, output=None) -> list[str]:
    """Server Edition serves every company in the list (#358): bring each
    one besides the company it starts for up to this version first, the
    way launch_company() does that one. A company that cannot be (no key
    for an encrypted file, a failed migration) is named and skipped; the
    server refuses to open it until it is. Returns the files skipped."""
    from app.services import company_file, company_service

    skipped = []
    for entry in company_service.manifest_list_companies():
        filename = entry["file"]
        if filename == started:
            continue
        db_path = company_service.company_db_path(filename)
        if db_path is None or not db_path.exists():
            continue
        key = None
        if company_file.is_encrypted_file(db_path):
            key = company_file.install_key()
            if key is None:
                print(
                    f"WARNING: {filename} is encrypted under a key of its own "
                    "and is not served; set COMPANY_FILE_ENCRYPTION_SECRET to serve it."
                )
                skipped.append(filename)
                continue
        try:
            migrate("sqlite:///" + db_path.as_posix(), output=output, key=key)
        except Exception as exc:  # one company must not stop the others
            print(
                f"WARNING: could not bring {filename} up to date ({exc}); "
                "it is not served until it is."
            )
            skipped.append(filename)
    return skipped


def _start_default_company(
    port: int, bind_host: str = "127.0.0.1", output=None
) -> subprocess.Popen | None:
    """Open the last-used company (or the first, or a new one) and start
    the server for it. None, with the reason printed, if that fails."""
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
                return None
            filename = result["file"]

    # every company the server will serve (#358); not while another server
    # has them open (launch_company says so below)
    if bind_host != "127.0.0.1" and not _server_already_running(port):
        _migrate_other_companies(filename, output=output)

    print(f"Opening company file: {filename}")
    try:
        return launch_company(
            filename, port, bind_host=bind_host, persist=False, output=output
        )
    except LockedCompany:
        from app.services import company_keyring

        print(f"ERROR: {company_keyring.HEADLESS_MESSAGE}")
        return None
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return None


def run_headless(port: int, bind_host: str = "127.0.0.1") -> int:
    proc = _start_default_company(port, bind_host)
    if proc is None:
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
    perpetually.

    POSIX (macOS + Linux) polls the parent: signal 0 and getppid. Windows
    (2.22.3 gate, W-7) holds a handle on the parent process and waits on
    it: WaitForSingleObject returns when the launcher is gone however it
    went — Task Manager's End task, a crash, TerminateProcess — which the
    window-close cleanup never saw, so the server sat on its port and the
    next launch was refused as "already running". The handle pins the
    process object, so a reused pid cannot fool it.

    (Deliberate "keep serving after the window closes" is a designed
    v2.6 feature — tray indicator + relaunch adoption — not an accident
    of orphaning.)
    """
    import threading
    import time as _t

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        INFINITE = 0xFFFFFFFF
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        handle = k32.OpenProcess(SYNCHRONIZE, False, int(parent_pid))
        if not handle:
            return  # no such process, or no right to wait on it: serve as before

        def _wait():
            k32.WaitForSingleObject(handle, INFINITE)
            os._exit(0)  # parent gone: take the port down with us

        threading.Thread(target=_wait, daemon=True, name="parent-watch").start()
        return

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


def _win32_dlls():
    """The three Windows DLLs the timer code needs; a seam for tests."""
    import ctypes

    return ctypes.windll.winmm, ctypes.windll.kernel32, ctypes.windll.ntdll


def _timer_resolution_ms(ntdll) -> float | None:
    """The current system timer resolution, from NtQueryTimerResolution
    (100-ns units), or None if it cannot be read."""
    import ctypes

    # ULONG is 32 bits on Windows; c_uint32 says so on every platform
    lo, hi, cur = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32()
    try:
        if (
            ntdll.NtQueryTimerResolution(
                ctypes.byref(lo), ctypes.byref(hi), ctypes.byref(cur)
            )
            != 0
        ):
            return None
    except Exception:
        return None
    return cur.value / 10_000


def raise_timer_resolution(period_ms: int = 1):
    """Windows: serve with a 1 ms timer instead of the 15.625 ms default.

    Issue #107 (skytech, 2.9.3 gate): about half of all requests on Windows
    waited exactly one scheduler tick — a trivial /health cost the same as
    a full invoice write, and the histogram had a second peak on 15.625 ms.
    A request bounces between the event loop and a worker thread (every
    sync route, every SQLite call that releases the GIL), and each hand-off
    is a timed wait that Windows rounds up to the tick. timeBeginPeriod(1)
    is the documented fix and is what browsers do while active.

    Two things the docs say that matter here. Since Windows 10 2004 the
    request is per-process, so it has to be made in THIS process — the
    server child — not the launcher. And Windows 11 ignores the request
    from a process with no visible window unless it opts out with
    SetProcessInformation, which is exactly what a --_serve child is.

    Returns a function that undoes it (timeEndPeriod must be matched).
    The log line names the resolution before and after, so a gate can
    confirm the request took effect rather than assume it.

    OFF unless SLOWBOOKS_TIMER_RESOLUTION_MS is set. On the 2.13.0 gate
    skytech put a Windows 11 box back at the true 15.625 ms default and
    measured the UNFIXED build: median 1.8 ms, zero samples in the tick
    band. The 2.9.3 symptom does not reproduce there, and a 1 ms timer
    in a background process is a power cost — so it is not imposed on
    every install for a benefit nobody has measured. Set the variable to
    1 and the log line says whether the request took; that is the
    instrument for anyone who does see the tick.
    """
    if sys.platform != "win32":
        return lambda: None
    requested = os.environ.get("SLOWBOOKS_TIMER_RESOLUTION_MS", "").strip()
    if not requested:
        print(
            "timer resolution: left at the system default "
            "(set SLOWBOOKS_TIMER_RESOLUTION_MS=1 to request 1 ms)"
        )
        return lambda: None
    try:
        period_ms = max(1, int(requested))
    except ValueError:
        print(
            f"timer resolution: SLOWBOOKS_TIMER_RESOLUTION_MS={requested!r} is not a number"
        )
        return lambda: None
    try:
        import ctypes

        winmm, kernel32, ntdll = _win32_dlls()
    except Exception:
        return lambda: None

    before = _timer_resolution_ms(ntdll)
    try:
        # PROCESS_POWER_THROTTLING_STATE { Version=1, ControlMask, StateMask }
        # ControlMask selects IGNORE_TIMER_RESOLUTION (0x4); StateMask 0
        # turns that throttling OFF — "always honor timer resolution
        # requests", per the SetProcessInformation reference. Fails
        # harmlessly (returns 0) on Windows 10, which has no such throttle.
        class _PowerThrottling(ctypes.Structure):
            _fields_ = [
                ("Version", ctypes.c_uint32),
                ("ControlMask", ctypes.c_uint32),
                ("StateMask", ctypes.c_uint32),
            ]

        state = _PowerThrottling(1, 0x4, 0)
        kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(),
            4,  # ProcessPowerThrottling
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
    except Exception:
        pass

    try:
        if winmm.timeBeginPeriod(period_ms) != 0:  # TIMERR_NOCANDO
            print(f"timer resolution: request for {period_ms} ms refused")
            return lambda: None
    except Exception:
        return lambda: None

    after = _timer_resolution_ms(ntdll)
    fmt = lambda v: "unknown" if v is None else f"{v:.3f} ms"  # noqa: E731
    print(f"timer resolution: was {fmt(before)}, now {fmt(after)}")

    def undo():
        try:
            winmm.timeEndPeriod(period_ms)
        except Exception:
            pass

    return undo


def _serve() -> int:
    """Internal: run the uvicorn server in this process. start_server()
    launches every server child this way: the frozen build re-execs the
    bundled exe with --_serve (it has no interpreter for `-m uvicorn`), and
    a source run re-runs this script with it, so both get the parent
    watcher. All configuration (DATABASE_URL, APP_PORT, SLOWBOOKS_*)
    arrives via the environment from _server_env()."""
    import uvicorn

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
    restore_timer = raise_timer_resolution()
    try:
        uvicorn.run(app.main.app, host=host, port=port, use_colors=False)
    finally:
        restore_timer()
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
    proc = launch_company(filename, port, output=child_out, persist=False)
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

    # The platform-native OCR engine rides in the frozen bundle as hidden
    # imports (pyobjc Vision / winrt). Nothing else in this smoke test
    # touches OCR, so prove the bridge survived freezing here — on macOS
    # Vision ships with the OS and must be the selected engine; on Windows
    # the CI runner may lack OCR language packs, so only report.
    print("smoke: checking the OCR engine...")
    from app.services import ocr_engines

    status = ocr_engines.engine_status(None)
    print(
        f"smoke: ocr engine={status.get('engine')} "
        f"available={status.get('available')} version={status.get('version')}"
    )
    if sys.platform == "darwin" and status.get("engine") != "vision":
        print("smoke: FAIL Apple Vision is not the selected engine in the bundle")
        return 1

    # Point 1 of #290: the encrypted-file driver rides in the bundle. The
    # module is imported lazily (app/services/company_file.py), so prove
    # PyInstaller collected it by reading SQLCipher's version through it,
    # then write one encrypted scratch file and read it back.
    print("smoke: reading the SQLCipher version through the bundled module...")
    from app.services import company_file

    cipher = company_file.cipher_version()
    if not cipher:
        print("smoke: FAIL sqlcipher3 is not in the bundle")
        return 1
    print(f"smoke: sqlcipher {cipher}")
    scratch = get_data_dir() / "smoke-cipher.db"
    scratch.unlink(missing_ok=True)
    key = os.urandom(company_file.KEY_BYTES)
    conn = company_file.connect_raw(scratch, key)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    if not company_file.is_encrypted_file(scratch):
        print("smoke: FAIL the scratch file is not encrypted on disk")
        return 1
    conn = company_file.connect_raw(scratch, key)
    row = conn.execute("SELECT x FROM t").fetchone()
    conn.close()
    scratch.unlink(missing_ok=True)
    if row != (1,):
        print("smoke: FAIL the encrypted scratch file did not read back")
        return 1

    print("smoke: PASS")
    return 0


def _repair_schema(argv) -> int:
    """`SlowBooksPro --_repair-schema --database-url <url> [--dry-run]`.

    Runs the repair in-process so it works from a frozen bundle, where a
    separate interpreter is neither present nor able to import `app`.
    """
    url = None
    for i, a in enumerate(argv):
        if a == "--database-url" and i + 1 < len(argv):
            url = argv[i + 1]
        elif a.startswith("--database-url="):
            url = a.split("=", 1)[1]
    if not url:
        print(
            "usage: SlowBooksPro --_repair-schema --database-url <url> [--dry-run]",
            file=sys.stderr,
        )
        return 2

    # BEFORE importing anything that touches app.config, and that is the
    # whole fix. `app/config.py` reads BASE_DIR/.env — inside the bundle when
    # frozen, not the user's data dir — so DATABASE_URL is unset, falls back
    # to the PostgreSQL default, and `app/database.py` creates its engine AT
    # MODULE SCOPE. `migrations/env.py` imports that module, so the import
    # died on psycopg2 before the migration ever looked at the URL we passed.
    #
    # The normal startup path already points DATABASE_URL at the chosen
    # company file before serving; this entry point ran before that step and
    # inherited none of it. @skytech traced it on the 2.12.1 gate, and it was
    # the FIFTH appearance of one class: an instruction the reader cannot
    # carry out. Their own first diagnosis was a cross-database hazard, which
    # they tested and withdrew — the real cause is smaller and this is it.
    os.environ["DATABASE_URL"] = url

    from app.services.schema_repair import repair

    result = repair(url, dry_run="--dry-run" in argv)
    print(f"revision before : {result.started_at}")
    print(f"revision now    : {result.now_at}")
    if result.dropped:
        print(f"dropped (empty) : {', '.join(result.dropped)}")
    print(f"result          : {'OK' if result.ok else 'FAILED'} - {result.message}")
    return 0 if result.ok else 1


def run_mcp(port: int | None = None) -> int:
    """--mcp: the stdio MCP server against the already-running app. Prepares
    nothing (no .env, company, alembic or uvicorn); only stderr may print."""
    if port or not os.environ.get("SLOWBOOKS_URL"):
        # An explicit --port wins over SLOWBOOKS_URL; else .env's APP_PORT.
        port = port or int(get_env_value("APP_PORT") or "3001")
        os.environ["SLOWBOOKS_URL"] = f"http://127.0.0.1:{port}"
    if not os.environ.get("SLOWBOOKS_TOKEN_FILE"):
        os.environ["SLOWBOOKS_TOKEN_FILE"] = str(get_data_dir() / "mcp-token")
    from app.mcp.server import run

    return run()


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
    # And make the encoding UTF-8, not the console codepage: a frozen
    # console=False build with piped stdio (SSH, CI, an agent) inherits
    # cp1252 on US Windows and prints "?" for every arrow and dash in
    # --help and in a fatal-startup message (2.9.0 Windows gate). A real
    # Windows console uses the wide-character API regardless of this
    # setting, so it costs nothing there.
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None:
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass  # not a TextIOWrapper (test harness, log redirect)

    # Not a user flag — the frozen server child (see start_server) and
    # argparse must never meet, so handle it before parsing.
    if "--_serve" in sys.argv:
        return _serve()

    # Same reason, same shape: the repair has to run INSIDE the frozen
    # runtime, because that is the only place `app.services` is importable.
    #
    # 2.12.1 shipped scripts/repair-schema.py in the bundle and the startup
    # refusal named it — and both QA agents found that nothing on the machine
    # can execute it. `_internal/app/` holds only static and templates; the
    # Python modules live inside the executable. On Windows the printed
    # `python3` is the Microsoft Store alias stub, zero bytes.
    #
    # That was the FOURTH appearance of one class: an error telling the
    # reader to do something they cannot do. The test I added asserted the
    # named path EXISTS, which is exactly the assertion that passes while the
    # instruction still fails. It executes it now.
    if "--_repair-schema" in sys.argv:
        return _repair_schema(sys.argv)

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
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="run the local MCP server over stdio against the running app "
        "(see docs/agents.md)",
    )
    args = parser.parse_args()
    if args.data_dir:
        os.environ["SLOWBOOKS_DATA_DIR"] = str(Path(args.data_dir).resolve())

    # Before any stdout/stderr redirect: the MCP transport is stdio.
    if args.mcp:
        return run_mcp(args.port)

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
        os.environ.setdefault("SLOWBOOKS_ENV_FILE", str(env_file()))
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
        # --serve-lan is Server Edition; every other path is the native app.
        deployment = "Server Edition" if args.serve_lan else "native"
        _show_error_box(
            "SlowBooks Pro hit an unexpected error and could not start.\n\n"
            f"Details were written to:\n{log_path}",
            mailto=_support_mailto(log_path, deployment),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
