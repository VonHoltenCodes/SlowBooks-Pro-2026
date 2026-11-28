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
def get_companies(request: Request, db: Session = Depends(get_db)):
    companies = list_companies(db)
    if getattr(request.state, "token_principal", None):
        # A token was made in one company and works only there: it is told
        # of that company alone, never the names of the others this install
        # keeps.
        return [c for c in companies if c.get("is_current")]
    _add_logos(companies, db)
    return companies


def _add_logos(companies: list[dict], db: Session) -> None:
    """Each company's own logo on Company Files (2.32.0), as ``logo_url``,
    for a company that turned "Show the company logo in SlowBooks Pro" on;
    the key is absent for every other company, so the list reads as it did.
    The company in hand, and on a server serving several, each other company
    whose file is already open. Listing companies never opens, unlocks or
    upgrades a company file (as on origin/main, it reads only the list): a
    served company not open now shows no logo here, as the sign-in route
    would refuse it too; a desktop's other company files are not opened
    either (the company picker shows theirs)."""
    import app.database as db_module
    from app.database import use_company
    from app.services import company_logo, served_companies

    served = served_companies.enabled()
    for c in companies:
        filename = c.get("file") if served else None
        try:
            if c.get("is_current"):
                row = company_logo.shown_logo(db)
            elif (
                filename
                and served_companies.is_served(filename)
                and db_module.company_engine_is_open(filename)
            ):
                with use_company(filename):
                    other = db_module.SessionLocal()
                    try:
                        row = company_logo.shown_logo(other)
                    finally:
                        other.close()
            else:
                row = None
        except Exception:  # a company that cannot be opened now: no logo
            row = None
        if row is not None:
            c["logo_url"] = company_logo.public_url(row, filename)


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
