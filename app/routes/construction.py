"""Construction mode — the one-time setup a contractor company runs.

Everything here is gated by Settings -> company_type = construction on the
SPA side; the API itself answers for any company (a business that calls
setup simply gets the contractor accounts and the standard cost codes).
Mirrors app/routes/nonprofit.py: idempotent, "nothing in your data
changes", no reverse path."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cost_codes import CostCode
from app.routes.cost_codes import load_standard_cost_codes
from app.schemas.accounts import AccountResponse
from app.services.accounting import CONSTRUCTION_ACCOUNTS, ensure_construction_accounts
from app.services.job_costing import ensure_default_cost_types

router = APIRouter(prefix="/api/construction", tags=["construction"])


@router.post("/setup", response_model=list[AccountResponse])
def setup(db: Session = Depends(get_db)):
    """Set a company up for construction: the contractor accounts
    (retainage, customer deposits, use tax, CIP, equipment and other job
    costs — numbers yield to an existing chart), the labor / material /
    subcontract / equipment / other cost types, and — only when the company
    has no cost codes yet — the standard CSI MasterFormat set. A company
    that has built its own cost-code chart keeps it untouched, so this is
    safe to call any number of times and changes no posting. The Settings
    page calls it when a company switches to construction.

    (Cost types aren't a toggle setting — they are seeded on demand — so
    "enable cost types" means ensure the five exist before the codes that
    reference them land.)"""
    accounts = ensure_construction_accounts(db)
    db.commit()
    ensure_default_cost_types(db)
    # Only seed the CSI divisions into a company with no codes of its own:
    # the loader skips matching code strings, but a company with a custom
    # chart should not have 26 CSI divisions appear beside it.
    if db.query(CostCode).count() == 0:
        load_standard_cost_codes(db)
    return [accounts[number] for number, _name, _type, _detail in CONSTRUCTION_ACCOUNTS]
