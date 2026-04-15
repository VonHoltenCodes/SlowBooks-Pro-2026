# ============================================================================
# Decompiled from qbw32.exe!CPrintManager + CInvoicePrintLayout
# Offset: 0x00220000
# Original used Crystal Reports 8.5 OCX embedded in an OLE container for
# print preview. The .RPT template files were stored as RT_RCDATA resources.
# We're using WeasyPrint + Jinja2 because Crystal Reports can go to hell.
# ============================================================================

from pathlib import Path
from io import BytesIO

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))

def _format_currency(value):
    try:
        v = float(value or 0)
        return f"${v:,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _format_date(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%b %d, %Y")
    return str(value)


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


def generate_analytics_pdf(dashboard: dict, period: dict, company_settings: dict) -> bytes:
    """Render the analytics dashboard snapshot as a printable PDF.

    `dashboard` is the raw `AnalyticsEngine.get_dashboard()` dict;
    `period` carries `{name, start, end}` metadata for the header;
    `company_settings` is the usual key-value dict the other PDFs get.
    """
    template = _jinja_env.get_template("analytics_pdf.html")
    html_str = template.render(
        dashboard=dashboard,
        period=period,
        company=company_settings,
    )
    return HTML(string=html_str).write_pdf()
