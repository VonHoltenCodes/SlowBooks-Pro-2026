# ============================================================================
# File store — a company's files, kept in its own database (stored_files).
#
# Every upload is a row of its own: the logo, attachments, employee
# documents and scanned receipts. Nothing is written to disk, so two
# companies can never land on one file, an updated "W-4.pdf" never replaces
# the first, and a backup of the company's database carries every file.
# ============================================================================

import base64
import hashlib
import logging
import re

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.models.attachments import Attachment
from app.models.stored_files import (
    KIND_ATTACHMENT,
    KIND_EMPLOYEE_DOCUMENT,
    KIND_LOGO,
    StoredFile,
)
from app.services.request_utils import content_disposition

logger = logging.getLogger(__name__)

# A served file is never a page: an SVG logo or an attached file opened
# straight from its URL runs no script and loads nothing (sandbox), whatever
# it holds. The app's own policy allows inline script, for the SPA.
SERVED_FILE_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
)

# What a person reads when the upgrade found nothing to copy.
MISSING_ATTACHMENT = (
    "This file was not in the shared folder when these books were upgraded, "
    "so there is nothing to download. Attach the file again."
)
MISSING_DOCUMENT = (
    "This document was not in the shared folder when these books were "
    "upgraded, so there is nothing to download. Upload it again."
)

# The company logo: the image types a logo may be, with the extension its
# name takes (from the verified type, never from the uploaded name).
LOGO_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}
LOGO_URL_PREFIX = "/api/uploads/logo/"
_LOGO_URL_RE = re.compile(r"\A/api/uploads/logo/(\d{1,12})\Z")


def store(
    db: Session,
    kind: str,
    data: bytes,
    original_name: str,
    content_type: str | None,
    token: str | None = None,
) -> StoredFile:
    """Keep ``data`` as a new row of this company's stored files."""
    row = StoredFile(
        kind=kind,
        original_name=(original_name or "file")[:255],
        content_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        token=token,
    )
    db.add(row)
    db.flush()
    return row


def stored_path(row: StoredFile) -> str:
    """What an attachment's file_path says now: the row of this company's
    stored files that holds it."""
    return f"stored_files/{row.id}"


def read_data(db: Session, stored_file_id: int | None) -> bytes | None:
    if stored_file_id is None:
        return None
    return (
        db.query(StoredFile.data)
        .filter(StoredFile.id == stored_file_id, StoredFile.missing.is_(False))
        .scalar()
    )


def delete_stored(db: Session, row: StoredFile | None) -> None:
    if row is not None:
        db.delete(row)


# --- Attachments and employee documents ------------------------------------


def add_attachment(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    filename: str,
    content_type: str | None,
    data: bytes,
    employee_id: int | None = None,
    doc_category: str | None = None,
) -> Attachment:
    """A new attachment (an employee document when ``employee_id`` is set)
    with its own stored file. Caller commits."""
    kind = KIND_EMPLOYEE_DOCUMENT if employee_id is not None else KIND_ATTACHMENT
    row = store(db, kind, data, filename, content_type)
    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        employee_id=employee_id,
        doc_category=doc_category,
        filename=filename,
        file_path=stored_path(row),
        mime_type=content_type,
        file_size=len(data),
        stored_file_id=row.id,
    )
    attachment.stored_file = row
    db.add(attachment)
    db.flush()
    return attachment


def attach_stored(
    db: Session, row: StoredFile, entity_type: str, entity_id: int, filename: str
) -> Attachment:
    """A file already kept here (a scanned receipt) becomes an attachment on
    a record: the same bytes, no copy. Caller commits."""
    row.kind = KIND_ATTACHMENT
    row.token = None
    row.original_name = filename[:255]
    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        filename=filename,
        file_path=stored_path(row),
        mime_type=row.content_type,
        file_size=row.size,
        stored_file_id=row.id,
    )
    attachment.stored_file = row
    db.add(attachment)
    db.flush()
    return attachment


def delete_attachment(db: Session, attachment: Attachment) -> None:
    """The attachment and its bytes, together. Caller commits."""
    stored = attachment.stored_file
    db.delete(attachment)
    # The row that points at the file goes first: PostgreSQL enforces the key.
    db.flush()
    delete_stored(db, stored)


def delete_record_attachments(db: Session, entity_type: str, entity_id: int) -> int:
    """Every file attached to one record, rows and bytes, for a route that
    deletes the record. SQLite hands a deleted record's id to the next
    one of its type, so files left behind would show on that record. A
    record that is voided or made inactive keeps its files: they are its
    history. Employee documents are never touched here. Caller commits."""
    rows = (
        db.query(Attachment)
        .filter(
            Attachment.entity_type == entity_type,
            Attachment.entity_id == entity_id,
            Attachment.employee_id.is_(None),
        )
        .all()
    )
    for attachment in rows:
        delete_attachment(db, attachment)
    return len(rows)


