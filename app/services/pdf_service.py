# ============================================================================
# PDF generation — WeasyPrint + Jinja2 templates.
# ============================================================================

from contextvars import ContextVar
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


# An install without WeasyPrint (a bare Windows server install, #807)
# answered every PDF route with a plain-text 500. The import failing now
# raises this, and app.main answers it 503 with the sentence; the print
# previews never reach the import, so they keep working.
WEASYPRINT_MISSING = (
    "This install cannot make PDFs because WeasyPrint or its Pango libraries"
    " are missing: install them as the install guide (INSTALL.md) says under"
    ' "PDFs need WeasyPrint", or use Print, which works without them.'
)


class PdfEngineMissing(RuntimeError):
    """WeasyPrint, or the native libraries it loads, is not installed."""


def _weasyprint():
    """(HTML, URLFetcher) — imported on first use, not at import time."""
    try:
        from weasyprint import HTML, URLFetcher
    except (ImportError, OSError) as exc:
        # OSError: the package is there and its pango/gobject DLLs are not
        raise PdfEngineMissing(WEASYPRINT_MISSING) from exc

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


# A report's Print page (#683) is the HTML its PDF is rendered from: the
# print route runs the PDF route with this set, and render_pdf hands the
# page over here instead of rendering it (app/routes/_print.py).
_print_capture: ContextVar[list | None] = ContextVar("pdf_print_capture", default=None)


def render_pdf(html_str: str) -> bytes:
    """Every PDF the app produces goes through here. PDF/UA-1 makes the
    output *tagged* — headings, tables and reading order are exposed to
    screen readers instead of a flat picture of text — which is what makes
    a W-2 or an invoice readable to a blind user. Falls back to a plain PDF
    if the installed WeasyPrint can't do the variant, so a render never
    fails on an environment quirk."""
    capture = _print_capture.get()
    if capture is not None:
        capture.append(html_str)
        return b""
    return _write_pdf(html_str)


def _write_pdf(html_str: str) -> bytes:
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
from app.services.estimating import (  # noqa: E402
    presentation_style as _presentation_style,
)
from app.services.estimate_bid import (  # noqa: E402
    spread_markup_prices as _spread_markup_prices,
)

_jinja_env.globals["estimate_totals"] = _estimate_totals
_jinja_env.globals["spread_markup_prices"] = _spread_markup_prices
# Which layout a construction estimate prints in (#774), as the form picks it.
_jinja_env.globals["presentation_style"] = _presentation_style


def paper_size_of(company_settings: dict) -> str:
    """'letter' or 'a4', from the pdf_paper_size setting (#807): every
    document's @page reads it, as the report PDFs always have. Checks,
    labels and the tax forms keep Letter, the stock they print on."""
    size = str((company_settings or {}).get("pdf_paper_size") or "letter").lower()
    return "a4" if size == "a4" else "letter"


def _render(template_name: str, company_settings: dict, **context) -> str:
    """Render a template with the company settings and its vocabulary
    (``terms('Invoice')`` in a template reads Pledge for a nonprofit)."""
    from app.services.terminology import terms_for

    template = _jinja_env.get_template(template_name)
    # Every document carries the company logo when one is set (discussion
    # #108: only the analytics PDF and the new-hire report ever received it).
    if "company_logo_data_uri" not in context:
        context["company_logo_data_uri"] = _company_logo_data_uri(company_settings)
    # Letter or A4, from the setting (#807)
    context.setdefault("paper_size", paper_size_of(company_settings))
    return template.render(
        company=company_settings, terms=terms_for(company_settings), **context
    )


