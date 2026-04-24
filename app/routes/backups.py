# ============================================================================
# Backup/Restore Routes — accessible from settings page
# Feature 11: Create, list, download, restore backups
# ============================================================================

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.backups import Backup
from app.services.backup_service import create_backup, restore_backup, list_backup_files, BACKUP_DIR

router = APIRouter(prefix="/api/backups", tags=["backups"])


class BackupCreate(BaseModel):
    notes: Optional[str] = None


class RestoreRequest(BaseModel):
    filename: str


@router.get("")
def list_backups(db: Session = Depends(get_db)):
    """List only backups whose files still exist on disk."""
    db_backups = db.query(Backup).order_by(Backup.created_at.desc()).all()
    return [
        {"id": b.id, "filename": b.filename, "file_size": b.file_size,
         "backup_type": b.backup_type, "notes": b.notes,
         "created_at": b.created_at.isoformat() if b.created_at else None}
        for b in db_backups
        if (BACKUP_DIR / b.filename).exists()
    ]


@router.post("")
def make_backup(data: BackupCreate = BackupCreate(), db: Session = Depends(get_db)):
    result = create_backup(db, notes=data.notes)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Backup failed"))
    return result


@router.get("/download/{filename}")
def download_backup(filename: str):
    filepath = (BACKUP_DIR / filename).resolve()
    if not filepath.is_relative_to(BACKUP_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    return FileResponse(str(filepath), filename=filepath.name, media_type="application/octet-stream")


@router.post("/restore")
def restore(data: RestoreRequest, db: Session = Depends(get_db)):
    # Validate filename to prevent path traversal
    filepath = (BACKUP_DIR / data.filename).resolve()
    if not filepath.is_relative_to(BACKUP_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    result = restore_backup(db, filepath.name)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Restore failed"))
    return result