def attachment_response(
    db: Session, attachment: Attachment, missing_message: str = MISSING_ATTACHMENT
) -> Response:
    """The attachment's bytes, named as it was uploaded."""
    data = None if attachment.missing else read_data(db, attachment.stored_file_id)
    if data is None:
        raise HTTPException(status_code=404, detail=missing_message)
    return Response(
        content=data,
        media_type=attachment.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(
                attachment.filename, "attachment"
            ),
            "Content-Security-Policy": SERVED_FILE_CSP,
        },
    )


# --- The company logo ------------------------------------------------------


def logo_url(row: StoredFile) -> str:
    """The logo's address, which is also the company_logo_path setting.
    A new upload is a new row, so the address changes with the image."""
    return f"{LOGO_URL_PREFIX}{row.id}"


def logo_id(company_logo_path: str | None) -> int | None:
    """The stored file a company_logo_path setting names, or None for no
    logo (or a value that is not a stored logo's address)."""
    m = _LOGO_URL_RE.match((company_logo_path or "").strip())
    return int(m.group(1)) if m else None


def _logo_row(db: Session, file_id: int | None) -> StoredFile | None:
    if file_id is None:
        return None
    return (
        db.query(StoredFile)
        .filter(StoredFile.id == file_id, StoredFile.kind == KIND_LOGO)
        .first()
    )


def current_logo(db: Session) -> StoredFile | None:
    """The logo the company's settings point at, if it is one of its own."""
    from app.services.settings_service import get_setting_raw

    return _logo_row(db, logo_id(get_setting_raw(db, "company_logo_path")))


def logo_row(db: Session, file_id: int) -> StoredFile | None:
    """A logo by id (never another kind of file)."""
    return _logo_row(db, file_id)


def _drop_logos(db: Session) -> None:
    for row in db.query(StoredFile).filter(StoredFile.kind == KIND_LOGO).all():
        db.delete(row)
    db.flush()


def replace_logo(db: Session, data: bytes, content_type: str) -> StoredFile:
    """The company's logo is now this image; the one before it is deleted
    with its bytes. Caller commits."""
    from app.services.settings_service import set_setting

    _drop_logos(db)
    row = store(
        db, KIND_LOGO, data, f"company_logo.{LOGO_TYPES[content_type]}", content_type
    )
    set_setting(db, "company_logo_path", logo_url(row))
    return row


def remove_logo(db: Session) -> None:
    """No logo, and no logo bytes kept. Caller commits."""
    from app.services.settings_service import set_setting

    _drop_logos(db)
    set_setting(db, "company_logo_path", "")


def logo_info(row: StoredFile | None) -> dict:
    """What Settings needs to show the logo and say where it came from."""
    if row is None:
        return {
            "path": None,
            "filename": None,
            "content_type": None,
            "size": 0,
            "from_shared_folder": False,
            "missing": False,
        }
    return {
        "path": logo_url(row),
        "filename": row.original_name,
        "content_type": row.content_type,
        "size": row.size,
        "from_shared_folder": bool(row.from_shared_folder),
        "missing": bool(row.missing),
    }


def logo_response(db: Session, row: StoredFile | None) -> Response:
    data = None if row is None or row.missing else read_data(db, row.id)
    if data is None or row.content_type not in LOGO_TYPES:
        raise HTTPException(status_code=404, detail="No logo is set.")
    return Response(
        content=data,
        media_type=row.content_type,
        headers={"Content-Security-Policy": SERVED_FILE_CSP},
    )


def logo_data_uri(company_settings: dict) -> str:
    """The company logo as a data: URI for a PDF, or "" (never an error: a
    document prints without its logo rather than not at all).

    PDF code is handed the company's settings, not a session, so this reads
    through the app's own session factory: the database this process
    serves, which is the company whose settings these are."""
    file_id = logo_id((company_settings or {}).get("company_logo_path"))
    if file_id is None:
        return ""
    import app.database as db_module

    try:
        with db_module.SessionLocal() as db:
            row = (
                db.query(StoredFile.content_type, StoredFile.data)
                .filter(
                    StoredFile.id == file_id,
                    StoredFile.kind == KIND_LOGO,
                    StoredFile.missing.is_(False),
                )
                .first()
            )
    except Exception:
        logger.exception("Could not read the company logo")
        return ""
    if row is None or not row.data or row.content_type not in LOGO_TYPES:
        return ""
    return (
        f"data:{row.content_type};base64,{base64.b64encode(row.data).decode('ascii')}"
    )
