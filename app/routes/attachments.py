# ============================================================================
# Attachments — file upload/download for invoices, bills, etc.
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.attachments import Attachment
from app.schemas.attachments import AttachmentResponse

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

UPLOAD_BASE = Path(__file__).parent.parent / "static" / "uploads" / "attachments"

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


@router.post("/{entity_type}/{entity_id}", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    entity_type: str,
    entity_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # Validate entity_type
    allowed_types = {"invoice", "bill", "estimate", "purchase_order", "vendor", "customer"}
    if entity_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid entity type. Allowed: {', '.join(allowed_types)}")

    # Sanitize filename and validate extension. Checking both extension and
    # Content-Type guards against Content-Type spoofing.
    safe_filename = Path(file.filename or "").name
    if not safe_filename or safe_filename.startswith('.'):
        raise HTTPException(status_code=400, detail="Invalid filename")
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

    # Create upload directory
    upload_dir = UPLOAD_BASE / entity_type / str(entity_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / safe_filename
    if not file_path.resolve().is_relative_to(upload_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")
    file_path.write_bytes(content)

    attachment = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        filename=safe_filename,
        file_path=str(file_path.relative_to(Path(__file__).parent.parent / "static")),
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

    static_base = (Path(__file__).parent.parent / "static").resolve()
    file_path = (static_base / attachment.file_path).resolve()
    if not file_path.is_relative_to(static_base):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.exists():
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

    # Delete file from disk
    file_path = Path(__file__).parent.parent / "static" / attachment.file_path
    if file_path.exists():
        file_path.unlink()

    db.delete(attachment)
    db.commit()
    return {"status": "deleted"}
