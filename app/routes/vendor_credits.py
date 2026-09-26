# ============================================================================
# Vendor Credits — the AP-side counterpart of a customer credit memo
#
# Issue #129 (CimarronSiteServices). A bill posts DR Expense / CR A/P; a
# vendor credit is its mirror, DR A/P / CR Expense. Applying one to a bill
# posts NOTHING — A/P already moved when the credit was issued, and the
# application only decides which bill it settles. That is exactly how a
# credit memo behaves against an invoice.
#
# The reason this is a document and not a journal entry: a manual JE against
# 2000 moves the general ledger without moving the vendor sub-ledger, so A/P
# aging and the vendor's balance stop agreeing with the control account. The
# reporter named that himself, and it is the same split as #119.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.bills import Bill, BillStatus
from app.models.contacts import Vendor
from app.models.items import Item
from app.models.transactions import Transaction
from app.models.vendor_credits import (
    VendorCredit,
    VendorCreditApplication,
    VendorCreditLine,
    VendorCreditStatus,
)
from app.routes._helpers import clamp_pagination
from app.schemas.vendor_credits import (
    VendorCreditApplicationCreate,
    VendorCreditCreate,
    VendorCreditResponse,
)
from app.services.accounting import (
    _q,
    compute_line_totals,
    create_journal_entry,
    get_ap_account_id,
    reversing_lines,
)
from app.services.inventory_service import get_inventory_asset_account_id
from app.services.closing_date import check_closing_date
from app.services.numbering import next_vendor_credit_number
from app.services.purchase_posting import (
    expense_account_for,
    spread,
    unit_cost_with_tax,
)

router = APIRouter(prefix="/api/vendor-credits", tags=["vendor_credits"])


def _with_vendor_name(vc: VendorCredit) -> VendorCreditResponse:
    resp = VendorCreditResponse.model_validate(vc)
    resp.vendor_name = vc.vendor.name if vc.vendor else None
    return resp


