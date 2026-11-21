# ============================================================================
# Trade packages (#550) — the bid packages a GC awards, mapped many-to-many
# to cost-code sections. A package is what a sub bids; a section is what costs
# post to. Bookkeeper-writable like cost codes. Nothing references a package
# yet, so DELETE just deletes; the is_active flag is kept for later steps.
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.services.terminology import said
from app.database import get_db
from app.models.cost_codes import CostCode
from app.models.trade_packages import TradePackage, TradePackageSection
from app.routes._helpers import reject_null_updates
from app.schemas.trade_packages import (
    TradePackageCreate,
    TradePackageResponse,
    TradePackageSectionOut,
    TradePackageUpdate,
)

router = APIRouter(prefix="/api/trade-packages", tags=["trade-packages"])


def _to_response(pkg: TradePackage) -> TradePackageResponse:
    # Sections sorted by code so every reader (list, picker) sees one order.
    codes = sorted(
        (m.cost_code for m in pkg.sections if m.cost_code is not None),
        key=lambda c: c.code,
    )
    return TradePackageResponse(
        id=pkg.id,
        name=pkg.name,
        notes=pkg.notes,
        is_active=pkg.is_active,
        sort_order=pkg.sort_order,
        section_ids=[c.id for c in codes],
        sections=[
            TradePackageSectionOut(id=c.id, code=c.code, label=c.label) for c in codes
        ],
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
    )


def _resolve_sections(db: Session, section_ids: list[int]) -> list[int]:
    """Validate that every id is a cost-code SECTION. A code that is not a
    section (a division, or a custom code) is rejected with 400 — costs post
    to sections, so only a section maps to a package."""
    resolved = []
    for sid in dict.fromkeys(section_ids):  # de-dupe, keep order
        code = db.get(CostCode, sid)
        if code is None:
            raise HTTPException(status_code=404, detail=f"Cost code {sid} not found")
        if code.level != "section":
            raise HTTPException(
                status_code=400,
                detail=f"Cost code '{code.code}' is not a section — "
                "only sections map to a trade package",
            )
        resolved.append(code.id)
    return resolved


def _set_sections(db: Session, pkg: TradePackage, section_ids: list[int]) -> None:
    ids = _resolve_sections(db, section_ids)
    db.query(TradePackageSection).filter(
        TradePackageSection.package_id == pkg.id
    ).delete()
    for cid in ids:
        db.add(TradePackageSection(package_id=pkg.id, cost_code_id=cid))
    db.flush()


def _get(db: Session, package_id: int) -> TradePackage:
    pkg = (
        db.query(TradePackage)
        .options(
            joinedload(TradePackage.sections).joinedload(TradePackageSection.cost_code)
        )
        .filter(TradePackage.id == package_id)
        .first()
    )
    if not pkg:
        raise HTTPException(status_code=404, detail="Trade package not found")
    return pkg


@router.get("", response_model=list[TradePackageResponse])
def list_trade_packages(include_inactive: bool = True, db: Session = Depends(get_db)):
    q = db.query(TradePackage).options(
        joinedload(TradePackage.sections).joinedload(TradePackageSection.cost_code)
    )
    if not include_inactive:
        q = q.filter(TradePackage.is_active.is_(True))
    rows = q.order_by(TradePackage.sort_order, TradePackage.name).all()
    return [_to_response(p) for p in rows]


@router.post("", response_model=TradePackageResponse, status_code=201)
def create_trade_package(data: TradePackageCreate, db: Session = Depends(get_db)):
    if db.query(TradePackage).filter(TradePackage.name.ilike(data.name)).first():
        raise HTTPException(
            status_code=409,
            detail=said("Trade package '{}' already exists", data.name),
        )
    pkg = TradePackage(
        name=data.name,
        notes=data.notes,
        is_active=data.is_active,
        sort_order=data.sort_order,
    )
    db.add(pkg)
    db.flush()
    if data.section_ids is not None:
        _set_sections(db, pkg, data.section_ids)
    db.commit()
    return _to_response(_get(db, pkg.id))


@router.put("/{package_id}", response_model=TradePackageResponse)
def update_trade_package(
    package_id: int, data: TradePackageUpdate, db: Session = Depends(get_db)
):
    pkg = _get(db, package_id)
    changes = reject_null_updates(
        TradePackage, data.model_dump(exclude_unset=True, exclude={"section_ids"})
    )
    if "name" in changes:
        clash = (
            db.query(TradePackage)
            .filter(TradePackage.name.ilike(changes["name"]))
            .first()
        )
        if clash and clash.id != pkg.id:
            raise HTTPException(
                status_code=409,
                detail=said("Trade package '{}' already exists", clash.name),
            )
    for key, value in changes.items():
        setattr(pkg, key, value)
    if data.section_ids is not None:
        _set_sections(db, pkg, data.section_ids)
    db.commit()
    return _to_response(_get(db, pkg.id))


@router.delete("/{package_id}")
def delete_trade_package(package_id: int, db: Session = Depends(get_db)):
    pkg = _get(db, package_id)
    db.delete(pkg)
    db.commit()
    return {"message": "Trade package deleted"}
