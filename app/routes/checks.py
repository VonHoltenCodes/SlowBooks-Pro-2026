# ============================================================================
# Check Printing — Generate check PDFs (standard 3-per-page format)
# ============================================================================

from datetime import date
from decimal import Decimal

from pydantic import Field
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payments import Payment
from app.models.bills import BillPayment, Bill
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.services.bank_register import payees_for, voided_transaction_ids
from app.models.accounts import Account
from app.schemas.common import StrictModel
from app.services.check_numbers import (
    next_check_number,
    refuse_duplicate,
    unprinted_payments,
)
from app.services.closing_date import check_closing_date
from app.services.pdf_service import generate_check_pdf, generate_checks_pdf
from app.services.request_utils import content_disposition
from app.routes.settings import ENUM_SETTINGS, _get_all as get_settings
from app.services.settings_service import SettingValueError, clean_setting_value

router = APIRouter(prefix="/api/checks", tags=["checks"])

# The postings a check is written in without a bill behind it: a bank
# register entry (+ Entry or the quick-entry row) and an Expense.
REGISTER_CHECK_SOURCES = ("bank_entry", "expense")


def vendor_address_fields(vendor) -> dict:
    """The payee's postal address for the check face (#726), merged into a
    check's data: the payee's name and the vendor's street lines, then
    "City, ST 12345". A vendor with no address (or no vendor) gives an empty
    list, and the face prints no address block at all."""
    if vendor is None:
        return {"payee_address": []}
    street = [
        (line or "").strip()
        for line in (vendor.address1, vendor.address2)
        if (line or "").strip()
    ]
    city = (vendor.city or "").strip()
    state = (vendor.state or "").strip()
    zip_code = (vendor.zip or "").strip()
    region = " ".join(x for x in (state, zip_code) if x)
    last = ", ".join(x for x in (city, region) if x)
    lines = street + ([last] if last else [])
    if not lines:
        return {"payee_address": []}
    return {"payee_address": [vendor.name, *lines] if vendor.name else lines}


def register_check_data(db: Session, txn_id: int) -> dict:
    """The check behind a register entry or an expense (discussion #354):
    money out of a bank account, payee from the vendor or the entry, the
    reference as the check number, each split line on the stub. A deposit,
    a card charge, a transfer, a void and a bill payment are refused with
    the reason; a bill payment prints from its bill."""
    txn = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.source_type == "bill_payment":
        raise HTTPException(
            status_code=400,
            detail="A bill payment's check prints from the bill it paid.",
        )
    if txn.source_type not in REGISTER_CHECK_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a bank register entry or an expense prints as a check; "
                "this entry is neither."
            ),
        )
    bank_line = next(
        (ln for ln in txn.lines if ln.account and ln.account.bank_kind == "bank"),
        None,
    )
    if bank_line is None:
        paid_by_card = any(
            ln.account and ln.account.bank_kind == "credit_card" for ln in txn.lines
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "This was paid by card, so there is no check to print."
                if paid_by_card
                else "This entry does not touch a bank account, so there is no check to print."
            ),
        )
    if (bank_line.credit or Decimal("0")) <= 0:
        raise HTTPException(
            status_code=400,
            detail="This is money coming in, so there is no check to print.",
        )
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(
            status_code=400, detail="This entry is void, so there is no check to print."
        )
    payee = payees_for(db, [txn]).get(txn.id, "") or ""
    # the stub lists what the check paid: the category of each line, with
    # its memo when the line carries one of its own. A credit line is a
    # refund netted against the check (#387), shown as a negative amount so
    # the lines add up to the check (e.g. 620.00 expense less a 120.00
    # refund = a 500.00 check).
    details = []
    for ln in txn.lines:
        if ln is bank_line:
            continue
        debit = ln.debit or Decimal("0")
        credit = ln.credit or Decimal("0")
        if debit <= 0 and credit <= 0:
            continue
        name = ln.account.name if ln.account else ""
        memo = (ln.description or "").strip()
        if memo and memo != payee and memo != name:
            name = f"{name} — {memo}" if name else memo
        amount = debit if debit > 0 else -credit
        details.append({"description": name or "Payment", "amount": amount})
    memo = (bank_line.description or "").strip()
    # The vendor behind the check, for its address (#726): an expense names
    # its vendor as its source, a register entry on its own row (#491).
    vendor_id = txn.source_id if txn.source_type == "expense" else txn.vendor_id
    vendor = (
        db.query(Vendor).filter(Vendor.id == vendor_id).first() if vendor_id else None
    )
    return {
        "payee": payee,
        "date": txn.date,
        "amount": bank_line.credit,
        "check_number": txn.reference or "",
        "memo": "" if memo == payee else memo,
        "details": details,
        "bank_account": bank_line.account.name,
        **vendor_address_fields(vendor),
    }