@router.get("", response_model=list[VendorCreditResponse])
def list_vendor_credits(
    vendor_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    q = db.query(VendorCredit).options(
        joinedload(VendorCredit.vendor),
        selectinload(VendorCredit.lines),
    )
    if vendor_id:
        q = q.filter(VendorCredit.vendor_id == vendor_id)
    if status:
        q = q.filter(VendorCredit.status == status)
    rows = q.order_by(VendorCredit.date.desc(), VendorCredit.id.desc())
    rows = rows.offset(skip).limit(limit).all()
    return [_with_vendor_name(vc) for vc in rows]


@router.get("/{vc_id}", response_model=VendorCreditResponse)
def get_vendor_credit(vc_id: int, db: Session = Depends(get_db)):
    vc = (
        db.query(VendorCredit)
        .options(joinedload(VendorCredit.vendor), selectinload(VendorCredit.lines))
        .filter(VendorCredit.id == vc_id)
        .first()
    )
    if not vc:
        raise HTTPException(status_code=404, detail="Vendor credit not found")
    return _with_vendor_name(vc)


@router.post("", response_model=VendorCreditResponse, status_code=201)
def create_vendor_credit(data: VendorCreditCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    if data.original_bill_id is not None:
        bill = db.query(Bill).filter(Bill.id == data.original_bill_id).first()
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")
        if bill.vendor_id != data.vendor_id:
            raise HTTPException(
                status_code=400,
                detail="That bill belongs to a different vendor.",
            )

    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "A vendor credit must be for more than zero. Enter the credit "
                "as a positive amount — the document is what makes it a credit."
            ),
        )

    vc = None
    # Same retry as invoices and credit memos: next_vendor_credit_number is
    # MAX+1 with no row lock, so two concurrent creates can compute the same
    # number and one loses at the UNIQUE constraint on flush.
    for _ in range(10):
        vc = VendorCredit(
            credit_number=next_vendor_credit_number(db),
            vendor_id=data.vendor_id,
            date=data.date,
            original_bill_id=data.original_bill_id,
            ref_number=data.ref_number,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            balance_remaining=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            status=VendorCreditStatus.ISSUED,
        )
        db.add(vc)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "credit_number" not in str(e.orig).lower():
                raise
            db.rollback()
            vc = None
    if vc is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a vendor credit number — please retry.",
        )

    journal_lines = []
    returns = []  # [(item, quantity, unit_cost), ...] for inventory lines

    # Round per line so the stored line amount matches compute_line_totals
    # and the credits land on the same cents as the rounded A/P debit.
    amounts = [
        _q(Decimal(str(ln.quantity)) * Decimal(str(ln.rate))) for ln in data.lines
    ]
    # The mirror of a bill: tax on a purchase was part of what the lines
    # cost, so the tax coming back reduces those same lines. It never
    # credits Sales Tax Payable, which is the tax owed on sales.
    tax_shares = spread(tax_amount, amounts)

    for i, line_data in enumerate(data.lines):
        amt = amounts[i]
        item = None
        if line_data.item_id:
            item = db.query(Item).filter(Item.id == line_data.item_id).first()

        if item is not None and item.track_inventory:
            # Returning stock to the vendor takes it off the shelf, so the
            # credit goes against the Inventory asset, not an expense — the
            # exact mirror of how a bill receives it. Refuse rather than
            # silently posting to an expense account, which is what
            # create_bill does for the same reason.
            posting_acct = get_inventory_asset_account_id(db, item)
            if not posting_acct:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Item '{item.name}' is inventory-tracked but has no "
                        "asset_account_id and no account #1300 (Inventory) is "
                        "seeded. Either set the item's inventory asset account "
                        "or add the Inventory account to the chart of accounts."
                    ),
                )
            if line_data.quantity > 0:
                returns.append(
                    (
                        item,
                        Decimal(str(line_data.quantity)),
                        unit_cost_with_tax(
                            amt, tax_shares[i], line_data.quantity, line_data.rate
                        ),
                    )
                )
        elif amt > 0 or line_data.account_id:
            posting_acct = expense_account_for(
                db,
                line_no=i + 1,
                description=line_data.description,
                account_id=line_data.account_id,
                item=item,
                vendor=vendor,
            )
        else:
            posting_acct = (item.expense_account_id if item else None) or (
                vendor.default_expense_account_id
            )

        db.add(
            VendorCreditLine(
                vendor_credit_id=vc.id,
                item_id=line_data.item_id,
                account_id=posting_acct,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=amt,
                job_id=line_data.job_id,
                class_id=line_data.class_id,
                cost_code_id=line_data.cost_code_id,
                line_order=line_data.line_order or i,
            )
        )

        if amt > 0 and posting_acct:
            journal_lines.append(
                {
                    "account_id": posting_acct,
                    "debit": Decimal("0"),
                    "credit": amt + tax_shares[i],
                    "description": line_data.description or "",
                    "job_id": line_data.job_id,
                    "class_id": line_data.class_id,
                    "cost_code_id": line_data.cost_code_id,
                }
            )

    ap_id = get_ap_account_id(db)
    journal_lines.append(
        {
            "account_id": ap_id,
            "debit": total,
            "credit": Decimal("0"),
            "description": f"Vendor Credit {vc.credit_number}",
        }
    )
    txn = create_journal_entry(
        db,
        data.date,
        f"Vendor Credit {vc.credit_number} - {vendor.name}",
        journal_lines,
        source_type="vendor_credit",
        source_id=vc.id,
        reference=vc.ref_number,
        class_id=vc.class_id,
        job_id=vc.job_id,
    )
    vc.transaction_id = txn.id

    if returns:
        from app.models.items import MovementType
        from app.services.inventory_service import _append_movement

        for item, qty, unit_cost in returns:
            # Negative quantity: the goods went back to the vendor. Costed at
            # the credit's own rate, not avg_cost, so a bill and the credit
            # that reverses it net to zero on both quantity and value.
            _append_movement(
                db,
                item,
                MovementType.RETURN_OUT,
                quantity=-qty,
                unit_cost=unit_cost,
                source_type="vendor_credit",
                source_id=vc.id,
                transaction_id=txn.id,
                memo=f"Return to vendor: {vc.credit_number}",
            )

    db.commit()
    db.refresh(vc)
    return _with_vendor_name(vc)


