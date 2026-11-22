from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_ as sqla_and_, or_ as sqla_or_
from sqlalchemy.orm import Session

from app.services.terminology import said
from app.database import get_db
from app.models.accounts import Account
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
from app.services.settings_service import new_lines_taxable
from app.services.inventory_service import (
    current_valuation,
    record_adjustment,
    record_opening_quantity,
)

router = APIRouter(prefix="/api/items", tags=["items"])


# The accounts an item's income or expense account may not be (#1038): the
# ones whose figure a sub-ledger keeps. An invoice line crediting Accounts
# Receivable would take the sale off what the customer owes while the
# invoice still says it is owed; the others the same way for the bills,
# the deposits and the stock. A bank or card account is left out too: money
# into or out of it is a deposit, a payment or a charge, not an item. Any
# other balance-sheet account is allowed (a security deposit credits a
# liability), as the bill line allows it (#766, W-143).
_ITEM_ACCOUNT_CONTROLS = {
    "1100": "which only invoices and customer payments post to",
    "2000": "which only bills and bill payments post to",
    "1200": "which holds customer payments until they are deposited",
    "1300": "which only an inventory item's purchases and sales post to",
}
_ACCOUNT_FIELDS = {
    "income_account_id": "income",
    "expense_account_id": "expense or cost of goods",
    "asset_account_id": "inventory asset",
}


def _other_items_on(db: Session, account_id: int, item=None):
    """The first item (other than `item`) whose income or expense account is
    `account_id`, or None: an invoice or bill line on it posts to the
    account without the stock moving."""
    q = db.query(Item).filter(
        sqla_or_(
            Item.income_account_id == account_id,
            Item.expense_account_id == account_id,
        )
    )
    if item is not None and getattr(item, "id", None) is not None:
        q = q.filter(Item.id != item.id)
    return q.order_by(Item.name).first()


def item_account_problem(
    db: Session, field: str, account: Account, tied: dict, item=None
):
    """Why `account` cannot be the item's `field` account, as the sentence
    the API answers with, or None when it can.

    Income and expense (#1038, W-196): not a bank or card account, not one
    a sub-ledger keeps (Accounts Receivable, Accounts Payable, Undeposited
    Funds, Inventory) and not any other account a Books Check row ties to
    a sub-ledger.

    The inventory asset account (W-207): an Other Current Asset account
    that is not a bank, Accounts Receivable, Undeposited Funds or any other
    tied account except an inventory one (1300 Inventory, or an account
    another stock item already keeps its stock in), and that no item
    invoices or buys through as its income or expense account. Every
    purchase and sale of the item posts to it beside the stock's quantity
    and cost, so anything else that moves it puts Books Check's Inventory
    row out, and the stock moving it puts the other row out."""
    from app.services.control_accounts import is_control

    what = _ACCOUNT_FIELDS[field]
    number = str(account.account_number or "")
    named = f"{number} {account.name}".strip()
    start = f"An item's {what} account can't be {named}"
    detail = getattr(account.detail_type, "value", account.detail_type)
    if account.bank_kind:
        kind = "card" if account.bank_kind == "credit_card" else "bank"
        return (
            f"{start}, a {kind} account: money into or out of it is "
            "a deposit, a payment or a charge. Choose another account."
        )
    asset_field = field == "asset_account_id"
    where = _ITEM_ACCOUNT_CONTROLS.get(number)
    if (
        where
        and is_control(number, account.account_type)
        and not (asset_field and number == "1300")
    ):
        return f"{start}, {where}; choose another account."
    if detail == "accounts_receivable":
        return (
            f"{start}, an accounts receivable account, which only "
            "invoices and customer payments post to; choose another account."
        )
    if detail == "accounts_payable":
        return (
            f"{start}, an accounts payable account, which only bills "
            "and bill payments post to; choose another account."
        )
    if asset_field and detail != "other_current_asset":
        return (
            f"{start}: an item's stock is kept in an Other Current Asset "
            "account, such as 1300 Inventory. Choose another account."
        )
    # Every other account a Books Check row ties to a sub-ledger (W-196):
    # Sales Tax Payable, Accrued Job Costs, the payroll liabilities, the
    # retainage accounts, an item's own inventory account. A line on one
    # moves the account and not what it is tied to. The inventory accounts
    # are what an item's asset account is for, so that field takes them.
    if account.id in tied:
        label, keeper = tied[account.id]
        if not (asset_field and label == "Inventory"):
            return (
                f"{start}: Books Check ties it to {keeper}, and a line "
                f"on an item would put its {label} row out by the line. Choose "
                "another account."
            )
    if asset_field:
        other = _other_items_on(db, account.id, item)
        if other is not None:
            return (
                f"{start}: the item {other.name} posts its sales or purchases "
                "to it, which would move it without the stock and put Books "
                "Check's Inventory row out. Choose another account."
            )
    return None


