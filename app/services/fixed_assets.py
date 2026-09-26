# ============================================================================
# Fixed assets service — the purchase in the books, depreciation math,
# disposal, CSV import.
#
# Depreciation runs are month-granular: a run covers the FULL months
# between the asset's depreciation start (purchase date, or the day
# after the last run) and the run date. Straight-line spreads
# (cost - salvage) over the effective life; declining-balance applies
# the annual rate to current book value. Both cap so book value never
# drops below salvage.
# ============================================================================

import csv
import io
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.bills import Bill, BillStatus
from app.models.transactions import Transaction
from app.models.fixed_assets import (
    DepreciationMethod,
    FixedAsset,
    FixedAssetStatus,
    FixedAssetType,
)
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_opening_balance_equity_id,
)
from app.services.bank_register import gl_balance, money_text, voided_transaction_ids

logger = logging.getLogger(__name__)

DISPOSAL_ACCOUNT_NUMBER = "7999"
DISPOSAL_ACCOUNT_NAME = "Gain/Loss on Asset Disposal"


def check_amounts(purchase_price, salvage_value) -> None:
    """Cost must be something, and salvage between nothing and the cost. A
    salvage value above cost was accepted (exploratory 2.17.3, W-M19)."""
    price = _q(Decimal(str(purchase_price or 0)))
    salvage = _q(Decimal(str(salvage_value or 0)))
    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail="Enter the purchase price: it must be more than $0.00.",
        )
    if salvage < 0:
        raise HTTPException(
            status_code=400, detail="Salvage value can't be less than $0.00."
        )
    if salvage > price:
        raise HTTPException(
            status_code=400,
            detail=(
                "Salvage value can't be more than the purchase price "
                f"({money_text(price)})."
            ),
        )


def book_value(asset: FixedAsset) -> Decimal:
    return _q(
        Decimal(str(asset.purchase_price))
        - Decimal(str(asset.accumulated_depreciation or 0))
    )


def next_asset_number(db: Session) -> str:
    count = db.query(FixedAsset).count()
    candidate = count + 1
    while True:
        number = f"FA-{candidate:04d}"
        if not db.query(FixedAsset).filter(FixedAsset.asset_number == number).first():
            return number
        candidate += 1


def _require_type_accounts(asset_type: FixedAssetType):
    missing = [
        label
        for label, value in (
            (
                "accumulated depreciation",
                asset_type.accumulated_depreciation_account_id,
            ),
            ("depreciation expense", asset_type.depreciation_expense_account_id),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Asset type '{asset_type.name}' is missing account mappings: "
                f"{', '.join(missing)}"
            ),
        )


