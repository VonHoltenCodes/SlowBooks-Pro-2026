from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    field_serializer,
)

from app.schemas.common import (
    check_line_fits,
    check_lines_fit,
    clean_line_cost_type,
    quantity_text,
    StrictModel,
    NewTaxRateFloat,
    TAX_RATE_DESCRIPTION,
    TaxRateOut,
    validate_non_negative_line,
    WholeCents,
)
from app.schemas.invoices import RateOut


class CreditMemoLineCreate(StrictModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: float = 1
    rate: float = 0
    # Whether the memo's tax rate applies to this line. None = the item's
    # flag (a non-taxable customer: never), as on an invoice line. Used for
    # the memo's tax; the line itself does not store it.
    is_taxable: Optional[bool] = None
    # The line's own class and job (#640); left out, the line posts with
    # the memo's, as an invoice line does. A job must be the customer's.
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # The cost code and type the credit takes revenue back from (#1055), as
    # on an invoice line; blank type takes the code's own.
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    @model_validator(mode="after")
    def _check_non_negative(self):
        # a quantity past its column at a rate of 0 raised in the rounding
        # (a 500, #782): each figure to its own column first, then the product
        # (W-145, the 2.31.14 gate), which past the money column raised too
        validate_non_negative_line(self.quantity, self.rate)
        check_line_fits(self.quantity, self.rate)
        return self


class CreditMemoLineResponse(BaseModel):
    id: int
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    rate: RateOut = Decimal("0")
    amount: Decimal = Decimal("0")
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    line_order: int = 0
    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class CreditApplicationCreate(StrictModel):
    invoice_id: int
    amount: WholeCents("An applied amount", float)


class CreditMemoCreate(StrictModel):
    customer_id: int
    date: dt_date
    original_invoice_id: Optional[int] = None
    # Left out: the original invoice's rate when one is named, else the
    # company's Default Tax Rate (#1129), as the Credit Memo form starts.
    tax_rate: NewTaxRateFloat = Field(
        None,
        description=(
            TAX_RATE_DESCRIPTION
            + " Left out (or null): the rate of original_invoice_id when one is "
            "named, else the company's Default Tax Rate from Settings, as the "
            "app's form starts a credit memo; send 0 for no tax. A non-taxable "
            "customer keeps that rate and is credited no tax."
        ),
    )
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    lines: list[CreditMemoLineCreate] = []
    # A credit memo for $0.00 is saved only when this says so; otherwise it
    # is refused with 409 "zero_total".
    allow_zero_total: bool = False

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("credit memo must have at least one line")
        check_lines_fit(v)
        return v


class CreditMemoResponse(BaseModel):
    id: int
    memo_number: str
    customer_id: int
    customer_name: Optional[str] = None
    # On the answer to a create or an update only: the exemption certificate
    # behind an exempt sale (the tax-exempt customer's, or the exempt job's)
    # is missing, or expired as of the document's date
    # (services/tax_exemption.certificate_warning). The sale is exempt all
    # the same; this is for the person to be told. Null otherwise.
    tax_note: Optional[str] = None
    # The same sentence in a field of its own: an API client that writes its
    # own tax_note over this answer's (the AI assistant connection does, for
    # a document sent without a tax_rate) still passes this one on.
    exemption_warning: Optional[str] = None
    status: str
    original_invoice_id: Optional[int] = None
    date: dt_date
    subtotal: Decimal = Decimal("0")
    tax_rate: TaxRateOut = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    amount_applied: Decimal = Decimal("0")
    balance_remaining: Decimal = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    is_write_off: bool = False
    # Set on an early-payment discount (#664): the payment it was taken with.
    discount_payment_id: Optional[int] = None
    # taken against the invoice's terms, with allow_discount_override (#665)
    discount_override: bool = False
    lines: list[CreditMemoLineResponse] = []
    created_at: Optional[datetime] = None
    # Void-and-replace (#689): the credit memo this one replaced, and the one that
    # replaced it (this one is then void).
    replaces_id: Optional[int] = None
    replaced_by_id: Optional[int] = None
    model_config = {"from_attributes": True}
