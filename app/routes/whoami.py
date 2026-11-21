# ============================================================================
# Who am I — the company, role and token behind a request.
#
# A program holding tokens for several companies asks this before it keeps
# one, to be sure which company's books the token opens and under which
# role. It needs a sign-in or a token like any other read: it sits outside
# /api/auth/, which answers for anyone, so a token that matches nothing is
# refused here rather than described.
# ============================================================================

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._roles import current_role
from app.services import served_companies
from app.services.settings_service import get_setting_raw

router = APIRouter(prefix="/api/whoami", tags=["whoami"])


@router.get("")
def whoami(request: Request, db: Session = Depends(get_db)):
    """{"company_file", "company_name", "role", "label"}: the company file
    this request works in ("" for a database that is not a file, as on
    PostgreSQL), its name, the role the request carries, and the token's
    label (null for a signed-in person)."""
    principal = getattr(request.state, "token_principal", None)
    return {
        "company_file": served_companies.current_file() or "",
        "company_name": get_setting_raw(db, "company_name") or "",
        "role": current_role(request),
        "label": principal.get("label") if principal else None,
    }
