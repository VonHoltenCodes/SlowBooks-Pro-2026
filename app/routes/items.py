from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_ as sqla_and_, or_ as sqla_or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.items import Item, InventoryMovement
from app.schemas.items import (
    ItemCreate,
    ItemUpdate,
    ItemResponse,
    InventoryMovementResponse,
    InventoryAdjustmentRequest,
    LowStockResponse,
)
from app.routes._helpers import get_or_404
from app.services.inventory_service import record_adjustment, current_valuation

router = APIRouter(prefix="/api/items", tags=["items"])


def _name_key(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def find_active_item_named(db: Session, name, exclude_id=None):
    """The active item that already carries this name — compared trimmed
    and without regard to capitals — or None. Compared in Python rather
    than SQL: SQLite's lower() folds only ASCII, so "Übergröße" and
    "ÜBERGRÖSSE" would have passed as different names."""
    key = _name_key(name)
    if not key:
        return None
    q = db.query(Item).filter(Item.is_active.is_(True))
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return next((it for it in q.all() if _name_key(it.name) == key), None)


def _refuse_duplicate_name(db: Session, name, exclude_id=None) -> None:
    """Two active items with one name show twice in every picker, and
    nothing on the screen says which is which (2.17.3 exploratory test,
    W-M16). Refuse, and say what to do instead."""
    clash = find_active_item_named(db, name, exclude_id)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(
                f'There is already an item named "{clash.name}". Use that '
                "item, give this one a different name, or make the other "
                "one inactive first."
            ),
        )


@router.get("", response_model=list[ItemResponse])
def list_items(
    active_only: bool = False,
    item_type: str = None,
    search: str = None,
    db: Session = Depends(get_db),
):
    q = db.query(Item)
    if active_only:
        q = q.filter(Item.is_active)
    if item_type:
        q = q.filter(Item.item_type == item_type)
    if search:
        q = q.filter(Item.name.ilike(f"%{search}%"))
    return q.order_by(Item.name).all()


@router.get("/low-stock", response_model=list[LowStockResponse])
def low_stock_items(db: Session = Depends(get_db)):
    """Items where quantity_on_hand <= reorder_point (and reorder_point > 0),
    plus any item whose on-hand has gone negative.

    Negative on-hand happens when a sale is invoiced before the receiving
    bill is entered (the inventory service allows it). Without surfacing
    these here, an operator who never sets a reorder_point can sell a
    widget into the red and never see a warning.

    Returned sorted worst-shortage-first so the most urgent re-orders come first.
    """
    rows = (
        db.query(Item)
        .filter(Item.track_inventory == True)  # noqa
        .filter(Item.is_active == True)  # noqa
        .filter(
            sqla_or_(
                sqla_and_(
                    Item.reorder_point > 0, Item.quantity_on_hand <= Item.reorder_point
                ),
                Item.quantity_on_hand < 0,
            )
        )
        .all()
    )
    out = []
    for it in rows:
        shortage = Decimal(str(it.reorder_point or 0)) - Decimal(
            str(it.quantity_on_hand or 0)
        )
        if shortage < 0:
            shortage = Decimal("0")
        out.append(
            LowStockResponse(
                id=it.id,
                name=it.name,
                quantity_on_hand=it.quantity_on_hand,
                reorder_point=it.reorder_point,
                avg_cost=it.avg_cost,
                shortage=shortage,
            )
        )
    out.sort(key=lambda r: r.shortage, reverse=True)
    return out


@router.get("/valuation")
def inventory_valuation(db: Session = Depends(get_db)):
    """Total inventory value (sum of qty * avg_cost across tracked items)."""
    return current_valuation(db)


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Item, item_id)


@router.get("/{item_id}/movements", response_model=list[InventoryMovementResponse])
def list_item_movements(
    item_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Inventory ledger for one item, newest first."""
    get_or_404(db, Item, item_id)  # 404 if item doesn't exist
    return (
        db.query(InventoryMovement)
        .filter(InventoryMovement.item_id == item_id)
        .order_by(InventoryMovement.id.desc())
        .limit(limit)
        .all()
    )


@router.post("/{item_id}/adjust", response_model=InventoryMovementResponse)
def adjust_inventory(
    item_id: int,
    data: InventoryAdjustmentRequest,
    db: Session = Depends(get_db),
):
    """Manual inventory adjustment (count correction, shrinkage, spoilage).

    Posts a one-sided JE to #5900 "Inventory Adjustments" (if seeded) or COGS
    as fallback. Quantity delta can be positive or negative.
    """
    item = get_or_404(db, Item, item_id)
    if not item.track_inventory:
        raise HTTPException(status_code=400, detail="Item is not inventory-tracked")

    mv = record_adjustment(
        db,
        item,
        quantity_delta=data.quantity_delta,
        unit_cost=data.unit_cost,
        memo=data.memo,
    )
    if mv is None:
        raise HTTPException(
            status_code=400, detail="No adjustment recorded (zero delta?)"
        )
    db.commit()
    db.refresh(mv)
    return mv


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    _refuse_duplicate_name(db, data.name)
    item = Item(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{item_id}", response_model=ItemResponse)
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id)
    update_data = data.model_dump(exclude_unset=True)
    # Never let the UI edit quantity_on_hand or avg_cost directly — those are
    # owned by the inventory ledger. Use /adjust instead.
    update_data.pop("quantity_on_hand", None)
    update_data.pop("avg_cost", None)
    if "name" in update_data and update_data["name"] is None:
        update_data.pop("name")  # an item always has a name
    # A rename, or bringing an inactive item back, must not make a second
    # active item of the same name. An edit that leaves the name alone is
    # not refused, so two duplicates made before this check can still be
    # edited — and one of them made inactive.
    renaming = "name" in update_data and _name_key(update_data["name"]) != _name_key(
        item.name
    )
    reactivating = update_data.get("is_active") is True and not item.is_active
    if update_data.get("is_active", item.is_active) and (renaming or reactivating):
        _refuse_duplicate_name(db, update_data.get("name", item.name), item.id)
    for key, val in update_data.items():
        setattr(item, key, val)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id)
    item.is_active = False
    db.commit()
    return {"message": "Item deactivated"}
