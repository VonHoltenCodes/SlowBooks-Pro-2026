from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.models.bills import Bill, BillStatus, BillPayment, BillPaymentAllocation
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.settings import DEFAULT_SETTINGS, Settings
from app.services.gst_calculations import round_money


GST101A_TEMPLATE = Path(__file__).parent.parent / "forms" / "gst101a-2023.pdf"
GST_FRACTION = Decimal("3") / Decimal("23")


def _setting_map(db: Session) -> dict:
    settings = DEFAULT_SETTINGS.copy()
    for row in db.query(Settings).all():
        settings[row.key] = row.value
    return settings


def _money(value) -> Decimal:
    return round_money(Decimal(str(value or 0)))


def _ratio(part, whole) -> Decimal:
    whole = Decimal(str(whole or 0))
    if whole == 0:
        return Decimal("0")
    return Decimal(str(part or 0)) / whole


def _add(target: dict, key: str, value) -> None:
    target[key] = _money(target.get(key, Decimal("0.00")) + Decimal(str(value or 0)))


def _line_parts(lines, multiplier: Decimal = Decimal("1")) -> dict:
    parts = {
        "standard_gross": Decimal("0.00"),
        "zero_rated": Decimal("0.00"),
        "excluded": Decimal("0.00"),
    }
    for line in lines:
        net = Decimal(str(line.amount or 0)) * multiplier
        rate = Decimal(str(line.gst_rate or 0))
        category = line.gst.category if getattr(line, "gst", None) else "taxable"
        if category == "taxable" and rate > 0:
            gst = round_money(net * rate)
            _add(parts, "standard_gross", net + gst)
        elif category == "zero_rated":
            _add(parts, "zero_rated", net)
        else:
            _add(parts, "excluded", net)
    return parts


def _source_item(source_type: str, number: str, txn_date: date, name: str, parts: dict, multiplier: Decimal = Decimal("1")) -> dict:
    return {
        "source_type": source_type,
        "number": number,
        "date": txn_date.isoformat(),
        "name": name or "",
        "ratio": float(multiplier),
        "standard_gross": float(_money(parts["standard_gross"])),
        "zero_rated": float(_money(parts["zero_rated"])),
        "excluded": float(_money(parts["excluded"])),
    }


def _apply_sales(parts: dict, totals: dict, sign: Decimal = Decimal("1")) -> None:
    _add(totals, "box5", sign * (parts["standard_gross"] + parts["zero_rated"]))
    _add(totals, "box6", sign * parts["zero_rated"])
    _add(totals, "excluded_sales", sign * parts["excluded"])


def _apply_purchases(parts: dict, totals: dict, sign: Decimal = Decimal("1")) -> None:
    _add(totals, "box11", sign * parts["standard_gross"])
    _add(totals, "excluded_purchases", sign * parts["excluded"])


