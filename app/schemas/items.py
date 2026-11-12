from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, StringConstraints
from app.schemas.common import NonBlankName, StrictModel, _blank_to_none
from app.schemas.invoices import RateOut

from app.models.items import ItemType, MovementType

# An item's code (#445): trimmed, and a blank one is no code, so an empty
# form field clears it rather than storing "".
ItemCode = Annotated[
    Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]],
    BeforeValidator(_blank_to_none),
]


class ItemCreate(StrictModel):
    # Trimmed and never blank, as a customer's or vendor's name is: the
    # duplicate check compares trimmed names.
    name: NonBlankName
    code: ItemCode = None
    item_type: ItemType
    description: Optional[str] = None
    rate: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    income_account_id: Optional[int] = None
    expense_account_id: Optional[int] = None
    # Left out, the company's "New lines and items are taxable" setting
    # decides (#486; on by default).
    is_taxable: Optional[bool] = None
    # A discount or concession item (#639): a line on it may carry a
    # negative price, taken off its income account.
    allows_negative_price: bool = False
    # Phase 11: inventory fields
    track_inventory: bool = False
    quantity_on_hand: Decimal = Decimal("0")
    reorder_point: Decimal = Decimal("0")
    asset_account_id: Optional[int] = None
    # The date the opening stock is booked (books set up as of 12/31 want it
    # in that period, not today). Only used on create and only when stock is
    # posted; defaults to today in the service.
    as_of: Optional[dt_date] = None


class ItemUpdate(StrictModel):
    name: Optional[NonBlankName] = None
    code: ItemCode = None
    item_type: Optional[ItemType] = None
    description: Optional[str] = None
    rate: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    income_account_id: Optional[int] = None
    expense_account_id: Optional[int] = None
    is_taxable: Optional[bool] = None
    is_active: Optional[bool] = None
    allows_negative_price: Optional[bool] = None
    track_inventory: Optional[bool] = None
    reorder_point: Optional[Decimal] = None
    asset_account_id: Optional[int] = None


class ItemResponse(BaseModel):
    id: int
    name: str
    code: Optional[str] = None
    item_type: ItemType
    description: Optional[str]
    rate: RateOut
    cost: RateOut
    income_account_id: Optional[int]
    expense_account_id: Optional[int]
    is_taxable: bool
    is_active: bool
    track_inventory: bool
    quantity_on_hand: Decimal
    reorder_point: Decimal
    avg_cost: Decimal
    asset_account_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    # The item's own "Discount or concession" flag (#639).
    allows_negative_price: bool = False
    # Whether a line on this item may carry a negative price: the flag
    # above, or a QuickBooks Online discount came in on the item. The sales
    # forms read this to let the line's price box go negative.
    is_discount: bool = False

    model_config = {"from_attributes": True}


# -------- Phase 11 schemas for inventory routes --------


class InventoryMovementResponse(BaseModel):
    id: int
    item_id: int
    date: datetime
    movement_type: MovementType
    quantity: Decimal
    unit_cost: Decimal
    balance_qty: Decimal
    balance_avg_cost: Decimal
    source_type: Optional[str]
    source_id: Optional[int]
    transaction_id: Optional[int]
    memo: Optional[str]
    # The movement this one reverses or returns against (W-68): a bill's
    # void or edit names each receipt it takes back out, a sale's void or
    # quantity-down edit the sale posting, a credit memo's return the sale
    # posting it comes off. None for a receipt, a sale, an adjustment, and
    # a reversal from before the link existed.
    reverses_movement_id: Optional[int] = None

    model_config = {"from_attributes": True}


class InventoryAdjustmentRequest(StrictModel):
    quantity_delta: Decimal
    unit_cost: Optional[Decimal] = None
    memo: Optional[str] = None


class InventoryAdjustmentLineIn(StrictModel):
    """One line of an adjustment of several items (#801): the item and
    either the change (`quantity_delta`, signed) or the count it is set to
    (`new_quantity`), the unit cost where it applies (the average when left
    out) and a memo."""

    item_id: int
    quantity_delta: Optional[Decimal] = None
    new_quantity: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    memo: Optional[str] = None


class InventoryAdjustmentCreate(StrictModel):
    """POST /api/inventory/adjustments (#801): `lines`, one per item, and a
    memo for the adjustment. One line may be sent the way a one-item
    adjustment is, at the top level: `item_id` with `quantity_delta` (or
    `new_quantity`), `unit_cost` and `memo`."""

    memo: Optional[str] = None
    lines: Optional[list[InventoryAdjustmentLineIn]] = None
    item_id: Optional[int] = None
    quantity_delta: Optional[Decimal] = None
    new_quantity: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None


class InventoryAdjustmentLineOut(BaseModel):
    line: int
    movement_id: int
    item_id: int
    item_name: str
    quantity_delta: Decimal
    unit_cost: Decimal
    # quantity x unit cost, signed as the change is
    amount: Decimal
    balance_qty: Decimal
    balance_avg_cost: Decimal
    memo: Optional[str] = None


class InventoryAdjustmentResponse(BaseModel):
    id: int
    date: dt_date
    memo: Optional[str] = None
    transaction_id: Optional[int] = None
    line_count: int
    # the lines' amounts added, signed: up is more stock on the books
    net_amount: Decimal
    lines: list[InventoryAdjustmentLineOut] = []


class LowStockResponse(BaseModel):
    id: int
    name: str
    quantity_on_hand: Decimal
    reorder_point: Decimal
    avg_cost: Decimal
    shortage: Decimal

    model_config = {"from_attributes": True}
