# ============================================================================
# Print pages for reports (#683)
#
# Invoices, bills and the other documents have a Print button beside Save
# PDF: /print-preview answers the HTML the PDF is drawn from, with a script
# that opens the print dialog on load. A report's /print does the same for
# the report: it runs the report's own PDF route (same query string, same
# role checks, same 404s) with pdf_service capturing the page in place of
# rendering it, and answers that page.
# ============================================================================

import functools
import inspect

from fastapi.responses import HTMLResponse

# One script for every print page (reports, documents, the Budgets sheets).
# In a browser it prints at load. In the desktop app the page is a document
# window: on a Mac a WKWebView's own window.print() does nothing, and the
# shell's print hook is put in only after load (pywebview injects it at
# didFinishNavigation and then fires "pywebviewready"), so printing at
# onload opened no dialog there, ever (NEW-208, 2.31.7 gate). There the
# page prints once, at pywebviewready or a moment after load, whichever
# comes first. A Print button on the sheet (bottom right, clear of the
# header; not printed) starts it again.
PRINT_SCRIPT = (
    "<style>.sb-print-again{position:fixed;bottom:10px;right:10px;z-index:9;"
    "font:13px sans-serif;padding:4px 14px;cursor:pointer}"
    "@media print{.sb-print-again{display:none}}</style>"
    '<button type="button" class="sb-print-again" id="sb-print-again">Print</button>'
    "<script>(function(){var done=false;"
    "function go(){if(done)return;done=true;window.print();}"
    "var shell=!/^https?:$/.test(location.protocol)||!!window.pywebview;"
    "if(shell){window.addEventListener('pywebviewready',go);"
    "window.addEventListener('load',function(){setTimeout(go,1500);});}"
    "else{window.addEventListener('load',go);}"
    "var b=document.getElementById('sb-print-again');"
    "if(b)b.addEventListener('click',function(){window.print();});"
    "})();</script>"
)


def print_html(html_str: str) -> str:
    """A document's HTML with the on-load print script the document
    previews use."""
    if "</body>" in html_str:
        return html_str.replace("</body>", PRINT_SCRIPT + "</body>", 1)
    return html_str + PRINT_SCRIPT


def print_page(pdf_endpoint):
    """The Print route for a PDF route: the same parameters and
    dependencies (the signature is the PDF route's), the page instead of
    the PDF."""
    from app.services import pdf_service

    def _answer(captured, response):
        if not captured:
            # the route answered without a page: pass it on as it is
            return response
        return HTMLResponse(content=print_html(captured[-1]))

    if inspect.iscoroutinefunction(pdf_endpoint):

        @functools.wraps(pdf_endpoint)
        async def page(*args, **kwargs):
            captured: list = []
            token = pdf_service._print_capture.set(captured)
            try:
                response = await pdf_endpoint(*args, **kwargs)
            finally:
                pdf_service._print_capture.reset(token)
            return _answer(captured, response)

    else:

        @functools.wraps(pdf_endpoint)
        def page(*args, **kwargs):
            captured: list = []
            token = pdf_service._print_capture.set(captured)
            try:
                response = pdf_endpoint(*args, **kwargs)
            finally:
                pdf_service._print_capture.reset(token)
            return _answer(captured, response)

    page.__doc__ = (
        "The report as a page that opens the print dialog: the HTML its PDF "
        "is rendered from (#683)."
    )
    return page


def add_print_routes(router) -> list[str]:
    """Beside every GET .../pdf route on ``router``, a GET .../print route.
    Returns the paths added."""
    from fastapi.routing import APIRoute

    added = []
    existing = {r.path for r in router.routes if isinstance(r, APIRoute)}
    for route in list(router.routes):
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not route.path.endswith("/pdf"):
            continue
        path = route.path[: -len("/pdf")] + "/print"
        if path in existing:
            continue
        router.add_api_route(
            path[len(router.prefix) :] if router.prefix else path,
            print_page(route.endpoint),
            methods=["GET"],
            name=f"{route.name}_print",
            response_class=HTMLResponse,
            tags=list(route.tags),
        )
        existing.add(path)
        added.append(path)
    return added
