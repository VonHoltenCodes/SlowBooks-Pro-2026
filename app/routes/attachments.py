# ============================================================================
# Attachments — file upload/download for invoices, bills, etc.
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.schemas.attachments import AttachmentResponse

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

STATIC_BASE = (Path(__file__).parent.parent / "static").resolve()
UPLOAD_BASE = (STATIC_BASE / "uploads" / "attachments").resolve()

# Map validated entity type -> directory name. Routing the user-provided
# entity_type through this dict (instead of using the raw string) gives
# CodeQL an unambiguous sanitization point for the path-injection taint flow.
_ENTITY_TYPE_DIRS = {
    "invoice": "invoice",
    "bill": "bill",
    "estimate": "estimate",
    "purchase_order": "purchase_order",
    "vendor": "vendor",
    "customer": "customer",
}

# Whitelist of MIME types we accept for attachments. Attachments are served
# back from /static/ so we reject anything that a browser would render and
# potentially execute (HTML, SVG with scripts, executables).
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}
ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".zip",
}

# Only plain word chars, spaces, hyphens, dots, and parens in filenames.
# Everything else gets rewritten out before the path is used. This is belt-
# and-braces on top of Path(...).name stripping any directory components.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._()\-]")


def _sanitize_filename(raw: str) -> str:
    """Strip path separators and restrict to a safe character set."""
    base = Path(raw or "").name
    if not base or base.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip()
    # Belt and braces: even after Path(...).name, reject anything that still
    # contains path separators or parent refs.
    if not cleaned or cleaned.startswith(".") or "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return cleaned


def _resolve_within(base: Path, *parts: str) -> Path:
    """Join parts onto base, resolve, and assert the result is inside base.

    Raises HTTPException(400) on any attempt to escape via .., symlinks, or
    absolute-path segments.
    """
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


@router.post("/{entity_type}/{entity_id}", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    entity_type: str,
    entity_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # Route entity_type through the whitelist map. `type_dir` is known-safe
    # static data from here on — user input no longer reaches the filesystem.
    type_dir = _ENTITY_TYPE_DIRS.get(entity_type)
    if type_dir is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entity type. Allowed: {', '.join(sorted(_ENTITY_TYPE_DIRS))}",
        )

    # entity_id is already an int from FastAPI path-parameter validation, so
    # str(entity_id) is only digits — safe to use as a path segment.
    safe_filename = _sanitize_filename(file.filename or "")
    extension = Path(safe_filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{extension}' not allowed",
        )
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"MIME type '{file.content_type}' not allowed",
        )

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    # Build and verify paths against the upload base BEFORE any filesystem op.
    upload_dir = _resolve_within(UPLOAD_BASE, type_dir, str(entity_id))
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = _resolve_within(upload_dir, safe_filename)
    file_path.write_bytes(content)

    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        filename=safe_filename,
        file_path=str(file_path.relative_to(STATIC_BASE)),
        mime_type=file.content_type,
        file_size=len(content),
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


@router.get("/{entity_type}/{entity_id}", response_model=list[AttachmentResponse])
def list_attachments(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Attachment)
        .filter(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
        .order_by(Attachment.uploaded_at.desc())
        .all()
    )


@router.get("/download/{attachment_id}")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = _resolve_within(STATIC_BASE, attachment.file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        str(file_path),
        filename=attachment.filename,
        media_type=attachment.mime_type or "application/octet-stream",
    )


@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = _resolve_within(STATIC_BASE, attachment.file_path)
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass  # DB row present but file already gone — delete the row anyway

    db.delete(attachment)
    db.commit()
    return {"status": "deleted"}
