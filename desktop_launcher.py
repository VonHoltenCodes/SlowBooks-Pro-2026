#!/usr/bin/env python3
# ============================================================================
# Slowbooks Pro 2026 — Desktop Launcher
#
# One double-click to run Slowbooks Pro as a native desktop window on a
# machine that already has Docker Desktop (WSL2 backend) and Python.
#
# What it does:
#   1. Makes sure .env exists (copies .env.example if not).
#   2. Makes sure PAYROLL_ENCRYPTION_SECRET is a real, strong value — the
#      thing people trip on when the shipped dev default is refused by the
#      startup guard. Generated once and written back to .env.
#   3. Sets APP_DEBUG=true — correct for a single-machine, loopback-only
#      deploy. It only skips the HTTPS/TLS *production* gates that don't apply
#      to localhost; employee bank PII is still encrypted with the real key.
#   4. Brings the Docker Compose stack up.
#   5. Waits for the app's /health endpoint.
#   6. Opens a native window (pywebview / WebView2), not a browser tab.
#
# Usage:
#   python desktop_launcher.py            # normal: set up, start, open window
#   python desktop_launcher.py --no-window  # start stack but don't open a window
#   python desktop_launcher.py --setup-only # only fix up .env, then exit
# ============================================================================

import argparse
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Must match the shipped default in app/config.py — the value the startup
# guard in app/main.py rejects against a real database.
DEV_KEY = "slowbooks-dev-payroll-key-change-me"

HEALTH_TIMEOUT_SECONDS = 180
HEALTH_POLL_INTERVAL_SECONDS = 2


def _log(msg: str) -> None:
    print(f"[slowbooks] {msg}", flush=True)


# ---- .env handling ---------------------------------------------------------


def ensure_env_file() -> None:
    """Create .env from .env.example on first run."""
    if ENV_PATH.exists():
        return
    if not ENV_EXAMPLE_PATH.exists():
        _log("ERROR: neither .env nor .env.example found — is this the repo root?")
        sys.exit(1)
    shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    _log("Created .env from .env.example")


def _read_env_lines() -> list[str]:
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _get_env_value(lines: list[str], key: str) -> str | None:
    """Return the current value for KEY from .env lines, or None if unset.

    Ignores commented-out (`# KEY=...`) lines — only a live assignment counts.
    """
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):]
    return None


def _upsert_env(lines: list[str], key: str, value: str) -> list[str]:
    """Set KEY=value in .env lines, replacing a live assignment if present.

    Leaves commented example lines (`# KEY=`) untouched and appends a real
    assignment when none exists.
    """
    prefix = f"{key}="
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith(prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def _write_env_lines(lines: list[str]) -> None:
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_encryption_secret() -> None:
    """Guarantee PAYROLL_ENCRYPTION_SECRET is a real, strong value in .env."""
    lines = _read_env_lines()
    current = _get_env_value(lines, "PAYROLL_ENCRYPTION_SECRET")
    if current and current.strip() and current.strip() != DEV_KEY:
        _log("PAYROLL_ENCRYPTION_SECRET already set — leaving it as-is")
        return
    new_secret = secrets.token_urlsafe(32)
    lines = _upsert_env(lines, "PAYROLL_ENCRYPTION_SECRET", new_secret)
    _write_env_lines(lines)
    _log("Generated a strong PAYROLL_ENCRYPTION_SECRET and wrote it to .env")


def ensure_app_debug_true() -> None:
    """Set APP_DEBUG=true for the loopback-only desktop deploy."""
    lines = _read_env_lines()
    if _get_env_value(lines, "APP_DEBUG") == "true":
        return
    lines = _upsert_env(lines, "APP_DEBUG", "true")
    _write_env_lines(lines)
    _log("Set APP_DEBUG=true (loopback desktop mode)")


def get_app_port() -> int:
    """Read APP_PORT from .env, defaulting to 3001."""
    if not ENV_PATH.exists():
        return 3001
    value = _get_env_value(_read_env_lines(), "APP_PORT")
    if value and value.strip().isdigit():
        return int(value.strip())
    return 3001


# ---- Docker ----------------------------------------------------------------


def _compose_base_cmd() -> list[str]:
    """Return the working compose command (`docker compose` or `docker-compose`)."""
    if shutil.which("docker"):
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                cwd=REPO_ROOT,
                capture_output=True,
                check=True,
            )
            return ["docker", "compose"]
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    _log(
        "ERROR: Docker was not found. Install Docker Desktop and make sure it "
        "is running, then try again."
    )
    sys.exit(1)


def compose_up() -> None:
    _log("Starting Docker Compose stack (this can take a while the first time)...")
    cmd = _compose_base_cmd() + ["up", "-d", "--build"]
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        _log(
            "ERROR: `docker compose up` failed. Make sure Docker Desktop is "
            "running, then try again."
        )
        sys.exit(result.returncode)


# ---- Health polling --------------------------------------------------------


def wait_for_health(port: int) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    _log(f"Waiting for the app to come up at {url} ...")
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    _log("App is up.")
                    return True
        except Exception:
            pass
        time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
    _log(
        f"ERROR: app did not become healthy within {HEALTH_TIMEOUT_SECONDS}s. "
        "Check container logs with: docker compose logs -f slowbooks"
    )
    return False


# ---- Window ----------------------------------------------------------------


def open_window(port: int) -> None:
    url = f"http://127.0.0.1:{port}"
    try:
        import webview  # pywebview
    except ImportError:
        _log(
            "pywebview is not installed, so I can't open a native window. "
            "Install it with:  pip install -r requirements-desktop.txt"
        )
        _log(f"Opening {url} in your default browser instead.")
        import webbrowser

        webbrowser.open(url)
        return
    _log("Opening Slowbooks Pro in a desktop window...")
    webview.create_window("Slowbooks Pro 2026", url, width=1280, height=860)
    webview.start()


# ---- Main ------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch Slowbooks Pro as a desktop app.")
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Start the stack but do not open a desktop window.",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Only prepare .env (secret + APP_DEBUG), then exit without Docker.",
    )
    args = parser.parse_args()

    ensure_env_file()
    ensure_encryption_secret()
    ensure_app_debug_true()

    if args.setup_only:
        _log("Setup complete (.env prepared). Exiting without starting Docker.")
        return

    compose_up()
    port = get_app_port()
    if not wait_for_health(port):
        sys.exit(1)

    if args.no_window:
        _log(f"Stack is running at http://127.0.0.1:{port} (window suppressed).")
        return

    open_window(port)


if __name__ == "__main__":
    main()
