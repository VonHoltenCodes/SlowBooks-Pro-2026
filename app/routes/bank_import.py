# ============================================================================
# Bank Feed Import — OFX/QFX file upload and import
# Feature 18: Upload → preview → confirm → auto-match by amount/date
# ============================================================================

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.services.ofx_import import parse_ofx, import_transactions

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


@router.post("/preview")
async def preview_ofx(file: UploadFile = File(...)):
    """Parse OFX/QFX file and return preview of transactions."""
    content = await file.read()
    try:
        # Try UTF-8 first, fall back to latin-1
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    transactions = parse_ofx(text)
    return {
        "count": len(transactions),
        "transactions": [
            {
                "fitid": t.get("fitid", ""),
                "date": t["date"].isoformat(),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "memo": t.get("memo", ""),
            }
            for t in transactions
        ],
    }


@router.post("/import/{bank_account_id}")
async def import_ofx(
    bank_account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """Import OFX/QFX transactions into a bank account."""
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    transactions = parse_ofx(text)
    result = import_transactions(db, bank_account_id, transactions)
    return result
