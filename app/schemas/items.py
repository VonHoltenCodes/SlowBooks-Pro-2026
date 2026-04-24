from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.items import ItemType, MovementType


class ItemCreate(BaseModel):
    name: str
    item_type: ItemType
    description: Optional[str] = None
    rate: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    income_account_id: Optional[int] = None
    expense_account_id: Optional[int] = None
    is_taxable: bool = True
    # Phase 11: inventory fields
    track_inventory: bool = False
    quantity_on_hand: Decimal = Decimal("0")
    reorder_point: Decimal = Decimal("0")
    asset_account_id: Optional[int] = None


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    item_type: Optional[ItemType] = None
    description: Optional[str] = None
    rate: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    income_account_id: Optional[int] = None
    expense_account_id: Optional[int] = None
    is_taxable: Optional[bool] = None
    is_active: Optional[bool] = None
    track_inventory: Optional[bool] = None
    reorder_point: Optional[Decimal] = None
    asset_account_id: Optional[int] = None


class ItemResponse(BaseModel):
    id: int
    name: str
    item_type: ItemType
    description: Optional[str]
    rate: Decimal
    cost: Decimal
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

    model_config = {"from_attributes": True}


class InventoryAdjustmentRequest(BaseModel):
    quantity_delta: Decimal
    unit_cost: Optional[Decimal] = None
    memo: Optional[str] = None


class LowStockResponse(BaseModel):
    id: int
    name: str
    quantity_on_hand: Decimal
    reorder_point: Decimal
    avg_cost: Decimal
    shortage: Decimal

    model_config = {"from_attributes": True}
