from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.settings import DEFAULT_SETTINGS, Settings


MONEY_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")


def round_money(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _round_rate(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


def prices_include_gst(db: Session) -> bool:
    row = db.query(Settings).filter(Settings.key == "prices_include_gst").first()
    value = row.value if row else DEFAULT_SETTINGS["prices_include_gst"]
    return str(value).lower() == "true"


@dataclass(frozen=True)
class GstLineInput:
    quantity: Decimal
    rate: Decimal
    gst_code: str
    gst_rate: Decimal
    category: str = "taxable"


@dataclass(frozen=True)
class GstLineResult:
    gst_code: str
    category: str
    net_amount: Decimal
    gst_amount: Decimal
    gross_amount: Decimal


@dataclass(frozen=True)
class GstDocumentResult:
    lines: list[GstLineResult]
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    taxable_total: Decimal
    zero_rated_total: Decimal
    exempt_total: Decimal
    no_gst_total: Decimal
    output_gst: Decimal
    input_gst: Decimal
    effective_tax_rate: Decimal


def _line_extended_amount(line: GstLineInput) -> Decimal:
    return Decimal(str(line.quantity)) * Decimal(str(line.rate))


def calculate_document_gst(
    lines: list[GstLineInput],
    prices_include_gst: bool = False,
    gst_context: str = "sales",
) -> GstDocumentResult:
    results = []
    for line in lines:
        extended = _line_extended_amount(line)
        rate = Decimal(str(line.gst_rate))
        category = line.category or "taxable"
        if category == "taxable" and rate > 0:
            if prices_include_gst:
                gross = round_money(extended)
                gst = round_money(gross * rate / (Decimal("1") + rate))
                net = gross - gst
            else:
                net = round_money(extended)
                gst = round_money(net * rate)
                gross = net + gst
        else:
            net = round_money(extended)
            gst = Decimal("0.00")
            gross = net
        results.append(GstLineResult(
            gst_code=line.gst_code,
            category=category,
            net_amount=net,
            gst_amount=gst,
            gross_amount=gross,
        ))

    subtotal = sum((line.net_amount for line in results), Decimal("0.00"))
    tax_amount = sum((line.gst_amount for line in results), Decimal("0.00"))
    total = sum((line.gross_amount for line in results), Decimal("0.00"))
    taxable_total = sum((line.net_amount for line in results if line.category == "taxable"), Decimal("0.00"))
    zero_rated_total = sum((line.net_amount for line in results if line.category == "zero_rated"), Decimal("0.00"))
    exempt_total = sum((line.net_amount for line in results if line.category == "exempt"), Decimal("0.00"))
    no_gst_total = sum((line.net_amount for line in results if line.category == "no_gst"), Decimal("0.00"))
    output_gst = tax_amount if gst_context == "sales" else Decimal("0.00")
    input_gst = tax_amount if gst_context == "purchase" else Decimal("0.00")
    effective_tax_rate = _round_rate(tax_amount / subtotal) if subtotal else Decimal("0.0000")

    return GstDocumentResult(
        lines=results,
        subtotal=round_money(subtotal),
        tax_amount=round_money(tax_amount),
        total=round_money(total),
        taxable_total=round_money(taxable_total),
        zero_rated_total=round_money(zero_rated_total),
        exempt_total=round_money(exempt_total),
        no_gst_total=round_money(no_gst_total),
        output_gst=round_money(output_gst),
        input_gst=round_money(input_gst),
        effective_tax_rate=effective_tax_rate,
    )