def _full_months_between(start: date, end: date) -> int:
    """Whole calendar months from `start` to `end` (0 when < 1 month)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def period_depreciation(asset: FixedAsset, run_date: date) -> Decimal:
    """Depreciation to post for the period ending run_date."""
    if asset.status != FixedAssetStatus.REGISTERED:
        return Decimal("0")
    start = asset.last_depreciation_date or asset.purchase_date
    months = _full_months_between(start, run_date)
    if months <= 0:
        return Decimal("0")

    cost = Decimal(str(asset.purchase_price))
    salvage = Decimal(str(asset.salvage_value or 0))
    depreciable_remaining = book_value(asset) - salvage
    if depreciable_remaining <= 0:
        return Decimal("0")

    atype = asset.asset_type
    if atype.depreciation_method == DepreciationMethod.STRAIGHT_LINE:
        life = Decimal(str(atype.effective_life_years or 0))
        if life <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Asset type '{atype.name}' has no effective life set",
            )
        monthly = (cost - salvage) / (life * 12)
    else:
        rate = Decimal(str(atype.annual_rate or 0))
        if rate <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Asset type '{atype.name}' has no annual rate set",
            )
        monthly = book_value(asset) * rate / 12

    amount = _q(monthly * months)
    return min(amount, _q(depreciable_remaining))


def run_depreciation(db: Session, run_date: date) -> dict:
    """Post depreciation for every registered asset up to run_date.

    One journal entry per asset (DR depreciation expense / CR accumulated
    depreciation) so drill-down stays per-asset. Assets with nothing to
    post (fully depreciated, < 1 month elapsed) are skipped and counted.
    """
    assets = (
        db.query(FixedAsset)
        .filter(FixedAsset.status == FixedAssetStatus.REGISTERED)
        .all()
    )
    posted = 0
    skipped = 0
    total = Decimal("0")
    for asset in assets:
        amount = period_depreciation(asset, run_date)
        if amount <= 0:
            skipped += 1
            continue
        atype = asset.asset_type
        _require_type_accounts(atype)
        create_journal_entry(
            db,
            run_date,
            f"Depreciation — {asset.asset_number} {asset.name}",
            [
                {
                    "account_id": atype.depreciation_expense_account_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": f"Depreciation {asset.asset_number}",
                },
                {
                    "account_id": atype.accumulated_depreciation_account_id,
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": f"Accumulated depreciation {asset.asset_number}",
                },
            ],
            source_type="depreciation",
            source_id=asset.id,
        )
        asset.accumulated_depreciation = _q(
            Decimal(str(asset.accumulated_depreciation or 0)) + amount
        )
        asset.last_depreciation_date = run_date
        posted += 1
        total += amount
    db.commit()
    return {"posted": posted, "skipped": skipped, "total": float(_q(total))}


def _disposal_account_id(db: Session) -> int:
    acct = (
        db.query(Account)
        .filter(Account.account_number == DISPOSAL_ACCOUNT_NUMBER)
        .first()
    ) or db.query(Account).filter(Account.name == DISPOSAL_ACCOUNT_NAME).first()
    if not acct:
        acct = Account(
            name=DISPOSAL_ACCOUNT_NAME,
            account_number=DISPOSAL_ACCOUNT_NUMBER,
            account_type=AccountType.EXPENSE,
            is_system=True,
        )
        db.add(acct)
        db.flush()
    return acct.id


def dispose_asset(
    db: Session,
    asset: FixedAsset,
    disposal_date: date,
    proceeds: Decimal,
    deposit_account_id: int,
) -> dict:
    """Sell/dispose: derecognize cost + accumulated, book proceeds, and
    post the residual as gain (credit) or loss (debit) on disposal."""
    if asset.status == FixedAssetStatus.DISPOSED:
        raise HTTPException(status_code=400, detail="Asset is already disposed")
    atype = asset.asset_type
    if not atype.asset_account_id:
        raise HTTPException(
            status_code=400,
            detail=f"Asset type '{atype.name}' has no fixed-asset account mapped",
        )
    _require_type_accounts(atype)

    cost = _q(Decimal(str(asset.purchase_price)))
    accumulated = _q(Decimal(str(asset.accumulated_depreciation or 0)))
    proceeds = _q(Decimal(str(proceeds or 0)))

    lines = []
    if proceeds > 0:
        lines.append(
            {
                "account_id": deposit_account_id,
                "debit": proceeds,
                "credit": Decimal("0"),
                "description": f"Disposal proceeds {asset.asset_number}",
            }
        )
    if accumulated > 0:
        lines.append(
            {
                "account_id": atype.accumulated_depreciation_account_id,
                "debit": accumulated,
                "credit": Decimal("0"),
                "description": f"Derecognize accumulated {asset.asset_number}",
            }
        )
    lines.append(
        {
            "account_id": atype.asset_account_id,
            "debit": Decimal("0"),
            "credit": cost,
            "description": f"Derecognize cost {asset.asset_number}",
        }
    )
    residual = proceeds + accumulated - cost  # >0 gain, <0 loss
    if residual != 0:
        lines.append(
            {
                "account_id": _disposal_account_id(db),
                "debit": -residual if residual < 0 else Decimal("0"),
                "credit": residual if residual > 0 else Decimal("0"),
                "description": f"{'Gain' if residual > 0 else 'Loss'} on disposal {asset.asset_number}",
            }
        )
    txn = create_journal_entry(
        db,
        disposal_date,
        f"Disposal — {asset.asset_number} {asset.name}",
        lines,
        source_type="asset_disposal",
        source_id=asset.id,
    )
    asset.status = FixedAssetStatus.DISPOSED
    asset.disposal_date = disposal_date
    asset.disposal_proceeds = proceeds
    db.commit()
    return {
        "transaction_id": txn.id,
        "gain_loss": float(_q(residual)),
    }


def import_assets_csv(db: Session, csv_text: str) -> dict:
    """Import assets from the CSV template:
    name,asset_type,purchase_date,purchase_price,salvage_value,description

    Asset types are matched by exact name and must exist (strict, like
    the IIF importer's vendor handling): a typo'd type surfaces as a row
    error rather than silently creating a half-configured type.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {"name", "asset_type", "purchase_date", "purchase_price"}
    headers = set(reader.fieldnames or [])
    if not required.issubset(headers):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must include columns: {sorted(required)}",
        )
    imported = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        # Row messages are CONSTRUCTED, never str(exc): CodeQL
        # (py/stack-trace-exposure) treats any exception text flowing to
        # the response as internals disclosure, builtin messages included.
        type_name = (row.get("asset_type") or "").strip()
        atype = (
            db.query(FixedAssetType).filter(FixedAssetType.name == type_name).first()
        )
        if not atype:
            errors.append({"row": i, "message": f"asset type '{type_name}' not found"})
            continue

        name = (row.get("name") or "").strip()
        if not name:
            errors.append({"row": i, "message": "name is required"})
            continue

        raw_date = (row.get("purchase_date") or "").strip()
        try:
            purchase_date = date.fromisoformat(raw_date)
        except ValueError:
            errors.append(
                {
                    "row": i,
                    "message": f"invalid purchase_date {raw_date!r} (expected YYYY-MM-DD)",
                }
            )
            continue

        amounts = {}
        bad_amount = False
        for field in ("purchase_price", "salvage_value"):
            raw = (row.get(field) or "0").strip() or "0"
            try:
                amounts[field] = _q(Decimal(raw))
            except InvalidOperation:
                errors.append(
                    {
                        "row": i,
                        "message": f"invalid {field} {raw!r} (expected a number)",
                    }
                )
                bad_amount = True
        if bad_amount:
            continue
        if amounts["purchase_price"] <= 0:
            errors.append({"row": i, "message": "purchase_price must be more than 0"})
            continue
        if not 0 <= amounts["salvage_value"] <= amounts["purchase_price"]:
            errors.append(
                {
                    "row": i,
                    "message": (
                        f"salvage_value {amounts['salvage_value']} must be between 0 "
                        f"and the purchase_price {amounts['purchase_price']}"
                    ),
                }
            )
            continue

        try:
            db.add(
                FixedAsset(
                    asset_number=next_asset_number(db),
                    name=name,
                    asset_type_id=atype.id,
                    purchase_date=purchase_date,
                    purchase_price=amounts["purchase_price"],
                    salvage_value=amounts["salvage_value"],
                    description=(row.get("description") or "").strip() or None,
                )
            )
            db.flush()
            imported += 1
        except Exception:
            # Unexpected failures: log the detail server-side, return a
            # generic row error, roll back so one bad row can't poison
            # the batch.
            logger.exception("Asset CSV import failed on row %s", i)
            errors.append({"row": i, "message": "Unexpected error importing this row"})
            db.rollback()
    db.commit()
    return {"imported": imported, "errors": errors}


