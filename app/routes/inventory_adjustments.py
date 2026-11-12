"""Inventory adjustments of several items (#801).

A count that moved twenty items was twenty adjustments, one per item
(POST /api/items/{id}/adjust, which is unchanged). An adjustment here holds
a line per item, posts one journal entry for the lot and writes one
movement per item, each naming the adjustment. A line that is wrong
refuses the whole adjustment, with a sentence naming the line and the
item, and nothing is posted.
"""

from datetime import date as date_type
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.items import InventoryAdjustment, InventoryMovement, Item
from app.routes._helpers import get_or_404
from app.schemas.items import (
    InventoryAdjustmentCreate,
    InventoryAdjustmentLineIn,
    InventoryAdjustmentResponse,
)
from app.services.accounting import _q
from app.services.closing_date import check_closing_date
from app.services.inventory_service import (
    MULTI_ADJUSTMENT_SOURCE,
    record_inventory_adjustment,
)

router = APIRouter(prefix="/api/inventory/adjustments", tags=["inventory"])

# A physical count of a whole stockroom fits; more than this is two counts.
MAX_LINES = 500

_ONE_LINE_FIELDS = ("item_id", "quantity_delta", "new_quantity", "unit_cost")


def _number(value: Decimal) -> str:
    """A quantity as a bookkeeper writes it: 12, 2.5, -3."""
    return format(Decimal(value).normalize(), "f")


def _lines_of(data: InventoryAdjustmentCreate) -> list[InventoryAdjustmentLineIn]:
    """The lines the request holds: `lines`, or the one line sent the way a
    one-item adjustment is (item_id at the top level)."""
    top = {k: getattr(data, k) for k in _ONE_LINE_FIELDS}
    one_line = any(v is not None for v in top.values())
    if data.lines is not None and one_line:
        raise HTTPException(
            status_code=400,
            detail="Send the items as lines, or one item at the top level, "
            "not both.",
        )
    if one_line:
        if data.item_id is None:
            raise HTTPException(
                status_code=400,
                detail="Name the item to adjust (item_id), or send the items "
                "as lines.",
            )
        return [InventoryAdjustmentLineIn(**top, memo=data.memo)]
    return list(data.lines or [])


def _problems(db: Session, lines: list) -> tuple[list[str], list]:
    """Every sentence a line earns, and the lines resolved to (item, change,
    unit cost, memo) when there is none. Each sentence names the line and,
    when there is one, the item."""
    problems = []
    resolved = []
    seen = {}
    items = {
        it.id: it
        for it in db.query(Item).filter(Item.id.in_({ln.item_id for ln in lines})).all()
    }
    for n, ln in enumerate(lines, start=1):
        item = items.get(ln.item_id)
        if item is None:
            problems.append(f"Line {n}: there is no item with id {ln.item_id}.")
            continue
        where = f"Line {n} ({item.name})"
        if not item.track_inventory:
            problems.append(
                f"{where}: this item is not inventory-tracked, so it has no "
                "count to adjust."
            )
            continue
        if item.id in seen:
            problems.append(
                f"{where}: {item.name} is on line {seen[item.id]} already; put "
                "its whole change on one line."
            )
            continue
        seen[item.id] = n
        if (ln.quantity_delta is None) == (ln.new_quantity is None):
            problems.append(
                f"{where}: give the change or the new quantity, one of the two."
            )
            continue
        on_hand = Decimal(str(item.quantity_on_hand or 0))
        if ln.new_quantity is not None:
            if ln.new_quantity < 0:
                problems.append(f"{where}: a new quantity cannot be below zero.")
                continue
            delta = ln.new_quantity - on_hand
            if delta == 0:
                problems.append(
                    f"{where}: the count is {_number(on_hand)} already; take "
                    "the line out or enter the new count."
                )
                continue
        else:
            delta = ln.quantity_delta
            if delta == 0:
                problems.append(
                    f"{where}: the change is zero; take the line out or enter "
                    "the change."
                )
                continue
        if ln.unit_cost is not None and ln.unit_cost < 0:
            problems.append(f"{where}: a unit cost cannot be below zero.")
            continue
        memo = (ln.memo or "").strip() or None
        resolved.append((item, delta, ln.unit_cost, memo))
    return problems, resolved


def _response(db: Session, adj: InventoryAdjustment) -> InventoryAdjustmentResponse:
    movements = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == MULTI_ADJUSTMENT_SOURCE,
            InventoryMovement.source_id == adj.id,
        )
        .order_by(InventoryMovement.id)
        .all()
    )
    names = dict(
        db.query(Item.id, Item.name)
        .filter(Item.id.in_({mv.item_id for mv in movements}))
        .all()
    )
    lines = []
    for n, mv in enumerate(movements, start=1):
        qty = Decimal(str(mv.quantity))
        cost = Decimal(str(mv.unit_cost))
        lines.append(
            {
                "line": n,
                "movement_id": mv.id,
                "item_id": mv.item_id,
                "item_name": names.get(mv.item_id, ""),
                "quantity_delta": qty,
                "unit_cost": cost,
                "amount": _q(qty * cost),
                "balance_qty": mv.balance_qty,
                "balance_avg_cost": mv.balance_avg_cost,
                "memo": mv.memo,
            }
        )
    return InventoryAdjustmentResponse(
        id=adj.id,
        date=adj.date,
        memo=adj.memo,
        transaction_id=adj.transaction_id,
        line_count=len(lines),
        net_amount=sum((ln["amount"] for ln in lines), Decimal("0")),
        lines=lines,
    )


@router.post("", response_model=InventoryAdjustmentResponse, status_code=201)
def create_inventory_adjustment(
    data: InventoryAdjustmentCreate, db: Session = Depends(get_db)
):
    """One adjustment of several items, dated today: one journal entry for
    the lot (source type `inventory_adjustment`) and one movement per item.
    Refused whole, nothing posted, when any line is wrong."""
    lines = _lines_of(data)
    if not lines:
        raise HTTPException(
            status_code=400,
            detail="An adjustment needs at least one line: an item and its "
            "change or new quantity.",
        )
    if len(lines) > MAX_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"An adjustment holds at most {MAX_LINES} lines; this one "
            f"has {len(lines)}. Split the count into two adjustments.",
        )
    problems, resolved = _problems(db, lines)
    if problems:
        raise HTTPException(
            status_code=400,
            detail="Nothing was posted. " + " ".join(problems),
        )
    today = date_type.today()
    check_closing_date(db, today)
    adj = InventoryAdjustment(date=today, memo=(data.memo or "").strip() or None)
    db.add(adj)
    db.flush()
    try:
        record_inventory_adjustment(db, adj, resolved)
    except Exception:
        db.rollback()
        raise
    db.commit()
    db.refresh(adj)
    return _response(db, adj)


@router.get("", response_model=list[InventoryAdjustmentResponse])
def list_inventory_adjustments(
    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)
):
    """Adjustments of several items, newest first, each with its lines."""
    rows = (
        db.query(InventoryAdjustment)
        .order_by(InventoryAdjustment.id.desc())
        .limit(limit)
        .all()
    )
    return [_response(db, adj) for adj in rows]


@router.get("/{adjustment_id}", response_model=InventoryAdjustmentResponse)
def get_inventory_adjustment(adjustment_id: int, db: Session = Depends(get_db)):
    """One adjustment with every line: the item, the change, the cost, what
    it left on hand and the memo."""
    return _response(
        db, get_or_404(db, InventoryAdjustment, adjustment_id, "Inventory adjustment")
    )
