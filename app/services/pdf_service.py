# ============================================================================
# Decompiled from qbw32.exe!CPrintManager + CInvoicePrintLayout
# Offset: 0x00220000
# Original used Crystal Reports 8.5 OCX embedded in an OLE container for
# print preview. The .RPT template files were stored as RT_RCDATA resources.
# We're using WeasyPrint + Jinja2 because Crystal Reports can go to hell.
# ============================================================================

from pathlib import Path
from io import BytesIO

from jinja2 import Environment, FileSystemLoader, pass_context
from weasyprint import HTML

from app.services.formatting import format_currency, format_date

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


@pass_context
def _format_currency(context, value):
    return format_currency(value, context.get("company", {}))


@pass_context
def _format_date(context, value):
    return format_date(value, context.get("company", {}))


_jinja_env.filters["currency"] = _format_currency
_jinja_env.filters["fdate"] = _format_date


def generate_invoice_pdf(invoice, company_settings: dict) -> bytes:
    template = _jinja_env.get_template("invoice_pdf.html")
    html_str = template.render(inv=invoice, company=company_settings)
    return HTML(string=html_str).write_pdf()


def generate_estimate_pdf(estimate, company_settings: dict) -> bytes:
    template = _jinja_env.get_template("estimate_pdf.html")
    html_str = template.render(est=estimate, company=company_settings)
    return HTML(string=html_str).write_pdf()


def generate_statement_pdf(customer, invoices, payments, company_settings: dict, as_of_date=None) -> bytes:
    template = _jinja_env.get_template("statement_pdf.html")
    html_str = template.render(
        customer=customer, invoices=invoices, payments=payments,
        company=company_settings, as_of_date=as_of_date,
    )
    return HTML(string=html_str).write_pdf()


def generate_payroll_payslip_pdf(pay_run, stub, employee, company_settings: dict) -> bytes:
    template = _jinja_env.get_template("payroll_payslip_pdf.html")
    html_str = template.render(
        pay_run=pay_run,
        stub=stub,
        employee=employee,
        company=company_settings,
    )
    return HTML(string=html_str).write_pdf()
