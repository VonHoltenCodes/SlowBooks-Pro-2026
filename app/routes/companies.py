# ============================================================================
# Multi-Company Routes — create and list company databases
# Feature 16: Company switching from UI
# ============================================================================

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import require_permissions
from app.services.company_service import create_company, list_companies

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    name: str
    database_name: str
    description: Optional[str] = None


@router.get("")
def get_companies(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("companies.view")),
):
    return list_companies(db)


@router.post("", status_code=201)
def new_company(
    data: CompanyCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("companies.manage")),
):
    result = create_company(db, data.name, data.database_name, data.description)
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result.get("public_error", "Failed to create company"),
        )
    return {
        "success": True,
        "company_id": result.get("company_id"),
        "database_name": result.get("database_name"),
    }