@router.post("/{vc_id}/apply")
def apply_vendor_credit(
    vc_id: int, data: VendorCreditApplicationCreate, db: Session = Depends(get_db)
):
    """Settle part of a bill with a credit the vendor already gave you.

    Posts nothing. A/P moved when the credit was issued; this decides which
    bill it pays down, and both sub-ledger rows move together so the aging
    stays tied to account 2000.
    """
    # Lock both rows for the read-check-write. Two concurrent applies would
    # otherwise both read the same balance_remaining and both pass the check,
    # spending the credit twice. No-op on SQLite; a real lock on Postgres.
    vc = (
        db.query(VendorCredit)
        .filter(VendorCredit.id == vc_id)
        .with_for_update()
        .first()
    )
    if not vc:
        raise HTTPException(status_code=404, detail="Vendor credit not found")
    if vc.status == VendorCreditStatus.VOID:
        raise HTTPException(status_code=400, detail="Vendor credit is voided")

    bill = db.query(Bill).filter(Bill.id == data.bill_id).with_for_update().first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="That bill is voided")
    if bill.vendor_id != vc.vendor_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "That bill belongs to a different vendor. A credit can only "
                "be applied to bills from the vendor who issued it."
            ),
        )

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be more than zero")
    if amount > Decimal(str(vc.balance_remaining or 0)):
        raise HTTPException(status_code=400, detail="Amount exceeds credit balance")
    if amount > Decimal(str(bill.balance_due or 0)):
        raise HTTPException(status_code=400, detail="Amount exceeds bill balance")

    db.add(
        VendorCreditApplication(
            vendor_credit_id=vc.id,
            bill_id=data.bill_id,
            amount=amount,
        )
    )

    vc.amount_applied = Decimal(str(vc.amount_applied or 0)) + amount
    vc.balance_remaining = Decimal(str(vc.balance_remaining or 0)) - amount
    if vc.balance_remaining == 0:
        vc.status = VendorCreditStatus.APPLIED

    bill.amount_paid = Decimal(str(bill.amount_paid or 0)) + amount
    bill.balance_due = Decimal(str(bill.balance_due or 0)) - amount
    bill.status = BillStatus.PAID if bill.balance_due == 0 else BillStatus.PARTIAL

    db.commit()
    return {
        "message": f"Applied {amount} to bill {bill.bill_number}",
        "credit_balance_remaining": str(vc.balance_remaining),
        "bill_balance_due": str(bill.balance_due),
    }


@router.post("/{vc_id}/void", response_model=VendorCreditResponse)
def void_vendor_credit(vc_id: int, db: Session = Depends(get_db)):
    """Reverse a vendor credit: put every application back on its bill, post
    the mirror-image entry, put returned stock back on the shelf, and mark it
    void."""
    from app.models.items import MovementType
    from app.services.inventory_service import _append_movement

    vc = (
        db.query(VendorCredit)
        .filter(VendorCredit.id == vc_id)
        .with_for_update()
        .first()
    )
    if not vc:
        raise HTTPException(status_code=404, detail="Vendor credit not found")
    if vc.status == VendorCreditStatus.VOID:
        raise HTTPException(status_code=400, detail="Vendor credit is already void")
    check_closing_date(db, vc.date)

    for app_row in list(vc.applications):
        bill = (
            db.query(Bill).filter(Bill.id == app_row.bill_id).with_for_update().first()
        )
        if bill:
            amt = Decimal(str(app_row.amount))
            bill.amount_paid = Decimal(str(bill.amount_paid or 0)) - amt
            bill.balance_due = Decimal(str(bill.balance_due or 0)) + amt
            bill.status = (
                BillStatus.PARTIAL if bill.amount_paid > 0 else BillStatus.UNPAID
            )
        db.delete(app_row)

    if vc.transaction_id:
        txn = db.query(Transaction).filter(Transaction.id == vc.transaction_id).first()
        if txn is not None:
            create_journal_entry(
                db,
                vc.date,
                f"VOID Vendor Credit {vc.credit_number}",
                reversing_lines(txn.lines),
                source_type="vendor_credit_void",
                source_id=vc.id,
                class_id=vc.class_id,
                job_id=vc.job_id,
            )

    for line in vc.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory and line.quantity > 0:
            _append_movement(
                db,
                item,
                MovementType.VOID,
                quantity=Decimal(str(line.quantity)),
                unit_cost=Decimal(str(line.rate or 0)),
                source_type="vendor_credit_void",
                source_id=vc.id,
                memo=f"VOID Vendor Credit {vc.credit_number}",
            )

    vc.amount_applied = Decimal("0")
    vc.balance_remaining = Decimal("0")
    vc.status = VendorCreditStatus.VOID
    db.commit()
    db.refresh(vc)
    return _with_vendor_name(vc)