def _refuse_item_accounts(db: Session, fields: dict, item=None) -> None:
    """Refuse (422), in a sentence, an income, expense or inventory asset
    account an item may not post through. An account the item already has
    is not refused, so an item imported that way can still be edited."""
    tied = None
    for field in _ACCOUNT_FIELDS:
        account_id = fields.get(field)
        if account_id is None or (
            item is not None and getattr(item, field) == account_id
        ):
            continue
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(
                status_code=422,
                detail=f"The {_ACCOUNT_FIELDS[field]} account chosen is not in "
                "the chart of accounts; choose another account.",
            )
        if tied is None:
            tied = refused_item_accounts(db)
        problem = item_account_problem(db, field, account, tied, item)
        if problem:
            raise HTTPException(status_code=422, detail=problem)


def refused_item_accounts(db: Session) -> dict[int, tuple[str, str]]:
    """{account id: (Books Check label, what it is tied to)}: the accounts
    an item may not newly post to beyond the ones refused by number, type or
    detail above, read from Books Check itself (books_check.tied_accounts)
    so the two cannot drift. GET /api/items/refused-accounts gives the form
    the same list, so it never offers them."""
    from app.services.books_check import tied_accounts

    return tied_accounts(db)


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
            detail=said(
                'There is already an item named "{}". Use that item, give '
                "this one a different name, or make the other one inactive "
                "first.",
                clash.name,
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
            detail=said("Item code {} is already used by {}.", code, clash.name),
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
    """Item responses, each item a line may carry a negative price on
    marked (is_discount: its own "Discount or concession" flag, #639, or a
    QuickBooks Online discount came in on it) so the sales forms let its
    line's price go negative."""
    from app.services.discount_items import negative_price_item_ids

    discounts = negative_price_item_ids(db)
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


@router.get("/refused-accounts")
def refused_accounts(
    field: str = Query("income_account_id"), db: Session = Depends(get_db)
):
    """The accounts the item form does not offer for an item's income or
    expense account beyond the bank, card, receivable, payable, Undeposited
    Funds and Inventory accounts it already leaves out: each with the reason
    the server gives when one is sent (W-196).

    `field=asset_account_id`: every account the inventory asset account
    may not be, each with the sentence the server refuses it with (W-207)."""
    tied = refused_item_accounts(db)
    if field == "asset_account_id":
        out = []
        for account in db.query(Account).order_by(Account.account_number):
            problem = item_account_problem(db, field, account, tied)
            if problem:
                out.append({"account_id": account.id, "reason": problem})
        return out
    if field not in _ACCOUNT_FIELDS:
        raise HTTPException(
            status_code=422,
            detail="Ask for the income, expense or inventory asset account's list.",
        )
    return [
        {"account_id": aid, "books_check_row": label, "tied_to": keeper}
        for aid, (label, keeper) in sorted(tied.items())
    ]


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
    _refuse_item_accounts(db, fields)
    if fields.get("is_taxable") is None:
        fields["is_taxable"] = new_lines_taxable(db)
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
    return _flag_discounts(db, [item])[0]


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
    _refuse_item_accounts(db, update_data, item)
    for key, val in update_data.items():
        setattr(item, key, val)
    db.commit()
    db.refresh(item)
    return _flag_discounts(db, [item])[0]


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id)
    item.is_active = False
    db.commit()
    return {"message": "Item deactivated"}