# ---------------------------------------------------------------------------
# The purchase in the books.
#
# Registering a $12,000 asset posted nothing to its asset account, but
# depreciation then credited accumulated depreciation, so the balance sheet
# showed negative net equipment (exploratory 2.17.3, W-M19). Registering
# now says how the purchase reaches the books:
#   paid_from        DR asset account / CR the bank or card account
#   opening_balance  owned before these books began: DR asset account /
#                    CR accumulated depreciation (what was already taken) /
#                    CR 3900 Opening Balance Equity (the rest)
#   bill | expense   bought on a bill or expense already entered: its cost
#                    is moved from the expense account(s) the document
#                    posted to into the asset account (nothing moves when
#                    the document posted to the asset account itself)
#   in_books         an opening balance or journal entry already put it in
#                    the asset account: nothing posts, and the account must
#                    hold at least the cost of every asset registered to it
# ---------------------------------------------------------------------------

ACQUISITION_METHODS = ("paid_from", "opening_balance", "bill", "expense", "in_books")
_ACQUISITION_SOURCES = ("asset_acquisition", "opening_balance")


def acquisition_transactions(db: Session, asset_ids) -> dict[int, int]:
    """asset id -> the journal entry that put its purchase in the books
    (a voided one doesn't count)."""
    ids = [int(i) for i in asset_ids]
    if not ids:
        return {}
    rows = (
        db.query(Transaction.source_id, Transaction.id)
        .filter(
            Transaction.source_type.in_(_ACQUISITION_SOURCES),
            Transaction.source_id.in_(ids),
        )
        .all()
    )
    voided = voided_transaction_ids(db, [txn_id for _, txn_id in rows])
    return {aid: txn_id for aid, txn_id in rows if txn_id not in voided}


