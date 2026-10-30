# ============================================================================
# Payment Terms — the terms list (#664), kept under Settings
#
# Every form that takes terms offers this list, and every document's due
# date comes from app/services/payment_terms.py. Writes are an
# administrator's (_ADMIN_WRITE_PREFIXES in app/main.py), as the settings
# are. A term is never deleted: one no longer offered is made inactive, and
# documents that carry it keep reading its rule.
# ============================================================================

from datetime import date as dt_date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bills import Bill
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice
from app.models.memorized import MemorizedTransaction
from app.models.payment_terms import DATE_DRIVEN, STANDARD, PaymentTerm
from app.models.recurring import RecurringInvoice
from app.routes._helpers import get_or_404
from app.schemas.payment_terms import (
    PaymentTermCreate,
    PaymentTermResponse,
    PaymentTermUpdate,
    TermsDatesResponse,
)
from app.services import payment_terms as parser

router = APIRouter(prefix="/api/payment-terms", tags=["payment-terms"])

RULE_FIELDS = (
    "kind",
    "net_days",
    "due_day",
    "cutoff_days",
    "discount_percent",
    "discount_days",
    "discount_day",
)


def _uses(db: Session, name: str, models) -> int:
    """How many rows of `models` carry this term, matched as the parser
    matches it (case and outer spaces aside)."""
    key = " ".join(name.split()).lower()
    return sum(
        db.query(func.count(m.id))
        .filter(func.lower(func.trim(m.terms)) == key)
        .scalar()
        or 0
        for m in models
    )


def _documents(db: Session, name: str) -> int:
    return _uses(db, name, (Invoice, Bill))


def _anywhere(db: Session, name: str) -> int:
    """Documents, customers, vendors, recurring invoices and memorized bills,
    and the default terms setting."""
    from app.services.settings_service import get_setting_raw

    count = _uses(
        db,
        name,
        (Invoice, Bill, Customer, Vendor, RecurringInvoice, MemorizedTransaction),
    )
    default = get_setting_raw(db, "default_terms") or ""
    if " ".join(default.split()).lower() == " ".join(name.split()).lower():
        count += 1
    return count


def _response(db: Session, term: PaymentTerm) -> PaymentTermResponse:
    resp = PaymentTermResponse.model_validate(term)
    resp.description = parser.describe(parser.rule_of(term))
    resp.documents = _documents(db, term.name)
    return resp


def _refuse(sentence: str):
    raise HTTPException(status_code=422, detail=sentence)


def _check_rule(v: dict) -> None:
    """The rule as a whole, in plain sentences. `v` is every field."""
    if v["kind"] == STANDARD:
        if v["net_days"] is None:
            _refuse("A standard term needs the number of days until it's due.")
        if not 0 <= v["net_days"] <= parser.MAX_DAYS:
            _refuse(f"Days until due must be 0 to {parser.MAX_DAYS}.")
        if v["due_day"] is not None or v["discount_day"] is not None:
            _refuse(
                "A standard term is due a number of days after the date; a day "
                "of the month belongs to a date-driven term."
            )
        if v["cutoff_days"]:
            _refuse("Only a date-driven term moves to the next month.")
    else:
        if v["due_day"] is None:
            _refuse("A date-driven term needs the day of the month it's due.")
        if not 1 <= v["due_day"] <= 31:
            _refuse("The day of the month it's due must be 1 to 31.")
        if not 0 <= (v["cutoff_days"] or 0) <= 31:
            _refuse("The days before the due day must be 0 to 31.")
        if v["net_days"] is not None or v["discount_days"] is not None:
            _refuse(
                "A date-driven term is due on a day of the month; a number of "
                "days belongs to a standard term."
            )

    pct = v["discount_percent"]
    when = v["discount_days"] if v["kind"] == STANDARD else v["discount_day"]
    if pct is None and when is None:
        return
    if pct is None or when is None:
        _refuse(
            "An early-payment discount needs both the percent and when it "
            "must be paid by; leave both blank for none."
        )
    if not Decimal("0") < pct < Decimal("100") or pct != round(pct, 2):
        _refuse("The discount percent must be more than 0 and under 100.")
    if v["kind"] == STANDARD:
        if not 0 <= when <= v["net_days"]:
            _refuse(
                "The discount must be taken within the days until it's due, "
                f"0 to {v['net_days']}."
            )
    elif not 1 <= when <= 31:
        _refuse("The day of the month for the discount must be 1 to 31.")


