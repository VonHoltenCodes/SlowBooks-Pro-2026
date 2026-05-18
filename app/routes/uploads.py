# ============================================================================
# File Uploads — company logo and other file uploads
# Feature 15: Infrastructure D (UploadFile pattern, static/uploads/)
# ============================================================================

from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import Settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_DIR = Path(__file__).parent.parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Map of allowed content-type -> filename extension. Deriving the extension
# from the verified content-type instead of from the user-supplied filename
# prevents a renamed-file from landing on disk with a misleading suffix.
#
# SVG note: SVG can contain inline <script> tags. Since the company logo is
# uploaded by the admin and served back from /static/, in a multi-tenant or
# externally-exposed deployment this is an XSS vector. Followups: sanitize
# uploaded SVGs (bleach / svg-hush) or serve /static/ with a strict CSP.
_LOGO_EXT_BY_TYPE = {
    "image/png":     "png",
    "image/jpeg":    "jpg",
    "image/gif":     "gif",
    "image/webp":    "webp",
    "image/svg+xml": "svg",
}

_LOGO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — generous for a logo, blocks abuse


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = _LOGO_EXT_BY_TYPE.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="Logo must be a PNG, JPEG, GIF, WebP, or SVG image "
                   f"(got '{file.content_type or 'unknown'}').",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > _LOGO_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Logo is too large ({len(content) // 1024} KB). "
                   f"Maximum {_LOGO_MAX_BYTES // (1024 * 1024)} MB.",
        )

    filename = f"company_logo.{ext}"
    filepath = UPLOAD_DIR / filename
    filepath.write_bytes(content)

    # Save path to settings
    logo_path = f"/static/uploads/{filename}"
    row = db.query(Settings).filter(Settings.key == "company_logo_path").first()
    if row:
        row.value = logo_path
    else:
        db.add(Settings(key="company_logo_path", value=logo_path))
    db.commit()

    return {"path": logo_path, "message": "Logo uploaded successfully"}
