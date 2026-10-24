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
from app.routes._helpers import get_or_404, reject_null_updates
from app.services.inventory_service import (
    current_valuation,
    record_adjustment,
    record_opening_quantity,
)

router = APIRouter(prefix="/api/items", tags=["items"])


def item_name_key(name) -> str:
    """An item name as the duplicate check compares it: trimmed, runs of
    spaces as one, capitals ignored ("design  hour" is "Design Hour")."""
    return " ".join(str(name or "").split()).casefold()


def find_active_item_named(db: Session, name, exclude_id=None):
    """The active item that already carries this name — compared trimmed
    and without regard to capitals — or None. Compared in Python rather
    than SQL: SQLite's lower() folds only ASCII, so "Übergröße" and
    "ÜBERGRÖSSE" would have passed as different names."""
    key = item_name_key(name)
    if not key:
        return None
    q = db.query(Item).filter(Item.is_active.is_(True))
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return next((it for it in q.all() if item_name_key(it.name) == key), None)


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


def item_code_key(code) -> str:
    """An item code as the uniqueness check compares it: trimmed, capitals
    ignored (#445). Compared in Python for the reason names are."""
    return str(code or "").strip().casefold()


def find_item_with_code(db: Session, code, exclude_id=None):
    """The item, active or not, that already carries this code, or None.
    Inactive items count: the unique index covers them too."""
    key = item_code_key(code)
    if not key:
        return None
    q = db.query(Item).filter(Item.code.isnot(None))
    if exclude_id is not None:
        q = q.filter(Item.id != exclude_id)
    return next((it for it in q.all() if item_code_key(it.code) == key), None)


def _refuse_duplicate_code(db: Session, code, exclude_id=None) -> None:
    """One code finds one item (#445), so a second is refused before the
    index would turn it into a 500."""
    clash = find_item_with_code(db, code, exclude_id)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Item code {code} is already used by {clash.name}.",
        )


@router.get("", response_model=list[ItemResponse])
def list_items(
    active_only: bool = False,
    inactive_only: bool = False,
    item_type: str = None,
    search: str = None,
    q: str = None,
    db: Session = Depends(get_db),
):
    term = (q or search or "").strip()
    query = db.query(Item)
    if active_only:
        query = query.filter(Item.is_active)
    if inactive_only:  # the list page's Inactive view (#210)
        query = query.filter(Item.is_active.is_(False))
    if item_type:
        query = query.filter(Item.item_type == item_type)
    if term:
        # name or code (#445): "2x6" finds "2x6-16 — 2x6x16 SPF" either way.
        # A typed % or _ is that character, not a LIKE wildcard: "50%" finds
        # "50% deposit" and not every name with a 50 in it.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(
            sqla_or_(
                Item.name.ilike(like, escape="\\"),
                Item.code.ilike(like, escape="\\"),
            )
        )
    return _flag_discounts(db, query.order_by(Item.name).all())


def _flag_discounts(db: Session, items):
    """Item responses, a Discount item marked so the forms let its line
    take a negative price."""
    from app.services.qbo_common import discount_item_ids

    discounts = discount_item_ids(db)
    out = []
    for item in items:
        resp = ItemResponse.model_validate(item)
        resp.is_discount = item.id in discounts
        out.append(resp)
    return out


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
    return _flag_discounts(db, [get_or_404(db, Item, item_id)])[0]


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
    """Create an item. A tracked item's starting quantity is not written to
    the item directly: it posts as its opening movement at the item's cost,
    with DR Inventory / CR Opening Balance Equity dated `as_of` (today when
    the form omits it; no entry when the cost is zero). An untracked item
    keeps the figure as given. Later changes to on-hand go through /adjust;
    PUT never sets it."""
    _refuse_duplicate_name(db, data.name)
    _refuse_duplicate_code(db, data.code)
    fields = data.model_dump()
    # as_of dates the opening entry only; it is not an Item column.
    as_of = fields.pop("as_of", None)
    opening_qty = Decimal("0")
    if data.track_inventory:
        opening_qty = fields.pop("quantity_on_hand") or Decimal("0")
        if opening_qty < 0:
            raise HTTPException(
                status_code=400,
                detail="A starting quantity on hand cannot be negative",
            )
    item = Item(**fields)
    db.add(item)
    db.flush()
    if opening_qty > 0:
        record_opening_quantity(db, item, opening_qty, data.cost, as_of=as_of)
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
    # An item always has a name (and an item_type): an explicit null on either
    # is refused with a 422 rather than the IntegrityError 500 it once caused.
    reject_null_updates(Item, update_data)
    # A rename, or bringing an inactive item back, must not make a second
    # active item of the same name. An edit that leaves the name alone is
    # not refused, so two duplicates made before this check can still be
    # edited — and one of them made inactive.
    new_name = update_data.get("name", item.name)
    renaming = item_name_key(new_name) != item_name_key(item.name)
    reactivating = update_data.get("is_active") is True and not item.is_active
    if update_data.get("is_active", item.is_active) and (renaming or reactivating):
        _refuse_duplicate_name(db, new_name, item.id)
    if update_data.get("code"):
        _refuse_duplicate_code(db, update_data["code"], item.id)
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
