from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.memorized import KIND_BILL, KINDS, MODES
from app.models.transactions import BASES
from app.schemas.bills import BillLineCreate
from app.schemas.common import (
    StrictModel,
    TaxRateFloat,
    TaxRateOut,
    clean_line_cost_type,
    quantity_text,
)
from app.schemas.invoices import RateOut
from app.schemas.journal import JournalLineCreate
from app.services.recurring_service import normalize_frequency
from app.services.validation_messages import plain_message


class MemorizedLineIn(StrictModel):
    """One line of a memorized bill (an item or account, a quantity and
    rate or an amount alone) or journal entry (an account and a debit or a
    credit), with the memo, job, class, cost code, cost type, function and
    billable mark either kind carries. Checked by the document's own line
    schema for its kind, so the rules are Enter Bill's and the journal
    form's."""

    account_id: Optional[int] = None
    description: Optional[str] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    function: Optional[str] = None
    is_billable: bool = False
    line_order: int = 0
    # a bill line
    item_id: Optional[int] = None
    quantity: Optional[float] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    # Use tax (construction), as on Enter Bill's line: null follows the
    # job's rule, true accrues it anyway, false never. A bill line only.
    use_tax: Optional[bool] = None
    # a journal line
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    def _dimensions(self) -> dict:
        out = {"is_billable": self.is_billable}
        for key in ("job_id", "class_id", "cost_code_id", "cost_type", "function"):
            if key in self.model_fields_set:
                out[key] = getattr(self, key)
        return out

    def as_bill_line(self) -> BillLineCreate:
        return BillLineCreate(
            item_id=self.item_id,
            account_id=self.account_id,
            description=self.description,
            quantity=self.quantity,
            rate=self.rate,
            amount=self.amount,
            line_order=self.line_order,
            use_tax=self.use_tax,
            **self._dimensions(),
        )

    def as_journal_line(self) -> JournalLineCreate:
        if self.account_id is None:
            raise ValueError("every journal line needs an account")
        return JournalLineCreate(
            account_id=self.account_id,
            description=self.description,
            debit=self.debit,
            credit=self.credit,
            **self._dimensions(),
        )


def _line_refusal(n: int, e: ValueError) -> ValueError:
    """A line the document's own line schema refused, as one sentence: its
    ValidationError's first error in words, never the validator's dump of
    the line (2.31.1 gate W-77)."""
    reason = plain_message(e.errors()[0]) if isinstance(e, ValidationError) else e
    return ValueError(f"line {n}: {reason}")


def _checked_lines(kind: str, lines: list[MemorizedLineIn]) -> list[MemorizedLineIn]:
    """The lines as the kind's own schema accepts them, priced (a bill
    line's amount alone becomes 1 x amount) and balanced (a journal)."""
    if kind == KIND_BILL:
        if not lines:
            raise ValueError("a memorized bill must have at least one line")
        for i, line in enumerate(lines):
            try:
                priced = line.as_bill_line()
            except ValueError as e:
                raise _line_refusal(i + 1, e)
            line.quantity, line.rate, line.amount = (
                priced.quantity,
                priced.rate,
                priced.amount,
            )
        return lines
    if len([ln for ln in lines if ln.debit or ln.credit]) < 2:
        raise ValueError(
            "a memorized journal entry needs at least two lines with an amount"
        )
    debits = credits = Decimal("0")
    for i, line in enumerate(lines):
        try:
            jl = line.as_journal_line()
        except ValueError as e:
            raise _line_refusal(i + 1, e)
        if jl.debit < 0 or jl.credit < 0:
            raise ValueError(f"line {i + 1}: debit and credit must be non-negative")
        if jl.debit > 0 and jl.credit > 0:
            raise ValueError(f"line {i + 1}: a line cannot have both debit and credit")
        debits += jl.debit
        credits += jl.credit
    if debits != credits:
        raise ValueError(
            "Debits and credits must be equal: this entry is out of balance "
            f"by ${abs(debits - credits):,.2f}"
        )
    return lines


