# ============================================================================
# Cost codes — CRUD for the job-costing chart, plus the standard (CSI
# MasterFormat) list loader. Codes referenced by posted lines are archived,
# never deleted. Bookkeeper-writable like classes and jobs.
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.accounts import Account
from app.models.bills import BillLine
from app.models.cost_codes import STANDARD_COST_CODES, CostCode
from app.models.transactions import TransactionLine
from app.schemas.cost_codes import CostCodeCreate, CostCodeResponse, CostCodeUpdate

router = APIRouter(prefix="/api/cost-codes", tags=["cost-codes"])


def _to_response(row: CostCode) -> CostCodeResponse:
    data = CostCodeResponse.model_validate(row)
    data.label = row.label
    data.account_name = row.account.name if row.account else None
    return data


def _get(db: Session, code_id: int) -> CostCode:
    row = (
        db.query(CostCode)
        .options(joinedload(CostCode.account))
        .filter(CostCode.id == code_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Cost code not found")
    return row


def _check_code_clash(db: Session, code: str, exclude_id=None):
    clash = db.query(CostCode).filter(CostCode.code.ilike(code)).first()
    if clash and clash.id != exclude_id:
        raise HTTPException(
            status_code=409, detail=f"Cost code '{clash.code}' already exists"
        )


@router.get("", response_model=list[CostCodeResponse])
def list_cost_codes(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(CostCode).options(joinedload(CostCode.account))
    if not include_inactive:
        q = q.filter(CostCode.is_active.is_(True))
    return [_to_response(r) for r in q.order_by(CostCode.code).all()]


@router.post("", response_model=CostCodeResponse, status_code=201)
def create_cost_code(data: CostCodeCreate, db: Session = Depends(get_db)):
    _check_code_clash(db, data.code)
    if data.account_id is not None and not db.get(Account, data.account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    row = CostCode(**data.model_dump())
    db.add(row)
    db.commit()
    return _to_response(_get(db, row.id))


@router.post("/standard", response_model=list[CostCodeResponse])
def load_standard_cost_codes(db: Session = Depends(get_db)):
    """Load the CSI MasterFormat divisions (plus Labor and Equipment
    Rental). Existing codes are left alone, so this is safe to repeat."""
    existing = {r.code.lower() for r in db.query(CostCode).all()}
    for code, name, cost_type in STANDARD_COST_CODES:
        if code.lower() in existing:
            continue
        db.add(CostCode(code=code, name=name, cost_type=cost_type))
    db.commit()
    return list_cost_codes(include_inactive=False, db=db)


@router.put("/{code_id}", response_model=CostCodeResponse)
def update_cost_code(code_id: int, data: CostCodeUpdate, db: Session = Depends(get_db)):
    row = _get(db, code_id)
    changes = data.model_dump(exclude_unset=True)
    if "code" in changes:
        _check_code_clash(db, changes["code"], row.id)
    if changes.get("account_id") is not None and not db.get(
        Account, changes["account_id"]
    ):
        raise HTTPException(status_code=404, detail="Account not found")
    for key, value in changes.items():
        setattr(row, key, value)
    db.commit()
    return _to_response(_get(db, row.id))


@router.delete("/{code_id}")
def delete_cost_code(code_id: int, db: Session = Depends(get_db)):
    row = _get(db, code_id)
    in_use = (
        db.query(TransactionLine.id)
        .filter(TransactionLine.cost_code_id == code_id)
        .first()
        or db.query(BillLine.id).filter(BillLine.cost_code_id == code_id).first()
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Cost code is used by posted lines — mark it inactive instead",
        )
    db.delete(row)
    db.commit()
    return {"message": "Cost code deleted"}
