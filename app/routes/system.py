# ============================================================================
# System info + update check
#
# /api/system tells the frontend what it's running (version, desktop mode);
# /api/system/update-check compares against the published latest.json on
# dl.slowbookspro.com on desktop installs, unless the operator turns it off
# with SLOWBOOKS_UPDATE_CHECK=0.
#
# The check is proxied through the backend (not fetched from the browser)
# so the manifest host needs no CORS relationship with the app, and it is
# strictly best-effort: offline, DNS failure, bad JSON — anything at all —
# just means "no update available". Never an error in the user's face.
# ============================================================================

import os
import platform
import sys
import time

import httpx
from fastapi import APIRouter

from app import __version__, build_info

router = APIRouter(prefix="/api/system", tags=["system"])

LATEST_URL = "https://dl.slowbookspro.com/latest.json"
_CHECK_TTL_SECONDS = 12 * 60 * 60

# Process-lifetime cache: the desktop app runs for a session at a time, so
# one real fetch per half-day is plenty.
_cache: dict = {"at": 0.0, "result": None}

_NO_UPDATE = {"update_available": False}


def _is_desktop() -> bool:
    return os.environ.get("SLOWBOOKS_DESKTOP") == "1"


_OFF = ("0", "false", "no", "off")


def _update_check_enabled() -> bool:
    """On for desktop installs, so an install on an old version hears about
    the new one; SLOWBOOKS_UPDATE_CHECK=0 in .env turns it off (the request
    tells dl.slowbookspro.com the install's IP address and version)."""
    setting = os.environ.get("SLOWBOOKS_UPDATE_CHECK", "1").strip().lower()
    return _is_desktop() and setting not in _OFF


def _is_server_mode() -> bool:
    """True when the launcher is serving beyond loopback (--serve-lan)."""
    return os.environ.get("SLOWBOOKS_SERVER_MODE") == "1"


def _parse(v) -> tuple:
    """'2.1.0' (or 'v2.1.0') → (2, 1, 0) for comparison."""
    out = []
    for chunk in str(v).strip().lstrip("vV").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


def _this_macos() -> str:
    """The running macOS version ("15.1"), or "" anywhere else."""
    if sys.platform != "darwin":
        return ""
    return platform.mac_ver()[0] or ""


def runs_here(data: dict) -> bool:
    """Whether the published release can run on this machine. latest.json
    may name the lowest macOS it runs on (`macos_minimum`); a Mac below it
    is not offered an update it could not open. Anywhere else, or with no
    minimum named, every release runs."""
    minimum = str(data.get("macos_minimum", "") or "").strip()
    here = _this_macos()
    if not minimum or not here:
        return True
    return _parse(here) >= _parse(minimum)


@router.get("")
async def system_info():
    # The build identity (#816): About shows <version>+<sha>, the same
    # string the smoke test and Contact support's diagnostics carry.
    sha = build_info.build_sha()
    return {
        "version": __version__,
        "commit": sha,
        "build_version": build_info.version_string(__version__, sha or ""),
        "desktop": _is_desktop(),
        "server_mode": _is_server_mode(),
        "update_check_enabled": _update_check_enabled(),
    }


@router.get("/update-check")
async def update_check():
    # Guard the network sink as well as the UI: a direct API request must
    # never turn an installation with default settings into an update ping.
    if not _update_check_enabled():
        return _NO_UPDATE

    now = time.monotonic()
    if _cache["result"] is not None and now - _cache["at"] < _CHECK_TTL_SECONDS:
        return _cache["result"]

    result = _NO_UPDATE
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(
                LATEST_URL,
                headers={"User-Agent": f"SlowBooksPro/{__version__}"},
            )
            resp.raise_for_status()
            data = resp.json()
        remote = str(data.get("version", "")).strip()
        download_url = str(data.get("download_url", "")).strip()
        if (
            remote
            and download_url
            and is_newer(remote, __version__)
            and runs_here(data)
        ):
            result = {
                "update_available": True,
                "latest_version": remote,
                "download_url": download_url,
                "notes_url": str(data.get("notes_url", "")).strip(),
            }
    except Exception:  # noqa: BLE001 — best-effort by design (see header)
        result = _NO_UPDATE

    _cache["at"] = now
    _cache["result"] = result
    return result
