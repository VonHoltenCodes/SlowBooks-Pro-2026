# ============================================================================
# Multi-Company Routes — create and list company databases
# Feature 16: Company switching from UI
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.schemas.common import StrictModel
from typing import Optional

from app.database import get_db
from app.routes._roles import require_admin
from app.services.company_service import (
    create_company,
    create_sample_company,
    list_companies,
)

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(StrictModel):
    name: str
    # Postgres (server) installs only — desktop/SQLite installs derive the
    # company's .db filename from the name.
    database_name: Optional[str] = None
    description: Optional[str] = None


@router.get("")
def get_companies(db: Session = Depends(get_db)):
    return list_companies(db)


@router.post("", status_code=201)
def new_company(data: CompanyCreate, db: Session = Depends(get_db)):
    result = create_company(db, data.name, data.database_name, data.description)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "Failed to create company")
        )
    return result


@router.post("/sample", status_code=201)
def new_sample_company(request: Request, db: Session = Depends(get_db)):
    """Try a sample company (#684): Henry Brown's Auto Body Shop from IRS
    Publication 583, in a company file of its own. An administrator's, like
    + New Company; the company this request works in is not touched."""
    require_admin(request)
    result = create_sample_company(db)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Failed to create the sample company"),
        )
    return result
