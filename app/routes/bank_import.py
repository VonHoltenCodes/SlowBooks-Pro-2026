from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_permissions
from app.services.ofx_import import import_transactions, parse_ofx

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


@router.post("/preview")
async def preview_ofx(
    file: UploadFile = File(...),
    auth=Depends(require_permissions("banking.manage")),
):
    content = await file.read()
    return {"transactions": parse_ofx(content), "account_id": None}


@router.post("/import/{bank_account_id}")
async def import_ofx(
    bank_account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.manage")),
):
    content = await file.read()
    result = import_transactions(db, bank_account_id, parse_ofx(content))
    return {
        "imported": result["imported"],
        "skipped_duplicates": result["skipped"],
        "total": result["total"],
    }
