# ============================================================================
# PDF generation — WeasyPrint + Jinja2 templates.
# ============================================================================

import base64
import mimetypes
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.services import storage

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))

# MIME types we'll embed as data URIs. Keep this tight — WeasyPrint will
# happily render whatever, but we don't want a path traversal turning into
# a binary smuggle vector.
_LOGO_ALLOWED_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/svg+xml",
    "image/webp",
}


def _company_logo_data_uri(company_settings: dict) -> str:
    """Return the company logo as a base64 data URI, or empty string.

    Constrains the file to the active upload storage root so a tampered
    `company_logo_path` setting can't read files outside that directory.
    """
    logo_path = (company_settings or {}).get("company_logo_path") or ""
    if not logo_path:
        return ""

    # Stored value is "/static/uploads/company_logo.png" — strip the URL
    # prefix and resolve relative to the static dir.
    relative = logo_path.lstrip("/")
    if relative.startswith("static/"):
        relative = relative[len("static/") :]
    uploads_dir = storage.uploads_root().resolve()
    candidate = (uploads_dir.parent / relative).resolve()

    # Path containment check — reject if the resolved path escapes the
    # uploads directory (defends against ../ in stored value).
    try:
        candidate.relative_to(uploads_dir)
    except ValueError:
        return ""

    if not candidate.is_file():
        return ""

    mime = mimetypes.guess_type(candidate.name)[0] or ""
    if mime not in _LOGO_ALLOWED_MIMES:
        return ""

    try:
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:{mime};base64,{encoded}"


# WeasyPrint is imported lazily (issue #121). Importing it pulls in the
# native pango/cairo/gobject stack, and doing that at module scope meant
# `import app.main` — and therefore the whole test suite — could not run on a
# machine without it. That is how a Windows-only failure reached a release:
# CI runs pytest on Linux only, and the one box that would have caught it
# could not import the app. Rendering a PDF still requires the stack; only
# importing the module no longer does.
_FETCHER = None


def _weasyprint():
    """(HTML, URLFetcher) — imported on first use, not at import time."""
    from weasyprint import HTML, URLFetcher

    return HTML, URLFetcher


def _build_safe_fetcher():
    """Restrict WeasyPrint to data: URIs only.

    Without this, user-controlled HTML (e.g. invoice notes, customer name
    fields) could embed <img src="file:///etc/passwd"> and have the server
    read and embed local files into the generated PDF. Templates currently
    need no external fetches; if that changes, whitelist specific https
    origins here rather than opening up file:// broadly.

    WeasyPrint 70 (CVE-2026-55073) made the fetcher a class so that every
    channel of write_pdf() honours it; ``allowed_protocols`` is the
    library's own gate, and ``fetch`` refuses anything else a second time.
    """
    _HTML, URLFetcher = _weasyprint()

    class _SafeURLFetcher(URLFetcher):
        def __init__(self):
            super().__init__(allowed_protocols=("data",))

        def fetch(self, url, headers=None):
            if not url.lower().startswith("data:"):
                raise ValueError(f"URL scheme not allowed in PDF templates: {url!r}")
            return super().fetch(url, headers=headers)

    return _SafeURLFetcher()


def _get_fetcher():
    """The document's fetcher, built on first use.

    WeasyPrint calls attributes on this object (``_fail_on_errors``), so it
    must be the URLFetcher instance itself — a plain callable wrapper is not
    a substitute.
    """
    global _FETCHER
    if _FETCHER is None:
        _FETCHER = _build_safe_fetcher()
    return _FETCHER


def __getattr__(name):
    """Module-level lazy attribute (PEP 562): `pdf_service._safe_url_fetcher`
    still resolves to the fetcher instance, but building it — and therefore
    importing WeasyPrint — happens on first access rather than at import."""
    if name == "_safe_url_fetcher":
        return _get_fetcher()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def render_pdf(html_str: str) -> bytes:
    """Every PDF the app produces goes through here. PDF/UA-1 makes the
    output *tagged* — headings, tables and reading order are exposed to
    screen readers instead of a flat picture of text — which is what makes
    a W-2 or an invoice readable to a blind user. Falls back to a plain PDF
    if the installed WeasyPrint can't do the variant, so a render never
    fails on an environment quirk."""
    HTML, _ = _weasyprint()
    doc = HTML(string=html_str, url_fetcher=_get_fetcher())
    try:
        return doc.write_pdf(pdf_variant="pdf/ua-1")
    except Exception:  # pragma: no cover - older WeasyPrint / font edge cases
        return doc.write_pdf()


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

# Templates may call terms('Invoice'); a direct render without company
# settings gets the business words. _render() overrides this per call.
from app.services.terminology import Terms as _Terms  # noqa: E402

_jinja_env.globals["terms"] = _Terms()


def _render(template_name: str, company_settings: dict, **context) -> str:
    """Render a template with the company settings and its vocabulary
    (``terms('Invoice')`` in a template reads Pledge for a nonprofit)."""
    from app.services.terminology import terms_for

    template = _jinja_env.get_template(template_name)
    # Every document carries the company logo when one is set (discussion
    # #108: only the analytics PDF and the new-hire report ever received it).
    if "company_logo_data_uri" not in context:
        context["company_logo_data_uri"] = _company_logo_data_uri(company_settings)
    return template.render(
        company=company_settings, terms=terms_for(company_settings), **context
    )


