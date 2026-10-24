"""Auth contract: every /api route rejects unauthenticated requests.

The session middleware in app.main gates everything except an explicit
exempt list. This test walks the ACTUAL route table and proves the
contract holds for every registered route — so a new router that
accidentally lands outside the middleware (or a future exemption typo
that over-matches) fails CI instead of shipping an open endpoint.

Ported concept from the joelmacklow fork's sensitive-route auth
contract suite, re-derived against this app's route table.
"""

import re

import pytest

import app.main as main_module
from app.main import app, _AUTH_EXEMPT_EXACT, _AUTH_EXEMPT_PREFIXES

# Optional: the payments refactor adds a regex exemption. Tolerate its
# absence so this contract holds on branches with and without it.
_AUTH_EXEMPT_RE = getattr(main_module, "_AUTH_EXEMPT_RE", None)

# Routes that are public BY DESIGN, as (pattern, justification). An exempt
# route matching none of these fails the contract: what authenticates the
# request instead of the session?
_PUBLIC_JUSTIFIED = [
    (re.compile(r"^/api/auth/"), "the auth endpoints themselves"),
    (re.compile(r"^/api/stripe/webhook$"), "provider signature is the auth"),
    (re.compile(r"^/api/mercury/webhook$"), "raw-body HMAC and timestamp authenticate"),
    (
        re.compile(r"^/api/payments/[^/]+/webhook$"),
        "provider signature is the auth",
    ),
    (
        re.compile(r"^/api/payments/[^/]+/create-checkout-session$"),
        "payment_token capability + rate limit",
    ),
]


def _fill_params(path: str) -> str:
    """Substitute path params with plausible literals."""
    return re.sub(r"\{[^}]+\}", "1", path)


def _iter_app_routes(routes):
    """Yield concrete routes across FastAPI versions.

    FastAPI 0.141 wraps each include_router() in an _IncludedRouter that
    carries no .path itself — the real APIRoutes live on its
    .original_router. Older versions put routes directly in app.routes.
    Duck-typed so both shapes work.
    """
    for route in routes:
        if hasattr(route, "path"):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_app_routes(inner.routes)


def _api_routes():
    out = []
    for route in _iter_app_routes(app.routes):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/"):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            out.append((method, path))
    return sorted(out)


def _is_exempt(path: str) -> bool:
    filled = _fill_params(path)
    if filled in _AUTH_EXEMPT_EXACT or filled.startswith(_AUTH_EXEMPT_PREFIXES):
        return True
    return bool(_AUTH_EXEMPT_RE and _AUTH_EXEMPT_RE.match(filled))


def test_route_table_is_not_empty():
    """A FastAPI upgrade that changes the route-table shape must fail
    loudly here, not silently empty the parametrized contract below
    (which pytest reports as a single skip)."""
    assert len(_api_routes()) > 100


@pytest.mark.parametrize("method,path", _api_routes())
def test_api_route_auth_contract(unauthed_client, method, path):
    """Non-exempt routes must 401 unauthenticated; exempt routes must
    carry a documented justification above."""
    filled = _fill_params(path)
    if _is_exempt(path):
        assert any(pat.match(filled) for pat, _ in _PUBLIC_JUSTIFIED), (
            f"{method} {path} is exempt from session auth but matches no "
            f"justified-public pattern. If this route is deliberately "
            f"public, add a pattern + justification to _PUBLIC_JUSTIFIED; "
            f"otherwise tighten the exemption in app/main.py."
        )
        return

    resp = unauthed_client.request(method, filled)
    assert resp.status_code == 401, (
        f"{method} {path} returned {resp.status_code} without a session — "
        f"expected 401. Route may be registered outside the auth middleware."
    )
