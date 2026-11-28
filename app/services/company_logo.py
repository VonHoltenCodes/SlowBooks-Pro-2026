# ============================================================================
# The company's own logo in SlowBooks Pro (2.32.0).
#
# One setting, "Show the company logo in SlowBooks Pro" (company_logo_in_app,
# off by default, an administrator's, offered only when a logo is uploaded):
# on, the logo the company already uploaded in Settings shows in the top bar
# while signed in, on the sign-in screen, and beside the company in the lists
# of companies (Company Files, and the desktop app's company picker). Off,
# nothing anywhere changes.
#
# Signed in, the bar points at the logo's own address (/api/uploads/logo/<id>).
# Before a sign-in there is no session to fetch that with, so a company that
# turned the setting on has its logo served at PUBLIC_PATH (app/routes/auth.py)
# — the image and nothing else, the way the employee portal serves it. A
# company that has not opted in answers exactly as one with no logo.
# ============================================================================

import base64
import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from app.models.settings import Settings
from app.models.stored_files import KIND_LOGO, StoredFile
from app.services import file_store

logger = logging.getLogger(__name__)

SETTING = "company_logo_in_app"
PUBLIC_PATH = "/api/auth/company-logo"
_LOGO_PATH_KEY = "company_logo_path"
# The upload limit (app/routes/uploads.py): nothing larger is ever stored.
MAX_PICKER_LOGO_BYTES = 5 * 1024 * 1024


def _opted_in_logo_id(db: Session) -> int | None:
    """The stored logo's id when the company turned the setting on, else
    None. One read of both keys, so a company that has not opted in and
    one with no logo cost the same."""
    values = dict(
        db.query(Settings.key, Settings.value)
        .filter(Settings.key.in_((SETTING, _LOGO_PATH_KEY)))
        .all()
    )
    if (values.get(SETTING) or "").strip() != "true":
        return None
    return file_store.logo_id(values.get(_LOGO_PATH_KEY))


def shown_logo(db: Session) -> StoredFile | None:
    """The company's logo when it shows in the app: opted in, uploaded, its
    bytes kept and its type one a logo may be. Never raises."""
    try:
        file_id = _opted_in_logo_id(db)
        if file_id is None:
            return None
        row = file_store.logo_row(db, file_id)
    except Exception:  # a sign-in screen never fails over a logo
        logger.exception("Could not read the company logo setting")
        return None
    if row is None or row.missing or row.content_type not in file_store.LOGO_TYPES:
        return None
    return row


def public_url(row: StoredFile, filename: str | None = None) -> str:
    """The logo's address before a sign-in. ``filename`` names the company on
    a server that serves several (the name its sign-in screen already lists);
    ``v`` changes with each new upload."""
    query = {"file": filename, "v": row.id} if filename else {"v": row.id}
    return f"{PUBLIC_PATH}?{urlencode(query, quote_via=quote)}"


# --- The desktop app's company picker ---------------------------------------
# The picker is drawn before any company's server runs, so the launcher reads
# a company file itself: read-only, never an encrypted one (its logo is inside
# a file that is locked until the picker unlocks it), and only for a company
# that turned the setting on.


def logo_data_uri_from_file(path: Path) -> str:
    """The logo of the company in this SQLite file as a data: URI, or "".
    Never raises, never writes, never opens an encrypted file."""
    try:
        from app.services import company_file
        from app.services.legacy_uploads import _wal_mode

        path = Path(path)
        if not path.is_file() or company_file.is_encrypted_file(path):
            return ""
        # As legacy_uploads reads a company nobody has open: a WAL-mode file
        # with no -wal beside it is read as it lies (immutable), so nothing
        # is left beside it; any other file with its locks, read-only.
        idle_wal = _wal_mode(path) and not path.with_name(path.name + "-wal").exists()
        uri = path.resolve().as_uri() + (
            "?mode=ro&immutable=1" if idle_wal else "?mode=ro"
        )
        conn = company_file.sqlite_module().connect(uri, uri=True, timeout=1)
    except Exception:
        return ""
    try:
        values = dict(
            conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                (SETTING, _LOGO_PATH_KEY),
            ).fetchall()
        )
        if (values.get(SETTING) or "").strip() != "true":
            return ""
        file_id = file_store.logo_id(values.get(_LOGO_PATH_KEY))
        if file_id is None:
            return ""
        found = conn.execute(
            "SELECT content_type, data FROM stored_files "
            "WHERE id = ? AND kind = ? AND missing = 0",
            (file_id, KIND_LOGO),
        ).fetchone()
    except Exception:
        return ""
    finally:
        conn.close()
    if not found:
        return ""
    content_type, data = found
    if content_type not in file_store.LOGO_TYPES or not data:
        return ""
    if len(data) > MAX_PICKER_LOGO_BYTES:
        return ""
    return f"data:{content_type};base64,{base64.b64encode(bytes(data)).decode('ascii')}"
