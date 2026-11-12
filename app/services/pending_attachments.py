# ============================================================================
# Attachments on a document not yet saved (#796).
#
# A file dropped on a new bill, expense or card charge has no record id to
# hang on yet. It is kept as a row of the company's own stored files (kind
# pending_attachment), addressed by a random id, until the form saves: the
# create call names the ids and the files become that document's
# attachments in the same database transaction (the same bytes, no copy).
# A form abandoned without saving binds nothing; its files are swept after
# PENDING_TTL_HOURS, on the next upload of one.
#
# A pending file is the sign-in's that uploaded it (a user, or an API
# token): the last eight characters of its id are a mark of that sign-in,
# so another sign-in that learns an id can neither bind nor discard it.
# ============================================================================

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.stored_files import KIND_PENDING_ATTACHMENT, StoredFile
from app.services import file_store

PENDING_TTL_HOURS = 24
# The most files one create call binds: a form's worth, not a bulk import.
MAX_PER_DOCUMENT = 20

_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

STALE = (
    "One of the files attached to this form is no longer waiting to be saved "
    "with it: a file attached before the first save is kept for "
    f"{PENDING_TTL_HOURS} hours. Remove it from the Attachments list, attach "
    "it again, then save."
)
TOO_MANY = (
    f"A document takes up to {MAX_PER_DOCUMENT} files attached before its "
    "first save. Save it, then attach the rest to the saved document."
)


def _aware(dt: datetime | None) -> datetime:
    """SQLite hands a stored UTC time back without its zone."""
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _owner_mark(db: Session) -> str:
    """Eight hex characters naming the sign-in this request is made under."""
    who = db.info.get("acting_username") or ""
    return hashlib.sha256(f"pending-attachment:{who}".encode()).hexdigest()[:8]


def expires_at(row: StoredFile) -> datetime:
    return _aware(row.created_at) + timedelta(hours=PENDING_TTL_HOURS)


def _expired(row: StoredFile, now: datetime) -> bool:
    return now > expires_at(row)


def sweep(db: Session) -> int:
    """Delete the pending files past their time. The caller commits."""
    now = datetime.now(timezone.utc)
    removed = 0
    for row in (
        db.query(StoredFile).filter(StoredFile.kind == KIND_PENDING_ATTACHMENT).all()
    ):
        if _expired(row, now):
            db.delete(row)
            removed += 1
    if removed:
        db.flush()
    return removed


def hold(
    db: Session, *, filename: str, content_type: str | None, data: bytes
) -> StoredFile:
    """Keep an upload until its document is saved. Sweeps the expired ones
    first. The caller commits."""
    sweep(db)
    pending_id = secrets.token_hex(12) + _owner_mark(db)
    return file_store.store(
        db, KIND_PENDING_ATTACHMENT, data, filename, content_type, token=pending_id
    )


def _own(db: Session, pending_id: str) -> StoredFile | None:
    """This sign-in's pending file with that id, expired or not."""
    if not isinstance(pending_id, str) or not _ID_RE.fullmatch(pending_id):
        return None
    if pending_id[-8:] != _owner_mark(db):
        return None
    return (
        db.query(StoredFile)
        .filter(
            StoredFile.kind == KIND_PENDING_ATTACHMENT,
            StoredFile.token == pending_id,
        )
        .first()
    )


def discard(db: Session, pending_id: str) -> None:
    """Drop this sign-in's pending file. Idempotent. The caller commits."""
    row = _own(db, pending_id)
    if row is not None:
        db.delete(row)


def take(db: Session, pending_ids: list[str] | None) -> list[StoredFile]:
    """The pending files a create call names, checked before anything is
    posted: each must be this sign-in's and still waiting, or the whole save
    is refused with a sentence and nothing is written."""
    ids = list(dict.fromkeys(pending_ids or []))
    if len(ids) > MAX_PER_DOCUMENT:
        raise HTTPException(status_code=400, detail=TOO_MANY)
    now = datetime.now(timezone.utc)
    rows = []
    for pending_id in ids:
        row = _own(db, pending_id)
        if row is None or _expired(row, now):
            raise HTTPException(status_code=400, detail=STALE)
        rows.append(row)
    return rows


def bind(db: Session, rows: list[StoredFile], entity_type: str, entity_id: int):
    """The pending files become the saved document's attachments, in the
    caller's transaction. The caller commits."""
    return [
        file_store.attach_stored(db, row, entity_type, entity_id, row.original_name)
        for row in rows
    ]
