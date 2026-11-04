# ============================================================================
# File Uploads — the company logo, and the folder earlier versions shared
# Feature 15: Infrastructure D (UploadFile pattern)
#
# The logo is kept in the company's own database (app/services/file_store.py),
# not in the uploads folder every company on a desktop install shared: there
# it was one file, company_logo.<ext>, so one company's upload became every
# company's logo (2.18.0 gate, macbase1 NEW-14 and skytech).
#
# /legacy is that shared folder: an administrator sees what it still holds
# and removes it once every company has copied its files in
# (app/services/legacy_uploads.py).
# ============================================================================

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._roles import require_admin
from app.services import file_store, legacy_uploads

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
logger = logging.getLogger(__name__)

# Map of allowed content-type -> filename extension. Deriving the extension
# from the verified content-type instead of from the user-supplied filename
# means a renamed file is never stored or served under a misleading type.
#
# SVG note: SVG can contain inline <script>. The logo is only ever shown
# through <img> (which runs no script) or embedded in a PDF, and the route
# that serves it sends a sandboxing Content-Security-Policy, so opening its
# URL directly runs nothing either.
_LOGO_EXT_BY_TYPE = file_store.LOGO_TYPES

_LOGO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — generous for a logo, blocks abuse


DAMAGED_IMAGE = (
    "The image file is cut short or damaged; export it again and upload that."
)


def _refuse_damaged_image(content: bytes) -> None:
    """A raster logo is decoded whole before it is kept (2.31.5 gate
    NEW-201): a PNG cut short passed the type, empty and size checks, and
    every PDF that carries the logo then failed in WeasyPrint ("Truncated
    File Read"). SVG is not decoded here.

    verify() reads a PNG through to its end chunk and checks every chunk;
    it is what catches the cut-short PNG, since WeasyPrint turns on Pillow's
    LOAD_TRUNCATED_IMAGES for the whole process and load() alone then
    passes it. load() then decodes the pixels."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
        with Image.open(io.BytesIO(content)) as img:
            img.load()
    except Exception:
        raise HTTPException(status_code=400, detail=DAMAGED_IMAGE)


@router.post("/logo")
async def upload_logo(
    request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    # the logo is a company setting, and settings are the administrator's
    require_admin(request)
    content_type = (file.content_type or "").lower()
    if content_type not in _LOGO_EXT_BY_TYPE:
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

    if content_type != "image/svg+xml":
        _refuse_damaged_image(content)

    row = file_store.replace_logo(db, content, content_type)
    db.commit()
    return {"path": file_store.logo_url(row), "message": "Logo uploaded successfully"}


@router.get("/logo")
def logo_info(db: Session = Depends(get_db)):
    """The current logo's address and where it came from: Settings says so
    when the upgrade copied it in from the folder every company shared."""
    return file_store.logo_info(file_store.current_logo(db))


@router.delete("/logo")
def remove_logo(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    file_store.remove_logo(db)
    db.commit()
    return {"status": "deleted"}


@router.get("/logo/{file_id}")
def logo_image(file_id: int, db: Session = Depends(get_db)):
    """The logo image, from this company's own database."""
    return file_store.logo_response(db, file_store.logo_row(db, file_id))


@router.get("/legacy")
def legacy_folder(request: Request, db: Session = Depends(get_db)):
    """What the folder releases before 2.18.0 kept every company's uploads
    in still holds, and which companies still have to copy their files
    from it: {"files", "bytes", "pending_companies", "can_remove"}."""
    require_admin(request)
    return legacy_uploads.folder_state(db)


@router.delete("/legacy")
def remove_legacy_folder(request: Request, db: Session = Depends(get_db)):
    """Delete the files in that folder, once no company still needs them.
    Each company keeps its own copies in its own database."""
    require_admin(request)
    try:
        result = legacy_uploads.remove_files(db)
    except legacy_uploads.StillNeeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result["removed"]:
        # Who cleared it and when: the files were W-4s, I-9s and the rest.
        from app.services.audit import log_event

        try:
            log_event(
                db,
                table_name="shared_uploads",
                record_id=0,
                action="DELETE",
                new_values=result,
                source="admin",
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not record the shared folder's removal")
    return result
