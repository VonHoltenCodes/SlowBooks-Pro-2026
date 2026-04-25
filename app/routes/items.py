from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("", response_model=list[ItemResponse])
def list_items(active_only: bool = False, item_type: str = None, search: str = None, db: Session = Depends(get_db)):
    q = db.query(Item)
    if active_only:
        q = q.filter(Item.is_active == True)
    if item_type:
        q = q.filter(Item.item_type == item_type)
    if search:
        q = q.filter(Item.name.ilike(f"%{search}%"))
    return q.order_by(Item.name).all()


@router.get("/low-stock", response_model=list[LowStockResponse])
def low_stock_items(db: Session = Depends(get_db)):
    """Items where quantity_on_hand <= reorder_point (and reorder_point > 0).

    Returned sorted worst-shortage-first so the most urgent re-orders come first.
    """
    rows = (
        db.query(Item)
        .filter(Item.track_inventory == True)  # noqa
        .filter(Item.is_active == True)  # noqa
        .filter(Item.reorder_point > 0)
        .filter(Item.quantity_on_hand <= Item.reorder_point)
        .all()
    )
    out = []
    for it in rows:
        shortage = Decimal(str(it.reorder_point or 0)) - Decimal(str(it.quantity_on_hand or 0))
        if shortage < 0:
            shortage = Decimal("0")
        out.append(LowStockResponse(
            id=it.id,
            name=it.name,
            quantity_on_hand=it.quantity_on_hand,
            reorder_point=it.reorder_point,
            avg_cost=it.avg_cost,
            shortage=shortage,
        ))
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
        db, item,
        quantity_delta=data.quantity_delta,
        unit_cost=data.unit_cost,
        memo=data.memo,
    )
    if mv is None:
        raise HTTPException(status_code=400, detail="No adjustment recorded (zero delta?)")
    db.commit()
    db.refresh(mv)
    return mv


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
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
