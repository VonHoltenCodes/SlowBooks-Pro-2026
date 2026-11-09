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

from app.models.estimates import EstimateStatus
from app.schemas.common import (
    StrictModel,
    TaxRate,
    choice_error,
    TaxRateOut,
    clean_line_cost_type,
    validate_non_negative_line,
    check_line_fits,
    check_lines_fit,
    quantity_text,
)
from app.schemas.invoices import RateOut

LINE_KINDS = ("base", "allowance", "alternate", "unit_price")
_LINE_KIND_LABELS = dict(
    base="Base", allowance="Allowance", alternate="Alternate", unit_price="Unit price"
)


class EstimateLineCreate(StrictModel):
    item_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    is_taxable: Optional[bool] = None
    class_name: Optional[str] = None
    job_id: Optional[int] = None
    # Construction bid shape (#551); harmless defaults for an ordinary line.
    trade_package_id: Optional[int] = None
    line_kind: str = "base"
    unit: Optional[str] = None
    alternate_accepted: bool = False
    line_order: int = 0

    _cost_type = field_validator("cost_type")(clean_line_cost_type)

    @field_validator("line_kind")
    @classmethod
    def _kind(cls, v):
        v = (v or "base").strip() or "base"
        if v not in LINE_KINDS:
            raise choice_error("Line kind", _LINE_KIND_LABELS, "line_kind")
        return v

    @model_validator(mode="after")
    def _check_non_negative(self):
        # a deduct alternate's negative figures must fit the column too
        check_line_fits(self.quantity, self.rate)
        if self.unit_cost is not None:
            check_line_fits(self.quantity, self.unit_cost)
        # A deduct alternate carries a negative amount (its rate is a credit),
        # so the non-negative rule skips alternates; every other kind keeps it.
        if self.line_kind != "alternate":
            validate_non_negative_line(self.quantity, self.rate)
            # a line priced at cost would otherwise store a negative line from
            # a negative unit cost; a deduct alternate keeps its negative cost
            if self.unit_cost is not None and self.unit_cost < 0:
                raise ValueError("unit_cost must not be negative")
        return self


class EstimateLineResponse(BaseModel):
    id: int
    item_id: Optional[int]
    description: Optional[str]
    quantity: Decimal
    rate: RateOut
    amount: Decimal
    cost_code_id: Optional[int] = None
    cost_type: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    is_taxable: bool = True
    class_name: Optional[str]
    job_id: Optional[int] = None
    trade_package_id: Optional[int] = None
    trade_package_name: Optional[str] = None
    line_kind: str = "base"
    unit: Optional[str] = None
    alternate_accepted: bool = False
    line_order: int

    model_config = {"from_attributes": True}

    @field_serializer("quantity")
    def _quantity(self, value):
        return quantity_text(value)


class EstimateCreate(StrictModel):
    customer_id: int
    date: dt_date
    expiration_date: Optional[dt_date] = None
    tax_rate: TaxRate = Decimal("0")
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # Compounding markups as percent values (5.5 = 5.5%), #551.
    gc_pct: Decimal = Decimal("0")
    insurance_pct: Decimal = Decimal("0")
    fee_pct: Decimal = Decimal("0")
    bond_pct: Decimal = Decimal("0")
    markup_allowances: bool = False
    # Owner-PDF presentation (construction only): show the markup rows, or
    # spread them into each package's price. `terms` None starts from the
    # company's default terms text.
    show_markup_rows: bool = True
    terms: Optional[str] = None
    lines: list[EstimateLineCreate] = []

    @field_validator("lines")
    @classmethod
    def _require_lines(cls, v):
        if not v:
            raise ValueError("estimate must have at least one line")
        check_lines_fit(v)
        return v


class EstimateUpdate(StrictModel):
    customer_id: Optional[int] = None
    date: Optional[dt_date] = None
    expiration_date: Optional[dt_date] = None
    status: Optional[EstimateStatus] = None
    tax_rate: Optional[TaxRate] = None
    notes: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    gc_pct: Optional[Decimal] = None
    insurance_pct: Optional[Decimal] = None
    fee_pct: Optional[Decimal] = None
    bond_pct: Optional[Decimal] = None
    markup_allowances: Optional[bool] = None
    show_markup_rows: Optional[bool] = None
    terms: Optional[str] = None
    lines: Optional[list[EstimateLineCreate]] = None

    @field_validator("lines")
    @classmethod
    def _lines_fit(cls, v):
        if v:
            check_lines_fit(v)
        return v


class EstimatePackageTotal(BaseModel):
    package_id: Optional[int] = None
    package_name: str
    cost: Decimal
    price: Decimal
    line_count: int


class EstimateAlternateOut(BaseModel):
    id: Optional[int] = None
    description: Optional[str] = None
    amount: Decimal
    accepted: bool


class EstimateUnitPriceOut(BaseModel):
    id: Optional[int] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    rate: Decimal
    quantity: Decimal


class EstimateTotals(BaseModel):
    """The bid math (#551): the lines stay at cost, the four markups are their
    own figures under the subtotal, the bid total is subtotal + markups
    (pre-tax), and the package / alternate / unit-price breakdowns."""

    markup_allowances: bool = False
    markup_base: Decimal
    gc_pct: Decimal
    insurance_pct: Decimal
    fee_pct: Decimal
    bond_pct: Decimal
    gc: Decimal
    insurance: Decimal
    fee: Decimal
    bond: Decimal
    markup_total: Decimal
    priced_direct: Decimal
    allowance_total: Decimal
    subtotal: Decimal
    bid_total: Decimal
    # The cost budget accepting locks: package costs, allowances at their
    # amount, and the general-conditions, insurance and bond costs.
    budget_cost: Decimal = Decimal("0")
    bid_tax: Decimal
    bid_total_with_tax: Decimal
    by_package: list[EstimatePackageTotal] = []
    alternates: list[EstimateAlternateOut] = []
    unit_prices: list[EstimateUnitPriceOut] = []


class EstimateAccept(StrictModel):
    job_id: Optional[int] = None


class EstimateLost(StrictModel):
    reason: Optional[str] = Field(default=None, max_length=500)


def totals_from_dict(data: dict) -> EstimateTotals:
    """Build the totals model from estimate_totals()'s dict, so the response
    carries a real EstimateTotals (not a raw dict Pydantic would re-serialize
    with a warning)."""
    return EstimateTotals.model_validate(data)


class EstimateResponse(BaseModel):
    id: int
    estimate_number: str
    customer_id: int
    status: EstimateStatus
    date: dt_date
    expiration_date: Optional[dt_date]
    subtotal: Decimal
    tax_rate: TaxRateOut
    tax_amount: Decimal
    total: Decimal
    gc_pct: Decimal = Decimal("0")
    insurance_pct: Decimal = Decimal("0")
    fee_pct: Decimal = Decimal("0")
    bond_pct: Decimal = Decimal("0")
    markup_allowances: bool = False
    show_markup_rows: bool = True
    terms: Optional[str] = None
    accepted_at: Optional[datetime] = None
    notes: Optional[str]
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    job_name: Optional[str] = None
    job_site_address: Optional[str] = None
    converted_invoice_id: Optional[int]
    lines: list[EstimateLineResponse] = []
    customer_name: Optional[str] = None
    totals: Optional[EstimateTotals] = None
    # True when the estimate's job bills by pay application (a contract type, a
    # schedule of values or a pay application), so Convert is refused.
    bills_by_pay_app: bool = False
    # Bought-scope lines with a price but no cost code (the Accept confirm
    # says how many), and the sentences Accept answers with.
    uncoded_lines: int = 0
    warnings: list[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
