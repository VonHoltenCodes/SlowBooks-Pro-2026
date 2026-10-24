# ============================================================================
# Backup/Restore Routes — accessible from settings page
# Feature 11: Create, list, download, restore backups
# ============================================================================


import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.schemas.common import StrictModel
from typing import Optional

from app.database import get_db
from app.routes._roles import may_download_backups
from app.services import backup_service
from app.services.backup_service import (
    PRE_RESTORE_TAG,
    create_backup,
    restore_backup,
)

router = APIRouter(prefix="/api/backups", tags=["backups"])
logger = logging.getLogger(__name__)


class BackupCreate(StrictModel):
    notes: Optional[str] = None


class RestoreRequest(StrictModel):
    filename: str
    # A backup that belongs to another company is refused (409, code
    # "other_company") unless the person has confirmed it a second time.
    allow_other_company: bool = False
    # A backup encrypted under another key (409, code "other_key") is
    # opened with the recovery key it was made under (#290).
    recovery_key: Optional[str] = None


@router.get("")
def list_backups(db: Session = Depends(get_db)):
    """This company's backups whose files still exist on disk. The folder is
    shared by every company on the machine; another company's backups are
    not listed (explore 2.17.3, macbase1 F25)."""
    return backup_service.list_company_backups(db)


@router.post("")
def make_backup(data: BackupCreate = BackupCreate(), db: Session = Depends(get_db)):
    result = create_backup(db, notes=data.notes)
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "Backup failed")
        )
    return result


def _company_backup_path(db: Session, filename: str):
    """A path for one of THIS company's listed backups, else a 404. The
    listing is the system of record for what may be served;
    backup_path() (basename + allow-list + normpath containment) is the
    sanitizer static analyzers recognise (CodeQL py/path-injection)."""
    filepath = backup_service.backup_path(filename)
    if filepath is None:
        raise HTTPException(status_code=400, detail="Invalid filename")
    listed = {b["filename"] for b in backup_service.list_company_backups(db)}
    if filepath.name not in listed or not filepath.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    return filepath


@router.get("/download/{filename}")
def download_backup(filename: str, request: Request, db: Session = Depends(get_db)):
    # A backup is the whole company: every record, the sign-ins' password
    # hashes, the encrypted keys, and the payroll and HR records only an
    # administrator may read. Any role could download it (2.18.0 gate: a
    # read-only sign-in took the whole database). An API token an
    # administrator let make backups may take its copy too (#434).
    if not may_download_backups(request):
        raise HTTPException(status_code=403, detail="Admin role required")
    filepath = _company_backup_path(db, filename)
    # The whole company left the server: the audit log says who took it
    # ("token:<label>" for a program, #434).
    from app.services.audit import log_event

    log_event(
        db,
        table_name="backups",
        record_id=0,
        action="EXPORT",
        new_values={"filename": filepath.name},
    )
    db.commit()
    return FileResponse(
        str(filepath), filename=filepath.name, media_type="application/octet-stream"
    )


@router.post("/restore")
def restore(data: RestoreRequest, db: Session = Depends(get_db)):
    """Replace this company's books with a backup.

    Refused, with nothing changed: a name that is not a backup file (400),
    a file that is not there (404), another company's backup unless
    confirmed again (409 "other_company" — every company's backups share
    one folder, and this used to copy any of them over the open company),
    and a copy made by a newer version this one cannot open (409
    "newer_version"). Otherwise a safety backup of the books as they are is
    taken first, and named in the answer, so a restore can be undone."""
    filepath = backup_service.backup_path(data.filename)
    if filepath is None:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filepath.exists():
        raise HTTPException(
            status_code=404, detail=f"Backup file not found: {filepath.name}"
        )

    other = backup_service.other_company(db, filepath.name)
    if other and not data.allow_other_company:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "other_company",
                "message": (
                    f"This backup holds the books of {other['backup_company']}, "
                    f"not {other['current_company']}. Restoring it would replace "
                    f"{other['current_company']}'s books with it. (If you renamed "
                    "this company since the backup was made, it is yours.)"
                ),
                **other,
            },
        )
    newer = backup_service.backup_revision_problem(filepath.name)
    if newer:
        raise HTTPException(
            status_code=409, detail={"code": "newer_version", "message": newer}
        )
    locked = backup_service.backup_key_problem(filepath.name, data.recovery_key)
    if locked:
        raise HTTPException(status_code=409, detail=locked)

    # Restore overwrites the entire database — leave a breadcrumb BEFORE the
    # operation. The audit_log itself may be replaced by the restore, so this
    # row records intent in the CURRENT (pre-restore) DB; pair it with the
    # backup-file's own contents for the full picture. Committed immediately
    # so it survives even if the restore process aborts mid-way — and so the
    # safety backup below carries it.
    from app.services.audit import log_event

    log_event(
        db,
        table_name="backups",
        record_id=0,
        action="RESTORE",
        new_values={"filename": filepath.name},
        source="admin",
    )
    db.commit()

    safety = create_backup(
        db,
        notes=f"Taken automatically just before restoring {filepath.name}",
        backup_type="pre-restore",
        tag=PRE_RESTORE_TAG,
    )
    if not safety.get("success"):
        raise HTTPException(
            status_code=500,
            detail=(
                "Nothing was restored: a safety backup of the books as they are "
                "could not be made first. " + str(safety.get("error") or "")
            ).strip(),
        )

    result = restore_backup(db, filepath.name, data.recovery_key)
    if not result.get("success"):
        # Map the service's error string to the right HTTP code so a missing
        # backup file is a 404 (operator can fix it) and a bad name is a 400,
        # rather than every failure looking like an unhelpful 500.
        err = result.get("error", "Restore failed")
        if "Invalid filename" in err:
            status_code = 400
        elif "not found" in err.lower():
            status_code = 404
        else:
            status_code = 500
            # A copy that failed part-way must not be left in place: put the
            # books back from the safety copy, and say where it is either way.
            restore_backup(db, safety["filename"])
            err = (
                f"{err} Your books as they were are in the safety backup "
                f"{safety['filename']}."
            )
        raise HTTPException(status_code=status_code, detail=err)

    upgraded = backup_service.bring_restored_books_up_to_date()
    if not upgraded.get("success"):
        # Put the books back as they were rather than leave a file this
        # version cannot use.
        restore_backup(db, safety["filename"])
        raise HTTPException(
            status_code=500,
            detail=(
                f"{upgraded.get('error')} Your books are as they were before the "
                f"restore (safety backup {safety['filename']})."
            ),
        )
    # The books now carry the backup's company name. The company list (the
    # picker) kept the name from before the restore, so a backup made before
    # a rename left the two disagreeing until the next start (2.18.0 gate,
    # skytech N4). Same rule as a rename in Settings: the books' name wins.
    restored_name = backup_service.read_backup_facts(filepath.name).get("company_name")
    if restored_name:
        from app.services.company_service import sync_manifest_name

        try:
            sync_manifest_name(restored_name)
        except OSError:
            # the books are restored; the list catches up at the next start
            logger.exception("Could not rename the company list entry")
    return {**result, "safety_backup": safety["filename"]}
