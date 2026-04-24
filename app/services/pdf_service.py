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
from weasyprint import HTML, default_url_fetcher

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))


def _safe_url_fetcher(url, timeout=10, ssl_context=None):
    """Restrict WeasyPrint to data: URIs only.

    Without this, user-controlled HTML (e.g. invoice notes, customer name
    fields) could embed <img src="file:///etc/passwd"> and have the server
    read and embed local files into the generated PDF. Templates currently
    need no external fetches; if that changes, whitelist specific https
    origins here rather than opening up file:// broadly.
    """
    if url.startswith("data:"):
        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)
    raise ValueError(f"URL scheme not allowed in PDF templates: {url!r}")

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
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def generate_estimate_pdf(estimate, company_settings: dict) -> bytes:
    template = _jinja_env.get_template("estimate_pdf.html")
    html_str = template.render(est=estimate, company=company_settings)
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def generate_statement_pdf(customer, invoices, payments, company_settings: dict, as_of_date=None) -> bytes:
    template = _jinja_env.get_template("statement_pdf.html")
    html_str = template.render(
        customer=customer, invoices=invoices, payments=payments,
        company=company_settings, as_of_date=as_of_date,
    )
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def generate_analytics_pdf(dashboard: dict, period: dict, company_settings: dict) -> bytes:
    """Render the analytics dashboard snapshot as a printable PDF."""
    template = _jinja_env.get_template("analytics_pdf.html")
    html_str = template.render(
        dashboard=dashboard,
        period=period,
        company=company_settings,
    )
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def _amount_to_words(amount) -> str:
    """Convert a decimal amount to words for check printing."""
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
            'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen',
            'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
            'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def _int_to_words(n):
        if n == 0:
            return 'Zero'
        if n < 0:
            return 'Negative ' + _int_to_words(-n)
        parts = []
        if n >= 1000000:
            parts.append(_int_to_words(n // 1000000) + ' Million')
            n %= 1000000
        if n >= 1000:
            parts.append(_int_to_words(n // 1000) + ' Thousand')
            n %= 1000
        if n >= 100:
            parts.append(ones[n // 100] + ' Hundred')
            n %= 100
        if n >= 20:
            word = tens[n // 10]
            if n % 10:
                word += '-' + ones[n % 10]
            parts.append(word)
        elif n > 0:
            parts.append(ones[n])
        return ' '.join(parts)

    amt = float(amount or 0)
    dollars = int(amt)
    cents = round((amt - dollars) * 100)
    return f"{_int_to_words(dollars)} and {cents:02d}/100"


def generate_collection_letter_pdf(customer, invoices, company_settings: dict, letter_type: str, total_due) -> bytes:
    from datetime import date as _date
    template = _jinja_env.get_template("collection_letter.html")
    html_str = template.render(
        customer=customer, invoices=invoices, company=company_settings,
        letter_type=letter_type, total_due=total_due, today=_date.today(),
    )
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()


def generate_check_pdf(check_data: dict, company_settings: dict) -> bytes:
    template = _jinja_env.get_template("check_pdf.html")
    check_data["amount_words"] = _amount_to_words(check_data.get("amount", 0))
    html_str = template.render(check=check_data, company=company_settings)
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