def render_invoice_html(invoice, company_settings: dict) -> str:
    """Use the same invoice face and logo preference for PDF and Print."""
    from app.services.donor_documents import invoice_pdf_context

    return _render(
        "invoice_pdf.html",
        company_settings,
        inv=invoice,
        company_logo_data_uri=(
            _company_logo_data_uri(company_settings)
            if company_settings.get("invoice_show_logo", "true") != "false"
            else ""
        ),
        **invoice_pdf_context(invoice, company_settings),
    )


def generate_invoice_pdf(invoice, company_settings: dict) -> bytes:
    return render_pdf(render_invoice_html(invoice, company_settings))


def generate_estimate_pdf(estimate, company_settings: dict) -> bytes:
    html_str = _render("estimate_pdf.html", company_settings, est=estimate)
    return render_pdf(html_str)


def generate_statement_pdf(
    customer, invoices, payments, company_settings: dict, as_of_date=None
) -> bytes:
    html_str = _render(
        "statement_pdf.html",
        company_settings,
        customer=customer,
        invoices=invoices,
        payments=payments,
        as_of_date=as_of_date,
    )
    return render_pdf(html_str)


def generate_analytics_pdf(
    dashboard: dict, period: dict, company_settings: dict
) -> bytes:
    """Render the analytics dashboard snapshot as a printable PDF."""
    from app.services.terminology import terms_for

    template = _jinja_env.get_template("analytics_pdf.html")
    html_str = template.render(
        dashboard=dashboard,
        period=period,
        company=company_settings,
        terms=terms_for(company_settings),
        company_logo_data_uri=_company_logo_data_uri(company_settings),
    )
    return render_pdf(html_str)


def _amount_to_words(amount) -> str:
    """Convert a decimal amount to words for check printing."""
    ones = [
        "",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = [
        "",
        "",
        "Twenty",
        "Thirty",
        "Forty",
        "Fifty",
        "Sixty",
        "Seventy",
        "Eighty",
        "Ninety",
    ]

    def _int_to_words(n):
        if n == 0:
            return "Zero"
        if n < 0:
            return "Negative " + _int_to_words(-n)
        parts = []
        if n >= 1000000:
            parts.append(_int_to_words(n // 1000000) + " Million")
            n %= 1000000
        if n >= 1000:
            parts.append(_int_to_words(n // 1000) + " Thousand")
            n %= 1000
        if n >= 100:
            parts.append(ones[n // 100] + " Hundred")
            n %= 100
        if n >= 20:
            word = tens[n // 10]
            if n % 10:
                word += "-" + ones[n % 10]
            parts.append(word)
        elif n > 0:
            parts.append(ones[n])
        return " ".join(parts)

    amt = float(amount or 0)
    dollars = int(amt)
    cents = round((amt - dollars) * 100)
    return f"{_int_to_words(dollars)} and {cents:02d}/100"


def generate_collection_letter_pdf(
    customer, invoices, company_settings: dict, letter_type: str, total_due
) -> bytes:
    from datetime import date as _date

    html_str = _render(
        "collection_letter.html",
        company_settings,
        customer=customer,
        invoices=invoices,
        letter_type=letter_type,
        total_due=total_due,
        today=_date.today(),
    )
    return render_pdf(html_str)


def generate_acknowledgment_letter_pdf(
    customer, gift: dict, irs: dict, company_settings: dict, body_html: str, today=None
) -> bytes:
    """A donor acknowledgment letter: letterhead, the rendered (sandboxed,
    autoescaped) body from the editable template, and the gift box with
    the IRS figures."""
    from datetime import date as _date

    html_str = _render(
        "acknowledgment_letter.html",
        company_settings,
        customer=customer,
        gift=gift,
        irs=irs,
        body_html=body_html,
        today=today or _date.today(),
    )
    return render_pdf(html_str)


def generate_giving_statement_pdf(
    statements: list, company_settings: dict, year: int
) -> bytes:
    """Year-end giving statements, one per donor, a page break between
    them — one PDF prints as the January mailing."""
    return render_pdf(
        _render(
            "giving_statement_pdf.html",
            company_settings,
            statements=statements,
            year=year,
        )
    )


def generate_check_pdf(check_data: dict, company_settings: dict) -> bytes:
    """Checks print on pre-printed stock that already carries the bank's
    and the company's marks, so this is the one document that deliberately
    bypasses _render() and gets no logo."""
    template = _jinja_env.get_template("check_pdf.html")
    check_data["amount_words"] = _amount_to_words(check_data.get("amount", 0))
    html_str = template.render(check=check_data, company=company_settings)
    return render_pdf(html_str)


def generate_report_pdf(sections: list, company_settings: dict) -> bytes:
    """Render one or more financial-report sections into a single PDF.

    sections: [{title, period, columns: [...], rows: [{cells, style}]}]
    Paper size comes from the pdf_paper_size setting (letter | a4) so US
    and international installs both print natively.
    """
    from datetime import date

    html_str = _render(
        "report_pdf.html",
        company_settings,
        sections=sections,
        paper_size=(company_settings.get("pdf_paper_size") or "letter").lower(),
        generated_on=date.today().isoformat(),
    )
    return render_pdf(html_str)
