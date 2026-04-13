# ============================================================================
# Tax Report Routes — Schedule C generation and tax mappings
# Feature 19: Generate Schedule C from P&L data
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.tax import TaxCategoryMapping
from app.models.accounts import Account
from app.schemas.tax import TaxMappingCreate, TaxMappingResponse

router = APIRouter(prefix="/api/tax", tags=["tax"])


SCHEDULE_C_DISABLED_DETAIL = (
    "Schedule C is disabled for SlowBooks NZ. "
    "NZ income-tax output has not yet been implemented."
)


@router.get("/schedule-c")
def schedule_c_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail=SCHEDULE_C_DISABLED_DETAIL)


@router.get("/schedule-c/csv")
def schedule_c_csv(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail=SCHEDULE_C_DISABLED_DETAIL)


@router.get("/mappings", response_model=list[TaxMappingResponse])
def list_mappings(db: Session = Depends(get_db)):
    mappings = db.query(TaxCategoryMapping).all()
    results = []
    for m in mappings:
        resp = TaxMappingResponse.model_validate(m)
        if m.account:
            resp.account_name = m.account.name
            resp.account_number = m.account.account_number
        results.append(resp)
    return results


@router.post("/mappings", response_model=TaxMappingResponse, status_code=201)
def create_mapping(data: TaxMappingCreate, db: Session = Depends(get_db)):
    existing = db.query(TaxCategoryMapping).filter(TaxCategoryMapping.account_id == data.account_id).first()
    if existing:
        existing.tax_line = data.tax_line
    else:
        existing = TaxCategoryMapping(account_id=data.account_id, tax_line=data.tax_line)
        db.add(existing)
    db.commit()
    db.refresh(existing)
    resp = TaxMappingResponse.model_validate(existing)
    acct = db.query(Account).filter(Account.id == existing.account_id).first()
    if acct:
        resp.account_name = acct.name
        resp.account_number = acct.account_number
    return resp
