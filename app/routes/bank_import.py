from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.bank_import import BankImportResult
from app.services.auth import require_permissions
from app.services.bank_import import import_ofx_file, preview_ofx_file

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


@router.post("/preview")
async def preview_ofx(
    file: UploadFile = File(...),
    auth=Depends(require_permissions("banking.manage")),
):
    content = await file.read()
    return preview_ofx_file(content)


@router.post("/import/{bank_account_id}")
async def import_ofx(
    bank_account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.manage")),
):
    content = await file.read()
    return import_ofx_file(db, bank_account_id, content)
