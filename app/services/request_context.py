# ============================================================================
# Per-request context — who is acting.
#
# Set by the session middleware, read by the audit hooks (which live at
# the SQLAlchemy layer and have no access to the request). A contextvar
# survives async task switches, so concurrent Server Edition requests
# can't bleed identities into each other's audit rows.
# ============================================================================

import contextvars

acting_username: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "acting_username", default=None
)

# The closing-date override password a signed-in person sent with this
# request (X-Closing-Date-Password), for check_closing_date. Like the
# username, get_db also stamps it on the request's Session, the path that
# holds on every runtime; this is the fallback for a Session opened outside
# get_db. Never logged, never echoed.
closing_date_password: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "closing_date_password", default=None
)

# What an import's writes are recorded as (#894): "import:<the import's
# name>" while a request runs one of the import routes, so the audit rows it
# writes say which import made them (the Activity panel then reads "by the
# system, in the IIF import"). None for every other request: its rows keep
# the ordinary source.
acting_source: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "acting_source", default=None
)

IMPORT_SOURCE_PREFIX = "import:"

# The import routes, by path prefix, and the import's name as a person says it
_IMPORT_PATHS = (
    ("/api/iif/import", "IIF"),
    ("/api/csv/import/", "CSV"),
    ("/api/bank-import/", "bank statement"),
    ("/api/fixed-assets/import-csv", "fixed asset CSV"),
    ("/api/cost-codes/import", "cost code"),
    ("/api/payroll/opening-ytd/import", "opening payroll"),
)


def import_source(method: str, path: str) -> str | None:
    """The audit source for a request that runs an import, or None."""
    if (method or "").upper() != "POST" or not path:
        return None
    if path.startswith("/api/migration/") and path.endswith("/import"):
        key = path[len("/api/migration/") : -len("/import")].strip("/")
        name = key.replace("_", " ")
        try:
            from app.routes.migration import SOURCES

            name = SOURCES[key][0]
        except Exception:
            pass
        return IMPORT_SOURCE_PREFIX + name
    for prefix, name in _IMPORT_PATHS:
        if path.startswith(prefix):
            return IMPORT_SOURCE_PREFIX + name
    return None