def _asset_account(atype: FixedAssetType) -> int:
    if not atype.asset_account_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Asset type '{atype.name}' has no fixed-asset account. Choose one "
                "for the type first, so the purchase has somewhere to go."
            ),
        )
    return atype.asset_account_id


def registered_cost(db: Session, asset_account_id: int) -> Decimal:
    """The cost of every registered asset whose type keeps it in this account."""
    total = Decimal("0")
    for asset in (
        db.query(FixedAsset)
        .join(FixedAssetType, FixedAsset.asset_type_id == FixedAssetType.id)
        .filter(
            FixedAssetType.asset_account_id == asset_account_id,
            FixedAsset.status == FixedAssetStatus.REGISTERED,
        )
        .all()
    ):
        total += Decimal(str(asset.purchase_price))
    return _q(total)


def books_cover(db: Session, asset_account_id: int) -> tuple[bool, Decimal, Decimal]:
    """(the account holds every registered asset's cost, what it holds,
    what the register says it should)."""
    held = _q(gl_balance(db, asset_account_id))
    registered = registered_cost(db, asset_account_id)
    return held >= registered, held, registered


def _document(db: Session, acq) -> tuple[Transaction, str, str]:
    """The bill's or expense's journal entry, how to name it, and its
    reference number (a bill's own number; an expense's reference)."""
    if acq.method == "bill":
        bill = db.get(Bill, acq.bill_id) if acq.bill_id else None
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")
        if bill.status == BillStatus.VOID:
            raise HTTPException(status_code=400, detail="That bill is void.")
        txn = db.get(Transaction, bill.transaction_id) if bill.transaction_id else None
        if txn is None:
            raise HTTPException(
                status_code=400, detail="That bill isn't in the books yet."
            )
        # (lengths capped: the label ends up in a 300-character line memo)
        label = f"bill {(bill.bill_number or '')[:60]}"
        reference = bill.bill_number or ""
    else:
        txn = (
            db.query(Transaction)
            .filter(
                Transaction.id == (acq.expense_id or 0),
                Transaction.source_type == "expense",
            )
            .first()
        )
        if txn is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        payee = (txn.description or "").removeprefix("Expense: ").strip()
        label = f"expense to {(payee or 'no payee')[:60]}"
        if txn.reference:
            label += f" ({txn.reference[:40]})"
        reference = txn.reference or ""
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail=f"That {acq.method} is void.")
    return txn, label, reference


def _capitalize(db: Session, asset: FixedAsset, acq, asset_account_id: int):
    """Move the asset's cost out of the expense account(s) a bill or expense
    posted it to."""
    txn, label, reference = _document(db, acq)
    cost = _q(Decimal(str(asset.purchase_price)))
    on_asset = Decimal("0")
    spent = defaultdict(Decimal)
    for ln in txn.lines:
        if ln.account_id == asset_account_id:
            on_asset += Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))
        elif (
            ln.account
            and ln.account.account_type in (AccountType.EXPENSE, AccountType.COGS)
            and ln.debit
        ):
            spent[ln.account_id] += Decimal(str(ln.debit))
    if on_asset > 0:
        # The document put the purchase in the asset account already.
        if on_asset < cost:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The {label} put {money_text(on_asset)} in the asset account; "
                    f"this asset costs {money_text(cost)}. Check the purchase price."
                ),
            )
        return None
    # What other assets have already taken from this document.
    tag = f" capitalized from {label}"
    for other in (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "asset_acquisition",
            Transaction.date == txn.date,
        )
        .all()
    ):
        if other.id in voided_transaction_ids(db, [other.id]):
            continue
        for ln in other.lines:
            if ln.credit and (ln.description or "").endswith(tag):
                spent[ln.account_id] -= Decimal(str(ln.credit))
    available = _q(sum((v for v in spent.values() if v > 0), Decimal("0")))
    if available < cost:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The {label} has {money_text(available)} on expense accounts that "
                f"isn't an asset yet; this asset costs {money_text(cost)}. Check "
                "the purchase price, or choose the account it was paid from instead."
            ),
        )
    lines = [
        {
            "account_id": asset_account_id,
            "debit": cost,
            "credit": Decimal("0"),
            "description": f"{asset.asset_number} {asset.name}",
        }
    ]
    remaining = cost
    for account_id, amount in sorted(spent.items(), key=lambda kv: (-kv[1], kv[0])):
        if remaining <= 0 or amount <= 0:
            continue
        take = min(amount, remaining)
        lines.append(
            {
                "account_id": account_id,
                "debit": Decimal("0"),
                "credit": _q(take),
                "description": f"{asset.asset_number}{tag}",
            }
        )
        remaining -= take
    return create_journal_entry(
        db,
        txn.date,
        f"Capitalize {asset.asset_number} {asset.name} from the {label}",
        lines,
        source_type="asset_acquisition",
        source_id=asset.id,
        reference=reference,
    )


