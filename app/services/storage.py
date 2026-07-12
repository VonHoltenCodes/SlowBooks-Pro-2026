# ============================================================================
# Storage roots — where user-generated files (uploads, attachments, backups)
# live on disk.
#
# Server installs keep the historical locations (app/static/uploads and
# <repo>/backups) so existing deployments, URLs, and volume mounts are
# untouched. Desktop installs set SLOWBOOKS_DATA_DIR (launcher-managed,
# per-user, e.g. %LOCALAPPDATA%\SlowBooksPro\data) because the install dir
# (Program Files) is read-only at runtime — everything writable is
# redirected under the data dir.
#
# Attachment paths stored in the database are relative to files_root() in
# BOTH modes ("uploads/attachments/..."), so a company database keeps
# working when moved between a desktop and a server install.
# ============================================================================

import os
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent  # app/


def files_root() -> Path:
    """Root for user-generated web-served files (logo uploads, attachments)."""
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    if override:
        return Path(override)
    return _APP_DIR / "static"


def uploads_root() -> Path:
    return files_root() / "uploads"


def backups_root() -> Path:
    """Root for database backup files."""
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    if override:
        return Path(override) / "backups"
    return _APP_DIR.parent / "backups"
