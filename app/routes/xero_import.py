from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.xero_import import XeroImportDryRunResponse, XeroImportExecuteResponse
from app.services.auth import require_permissions
from app.services.xero_import import detect_file_type, dry_run_import, execute_import

router = APIRouter(prefix="/api/xero-import", tags=["xero_import"])


def _load_files(files: list[UploadFile]) -> dict[str, tuple[str, str]]:
    file_map = {}
    for file in files:
        file_type = detect_file_type(file.filename or '')
        if not file_type:
            continue
        content_bytes = file.file.read()
        try:
            text = content_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content_bytes.decode('cp1252', errors='replace')
        file_map[file_type] = (file.filename, text)
    return file_map


@router.post('/dry-run', response_model=XeroImportDryRunResponse)
async def dry_run_xero_import(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions('accounts.manage')),
):
    return dry_run_import(_load_files(files))


@router.post('/import', response_model=XeroImportExecuteResponse)
async def import_xero_bundle(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions('accounts.manage')),
):
    return execute_import(db, _load_files(files))
