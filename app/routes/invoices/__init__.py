from app.routes.invoices._router import router  # noqa: F401

# before crud: its GET /{invoice_id} would take "print-preview" for an id
from app.routes.invoices import print_all  # noqa: F401  registers routes
from app.routes.invoices import crud  # noqa: F401  registers routes
from app.routes.invoices import documents  # noqa: F401  registers routes
from app.routes.invoices import lifecycle  # noqa: F401  registers routes
from app.routes.invoices import po_links  # noqa: F401  registers routes
from app.routes.invoices.helpers import _due_date_from_terms  # noqa: F401