def _name_taken(db: Session, name: str, except_id: int | None = None) -> bool:
    key = " ".join(name.split()).lower()
    q = db.query(PaymentTerm)
    if except_id is not None:
        q = q.filter(PaymentTerm.id != except_id)
    return any(" ".join(t.name.split()).lower() == key for t in q.all())


@router.get("", response_model=list[PaymentTermResponse])
def list_terms(include_inactive: bool = False, db: Session = Depends(get_db)):
    """The terms list, by name. A company made before the list gets the
    terms the forms always offered, the first time it is asked for."""
    if db.query(PaymentTerm.id).first() is None:
        parser.ensure_seeded(db)
        db.commit()
    q = db.query(PaymentTerm)
    if not include_inactive:
        q = q.filter(PaymentTerm.is_active.is_(True))
    return [_response(db, t) for t in q.order_by(PaymentTerm.name).all()]


@router.get("/dates", response_model=TermsDatesResponse)
def terms_dates(
    date: dt_date,
    terms: str = "",
    document: Literal["invoice", "bill"] = Query("invoice"),
    db: Session = Depends(get_db),
):
    """The due date, early-payment discount and any note that a document
    dated `date` with these terms gets: what the forms show as terms are
    picked, by the same parser the save uses."""
    got = parser.terms_dates(db, date, terms, document)
    return TermsDatesResponse(
        due_date=got.due_date,
        discount_percent=got.discount_percent,
        discount_date=got.discount_date,
        discount_line=parser.discount_line(got.discount_percent, got.discount_date),
        note=got.note,
    )


@router.get("/{term_id}", response_model=PaymentTermResponse)
def get_term(term_id: int, db: Session = Depends(get_db)):
    return _response(db, get_or_404(db, PaymentTerm, term_id, "Payment term"))


@router.post("", response_model=PaymentTermResponse, status_code=201)
def create_term(data: PaymentTermCreate, db: Session = Depends(get_db)):
    # The first term a company adds joins the ones the forms always offered.
    parser.ensure_seeded(db)
    if _name_taken(db, data.name):
        raise HTTPException(
            status_code=400, detail=f'"{data.name}" is already on the terms list.'
        )
    values = data.model_dump()
    _check_rule(values)
    term = PaymentTerm(**values)
    db.add(term)
    db.commit()
    db.refresh(term)
    return _response(db, term)


@router.put("/{term_id}", response_model=PaymentTermResponse)
def update_term(term_id: int, data: PaymentTermUpdate, db: Session = Depends(get_db)):
    term = get_or_404(db, PaymentTerm, term_id, "Payment term")
    changes = data.model_dump(exclude_unset=True)
    for key in ("name", "kind", "cutoff_days", "is_active"):
        if key in changes and changes[key] is None:
            raise HTTPException(
                status_code=422, detail=f"{key} can't be blank; leave it out instead."
            )
    if "name" in changes and changes["name"] != term.name:
        if _name_taken(db, changes["name"], except_id=term.id):
            raise HTTPException(
                status_code=400,
                detail=f'"{changes["name"]}" is already on the terms list.',
            )
        used = _anywhere(db, term.name)
        if used:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{term.name} is in use ({used} documents, customers, vendors "
                    "or settings carry it), so its name stays. Add a new term "
                    "and make this one inactive."
                ),
            )
    rule = {k: getattr(term, k) for k in RULE_FIELDS}
    proposed = {**rule, **{k: v for k, v in changes.items() if k in RULE_FIELDS}}
    if proposed["kind"] != rule["kind"]:
        # A term changing kind drops the other kind's fields it had.
        other = (
            ("net_days", "discount_days")
            if proposed["kind"] == DATE_DRIVEN
            else ("due_day", "discount_day")
        )
        for key in other:
            if key not in changes:
                proposed[key] = None
        if proposed["kind"] == STANDARD and "cutoff_days" not in changes:
            proposed["cutoff_days"] = 0
    proposed["discount_percent"] = (
        Decimal(str(proposed["discount_percent"]))
        if proposed["discount_percent"] is not None
        else None
    )
    if proposed != {
        **rule,
        "discount_percent": (
            Decimal(str(rule["discount_percent"]))
            if rule["discount_percent"] is not None
            else None
        ),
    }:
        docs = _documents(db, term.name)
        if docs:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{term.name} is on {docs} invoices and bills, so its rule "
                    "stays as they were made under it. Add a new term and make "
                    "this one inactive."
                ),
            )
        _check_rule(proposed)
    for key, val in proposed.items():
        setattr(term, key, val)
    if "name" in changes:
        term.name = changes["name"]
    if "is_active" in changes:
        term.is_active = changes["is_active"]
    db.commit()
    db.refresh(term)
    return _response(db, term)
