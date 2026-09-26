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
