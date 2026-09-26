# ============================================================================
# Bank Feed Import — OFX/QFX + CSV file upload and import
# Feature 18: Upload → preview → confirm → auto-match by amount/date
# ============================================================================

import json
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.services.ofx_import import parse_ofx, import_transactions
from app.services.bank_csv_import import parse_csv, import_csv_transactions
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/bank-import", tags=["bank_import"])


@router.post("/preview")
async def preview_ofx(file: UploadFile = File(...)):
    """Parse OFX/QFX file and return preview of transactions."""
    content = await read_limited(file, label="Bank file")
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

    content = await read_limited(file, label="Bank file")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    transactions = parse_ofx(text)
    result = import_transactions(db, bank_account_id, transactions)
    return result


def _mapping(raw: Optional[str]) -> Optional[dict]:
    """The import dialog's column choices, sent as a JSON form field."""
    if not raw:
        return None
    try:
        mapping = json.loads(raw)
    except ValueError:
        mapping = None
    if not isinstance(mapping, dict):
        raise HTTPException(
            status_code=400,
            detail="The column choices could not be read. Choose them again.",
        )
    return mapping


@router.post("/preview-csv")
async def preview_csv(
    file: UploadFile = File(...), mapping: Optional[str] = Form(None)
):
    """Parse CSV bank statement and return preview of transactions. An
    unrecognised layout answers with its columns and a few rows, for the
    dialog's mapping step; `mapping` (JSON) is that step's answer."""
    content = await read_limited(file, label="CSV file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = parse_csv(text, _mapping(mapping))
    if result["error"]:
        out = {
            "format": result["format"],
            "error": result["error"],
            "count": 0,
            "transactions": [],
        }
        for key in ("header_row", "sample", "has_header", "suggested"):
            if key in result:
                out[key] = result[key]
        return out

    return {
        "format": result["format"],
        "unread": result.get("unread", 0),
        "count": len(result["transactions"]),
        "transactions": [
            {
                "date": (
                    t["date"].isoformat()
                    if hasattr(t["date"], "isoformat")
                    else str(t["date"])
                ),
                "amount": float(t["amount"]),
                "payee": t.get("payee", ""),
                "description": t.get("description", ""),
                "check_number": t.get("check_number"),
                "fee": float(t["fee"]) if t.get("fee") else None,
            }
            for t in result["transactions"]
        ],
    }


@router.post("/import-csv/{bank_account_id}")
async def import_csv(
    bank_account_id: int,
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Import CSV bank transactions into a bank account.

    Auto-detects format (Bank of America detail, Chase checking/credit,
    PayPal, or a header naming date / description / amount), or takes the
    dialog's column `mapping` (JSON) for a layout detection missed.
    Deduplicates by content-derived import_id (re-imports and overlapping
    exports skip; legitimate same-day duplicates still import).
    Auto-applies bank rules after import.
    """
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    content = await read_limited(file, label="CSV file")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = import_csv_transactions(
        db, bank_account_id, text, mapping=_mapping(mapping)
    )
    return result
