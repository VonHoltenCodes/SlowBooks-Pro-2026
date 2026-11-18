# ============================================================================
# Print all (#1007): the invoices the list shows, in one print preview.
#
# GET /api/invoices/print-preview?ids=1,2,3 draws each invoice with the same
# renderer as its own Print (render_invoice_html, the PDF's face) and puts
# them in one page, in the order asked, each starting on a page of its own.
# GET /api/invoices/pdf?ids=1,2,3 is the same run as one PDF: what a Mac
# prints for Print all, since a document's Print prints its PDF there
# (#1030), and a file to keep. Printing marks nothing as sent, so every
# reading role may print, as with one invoice's preview.
#
# Registered before crud's GET /{invoice_id}, which would otherwise take
# "print-preview" and "pdf" for an invoice id.
# ============================================================================

import re

from fastapi import Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.routes._print import print_html
from app.routes.invoices._router import router
from app.services.pdf_service import render_invoice_html, render_pdf
from app.services.request_utils import content_disposition
from app.services.settings_service import get_all_settings as get_settings

MAX_INVOICES = 200

NONE_CHOSEN = "Choose at least one invoice to print."
NOT_IDS = "The invoices to print are given as their ids, separated by commas."
TOO_MANY = (
    f"Print all prints up to {MAX_INVOICES} invoices at a time. "
    "Narrow the list and print it in parts."
)

# Each invoice in a section of its own; every section after the first starts
# a new page. The totals box floats right; the footer, last in each section
# with clear: both, closes its floats, so no clearing block of the section's
# own can trail onto an empty sheet (#1030). The run has one head, the first
# invoice's, so a page header naming that invoice (#1035) would name it on
# every invoice's pages: the run's pages are headed "Page n of N" of the
# run instead, after the first.
_PAGE_STYLE = (
    "<style>.sb-print-doc+.sb-print-doc{page-break-before:always;break-before:page}"
    '@page{@top-center{content:"Page " counter(page) " of " counter(pages);'
    "font-size:7pt;color:#999}}"
    "@page :first{@top-center{content:none}}"
    "</style>"
)

_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)


def _parse_ids(ids: str) -> list[int]:
    parts = [p.strip() for p in (ids or "").split(",") if p.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail=NONE_CHOSEN)
    if not all(p.isdigit() for p in parts):
        raise HTTPException(status_code=400, detail=NOT_IDS)
    seen: list[int] = []
    for p in parts:
        n = int(p)
        if n not in seen:
            seen.append(n)
    if len(seen) > MAX_INVOICES:
        raise HTTPException(status_code=400, detail=TOO_MANY)
    return seen


def combine_documents(pages: list[str]) -> str:
    """Several full HTML documents drawn from one template as one page: the
    first one's head, then each one's body in a section of its own."""
    first = pages[0]
    head_end = first.lower().find("<body")
    head = first[:head_end] if head_end >= 0 else ""
    if "</head>" in head:
        head = head.replace("</head>", _PAGE_STYLE + "</head>", 1)
    else:
        head += _PAGE_STYLE
    sections = []
    for page in pages:
        m = _BODY_RE.search(page)
        inner = m.group(1) if m else page
        sections.append(f'<section class="sb-print-doc">{inner}</section>')
    return head + "<body>" + "".join(sections) + "</body></html>"


def _run(ids: str, db: Session) -> tuple[list, str]:
    """The invoices named in ``ids``, in that order, and the one page that
    draws them all."""
    wanted = _parse_ids(ids)
    found = {
        inv.id: inv for inv in db.query(Invoice).filter(Invoice.id.in_(wanted)).all()
    }
    if len(found) != len(wanted):
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    invoices = [found[i] for i in wanted]
    return invoices, combine_documents(
        [render_invoice_html(inv, company) for inv in invoices]
    )


@router.get("/print-preview")
def invoices_print_preview(ids: str = Query(default=""), db: Session = Depends(get_db)):
    """The invoices named in ``ids`` as one page for the print dialog, each
    on its own page(s), in the order given (#1007)."""
    _, page = _run(ids, db)
    return HTMLResponse(content=print_html(page))


@router.get("/pdf")
def invoices_pdf(ids: str = Query(default=""), db: Session = Depends(get_db)):
    """The invoices named in ``ids`` as one PDF, each on its own page(s), in
    the order given: Print all's PDF, which a Mac prints (#1030)."""
    invoices, page = _run(ids, db)
    numbers = [inv.invoice_number for inv in invoices]
    name = (
        f"Invoices_{numbers[0]}.pdf"
        if len(numbers) == 1
        else f"Invoices_{numbers[0]}_to_{numbers[-1]}.pdf"
    )
    return Response(
        content=render_pdf(page),
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(name)},
    )
