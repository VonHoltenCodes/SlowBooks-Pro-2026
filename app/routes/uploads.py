# ============================================================================
# File Uploads — company logo and other file uploads
# Feature 15: Infrastructure D (UploadFile pattern, static/uploads/)
# ============================================================================

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import Settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_DIR = Path(__file__).parent.parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif"}


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only PNG, JPEG, GIF, or SVG images are allowed")

    # Save file
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "png"
    filename = f"company_logo.{ext}"
    filepath = UPLOAD_DIR / filename

    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Save path to settings
    logo_path = f"/static/uploads/{filename}"
    row = db.query(Settings).filter(Settings.key == "company_logo_path").first()
    if row:
        row.value = logo_path
    else:
        db.add(Settings(key="company_logo_path", value=logo_path))
    db.commit()

    return {"path": logo_path, "message": "Logo uploaded successfully"}
