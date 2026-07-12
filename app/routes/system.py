# ============================================================================
# System info + update check
#
# /api/system tells the frontend what it's running (version, desktop mode);
# /api/system/update-check compares against the published latest.json on
# dl.slowbookspro.com and powers the footer "Update available" badge on
# desktop installs — same pattern as EasyAmp's footer badge.
#
# The check is proxied through the backend (not fetched from the browser)
# so the manifest host needs no CORS relationship with the app, and it is
# strictly best-effort: offline, DNS failure, bad JSON — anything at all —
# just means "no update available". Never an error in the user's face.
# ============================================================================

import os
import time

import httpx
from fastapi import APIRouter

from app import __version__

router = APIRouter(prefix="/api/system", tags=["system"])

LATEST_URL = "https://dl.slowbookspro.com/latest.json"
_CHECK_TTL_SECONDS = 12 * 60 * 60

# Process-lifetime cache: the desktop app runs for a session at a time, so
# one real fetch per half-day is plenty.
_cache: dict = {"at": 0.0, "result": None}

_NO_UPDATE = {"update_available": False}


def _is_desktop() -> bool:
    return os.environ.get("SLOWBOOKS_DESKTOP") == "1"


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


@router.get("")
async def system_info():
    return {"version": __version__, "desktop": _is_desktop()}


@router.get("/update-check")
async def update_check():
    if not _is_desktop():
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
        if remote and download_url and is_newer(remote, __version__):
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