def post_acquisition(db: Session, asset: FixedAsset, acq):
    """Put the asset's purchase in the books (see the block comment above).
    Flushes; the caller commits. Returns the journal entry, or None when
    nothing needed posting."""
    if acq.method not in ACQUISITION_METHODS:
        raise HTTPException(
            status_code=400, detail="Choose how the asset was paid for."
        )
    atype = asset.asset_type or db.get(FixedAssetType, asset.asset_type_id)
    asset_account_id = _asset_account(atype)
    cost = _q(Decimal(str(asset.purchase_price)))
    name = f"{asset.asset_number} {asset.name}"

    if acq.method == "in_books":
        covered, held, registered = books_cover(db, asset_account_id)
        if not covered:
            acct = db.get(Account, asset_account_id)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{acct.name} holds {money_text(held)} in the books, less than "
                    f"the {money_text(registered)} of assets registered to it with "
                    f"this one. Its {money_text(cost)} purchase isn't in the books "
                    "yet: choose how it was paid for, so it is posted."
                ),
            )
        return None

    if acq.method == "paid_from":
        paid_from = db.get(Account, acq.account_id) if acq.account_id else None
        if paid_from is None or not paid_from.bank_kind:
            raise HTTPException(
                status_code=400,
                detail="Pick the bank or card account the asset was paid from.",
            )
        return create_journal_entry(
            db,
            asset.purchase_date,
            f"Purchase: {name}",
            [
                {
                    "account_id": asset_account_id,
                    "debit": cost,
                    "credit": Decimal("0"),
                    "description": name,
                },
                {
                    "account_id": paid_from.id,
                    "debit": Decimal("0"),
                    "credit": cost,
                    "description": name,
                },
            ],
            source_type="asset_acquisition",
            source_id=asset.id,
            reference=acq.reference or "",
        )

    if acq.method == "opening_balance":
        as_of = acq.as_of or asset.purchase_date
        if as_of < asset.purchase_date:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The day your books began can't be before the purchase date. "
                    "An asset bought after that was paid from a bank or card account."
                ),
            )
        taken = _q(Decimal(str(acq.accumulated_depreciation or 0)))
        depreciable = cost - _q(Decimal(str(asset.salvage_value or 0)))
        if taken < 0 or taken > depreciable:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Depreciation already taken must be between $0.00 and "
                    f"{money_text(depreciable)} (the cost less the salvage value)."
                ),
            )
        if taken and Decimal(str(asset.accumulated_depreciation or 0)):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This asset has depreciation posted in these books already; "
                    "post its opening balance at cost, with no depreciation taken."
                ),
            )
        lines = [
            {
                "account_id": asset_account_id,
                "debit": cost,
                "credit": Decimal("0"),
                "description": name,
            }
        ]
        if taken:
            _require_type_accounts(atype)
            lines.append(
                {
                    "account_id": atype.accumulated_depreciation_account_id,
                    "debit": Decimal("0"),
                    "credit": taken,
                    "description": f"Depreciation taken before these books: {name}",
                }
            )
        if cost - taken:
            lines.append(
                {
                    "account_id": get_opening_balance_equity_id(db),
                    "debit": Decimal("0"),
                    "credit": cost - taken,
                    "description": name,
                }
            )
        txn = create_journal_entry(
            db,
            as_of,
            f"Opening balance: {name}",
            lines,
            source_type="opening_balance",
            source_id=asset.id,
        )
        if taken:
            asset.accumulated_depreciation = taken
        if as_of > asset.purchase_date and not asset.last_depreciation_date:
            # What was taken before the books began is the figure above;
            # the books depreciate it from here.
            asset.last_depreciation_date = as_of
        return txn

    return _capitalize(db, asset, acq, asset_account_id)
