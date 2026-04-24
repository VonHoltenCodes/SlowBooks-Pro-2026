# ============================================================================
# Decompiled from qbw32.exe!CItemManager  Offset: 0x000F4E00
# Original Btrieve table: ITEM.DAT (record size 0x01C0, 4 key segments)
# Intuit called these "ItemRef" entries — the SDK exposed them through the
# IItemQuery interface. Type field was a WORD that mapped to the
# qbXMLItemTypeEnum in the QBFC COM library.
#
# Phase 11: Added inventory tracking (quantity_on_hand, reorder_point,
# weighted-average cost via InventoryMovement ledger). QB2003 stored these
# in ITEM.DAT fields 0x14-0x18; we split them into a proper ledger table.
# ============================================================================

import enum

from sqlalchemy import Column, Integer, String, Enum, Boolean, Numeric, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database import Base


class ItemType(str, enum.Enum):
    # qbXMLItemTypeEnum @ 0x000F5090
    PRODUCT = "product"      # itInventory (0x00)
    SERVICE = "service"      # itService (0x01)
    MATERIAL = "material"    # itNonInventory (0x02) — we renamed this for clarity
    LABOR = "labor"          # itOtherCharge (0x03) — labor/hourly billing


class MovementType(str, enum.Enum):
    """Reason for an inventory movement row."""
    PURCHASE = "purchase"      # received via bill (+qty, updates weighted avg cost)
    SALE = "sale"              # shipped via invoice (-qty, posts COGS)
    ADJUSTMENT = "adjustment"  # manual adjustment (inventory count, shrinkage, etc.)
    RETURN_IN = "return_in"    # customer return (+qty)
    RETURN_OUT = "return_out"  # vendor return (-qty)
    VOID = "void"              # reversal of a sale/purchase (opposite sign)


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    item_type = Column(Enum(ItemType), nullable=False)
    description = Column(Text, nullable=True)
    rate = Column(Numeric(12, 2), default=0)
    cost = Column(Numeric(12, 2), default=0)  # standard/last cost; weighted avg lives in `avg_cost`
    income_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    is_taxable = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)

    # ---- Phase 11: inventory tracking ----
    # Only items with track_inventory=True hit the inventory ledger; the rest
    # (services, labor, non-inventory materials) bypass it entirely.
    track_inventory = Column(Boolean, default=False, nullable=False)
    quantity_on_hand = Column(Numeric(14, 4), default=0, nullable=False)
    reorder_point = Column(Numeric(14, 4), default=0, nullable=False)
    avg_cost = Column(Numeric(14, 4), default=0, nullable=False)  # weighted average unit cost
    asset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # defaults to Inventory (1300)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    income_account = relationship("Account", foreign_keys=[income_account_id])
    expense_account = relationship("Account", foreign_keys=[expense_account_id])
    asset_account = relationship("Account", foreign_keys=[asset_account_id])
    movements = relationship("InventoryMovement", back_populates="item", cascade="all, delete-orphan")


class InventoryMovement(Base):
    """Per-item ledger row. Every change to quantity_on_hand writes one of
    these, giving a full audit trail plus the ability to rebuild qty/avg_cost
    deterministically from history.
    """

    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, index=True)
    date = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    movement_type = Column(Enum(MovementType), nullable=False)
    # Signed quantity: +1.000 for purchases/returns-in, -1.000 for sales/returns-out
    quantity = Column(Numeric(14, 4), nullable=False)
    unit_cost = Column(Numeric(14, 4), nullable=False)  # cost at time of movement
    # Denormalized running state (post-movement). Cheap sanity check + fast qty queries.
    balance_qty = Column(Numeric(14, 4), nullable=False)
    balance_avg_cost = Column(Numeric(14, 4), nullable=False)
    # Source document linkage (nullable for manual adjustments)
    source_type = Column(String(32), nullable=True)  # "invoice" | "bill" | "adjustment"
    source_id = Column(Integer, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)  # COGS/inventory JE
    memo = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    item = relationship("Item", back_populates="movements")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
