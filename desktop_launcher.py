"""SlowBooks Pro 2026 — native desktop launcher.

Entry point for the no-Docker desktop install (Windows-first, but runs
anywhere Python does). What it does, in order:

  1. Prepares .env (copies .env.example on first run, generates a real
     PAYROLL_ENCRYPTION_SECRET, sets APP_DEBUG=true / FORCE_HTTPS=false /
     APP_HOST=127.0.0.1 — correct for a loopback-only desktop install).
  2. Shows a company picker (like QuickBooks' "File → Open Company"):
     each company is its own SQLite file under
     %LOCALAPPDATA%\\SlowBooksPro\\data\\companies\\, tracked in
     companies.json. Pick one or create a new one.
  3. Points DATABASE_URL at the chosen company's .db file, runs
     `alembic upgrade head` (idempotent), starts uvicorn on 127.0.0.1,
     and opens the app in a native window (pywebview → WebView2).
  4. When the window closes, the server is shut down.

To switch companies: close the window and relaunch — the picker appears
again. Flags:
  --no-window   start the server and print the URL (no native window)
  --setup-only  prepare .env and data directories, then exit
  --port N      override the port (default: APP_PORT from .env, else 3001)
"""

import argparse
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

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


def get_data_dir() -> Path:
    """Same resolution as app.services.company_service.data_dir(), duplicated
    here so --setup-only works before the app's dependencies are installed."""
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SlowBooksPro" / "data"
    return Path.home() / ".slowbookspro" / "data"


def prepare_env() -> None:
    """Idempotent first-run .env preparation."""
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(
                ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
            )
        else:
            ENV_FILE.write_text("", encoding="utf-8")
        print(f"Created {ENV_FILE}")

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

    data_dir = get_data_dir()
    (data_dir / "companies").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _server_env(db_url: str, port: int) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": db_url,
            "APP_DEBUG": "true",
            "FORCE_HTTPS": "false",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(port),
            "SLOWBOOKS_DATA_DIR": str(get_data_dir()),
        }
    )
    return env


def migrate(db_url: str) -> None:
    """Run `alembic upgrade head` against the chosen company database.

    Only meaningfully does work the first time a company file is opened
    (or after an app update ships new migrations); safe to run every time.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=_server_env(db_url, 0),
        check=True,
    )


def start_server(db_url: str, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=_server_env(db_url, port),
    )


def wait_for_health(proc: subprocess.Popen, port: int, timeout: float = 120) -> bool:
    url = f"http://127.0.0.1:{port}/health"
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


def launch_company(filename: str, port: int) -> subprocess.Popen:
    """Point the app at a company file, migrate it, and start the server."""
    from app.services import company_service

    db_path = company_service.company_db_path(filename)
    if db_path is None:
        raise ValueError(f"Invalid company file name: {filename!r}")

    db_url = "sqlite:///" + db_path.as_posix()
    set_env_value("DATABASE_URL", db_url)
    company_service.set_last_opened(filename)

    migrate(db_url)

    proc = start_server(db_url, port)
    if not wait_for_health(proc, port):
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
  if (result && !result.success) {
    setStatus(result.error || 'Could not open company.', true);
    setBusy(false);
  }
  // On success the Python side navigates this window to the app.
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


class PickerApi:
    """js_api bridge for the company-picker page (window.pywebview.api)."""

    def __init__(self, port: int):
        self.port = port
        self.window = None
        self.server: subprocess.Popen | None = None

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
        try:
            self.server = launch_company(filename, self.port)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        if self.window is not None:
            self.window.load_url(f"http://127.0.0.1:{self.port}")
        return {"success": True}


def run_window(port: int) -> int:
    try:
        import webview
    except ImportError:
        print(
            "pywebview is not installed. Install it with:\n"
            "    pip install -r requirements-desktop.txt\n"
            "or start without a native window:\n"
            "    python desktop_launcher.py --no-window"
        )
        return 1

    api = PickerApi(port)
    window = webview.create_window(
        "SlowBooks Pro 2026",
        html=PICKER_HTML,
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    api.window = window
    try:
        webview.start()  # blocks until the window is closed
    finally:
        stop_server(api.server)
    return 0


# ---------------------------------------------------------------------------
# Headless mode (--no-window): for scripting and debugging
# ---------------------------------------------------------------------------


def run_headless(port: int) -> int:
    from app.services import company_service

    filename = company_service.get_last_opened()
    if filename is None:
        companies = company_service.manifest_list_companies()
        if companies:
            filename = companies[0]["file"]
        else:
            print("No companies yet — creating 'My Company'.")
            result = company_service.manifest_create_company("My Company")
            if not result["success"]:
                print(f"ERROR: {result['error']}")
                return 1
            filename = result["file"]

    print(f"Opening company file: {filename}")
    try:
        proc = launch_company(filename, port)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"SlowBooks Pro is running at http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server(proc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="prepare .env and data directories, then exit",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="start the server and print the URL instead of opening a window",
    )
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    prepare_env()
    # Pin the data dir for this process and every child (uvicorn, alembic),
    # so the app's company_service resolves the same location.
    os.environ["SLOWBOOKS_DATA_DIR"] = str(get_data_dir())

    if args.setup_only:
        print("Setup complete.")
        return 0

    port = args.port or int(get_env_value("APP_PORT") or "3001")

    if args.no_window:
        return run_headless(port)
    return run_window(port)


if __name__ == "__main__":
    sys.exit(main())