class MemorizedCreate(StrictModel):
    kind: str  # bill | journal
    name: str
    mode: str = "remind"  # auto | remind
    frequency: str
    interval: int = 1
    # the first date; the schedule's next date starts here
    start_date: dt_date
    end_date: Optional[dt_date] = None
    # stop after this many entries (counting any already made)
    occurrences: Optional[int] = None
    # a bill
    vendor_id: Optional[int] = None
    bill_number: Optional[str] = None
    terms: Optional[str] = None
    # An explicit pick of a list entry for a memorized bill (#664). 1..2**63-1.
    payment_term_id: Optional[int] = Field(None, ge=1, le=9223372036854775807)
    tax_rate: TaxRateFloat = 0
    notes: Optional[str] = None
    # a journal entry
    description: Optional[str] = None
    reference: Optional[str] = None
    is_adjusting: bool = False
    basis: Optional[str] = None
    # both
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[MemorizedLineIn] = []

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("name may not be blank")
        if len(v) > 120:
            raise ValueError("name must be 120 characters or fewer")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "").strip().lower()
        if v not in KINDS:
            raise ValueError("kind must be bill or journal")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v):
        v = (v or "").strip().lower()
        if v not in MODES:
            raise ValueError(
                "mode must be auto (entered on its date) or remind (listed as "
                "due until you enter it)"
            )
        return v

    @field_validator("basis")
    @classmethod
    def _basis(cls, v):
        if v is not None and v not in BASES:
            raise ValueError(f"basis must be one of {', '.join(BASES)}")
        return v

    @field_validator("occurrences")
    @classmethod
    def _occurrences(cls, v):
        if v is not None and v < 1:
            raise ValueError("occurrences must be 1 or more, or left out")
        return v

    @model_validator(mode="after")
    def _whole(self):
        self.frequency, self.interval = normalize_frequency(
            self.frequency, self.interval
        )
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"the end date ({self.end_date.isoformat()}) is before the "
                f"first date ({self.start_date.isoformat()})"
            )
        if self.kind == KIND_BILL:
            if self.vendor_id is None:
                raise ValueError("a memorized bill needs a vendor")
        elif not (self.description or "").strip():
            raise ValueError("a memorized journal entry needs a description")
        self.lines = _checked_lines(self.kind, self.lines)
        return self


class MemorizedUpdate(StrictModel):
    """Whatever is sent changes, the rest stays; lines, when sent, replace
    the lines. The kind is fixed. next_date moves the schedule (a skipped
    month, a day changed); the day it names becomes the day of the month
    the schedule holds to."""

    name: Optional[str] = None
    mode: Optional[str] = None
    frequency: Optional[str] = None
    interval: Optional[int] = None
    next_date: Optional[dt_date] = None
    end_date: Optional[dt_date] = None
    occurrences: Optional[int] = None
    vendor_id: Optional[int] = None
    bill_number: Optional[str] = None
    terms: Optional[str] = None
    # An explicit pick of a list entry for a memorized bill (#664). 1..2**63-1.
    payment_term_id: Optional[int] = Field(None, ge=1, le=9223372036854775807)
    tax_rate: Optional[TaxRateFloat] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    is_adjusting: Optional[bool] = None
    basis: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: Optional[list[MemorizedLineIn]] = None


class EnterRequest(StrictModel):
    """Enter now: the occurrence the page showed (its scheduled date), dated
    that date unless a date is given. An occurrence that is no longer the
    schedule's next one is refused (409) and nothing is posted; left out
    (an API caller), the schedule's next date is entered."""

    occurrence: Optional[dt_date] = None
    date: Optional[dt_date] = None


class MemorizedLineResponse(BaseModel):
    id: int
    line_order: int = 0
    account_id: Optional[int] = None
    description: Optional[str] = None
    job_id: Optional[int] = None
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    function: Optional[str] = None
    is_billable: bool = False
    item_id: Optional[int] = None
    quantity: Optional[Decimal] = None
    rate: Optional[RateOut] = None
    amount: Optional[Decimal] = None
    use_tax: Optional[bool] = None
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return None if value is None else quantity_text(value)


class ScheduleRow(BaseModel):
    """One line of the Memorized transactions page: a recurring invoice
    (kind "invoice", its id under /api/recurring), a memorized bill or a
    memorized journal entry (its id under /api/memorized)."""

    kind: str
    id: int
    name: str
    party: Optional[str] = None
    mode: str
    frequency: str
    interval: int = 1
    frequency_label: str
    next_date: dt_date
    end_date: Optional[dt_date] = None
    occurrences: Optional[int] = None
    entered_count: int = 0
    status: str
    is_due: bool = False
    last_entered_on: Optional[dt_date] = None
    last_document_id: Optional[int] = None
    amount: Decimal = Decimal("0")


class MemorizedResponse(BaseModel):
    id: int
    kind: str
    name: str
    mode: str
    frequency: str
    interval: int = 1
    frequency_label: str = ""
    start_date: dt_date
    next_date: dt_date
    anchor_day: int
    end_date: Optional[dt_date] = None
    occurrences: Optional[int] = None
    entered_count: int = 0
    is_active: bool = True
    status: str = ""
    is_due: bool = False
    last_entered_on: Optional[dt_date] = None
    last_document_id: Optional[int] = None
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    bill_number: Optional[str] = None
    terms: Optional[str] = None
    tax_rate: TaxRateOut = Decimal("0")
    notes: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    is_adjusting: bool = False
    basis: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    amount: Decimal = Decimal("0")
    lines: list[MemorizedLineResponse] = []
    model_config = {"from_attributes": True}


class EnterResponse(BaseModel):
    kind: str
    document_id: int
    # the scheduled date entered, and the date the document carries
    occurrence: dt_date
    date: dt_date
    next_date: dt_date
    status: str
    message: str