def render_invoice_html(invoice, company_settings: dict) -> str:
    """Use the same invoice face and logo preference for PDF and Print."""
    from sqlalchemy.orm import object_session

    from app.services.donor_documents import invoice_pdf_context
    from app.services.pay_applications import (
        invoice_is_release_only,
        invoice_retainage_lines,
        pay_application_number,
    )
    from app.services.payment_terms import (
        document_discount_line,
        document_discount_taken,
    )

    _db = object_session(invoice)
    number = pay_application_number(_db, invoice)
    return _render(
        "invoice_pdf.html",
        company_settings,
        inv=invoice,
        release_only=invoice_is_release_only(invoice),
        pay_application_number=number,
        # what a line-less pay application invoice bills, in place of an
        # empty table (#815)
        retainage_lines=invoice_retainage_lines(invoice, number),
        discount_line=document_discount_line(invoice),
        # the discount already taken, shown in place of the offer (#664)
        discount_taken=(
            document_discount_taken(_db, invoice) if _db is not None else None
        ),
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
    GET /api/deposits/{id} answers it. With deposit slip stock set to
    pre-printed (#798) it is the figures alone, laid onto the bank's slip."""
    if deposit_slip_stock(company_settings) == "preprinted":
        return render_deposit_slip_stock_html(deposit, company_settings)
    return _render("deposit_pdf.html", company_settings, deposit=deposit)


def deposit_slip_stock(company_settings: dict) -> str:
    """What the deposit slip prints on (#798): "plain" paper, SlowBooks'
    own slip (the default), or the bank's "preprinted" slip."""
    stock = company_settings.get("deposit_slip_stock")
    return "preprinted" if stock == "preprinted" else "plain"


def _slip_amount(value) -> str:
    """An amount as a deposit slip's box takes it: digits, commas and the
    cents, no dollar sign (the slip's box carries its own)."""
    from decimal import Decimal

    return f"{Decimal(str(value or 0)):,.2f}"


def _slip_inches(company_settings: dict, key: str) -> float:
    try:
        return float(company_settings.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _slip_date(value) -> str:
    """The date as a slip's small box takes it: 09/12/2026."""
    import datetime

    if isinstance(value, str) and value:
        try:
            value = datetime.date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%m/%d/%Y") if hasattr(value, "strftime") else ""


# an amount's box: the amount ends at the field's x, inside this width
_SLIP_AMOUNT_WIDTH_IN = 2.5


def deposit_slip_pages(deposit: dict, company_settings: dict) -> list:
    """The figures of a deposit on pre-printed slip stock (#798), as pages
    of [{"field", "text", "left", "top", "size", "amount"}] in inches from
    the sheet's top-left, the nudges already added. The first page carries
    the date, the account, the cash, the first check lines, the checks
    subtotal and the total; checks past the slip's lines continue on more
    pages at the same lines, with the date and account, for the slip's back
    or a second slip. Every payment that is not cash goes on a check line.
    A void deposit prints its date, its account and VOID in the total's
    box, no figures."""
    from decimal import Decimal

    from app.services.settings_service import (
        DEPOSIT_SLIP_AMOUNTS,
        deposit_slip_layout,
    )

    layout = deposit_slip_layout(company_settings.get("deposit_slip_layout"))
    nx = _slip_inches(company_settings, "deposit_slip_nudge_x_in")
    ny = _slip_inches(company_settings, "deposit_slip_nudge_y_in")

    def place(field, text, line=0):
        spot = layout[field]
        if not spot.get("show", True) or text in (None, ""):
            return None
        amount = field in DEPOSIT_SLIP_AMOUNTS
        x = float(spot["x"]) + nx
        y = float(spot["y"]) + ny + line * float(layout["check_lines"]["spacing"])
        return {
            "field": field,
            "text": str(text),
            "left": round(x - _SLIP_AMOUNT_WIDTH_IN if amount else x, 4),
            "top": round(y, 4),
            "size": float(spot["size"]),
            "amount": amount,
        }

    date_text = _slip_date(deposit.get("date"))
    head = [place("date", date_text), place("account", deposit.get("account_name"))]
    if deposit.get("voided"):
        return [[f for f in head + [place("total", "VOID")] if f]]
    payments = deposit.get("payments") or []
    cash = [p for p in payments if p.get("method") == "Cash"]
    checks = [p for p in payments if p.get("method") != "Cash"]
    cash_total = sum((Decimal(str(p.get("amount") or 0)) for p in cash), Decimal(0))
    checks_total = sum((Decimal(str(p.get("amount") or 0)) for p in checks), Decimal(0))
    per_page = int(layout["check_lines"]["count"])
    chunks = [checks[i : i + per_page] for i in range(0, len(checks), per_page)]
    pages = []
    for index, chunk in enumerate(chunks or [[]]):
        fields = list(head)
        if index == 0:
            fields.append(place("cash", _slip_amount(cash_total) if cash else ""))
        for line, p in enumerate(chunk):
            number = (
                p.get("check_number")
                or p.get("payment_reference")
                or p.get("reference")
                or ""
            )
            fields.append(place("check_number", number, line))
            fields.append(place("check_amount", _slip_amount(p.get("amount")), line))
        if index == 0:
            if checks:
                fields.append(place("checks_subtotal", _slip_amount(checks_total)))
            fields.append(place("total", _slip_amount(deposit.get("amount"))))
        pages.append([f for f in fields if f])
    return pages


def render_deposit_slip_stock_html(deposit: dict, company_settings: dict) -> str:
    """The deposit on the bank's pre-printed slip (#798): the figures alone,
    each at its place on the slip (deposit_slip_pages), with no labels, no
    logo, no MICR line and no routing or account digits, which the slip
    already carries."""
    return _jinja_env.get_template("deposit_slip_stock.html").render(
        pages=deposit_slip_pages(deposit, company_settings),
        paper_size=paper_size_of(company_settings),
        amount_width=_SLIP_AMOUNT_WIDTH_IN,
    )


# The test slip's figures (#798): two checks and some cash, made-up numbers
# on a made-up account, so a test print is never a real deposit.
SAMPLE_DEPOSIT = {
    "account_name": "Sample Checking",
    "voided": False,
    "payments": [
        {"method": "Check", "check_number": "1001", "amount": "1234.56"},
        {"method": "Check", "check_number": "1002", "amount": "250.00"},
        {"method": "Cash", "amount": "75.00"},
    ],
    "amount": "1559.56",
}


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

    from sqlalchemy.orm import object_session

    from app.services.contracts import application_contract_sums

    # Contract sums as of the application (#723): the snapshot taken at
    # approval, or the job as it stands for a draft; the net change is the
    # approved change orders, never a difference against a missing original.
    sums = application_contract_sums(object_session(app), app, job)
    header = {
        "owner": (job.customer.name if job and job.customer else ""),
        "job_name": (job.full_name if job else ""),
        "net_change": float(sums["net_change"]),
        "contract_sum_original": float(sums["original"]),
        "contract_sum_to_date": float(sums["to_date"]),
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
    dated lines with a running balance, and the totals. The collection
    statements are this one too."""
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
        paper_size=paper_size_of(company_settings),
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


def check_position(company_settings: dict) -> str:
    """Where the check face sits on the page (#727): "bottom" (stub / stub /
    check, the default) or "top" (check / voucher / voucher, QuickBooks
    Desktop's standard voucher stock). Read apart from check_print_options()
    so the stock and nudge options keep their shape; every check print path
    renders through render_checks_html(), which reads both."""
    return "top" if company_settings.get("check_position") == "top" else "bottom"


def blank_stock(company_settings: dict) -> dict:
    """The blank-stock options (#739): `on` prints the logo, the bank's name
    and city, the fractional routing number and the MICR line from each
    check's `bank_details`; `micr_amount` also encodes the amount in the
    MICR line. Read apart from check_print_options() so its shape holds."""
    return {
        "on": company_settings.get("check_blank_stock") == "true",
        "micr_amount": company_settings.get("check_micr_amount") == "true",
    }


# Where each field of the check layout (#749) sits on the face: the element
# an offset moves, and whether its size and weight go on a span around the
# value (the value's own line keeps its height, so nothing laid out after it
# moves and its baseline stays put) or on the element itself (the payee's
# address, several lines, last on the face).
_CHECK_FIELD_BOXES = {
    "payee": (".pay-to-line", True),
    "payee_address": (".payee-address", False),
    "date": (".check-date", True),
    "amount": (".amount-box", True),
    "amount_words": (".amount-words", True),
    "memo": (".memo-text", True),
}


def check_field_layout_css(company_settings: dict) -> tuple:
    """The per-field check layout (#749) as (css, wrapped): the rules that
    move, size and bold each field the layout names, and the fields whose
    value is printed in a span for its size and weight. ("", set()) when
    the layout is empty, so the face is exactly what it was before it."""
    from app.services.settings_service import check_field_layout

    layout = check_field_layout(company_settings.get("check_field_layout"))
    rules = []
    wrapped = set()
    for field, (box, wrap) in _CHECK_FIELD_BOXES.items():
        attrs = layout.get(field)
        if not attrs:
            continue
        x, y = attrs.get("x", 0), attrs.get("y", 0)
        if x or y:
            rules.append(
                f"    .check-area {box} {{ transform: translate({x:g}in, {y:g}in); }}"
            )
        look = []
        if "size" in attrs:
            look.append(f"font-size: {attrs['size']:g}pt;")
            if wrap:
                # one line at its own baseline: a wrapped second line would
                # sit on the first and push what follows down
                look.append("line-height: 0; white-space: nowrap;")
        if "bold" in attrs:
            look.append(f"font-weight: {'bold' if attrs['bold'] else 'normal'};")
        if look:
            target = f".check-field-{field}" if wrap else box
            rules.append(f"    .check-area {target} {{ {' '.join(look)} }}")
            if wrap:
                wrapped.add(field)
    if wrapped:
        # a value kept on one line may run past the face; the face keeps its
        # width (a flex item is otherwise as wide as its widest line)
        rules.insert(0, "    .check-area { min-width: 0; }")
    return "\n".join(rules), wrapped


def _blank_stock_fields(check_data: dict, micr_amount: bool) -> None:
    """The bank's lines and the MICR line of one check on blank stock, from
    its `bank_details`; a check without routing and account numbers prints
    no MICR line (the print routes refuse it before it gets here)."""
    from decimal import Decimal

    from app.services.micr import micr_line, micr_svg

    bank = check_data.get("bank_details") or {}
    check_data["bank_name"] = bank.get("bank_name", "")
    check_data["bank_city_state"] = bank.get("bank_city_state", "")
    check_data["fractional_routing"] = bank.get("fractional_routing", "")
    check_data["micr_svg"] = ""
    if bank.get("routing_number") and bank.get("account_number"):
        line = micr_line(
            bank["routing_number"],
            bank["account_number"],
            check_data.get("check_number", ""),
            check_data.get("amount") if micr_amount else None,
        )
        check_data["micr_line"] = line
        # the sheet's width; its height takes in the clear band
        check_data["micr_svg"] = micr_svg(line, Decimal("8.5"), Decimal("0.625"))


def render_check_html(check_data: dict, company_settings: dict, options=None) -> str:
    """The check face as HTML. Checks print on stock that already carries the
    bank's and the company's marks, so this is the one document that
    deliberately bypasses _render() and gets no logo, unless it prints on
    blank stock (#739, blank_stock()), which carries nothing. The stock choice and
    alignment nudge come from check_print_options(); pass ``options={}`` to
    render with none of that context (the pre-#440 face, for a byte-for-byte
    comparison)."""
    return render_checks_html([check_data], company_settings, options)


def render_checks_html(checks: list, company_settings: dict, options=None) -> str:
    """One or more checks, each on its own page of the 3-per-page stock
    (two stubs and the check, or the check and two vouchers when the check
    position is "top", #727), in the order given (#603)."""
    template = _jinja_env.get_template("check_pdf.html")
    for check_data in checks:
        check_data["amount_words"] = _amount_to_words(check_data.get("amount", 0))
    opts = check_print_options(company_settings) if options is None else options
    blank = blank_stock(company_settings)
    extra = {}
    if blank["on"]:
        # blank stock carries nothing, so the face prints its labels and
        # lines whatever the stock setting says
        opts = {**opts, "stock": "plain"}
        for check_data in checks:
            _blank_stock_fields(check_data, blank["micr_amount"])
        extra = {"blank": True, "logo": _company_logo_data_uri(company_settings)}
    field_css, field_wrap = check_field_layout_css(company_settings)
    return template.render(
        checks=checks,
        company=company_settings,
        position=check_position(company_settings),
        field_css=field_css,
        field_wrap=field_wrap,
        **opts,
        **extra,
    )


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
    return render_pdf(render_report_html(sections, company_settings, landscape))


def render_report_html(
    sections: list, company_settings: dict, landscape: bool = False
) -> str:
    """The page a report PDF is rendered from (report_pdf.html), for a
    print page that has no PDF route of its own to run (the Budgets sheet
    and Budget vs Actual, NEW-208)."""
    from datetime import date

    return _render(
        "report_pdf.html",
        company_settings,
        sections=sections,
        paper_size=paper_size_of(company_settings),
        landscape=landscape,
        generated_on=date.today().isoformat(),
    )
