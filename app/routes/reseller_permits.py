# ============================================================================
# Reseller permit endpoints — CRUD + the "expiring soon" filter that
# powers the dashboard reminder. See app/models/reseller_permit.py for
# the model rationale.
# ============================================================================

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.reseller_permit import ResellerPermit

router = APIRouter(prefix="/api/reseller-permits", tags=["reseller-permits"])


# Lookup URLs for the official state verification pages. Each entry takes
# `{permit}` formatting in the URL template; if a state doesn't accept a
# pre-filled permit, we fall back to a generic landing page and the
# operator types the number manually.
def _normalize_permit_number(jurisdiction: str, raw: str) -> str:
    """Canonicalize a permit number before storage. Only fires when the
    digits-only form matches the state's expected length — otherwise the
    operator's input is preserved verbatim (legacy / non-standard IDs
    stay untouched). Prevents accidentally turning a test fixture like
    'SOON-1' into '1'."""
    raw = (raw or "").strip()
    jur = (jurisdiction or "").upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if jur == "WA" and len(digits) == 9:
        return digits
    if (
        jur == "CA"
        and 9 <= len(digits) <= 12
        and digits == raw.replace("-", "").replace(" ", "")
    ):
        return digits
    if jur == "TX" and len(digits) == 11:
        return digits
    return raw


def _validate_permit_format(jurisdiction: str, raw: str) -> tuple[bool, str]:
    """Soft-validate the permit format. Returns (ok, message).

    Returning ok=False does NOT block the write — operators with weird
    legacy permits would be stuck. Callers can choose to warn instead.
    """
    jur = (jurisdiction or "").upper()
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if jur == "WA":
        if len(digits) == 9:
            return True, "Format matches WA (9 digits)"
        return False, f"WA permits are 9 digits (you have {len(digits)})"
    if jur == "CA":
        if 9 <= len(digits) <= 12:
            return True, "Format matches CA"
        return False, f"CA permits are 9-12 digits (you have {len(digits)})"
    if jur == "TX":
        if len(digits) == 11:
            return True, "Format matches TX"
        return False, f"TX permits are 11 digits (you have {len(digits)})"
    return True, "No format rule on file for this state"


_LOOKUP_URLS: dict[str, str] = {
    # WA DoR's reseller permit verification — they accept the permit
    # number as a URL parameter. Free to use, no enrollment required.
    "WA": "https://secure.dor.wa.gov/gteunauth/_/#1",
    # CA CDTFA seller's permit verification:
    "CA": "https://onlineservices.cdtfa.ca.gov/_/",
    # NY State Department of Taxation and Finance — Sales Tax ID lookup:
    "NY": "https://www7b.tax.ny.gov/STLR/",
    # TX Comptroller — Sales Tax permit search:
    "TX": "https://mycpa.cpa.state.tx.us/staxpayersearch/",
    # FL DoR — Annual Resale Certificate verification:
    "FL": "https://floridarevenue.com/taxes/AVT/Pages/AVT.aspx",
}


class ResellerPermitIn(BaseModel):
    entity_type: str  # "customer" / "vendor" / "company"
    entity_id: Optional[int] = None
    jurisdiction: str
    permit_number: str
    issued_at: Optional[date] = None
    expires_at: Optional[date] = None
    notes: Optional[str] = None
    is_active: bool = True


class ResellerPermitOut(BaseModel):
    id: int
    entity_type: str
    entity_id: Optional[int]
    jurisdiction: str
    permit_number: str
    issued_at: Optional[date]
    expires_at: Optional[date]
    last_verified_at: Optional[datetime]
    verified_by: Optional[str]
    notes: Optional[str]
    is_active: bool
    # Computed fields the SPA wants for display:
    days_to_expire: Optional[int] = None
    is_expired: bool = False
    verification_url: Optional[str] = None

    model_config = {"from_attributes": True}


def _enrich(permit: ResellerPermit) -> ResellerPermitOut:
    """Compute the display-only fields the SPA + dashboard depend on."""
    today = date.today()
    days_to_expire = None
    is_expired = False
    if permit.expires_at:
        days_to_expire = (permit.expires_at - today).days
        is_expired = permit.expires_at < today

    # Pre-fill the state's lookup URL with the permit number if we know
    # how. Otherwise leave the operator to type it in.
    template = _LOOKUP_URLS.get((permit.jurisdiction or "").upper())
    verification_url = None
    if template:
        if "{permit}" in template:
            verification_url = template.format(permit=quote_plus(permit.permit_number))
        else:
            verification_url = template

    return ResellerPermitOut(
        id=permit.id,
        entity_type=permit.entity_type,
        entity_id=permit.entity_id,
        jurisdiction=permit.jurisdiction,
        permit_number=permit.permit_number,
        issued_at=permit.issued_at,
        expires_at=permit.expires_at,
        last_verified_at=permit.last_verified_at,
        verified_by=permit.verified_by,
        notes=permit.notes,
        is_active=permit.is_active,
        days_to_expire=days_to_expire,
        is_expired=is_expired,
        verification_url=verification_url,
    )


