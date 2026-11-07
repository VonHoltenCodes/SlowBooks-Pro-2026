# ============================================================================
# Cost codes — CRUD for the job-costing chart, plus the standard catalog
# loader (division titles + common sections). Typed codes are normalized to
# the "DD SS SS" division/section shape on save and import, a section is
# auto-parented to its division, and the level is derived from the shape.
# Codes referenced by posted lines are archived, never deleted.
# Bookkeeper-writable like classes and jobs.
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.routes._helpers import reject_null_updates
from app.models.accounts import Account
from app.models.bills import BillLine
from app.models.cost_codes import CostCode
from app.models.transactions import (
    LEDGER_BASIS_OPTION,
    READ_ALL,
    TransactionLine,
)
from app.schemas.cost_codes import CostCodeCreate, CostCodeResponse, CostCodeUpdate
from app.services.job_costing import (
    code_depth,
    code_tree,
    cost_type_map,
    import_cost_codes,
    parse_cost_code_csv,
    would_cycle,
)
from app.services.cost_code_catalog import (
    code_level,
    division_of,
    ensure_division,
    load_standard_catalog,
    map_new_section,
    normalize_code,
)
from app.schemas.common import StrictModel
from typing import Optional

router = APIRouter(prefix="/api/cost-codes", tags=["cost-codes"])


def _to_response(row: CostCode, db: Session | None = None) -> CostCodeResponse:
    data = CostCodeResponse.model_validate(row)
    data.label = row.label
    data.account_name = row.account.name if row.account else None
    data.parent_code = row.parent.code if row.parent else None
    if db is not None:
        data.depth = code_depth(db, row)
    data.children_count = len(row.children or [])
    return data


def _check_type_exists(db: Session, cost_type: Optional[str]):
    if cost_type is not None and cost_type not in cost_type_map(db):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown cost type '{cost_type}' — add it under Settings → Cost Types",
        )


def _check_parent(db: Session, parent_id: Optional[int], code_id: Optional[int] = None):
    if parent_id is None:
        return
    if not db.get(CostCode, parent_id):
        raise HTTPException(status_code=404, detail="Parent cost code not found")
    if code_id is not None and would_cycle(db, code_id, parent_id):
        raise HTTPException(status_code=422, detail="That parent would create a cycle")


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
    return [_to_response(r, db) for r in q.order_by(CostCode.code).all()]


def _tree_nodes(nodes, db):
    return [
        {
            **_to_response(n["code"], db).model_dump(),
            "children": _tree_nodes(n["children"], db),
        }
        for n in nodes
    ]


@router.get("/tree")
def cost_code_tree(include_inactive: bool = False, db: Session = Depends(get_db)):
    """Nested codes (division > code > sub-code) for pickers and Settings."""
    q = db.query(CostCode).options(joinedload(CostCode.account))
    if not include_inactive:
        q = q.filter(CostCode.is_active.is_(True))
    return _tree_nodes(code_tree(q.all()), db)


class CostCodeImport(StrictModel):
    rows: list[dict] = []
    csv: Optional[str] = None


@router.post("/import")
def import_codes(data: CostCodeImport, db: Session = Depends(get_db)):
    """Bulk load: JSON rows or CSV text of code,name,cost_type,parent_code.
    Existing codes are updated, parents linked in a second pass."""
    rows = list(data.rows)
    if data.csv:
        rows.extend(parse_cost_code_csv(data.csv))
    if not rows:
        raise HTTPException(status_code=422, detail="Nothing to import")
    result = import_cost_codes(db, rows)
    db.commit()
    return result


@router.post("", response_model=CostCodeResponse, status_code=201)
def create_cost_code(data: CostCodeCreate, db: Session = Depends(get_db)):
    values = data.model_dump()
    # Normalize to the "DD SS SS" shape and derive the level from it; a
    # custom code (L-100, EQ) is left as typed with a null level.
    values["code"] = normalize_code(values["code"])
    if not values.get("level"):
        values["level"] = code_level(values["code"])
    _check_code_clash(db, values["code"])
    _check_type_exists(db, values["cost_type"])
    _check_parent(db, values.get("parent_id"))
    if values.get("account_id") is not None and not db.get(
        Account, values["account_id"]
    ):
        raise HTTPException(status_code=404, detail="Account not found")
    # A section with no parent given sits under its division (created from the
    # title table when missing).
    if values.get("parent_id") is None:
        div = division_of(values["code"])
        if div and div.lower() != values["code"].lower():
            values["parent_id"] = ensure_division(db, div).id
    row = CostCode(**values)
    db.add(row)
    db.flush()
    map_new_section(db, row)
    db.commit()
    return _to_response(_get(db, row.id), db)


@router.post("/standard", response_model=list[CostCodeResponse])
def load_standard_cost_codes(db: Session = Depends(get_db)):
    """Load the standard division titles and a modest set of common sections,
    each section parented to its division. Existing codes are left alone, so
    this is safe to repeat."""
    load_standard_catalog(db)
    db.commit()
    return list_cost_codes(include_inactive=False, db=db)


@router.put("/{code_id}", response_model=CostCodeResponse)
def update_cost_code(code_id: int, data: CostCodeUpdate, db: Session = Depends(get_db)):
    row = _get(db, code_id)
    changes = reject_null_updates(CostCode, data.model_dump(exclude_unset=True))
    if "code" in changes:
        raw = changes["code"]
        # A code that already exists as typed (case-insensitive) is kept as
        # typed, so re-saving a numeric custom chart's "1000" never rewrites
        # it; otherwise normalize to the division/section shape.
        existing = db.query(CostCode).filter(CostCode.code.ilike(raw)).first()
        changes["code"] = raw if existing else normalize_code(raw)
        # Re-derive the level from the new shape unless the caller pinned one.
        if "level" not in changes:
            changes["level"] = code_level(changes["code"])
        _check_code_clash(db, changes["code"], row.id)
    if "cost_type" in changes:
        _check_type_exists(db, changes["cost_type"])
    if "parent_id" in changes:
        _check_parent(db, changes["parent_id"], row.id)
    if changes.get("account_id") is not None and not db.get(
        Account, changes["account_id"]
    ):
        raise HTTPException(status_code=404, detail="Account not found")
    for key, value in changes.items():
        setattr(row, key, value)
    db.commit()
    return _to_response(_get(db, row.id), db)


@router.delete("/{code_id}")
def delete_cost_code(code_id: int, db: Session = Depends(get_db)):
    row = _get(db, code_id)
    in_use = (
        db.query(TransactionLine.id)
        .filter(TransactionLine.cost_code_id == code_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .first()
        or db.query(BillLine.id).filter(BillLine.cost_code_id == code_id).first()
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Cost code is used by posted lines — mark it inactive instead",
        )
    from app.services.memorized_service import refuse_if_memorized

    refuse_if_memorized(db, "Cost code", "cost_code_id", code_id)
    db.delete(row)
    db.commit()
    return {"message": "Cost code deleted"}
