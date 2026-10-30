# ============================================================================
# PDF generation — WeasyPrint + Jinja2 templates.
# ============================================================================

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))


def _company_logo_data_uri(company_settings: dict) -> str:
    """Return the company logo as a base64 data URI, or empty string.

    The logo is read from the company's own database (the stored file its
    company_logo_path setting names), never from a file on disk: every
    company on a desktop install used to share one uploads/company_logo.png,
    so one company's invoices went out with another's logo (2.18.0 gate).
    Only a stored logo of an image type is ever embedded.
    """
    from app.services.file_store import logo_data_uri

    return logo_data_uri(company_settings)


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


def _money(body: str, negative: bool, code=None) -> str:
    """An amount's digits with its sign and currency: "-$10.00", the minus
    before the dollar sign (a discount line printed "$-10.00"), or with an
    ISO `code` "EUR -10.00". An amount that rounds to nothing has no sign."""
    sign = "-" if negative and body.strip("0.,") else ""
    return f"{code} {sign}{body}" if code else f"{sign}${body}"


def _format_currency(value, code=None):
    """Dollars ("$1,234.50", "-$10.00"), or with an ISO `code` "EUR
    1,234.50". A document in a foreign currency passes its code, so a euro
    invoice never prints as dollars (2.17.3 exploratory W-M2)."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    return _money(f"{abs(v):,.2f}", v < 0, code)


def _format_rate(value, code=None):
    """A unit price: two places ("$12.50"), or up to four when it has them
    ("$0.045"); sales line rates are kept to four places. A discount's is
    negative: "-$10.00"."""
    from decimal import Decimal, InvalidOperation

    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        d = Decimal("0")
    a = abs(d)
    if a == a.quantize(Decimal("0.01")):
        body = f"{a:,.2f}"
    else:
        body = format(a.normalize(), ",f")
    return _money(body, d < 0, code)


def _format_qty(value):
    """A line's quantity as the API shows it: two places ("3.00"), or the
    places it has when finer ("2.125"); quantities are kept to four
    (2.23.0, NEW-94: the PDFs printed the column raw, which read 2.1250
    once the column was widened)."""
    from app.schemas.common import quantity_text

    return quantity_text(value)


def _format_tax_percent(value):
    """A document's tax rate (a fraction) as the percent it prints: at least
    two places, up to the four a rate keeps ("8.875", "8.25", "7.00"). The
    documents printed "%.2f", so New York City's 8.875% read 8.88%. The Sales
    Tax report prints it the same way (SalesLines.taxPercent)."""
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    try:
        pct = Decimal(str(value or 0)) * 100
        pct = pct.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        pct = Decimal("0")
    if pct == pct.quantize(Decimal("0.01")):
        return f"{pct:.2f}"
    return format(pct.normalize(), "f")


def document_currency_code(doc, company_settings: dict) -> str:
    """The ISO code to print on a document's amounts: its own currency when
    that is not the home currency, else "" (plain dollars)."""
    code = (getattr(doc, "currency", None) or "").strip().upper()
    home = ((company_settings or {}).get("home_currency") or "USD").strip().upper()
    return code if code and code != home else ""


def _format_date(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%b %d, %Y")
    return str(value)


_jinja_env.filters["currency"] = _format_currency
_jinja_env.filters["rate"] = _format_rate
_jinja_env.filters["qty"] = _format_qty
_jinja_env.filters["tax_percent"] = _format_tax_percent
_jinja_env.filters["fdate"] = _format_date


def _format_pct_label(value):
    """A markup percent as it reads on a label: 8, 1.5, 10 — the stored value
    (8 = 8%) with any trailing zeros dropped (#551)."""
    from decimal import Decimal, InvalidOperation

    try:
        s = format(Decimal(str(value or 0)), "f")
    except (InvalidOperation, ValueError):
        s = "0"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


_jinja_env.filters["pct_label"] = _format_pct_label
_jinja_env.globals["document_currency_code"] = document_currency_code

from app.services.addresses import city_line, country_line  # noqa: E402

_jinja_env.globals["city_line"] = city_line
_jinja_env.globals["country_line"] = country_line

# Templates may call terms('Invoice'); a direct render without company
# settings gets the business words. _render() overrides this per call.
from app.services.terminology import Terms as _Terms  # noqa: E402

_jinja_env.globals["terms"] = _Terms()

# The construction estimate PDF (#551) groups and totals the bid through the
# same pure function the route and tests use.
from app.services.estimating import estimate_totals as _estimate_totals  # noqa: E402

_jinja_env.globals["estimate_totals"] = _estimate_totals


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
    from app.services.payment_terms import document_discount_line

    return _render(
        "invoice_pdf.html",
        company_settings,
        inv=invoice,
        discount_line=document_discount_line(invoice),
        company_logo_data_uri=(
            _company_logo_data_uri(company_settings)
            if company_settings.get("invoice_show_logo", "true") != "false"
            else ""
        ),
        **invoice_pdf_context(invoice, company_settings),
    )


def generate_invoice_pdf(invoice, company_settings: dict) -> bytes:
    return render_pdf(render_invoice_html(invoice, company_settings))


def render_deposit_html(deposit: dict, company_settings: dict) -> str:
    """A deposit slip (#260): the bank account, the date, each check or
    payment the deposit took (who, number, amount) and the total, for the
    envelope that walks to the bank. `deposit` is the deposit detail as
    GET /api/deposits/{id} answers it."""
    return _render("deposit_pdf.html", company_settings, deposit=deposit)


def generate_deposit_pdf(deposit: dict, company_settings: dict) -> bytes:
    return render_pdf(render_deposit_html(deposit, company_settings))


def render_labels_html(labels: list) -> str:
    """Avery 5160 labels, 30 to a Letter page (#436). `labels` is a flat
    list of {line1, line2, address1, address2, city_line}; the template
    breaks a page every 30 so a long list prints across sheets."""
    pages = [labels[i : i + 30] for i in range(0, len(labels), 30)] or [[]]
    template = _jinja_env.get_template("labels_5160_pdf.html")
    return template.render(pages=pages)


def generate_labels_pdf(labels: list) -> bytes:
    return render_pdf(render_labels_html(labels))


def generate_credit_memo_pdf(credit_memo, company_settings: dict) -> bytes:
    """A credit memo, printed the way an invoice is (credit memos had no
    PDF at all, so one could not be sent: 2.17.3 exploratory W-L19)."""
    return render_pdf(_render("credit_memo_pdf.html", company_settings, cm=credit_memo))


def generate_estimate_pdf(estimate, company_settings: dict) -> bytes:
    html_str = _render("estimate_pdf.html", company_settings, est=estimate)
    return render_pdf(html_str)


def render_change_order_html(co, totals: dict, company_settings: dict) -> str:
    """The owner change order / PCO document (#554): title, description, the
    price (sell) per line and per cost type, and the total. Cost and markup
    stay in the app — not on the owner's document. Reuses the estimate PDF's
    face."""
    return _render("change_order_pdf.html", company_settings, co=co, totals=totals)


def generate_change_order_pdf(co, totals: dict, company_settings: dict) -> bytes:
    return render_pdf(render_change_order_html(co, totals, company_settings))


def render_pay_application_html(app, pa, job, company_settings: dict) -> str:
    """The owner payment application (#555): the header summary (contract
    sum, net change by change orders, completed to date, retainage, total
    earned less retainage, previous applications, current payment due,
    deposit applied, balance to finish) and the line detail (the line
    table). `pa` is the PayApplicationOut; `job` carries the owner."""

    def f(v):
        return float(v) if v is not None else 0.0

    # Contract sums as of the application: the figures snapshotted when it was
    # approved, so a reprint doesn't pick up change orders approved later. A
    # draft (no snapshot yet) reads the job as it stands.
    if app.contract_sum_to_date is not None:
        original = f(app.contract_sum_original)
        current = f(app.contract_sum_to_date)
    else:
        original = f(job.original_contract_amount if job else 0)
        current = f(job.contract_amount if job else 0)
    header = {
        "owner": (job.customer.name if job and job.customer else ""),
        "job_name": (job.full_name if job else ""),
        "net_change": current - original,
        "contract_sum_original": original,
        "contract_sum_to_date": current,
    }
    return _render(
        "pay_application_pdf.html",
        company_settings,
        app=app,
        pa=pa,
        job=job,
        header=header,
        totals=pa.totals,
    )


def generate_pay_application_pdf(app, pa, job, company_settings: dict) -> bytes:
    return render_pdf(render_pay_application_html(app, pa, job, company_settings))


# The lien waiver wording by type. {amount}, {through} and {owner} are filled
# from the payment; progress waivers cover work through a date, final waivers
# the whole job. Conditional waivers take effect only once the payment is
# received; unconditional ones say it has been.
LIEN_WAIVER_TITLES = {
    "conditional_progress": "Conditional Waiver and Release on Progress Payment",
    "unconditional_progress": "Unconditional Waiver and Release on Progress Payment",
    "conditional_final": "Conditional Waiver and Release on Final Payment",
    "unconditional_final": "Unconditional Waiver and Release on Final Payment",
}


def render_lien_waiver_html(waiver, payment, job, company_settings: dict) -> str:
    """One lien waiver (#555) for a payment: the type picks the title and the
    wording (conditional / unconditional, progress / final). `job` carries
    the owner and project."""
    wtype = waiver.waiver_type
    return _render(
        "lien_waiver_pdf.html",
        company_settings,
        waiver=waiver,
        payment=payment,
        job=job,
        title=LIEN_WAIVER_TITLES.get(wtype, "Waiver and Release"),
        conditional=wtype.startswith("conditional"),
        final=wtype.endswith("final"),
        owner=(job.customer.name if job and job.customer else ""),
        project=(job.full_name if job else ""),
    )


def generate_lien_waiver_pdf(waiver, payment, job, company_settings: dict) -> bytes:
    return render_pdf(render_lien_waiver_html(waiver, payment, job, company_settings))


def generate_statement_pdf(
    customer, activity: dict, company_settings: dict, as_of_date=None
) -> bytes:
    """`activity` is routes/reports/receivables.statement_activity(): the
    dated lines with a running balance, and the totals."""
    html_str = _render(
        "statement_pdf.html",
        company_settings,
        customer=customer,
        activity=activity,
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
    statements: list, company_settings: dict, year: int, opening_message: str = ""
) -> bytes:
    """Year-end giving statements, one per donor, a page break between
    them — one PDF prints as the January mailing.

    The ministry's own opening message and footer (#415) ride here for every
    output — the single PDF, the all-donors stack and the emailed copy all
    call this one function — so the three never drift. The opening message is
    the default giving-statement saved message (passed in, since this layer
    has no db); the footer is a company setting. Empty prints what printed
    before, minus the product-name footer."""
    return render_pdf(
        _render(
            "giving_statement_pdf.html",
            company_settings,
            statements=statements,
            year=year,
            opening_message=(opening_message or "").strip(),
            footer_text=(company_settings.get("giving_statement_footer") or "").strip(),
        )
    )


def check_print_options(company_settings: dict) -> dict:
    """The one reading of the check-printing settings (#440), shared by every
    check print path so none of them drift. `stock` is "plain" (the full
    voucher) or "preprinted" (values only, onto stock that carries the labels
    and lines); `nudge_x`/`nudge_y` are the inch shifts of the check portion,
    as floats so a zero nudge is falsy in the template (and emits no
    transform, keeping plain stock byte-for-byte unchanged)."""

    def _inches(key):
        try:
            return float(company_settings.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    stock = company_settings.get("check_stock") or "plain"
    return {
        "stock": "preprinted" if stock == "preprinted" else "plain",
        "nudge_x": _inches("check_nudge_x_in"),
        "nudge_y": _inches("check_nudge_y_in"),
    }


def render_check_html(check_data: dict, company_settings: dict, options=None) -> str:
    """The check face as HTML. Checks print on stock that already carries the
    bank's and the company's marks, so this is the one document that
    deliberately bypasses _render() and gets no logo. The stock choice and
    alignment nudge come from check_print_options(); pass ``options={}`` to
    render with none of that context (the pre-#440 face, for a byte-for-byte
    comparison)."""
    return render_checks_html([check_data], company_settings, options)


def render_checks_html(checks: list, company_settings: dict, options=None) -> str:
    """One or more checks, each on its own page of the 3-per-page stock
    (two stubs and the check), in the order given (#603)."""
    template = _jinja_env.get_template("check_pdf.html")
    for check_data in checks:
        check_data["amount_words"] = _amount_to_words(check_data.get("amount", 0))
    opts = check_print_options(company_settings) if options is None else options
    return template.render(checks=checks, company=company_settings, **opts)


def generate_check_pdf(check_data: dict, company_settings: dict) -> bytes:
    return render_pdf(render_check_html(check_data, company_settings))


def generate_checks_pdf(checks: list, company_settings: dict) -> bytes:
    return render_pdf(render_checks_html(checks, company_settings))


def generate_report_pdf(
    sections: list, company_settings: dict, landscape: bool = False
) -> bytes:
    """Render one or more financial-report sections into a single PDF.

    sections: [{title, period, columns: [...], rows: [{cells, style}]}]
    Paper size comes from the pdf_paper_size setting (letter | a4) so US
    and international installs both print natively; `landscape` turns the
    page on its side for a grid with a column per class or job (#232).
    """
    from datetime import date

    html_str = _render(
        "report_pdf.html",
        company_settings,
        sections=sections,
        paper_size=(company_settings.get("pdf_paper_size") or "letter").lower(),
        landscape=landscape,
        generated_on=date.today().isoformat(),
    )
    return render_pdf(html_str)
