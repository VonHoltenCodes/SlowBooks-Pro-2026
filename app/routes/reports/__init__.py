from app.routes.reports._router import router  # noqa: F401
from app.routes.reports import financial  # noqa: F401  registers routes
from app.routes.reports import payables_tax  # noqa: F401  registers routes
from app.routes.reports import receivables  # noqa: F401  registers routes
from app.routes.reports import nonprofit  # noqa: F401  registers routes
from app.routes.reports import donors  # noqa: F401  registers routes
from app.routes.reports import package  # noqa: F401  registers routes (#343)
from app.routes.reports import duplicates  # noqa: F401  registers routes (#414)
from app.routes.reports import items  # noqa: F401  registers routes (#435)
from app.routes.reports import pl_detail  # noqa: F401  registers routes (#477)
from app.routes.reports import construction  # noqa: F401  registers routes (#556)
from app.routes.reports import estimates  # noqa: F401  registers routes (#795)
from app.routes.reports import books  # noqa: F401  registers routes (#893)

# A Print page beside every report PDF (#683): registered last, once every
# module above has added its routes.
from app.routes._print import add_print_routes  # noqa: E402

PRINT_ROUTES = add_print_routes(router)
