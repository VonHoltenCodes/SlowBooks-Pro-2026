# ============================================================================
# Payment Terms — the terms list (#664), kept under Settings
#
# Every form that takes terms offers this list, and every document's due
# date comes from app/services/payment_terms.py. An invoice or bill is tied
# to the entry its terms named when it was saved (payment_term_id), so a
# term is "in use" by id, never by matching text. Writes are an
# administrator's (_ADMIN_WRITE_PREFIXES in app/main.py), as the settings
# are. A term is never deleted: one no longer offered is made inactive, and
# documents saved under it keep reading its rule.
# ============================================================================

from datetime import date as dt_date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.schemas.common import MAX_REQUEST_YEAR, MIN_REQUEST_YEAR
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
# How the refusals name a field.
LABELS = {
    "name": "Name",
    "kind": "Kind",
    "cutoff_days": "Days before the due day",
    "is_active": "Active",
}
# A term name is at most what a document's terms column holds.
TERMS_MAX = 50
CENT_PERCENT = Decimal("0.01")


def _documents(db: Session, term: PaymentTerm) -> int:
    """The invoices and bills saved under the term: tied to it by id, so
    nothing that merely reads like its name counts, and nothing that does
    is missed."""
    return sum(
        db.query(func.count(m.id)).filter(m.payment_term_id == term.id).scalar() or 0
        for m in (Invoice, Bill)
    )


def _elsewhere(db: Session, name: str) -> int:
    """Customers, vendors, recurring invoices, memorized bills and the
    default terms setting that carry the name, matched as the parser
    matches text (parser._key: case and any whitespace aside)."""
    from app.services.settings_service import get_setting_raw

    key = parser._key(name)
    count = 0
    for model in (Customer, Vendor, RecurringInvoice, MemorizedTransaction):
        for text, n in (
            db.query(model.terms, func.count(model.id))
            .filter(model.terms.isnot(None))
            .group_by(model.terms)
        ):
            if parser._key(text) == key:
                count += n
    if parser._key(get_setting_raw(db, "default_terms")) == key:
        count += 1
    return count


def _response(db: Session, term: PaymentTerm) -> PaymentTermResponse:
    resp = PaymentTermResponse.model_validate(term)
    resp.description = parser.describe(parser.rule_of(term))
    resp.documents = _documents(db, term)
    resp.elsewhere = _elsewhere(db, term.name)
    return resp


def _carried(docs: int, others) -> str:
    """Where a term is, counted the same way in every refusal (#665: the
    rename and the rule refusals gave two totals a minute apart)."""
    said = f"is on {docs} invoices and bills saved under it"
    if others:
        said += (
            f", and {others} customers, vendors, schedules or settings carry its name"
        )
    return said


def _refuse(sentence: str):
    raise HTTPException(status_code=422, detail=sentence)


def _only(request: Request, *names: str) -> None:
    """The API never ignores input: a query parameter this route doesn't
    take is refused by name rather than read as its defaults."""
    for key in request.query_params.keys():
        if key not in names:
            _refuse(f"'{key}' is not a parameter this accepts.")


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
        # past 28 a cutoff can be longer than February, and a document
        # dated in it would be due sooner than its own cutoff (#665)
        if not 0 <= (v["cutoff_days"] or 0) <= 28:
            _refuse(
                "The days before the due day must be 0 to 28, so the cutoff "
                "fits inside February."
            )
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
    if not Decimal("0") < pct < Decimal("100"):
        _refuse("The discount percent must be more than 0 and under 100.")
    if pct != pct.quantize(CENT_PERCENT):
        _refuse(
            f"The discount percent is kept to two decimal places; {pct.normalize():f}% "
            "has more. Round it, to 2.56 say."
        )
    if v["kind"] == STANDARD:
        if not 0 <= when <= v["net_days"]:
            _refuse(
                "The discount must be taken within the days until it's due, "
                f"0 to {v['net_days']}."
            )
    elif not 1 <= when <= 31:
        _refuse("The day of the month for the discount must be 1 to 31.")


def _check_name(name: str, rule: dict) -> None:
    """A name the old parser already reads ("Net 30", "COD") keeps that
    reading: a term by that name with another rule would give documents a
    different date from the one their text has always meant."""
    read = parser.read_text(name)
    if read is None:
        return
    if rule["kind"] != STANDARD or rule["net_days"] != read.net_days:
        meaning = (
            "due on receipt"
            if not read.net_days
            else f"due in {parser._days(read.net_days)}"
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f'"{name}" already means {meaning}, so a term by that name keeps '
                "that rule. Give this term another name."
            ),
        )


def _name_taken(db: Session, name: str, except_id: int | None = None) -> bool:
    key = parser._key(name)
    q = db.query(PaymentTerm)
    if except_id is not None:
        q = q.filter(PaymentTerm.id != except_id)
    return any(parser._key(t.name) == key for t in q.all())


def _taken(name: str):
    raise HTTPException(
        status_code=409, detail=f'"{name}" is already on the terms list.'
    )