def bill_payment_check_data(db: Session, bp: BillPayment) -> dict:
    """The check a bill payment prints: the vendor, the payment's memo and
    each bill it paid on the stub, shared by the single and the batch print."""
    vendor = db.query(Vendor).filter(Vendor.id == bp.vendor_id).first()
    check_data = {
        "payee": vendor.name if vendor else "Unknown",
        "date": bp.date,
        "amount": bp.amount,
        "check_number": bp.check_number or "",
        "memo": (bp.notes or "").strip(),
        "details": [],
        "bank_account": "",
        **vendor_address_fields(vendor),
    }
    if bp.pay_from_account_id:
        bank = db.query(Account).filter(Account.id == bp.pay_from_account_id).first()
        check_data["bank_account"] = bank.name if bank else ""
    for alloc in bp.allocations:
        bill = db.query(Bill).filter(Bill.id == alloc.bill_id).first()
        check_data["details"].append(
            {
                "description": (
                    f"Bill #{bill.bill_number}" if bill else f"Bill #{alloc.bill_id}"
                ),
                "amount": alloc.amount,
            }
        )
    # The stub adds up to the check: a part paid ahead of any bill is
    # listed as such rather than left out.
    applied = sum((a.amount for a in bp.allocations), Decimal("0"))
    if bp.amount - applied > 0:
        check_data["details"].append(
            {
                "description": "Paid ahead (not applied to a bill)",
                "amount": bp.amount - applied,
            }
        )
    return check_data


@router.get("/test-print")
def test_print_check(
    check_stock: str = Query(default=None),
    check_position: str = Query(default=None),
    check_nudge_x_in: str = Query(default=None),
    check_nudge_y_in: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """A sample check with placeholder values (#440), printed through the
    same renderer and the same check-stock settings as a real check, so the
    alignment nudge can be set by holding the sheet against the stock.

    The Settings page passes the form's current (unsaved) stock, check
    position (#727) and nudge, so a nudge can be tried before it is saved;
    each is checked the way saving it would be. Any left out reads the saved
    setting. Nothing is stored. The sample carries a fictional payee address
    (#726) so the address block can be lined up too."""
    check_data = {
        "payee": "Sample Payee",
        "date": date.today(),
        "amount": Decimal("1234.56"),
        "check_number": "0000",
        "memo": "Alignment test",
        "details": [{"description": "Alignment test", "amount": Decimal("1234.56")}],
        "bank_account": "Sample Checking",
        "payee_address": ["Sample Payee", "123 Any Street", "Anytown, ST 00000"],
    }
    company = dict(get_settings(db))
    if check_stock is not None:
        if check_stock not in ENUM_SETTINGS["check_stock"]:
            raise HTTPException(
                status_code=422,
                detail="check_stock must be one of: plain, preprinted",
            )
        company["check_stock"] = check_stock
    if check_position is not None:
        if check_position not in ENUM_SETTINGS["check_position"]:
            raise HTTPException(
                status_code=422,
                detail="check_position must be one of: bottom, top",
            )
        company["check_position"] = check_position
    for key, value in (
        ("check_nudge_x_in", check_nudge_x_in),
        ("check_nudge_y_in", check_nudge_y_in),
    ):
        if value is not None:
            try:
                company[key] = clean_setting_value(key, value)
            except SettingValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition("Check_test_print.pdf")},
    )