def calculate_gst_return(
    db: Session,
    start_date: date,
    end_date: date,
    box9_adjustments: Decimal = Decimal("0.00"),
    box13_adjustments: Decimal = Decimal("0.00"),
) -> dict:
    settings = _setting_map(db)
    basis = settings.get("gst_basis") or DEFAULT_SETTINGS["gst_basis"]
    totals = {
        "box5": Decimal("0.00"),
        "box6": Decimal("0.00"),
        "box11": Decimal("0.00"),
        "excluded_sales": Decimal("0.00"),
        "excluded_purchases": Decimal("0.00"),
        "unallocated_payments": Decimal("0.00"),
        "unallocated_bill_payments": Decimal("0.00"),
    }
    items = []

    if basis == "payments":
        payments = (
            db.query(Payment)
            .filter(Payment.date >= start_date, Payment.date <= end_date)
            .order_by(Payment.date)
            .all()
        )
        for payment in payments:
            allocated = sum((Decimal(str(a.amount)) for a in payment.allocations), Decimal("0.00"))
            _add(totals, "unallocated_payments", Decimal(str(payment.amount)) - allocated)
            for allocation in payment.allocations:
                invoice = allocation.invoice
                if not invoice or invoice.status == InvoiceStatus.VOID:
                    continue
                allocation_ratio = _ratio(allocation.amount, invoice.total)
                parts = _line_parts(invoice.lines, allocation_ratio)
                _apply_sales(parts, totals)
                items.append(_source_item("payment", invoice.invoice_number, payment.date, invoice.customer.name if invoice.customer else "", parts, allocation_ratio))

        bill_payments = (
            db.query(BillPayment)
            .filter(BillPayment.date >= start_date, BillPayment.date <= end_date)
            .order_by(BillPayment.date)
            .all()
        )
        for payment in bill_payments:
            allocated = sum((Decimal(str(a.amount)) for a in payment.allocations), Decimal("0.00"))
            _add(totals, "unallocated_bill_payments", Decimal(str(payment.amount)) - allocated)
            for allocation in payment.allocations:
                bill = allocation.bill
                if not bill or bill.status == BillStatus.VOID:
                    continue
                allocation_ratio = _ratio(allocation.amount, bill.total)
                parts = _line_parts(bill.lines, allocation_ratio)
                _apply_purchases(parts, totals)
                items.append(_source_item("bill_payment", bill.bill_number, payment.date, bill.vendor.name if bill.vendor else "", parts, allocation_ratio))
    else:
        invoices = (
            db.query(Invoice)
            .filter(Invoice.date >= start_date, Invoice.date <= end_date)
            .filter(Invoice.status != InvoiceStatus.VOID)
            .order_by(Invoice.date)
            .all()
        )
        for invoice in invoices:
            parts = _line_parts(invoice.lines)
            _apply_sales(parts, totals)
            items.append(_source_item("invoice", invoice.invoice_number, invoice.date, invoice.customer.name if invoice.customer else "", parts))

        bills = (
            db.query(Bill)
            .filter(Bill.date >= start_date, Bill.date <= end_date)
            .filter(Bill.status != BillStatus.VOID)
            .order_by(Bill.date)
            .all()
        )
        for bill in bills:
            parts = _line_parts(bill.lines)
            _apply_purchases(parts, totals)
            items.append(_source_item("bill", bill.bill_number, bill.date, bill.vendor.name if bill.vendor else "", parts))

    credit_memos = (
        db.query(CreditMemo)
        .filter(CreditMemo.date >= start_date, CreditMemo.date <= end_date)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .order_by(CreditMemo.date)
        .all()
    )
    for memo in credit_memos:
        parts = _line_parts(memo.lines)
        _apply_sales(parts, totals, sign=Decimal("-1"))
        items.append(_source_item("credit_memo", memo.memo_number, memo.date, memo.customer.name if memo.customer else "", parts, Decimal("-1")))

    box5 = _money(totals["box5"])
    box6 = _money(totals["box6"])
    box7 = _money(box5 - box6)
    box8 = _money(box7 * GST_FRACTION)
    box9 = _money(box9_adjustments)
    box10 = _money(box8 + box9)
    box11 = _money(totals["box11"])
    box12 = _money(box11 * GST_FRACTION)
    box13 = _money(box13_adjustments)
    box14 = _money(box12 + box13)
    difference = _money(box10 - box14)
    if difference > 0:
        net_position = "payable"
    elif difference < 0:
        net_position = "refundable"
    else:
        net_position = "nil"

    return {
        "report_type": "gst_return",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "gst_basis": basis,
        "gst_period": settings.get("gst_period") or DEFAULT_SETTINGS["gst_period"],
        "boxes": {
            "5": float(box5),
            "6": float(box6),
            "7": float(box7),
            "8": float(box8),
            "9": float(box9),
            "10": float(box10),
            "11": float(box11),
            "12": float(box12),
            "13": float(box13),
            "14": float(box14),
            "15": float(abs(difference)),
        },
        "output_gst": float(box10),
        "input_gst": float(box14),
        "net_gst": float(abs(difference)),
        "net_position": net_position,
        "excluded_totals": {
            "sales": float(_money(totals["excluded_sales"])),
            "purchases": float(_money(totals["excluded_purchases"])),
        },
        "unallocated_payments_total": float(_money(totals["unallocated_payments"])),
        "unallocated_bill_payments_total": float(_money(totals["unallocated_bill_payments"])),
        "items": items,
    }


def _pdf_amount(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def _pdf_date(value: date | None) -> str:
    return value.strftime("%d/%m/%y") if value else ""


def _pdf_period(start: str, end: str) -> str:
    return f"{date.fromisoformat(start).strftime('%d/%m')}-{date.fromisoformat(end).strftime('%d/%m')}"


def _overlay_pdf(report: dict, company_settings: dict, return_due_date: date | None, phone: str | None) -> BytesIO:
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(595.276, 841.89))
    c.setFont("Helvetica", 8)
    c.drawString(426, 683, company_settings.get("gst_number") or company_settings.get("company_tax_id") or "")
    c.drawString(426, 660, _pdf_period(report["start_date"], report["end_date"]))
    c.drawString(426, 615, _pdf_date(return_due_date))
    c.drawString(285, 540, phone or company_settings.get("company_phone") or "")
    c.showPage()
    c.setFont("Helvetica", 8)
    c.drawString(433, 160, company_settings.get("gst_number") or company_settings.get("company_tax_id") or "")
    c.drawString(443, 137, date.fromisoformat(report["end_date"]).strftime("%d/%m/%y"))
    c.drawString(443, 108, _pdf_date(return_due_date))
    c.showPage()
    c.save()
    out.seek(0)
    return out


def generate_gst101a_pdf(
    report: dict,
    company_settings: dict,
    return_due_date: date | None = None,
    phone: str | None = None,
) -> bytes:
    reader = PdfReader(str(GST101A_TEMPLATE))
    overlay = PdfReader(_overlay_pdf(report, company_settings, return_due_date, phone))
    writer = PdfWriter()
    writer.append(reader)
    for page_number, page in enumerate(writer.pages):
        page.merge_page(overlay.pages[page_number])

    boxes = report["boxes"]
    fields = {
        "5.0": _pdf_amount(boxes["5"]),
        "5.1": _pdf_amount(boxes["6"]),
        "5.2": _pdf_amount(boxes["7"]),
        "5.3": _pdf_amount(boxes["8"]),
        "5.4": _pdf_amount(boxes["9"]),
        "5.5": _pdf_amount(boxes["10"]),
        "5.6": _pdf_amount(boxes["11"]),
        "5.7": _pdf_amount(boxes["12"]),
        "5.8": _pdf_amount(boxes["13"]),
        "5.9": _pdf_amount(boxes["14"]),
        "5.10.0": _pdf_amount(boxes["15"]),
        "Amount of Pay": _pdf_amount(boxes["15"] if report["net_position"] == "payable" else 0),
        "refund / gst": "/No" if report["net_position"] == "payable" else "/Yes",
    }

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)
    writer.set_need_appearances_writer(True)

    out = BytesIO()
    writer.write(out)
    return out.getvalue()