@router.get("", response_model=list[PaymentTermResponse])
def list_terms(
    request: Request, include_inactive: bool = False, db: Session = Depends(get_db)
):
    """The terms list, by name. A read: the list a company starts with is
    written when the company is made (and by the migration for one made
    before), never here."""
    _only(request, "include_inactive")
    q = db.query(PaymentTerm)
    if not include_inactive:
        q = q.filter(PaymentTerm.is_active.is_(True))
    return [_response(db, t) for t in q.order_by(PaymentTerm.name).all()]


@router.get("/dates", response_model=TermsDatesResponse)
def terms_dates(
    request: Request,
    date: dt_date,
    terms: str = "",
    document: Literal["invoice", "bill"] = Query("invoice"),
    invoice_id: Optional[int] = None,
    bill_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """The due date, early-payment discount and any note that a document
    dated `date` with these terms gets when saved: what the forms show as
    terms are picked, by the same parser the save uses. With `invoice_id`
    or `bill_id` (an edit) and terms that are the document's own text
    exactly, its own rule answers, as the save keeps it (#665)."""
    _only(request, "date", "terms", "document", "invoice_id", "bill_id")
    if len(terms) > TERMS_MAX:
        _refuse(f"Terms are at most {TERMS_MAX} characters; these are {len(terms)}.")
    if not MIN_REQUEST_YEAR <= date.year <= MAX_REQUEST_YEAR:
        _refuse(
            f"Date {date.isoformat()} is out of range; the year must be between "
            f"{MIN_REQUEST_YEAR} and {MAX_REQUEST_YEAR}."
        )
    if invoice_id is not None and bill_id is not None:
        _refuse("Send invoice_id or bill_id, not both.")
    doc = None
    if invoice_id is not None:
        doc = get_or_404(db, Invoice, invoice_id, "Invoice")
        document = "invoice"
    elif bill_id is not None:
        doc = get_or_404(db, Bill, bill_id, "Bill")
        document = "bill"
    try:
        if doc is not None and terms == (doc.terms or ""):
            got = parser.document_terms_dates(db, doc, date, document)
        else:
            got = parser.terms_dates(db, date, terms, document)
    except OverflowError:
        _refuse(
            f'"{terms.strip()}" from {date.isoformat()} runs past the calendar; '
            "give the due date instead."
        )
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
def create_term(
    data: PaymentTermCreate, request: Request, db: Session = Depends(get_db)
):
    _only(request)
    # The first term a company adds joins the ones the forms always offered.
    parser.ensure_seeded(db)
    if _name_taken(db, data.name):
        _taken(data.name)
    values = data.model_dump()
    _check_rule(values)
    _check_name(data.name, values)
    term = PaymentTerm(**values)
    db.add(term)
    db.commit()
    db.refresh(term)
    return _response(db, term)


@router.put("/{term_id}", response_model=PaymentTermResponse)
def update_term(
    term_id: int,
    data: PaymentTermUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    _only(request)
    term = get_or_404(db, PaymentTerm, term_id, "Payment term")
    changes = data.model_dump(exclude_unset=True)
    for key in ("name", "kind", "cutoff_days", "is_active"):
        if key in changes and changes[key] is None:
            _refuse(f"{LABELS[key]} can't be blank; leave it out instead.")
    docs = _documents(db, term)
    if "name" in changes and changes["name"] != term.name:
        if _name_taken(db, changes["name"], except_id=term.id):
            _taken(changes["name"])
        others = _elsewhere(db, term.name)
        if docs or others:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{term.name} {_carried(docs, others)}, so its name stays. Add "
                    "a new term and make this one inactive."
                ),
            )
    rule = {k: getattr(term, k) for k in RULE_FIELDS}
    rule["discount_percent"] = (
        Decimal(str(rule["discount_percent"]))
        if rule["discount_percent"] is not None
        else None
    )
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
    if proposed != rule:
        if docs:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{term.name} {_carried(docs, None)}, so its rule stays as "
                    "they were made under it. Add a new term and make this one "
                    "inactive."
                ),
            )
        _check_rule(proposed)
    _check_name(changes.get("name", term.name), proposed)
    for key, val in proposed.items():
        setattr(term, key, val)
    if "name" in changes:
        term.name = changes["name"]
    if "is_active" in changes:
        term.is_active = changes["is_active"]
    db.commit()
    db.refresh(term)
    return _response(db, term)


@router.delete("/{term_id}")
def delete_term(term_id: int):
    """A term is never deleted, so the documents saved under it keep their
    rule: 405 naming the way to stop offering it."""
    raise HTTPException(
        status_code=405,
        detail=(
            "A payment term is not deleted; documents saved under it keep its "
            f"rule. Make it inactive: PUT /api/payment-terms/{term_id} with "
            '{"is_active": false}.'
        ),
    )


@router.patch("/{term_id}")
def patch_term(term_id: int):
    raise HTTPException(
        status_code=405,
        detail=f"Change a payment term with PUT /api/payment-terms/{term_id}.",
    )