@router.get("/print")
def print_check(
    payment_id: int = Query(default=None),
    bill_payment_id: int = Query(default=None),
    transaction_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Generate a check PDF for a bill payment, or for a check written
    straight into the bank register or as an expense (`transaction_id`,
    discussion #354) — money going out.

    A customer payment is money coming IN: the check was written by the
    customer, so there is nothing to print. Offering it printed a check
    payable to the customer for the amount they paid us, with the stub
    listing internal invoice ids (explore 2.17.3, W-M9 / F12)."""
    if payment_id and not bill_payment_id:
        if not db.query(Payment).filter(Payment.id == payment_id).first():
            raise HTTPException(status_code=404, detail="Payment not found")
        raise HTTPException(
            status_code=400,
            detail=(
                "This is a payment you received, so there is no check to print. "
                "Checks print for bills you pay."
            ),
        )
    if bill_payment_id:
        bp = db.query(BillPayment).filter(BillPayment.id == bill_payment_id).first()
        if not bp:
            raise HTTPException(status_code=404, detail="Bill payment not found")
        check_data = bill_payment_check_data(db, bp)
    elif transaction_id:
        check_data = register_check_data(db, transaction_id)
    else:
        raise HTTPException(
            status_code=400, detail="Provide bill_payment_id or transaction_id"
        )

    company = get_settings(db)
    pdf_bytes = generate_check_pdf(check_data, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Check_{check_data['check_number']}.pdf"
                if check_data["check_number"]
                else "Check.pdf"
            )
        },
    )


class BatchCheck(StrictModel):
    bill_payment_id: int
    check_number: str = Field(min_length=1, max_length=50)


class PrintBatch(StrictModel):
    bank_account_id: int
    checks: list[BatchCheck] = Field(min_length=1)


def _bank_account(db: Session, account_id: int) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.bank_kind != "bank":
        raise HTTPException(
            status_code=400, detail="Checks are written on a bank account."
        )
    return account


@router.get("/unprinted")
def unprinted_checks(
    bank_account_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """The checks Pay Bills wrote on a bank account that are waiting to be
    printed (#603), oldest first, with the number the next one would take."""
    _bank_account(db, bank_account_id)
    payments = unprinted_payments(db, bank_account_id)
    vendors = {
        v.id: v.name
        for v in db.query(Vendor).filter(Vendor.id.in_({p.vendor_id for p in payments}))
    }
    return {
        "bank_account_id": bank_account_id,
        "next_check_number": next_check_number(db, bank_account_id),
        "checks": [
            {
                "bill_payment_id": p.id,
                "payee": vendors.get(p.vendor_id, "Unknown"),
                "date": p.date.isoformat(),
                "amount": f"{p.amount:.2f}",
                "memo": (p.notes or "").strip(),
            }
            for p in payments
        ],
    }


@router.post("/print-batch")
def print_check_batch(
    data: PrintBatch,
    db: Session = Depends(get_db),
    allow_duplicate_check_number: bool = Query(False),
):
    """Print several waiting checks as one PDF, one per page of the
    3-per-page stock in the order sent, and mark each printed by giving it
    the number it was printed with (#603). The numbers are the person's:
    each is digits and none repeats within the batch. A number the account
    already has is refused the way a single check's is (#481): the 409 that
    names the uses, under the duplicate-number warning setting, answered
    with ?allow_duplicate_check_number=true to keep it. Nothing is numbered
    unless every check is accepted and the PDF renders."""
    _bank_account(db, data.bank_account_id)
    ids = [c.bill_payment_id for c in data.checks]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="A check is listed twice.")
    numbers = [c.check_number.strip() for c in data.checks]
    if not all(n.isdigit() for n in numbers):
        raise HTTPException(
            status_code=400, detail="A check number is digits alone, like 1051."
        )
    # A number is the text it is stored and looked up as ("01051" and "1051"
    # are two numbers, as uses_of has it), so the batch is compared the same way
    if len(set(numbers)) != len(numbers):
        raise HTTPException(
            status_code=400, detail="Two checks in the batch have the same number."
        )
    waiting = {p.id: p for p in unprinted_payments(db, data.bank_account_id)}
    payments = []
    for pid in ids:
        bp = waiting.get(pid)
        if bp is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Bill payment {pid} is not waiting to be printed on this "
                    "account (already numbered, void, or paid another way)."
                ),
            )
        check_closing_date(db, bp.date)
        payments.append(bp)
    for number in numbers:
        refuse_duplicate(
            db, data.bank_account_id, number, allow=allow_duplicate_check_number
        )
    checks = []
    for bp, number in zip(payments, numbers):
        check = bill_payment_check_data(db, bp)
        check["check_number"] = number
        checks.append(check)
    pdf_bytes = generate_checks_pdf(checks, get_settings(db))
    for bp, number in zip(payments, numbers):
        bp.check_number = number
    db.commit()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Checks_{numbers[0]}-{numbers[-1]}.pdf"
                if len(numbers) > 1
                else f"Check_{numbers[0]}.pdf"
            )
        },
    )