@router.get("/validate-format")
def validate_format(
    jurisdiction: str = Query(...),
    permit_number: str = Query(...),
):
    """Lightweight format check. Same rules as the SPA's inline hint, but
    callable from scripts or third-party tooling that wants the canonical
    answer."""
    ok, msg = _validate_permit_format(jurisdiction, permit_number)
    return {
        "jurisdiction": jurisdiction.upper(),
        "permit_number": permit_number,
        "normalized": _normalize_permit_number(jurisdiction, permit_number),
        "ok": ok,
        "message": msg,
    }


@router.get("", response_model=list[ResellerPermitOut])
def list_permits(
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[int] = Query(default=None),
    jurisdiction: Optional[str] = Query(default=None),
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """List permits with optional filters. Most common SPA call is
    `?entity_type=customer&entity_id=X` to show the customer's permits."""
    q = db.query(ResellerPermit)
    if entity_type:
        q = q.filter(ResellerPermit.entity_type == entity_type)
    if entity_id is not None:
        q = q.filter(ResellerPermit.entity_id == entity_id)
    if jurisdiction:
        q = q.filter(ResellerPermit.jurisdiction == jurisdiction.upper())
    if active_only:
        q = q.filter(ResellerPermit.is_active == True)  # noqa: E712
    return [_enrich(p) for p in q.order_by(ResellerPermit.expires_at.asc()).all()]


@router.get("/expiring", response_model=list[ResellerPermitOut])
def expiring_permits(
    within_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Permits expiring within N days — powers the dashboard reminder.

    Already-expired permits are included so the operator sees them too;
    they sort to the front because their `days_to_expire` is negative.
    """
    cutoff = date.today() + timedelta(days=within_days)
    rows = (
        db.query(ResellerPermit)
        .filter(ResellerPermit.is_active == True)  # noqa: E712
        .filter(ResellerPermit.expires_at.isnot(None))
        .filter(ResellerPermit.expires_at <= cutoff)
        .order_by(ResellerPermit.expires_at.asc())
        .all()
    )
    return [_enrich(p) for p in rows]


@router.get("/{permit_id}", response_model=ResellerPermitOut)
def get_permit(permit_id: int, db: Session = Depends(get_db)):
    permit = db.query(ResellerPermit).filter(ResellerPermit.id == permit_id).first()
    if not permit:
        raise HTTPException(status_code=404, detail="Permit not found")
    return _enrich(permit)


@router.post("", response_model=ResellerPermitOut, status_code=201)
def create_permit(data: ResellerPermitIn, db: Session = Depends(get_db)):
    if data.entity_type not in ("customer", "vendor", "company"):
        raise HTTPException(
            status_code=400,
            detail="entity_type must be customer, vendor, or company",
        )
    jur = (data.jurisdiction or "").upper().strip()
    permit = ResellerPermit(
        entity_type=data.entity_type,
        entity_id=data.entity_id,
        jurisdiction=jur,
        permit_number=_normalize_permit_number(jur, data.permit_number),
        issued_at=data.issued_at,
        expires_at=data.expires_at,
        notes=data.notes,
        is_active=data.is_active,
    )
    db.add(permit)
    db.commit()
    db.refresh(permit)
    return _enrich(permit)


@router.put("/{permit_id}", response_model=ResellerPermitOut)
def update_permit(
    permit_id: int, data: ResellerPermitIn, db: Session = Depends(get_db)
):
    permit = db.query(ResellerPermit).filter(ResellerPermit.id == permit_id).first()
    if not permit:
        raise HTTPException(status_code=404, detail="Permit not found")
    permit.entity_type = data.entity_type
    permit.entity_id = data.entity_id
    permit.jurisdiction = (data.jurisdiction or "").upper().strip()
    permit.permit_number = _normalize_permit_number(
        permit.jurisdiction, data.permit_number
    )
    permit.issued_at = data.issued_at
    permit.expires_at = data.expires_at
    permit.notes = data.notes
    permit.is_active = data.is_active
    db.commit()
    db.refresh(permit)
    return _enrich(permit)


@router.delete("/{permit_id}")
def delete_permit(permit_id: int, db: Session = Depends(get_db)):
    permit = db.query(ResellerPermit).filter(ResellerPermit.id == permit_id).first()
    if not permit:
        raise HTTPException(status_code=404, detail="Permit not found")
    db.delete(permit)
    db.commit()
    return {"deleted": permit_id}


class VerificationStamp(BaseModel):
    verified_by: Optional[str] = None


@router.post("/{permit_id}/mark-verified", response_model=ResellerPermitOut)
def mark_verified(
    permit_id: int,
    data: VerificationStamp,
    db: Session = Depends(get_db),
):
    """Record that the operator just verified this permit against the
    official state lookup. Stamps `last_verified_at = now` and saves the
    free-form `verified_by` note (operator name, ticket number, etc.)."""
    permit = db.query(ResellerPermit).filter(ResellerPermit.id == permit_id).first()
    if not permit:
        raise HTTPException(status_code=404, detail="Permit not found")
    permit.last_verified_at = datetime.now(timezone.utc)
    permit.verified_by = (data.verified_by or "").strip() or None
    db.commit()
    db.refresh(permit)
    return _enrich(permit)
