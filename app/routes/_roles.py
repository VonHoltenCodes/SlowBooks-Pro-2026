"""Who is asking: the role behind a request, for routes that shape their
answer by it (the RBAC middleware in app.main decides allow/deny; this is
for the allowed-but-narrower case).

A session carries its role; an API token carries its own (the middleware
stamps ``request.state.token_principal``). A legacy single-user session has
no role and is the admin."""

from fastapi import HTTPException, Request

from app.models.users import ROLE_ADMIN


def current_role(request: Request) -> str:
    tp = getattr(getattr(request, "state", None), "token_principal", None)
    if tp and tp.get("role"):
        return tp["role"]
    try:
        return request.session.get("role") or ROLE_ADMIN
    except AssertionError:  # no SessionMiddleware (bare TestClient)
        return ROLE_ADMIN


def is_admin(request: Request) -> bool:
    return current_role(request) == ROLE_ADMIN


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Admin role required")


def may_download_backups(request: Request) -> bool:
    """An administrator, or an API token an administrator let make backups
    (#434). A backup is the whole company, so no other role reads one."""
    if is_admin(request):
        return True
    tp = getattr(getattr(request, "state", None), "token_principal", None)
    return bool(tp and tp.get("allow_backups"))
