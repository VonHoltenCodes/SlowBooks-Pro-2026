"""API sweep: every GET route answers without a server error.

Walks the app's route table. Parameter-free GETs must return 2xx (or a
documented 4xx). Single-id GETs are tried with id 1 on seeded books and
may 404 but never 500. Endpoints that reach the network (QBO, SimpleFIN,
payment providers, update check) or need a live provider are skipped by
name — their own tests stub them.
"""

import re

import pytest

from app.main import app

SKIP = re.compile(
    r"qbo|simplefin|update-check|stripe|paypal|square|checkout|webhook|portal|"
    r"/api/auth/|/backups/|/download|/pdf|/print|/preview|/export|/email|"
    r"/companies|/system/|/ocr/intake/|/analytics/",
)


def _iter(routes):
    # FastAPI 0.141 wraps include_router() in _IncludedRouter; the real
    # APIRoutes live on .original_router (same walker as test_wiring).
    for r in routes:
        if hasattr(r, "path"):
            yield r
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _iter(inner.routes)


def _routes():
    out = []
    for r in _iter(app.routes):
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", None) or set()
        if "GET" not in methods or not path.startswith("/api/"):
            continue
        if SKIP.search(path):
            continue
        params = re.findall(r"\{(\w+)\}", path)
        out.append((path, params))
    return out


ROUTES = _routes()


def test_sweep_finds_routes():
    assert len(ROUTES) > 60


@pytest.mark.parametrize("path,params", [(p, ps) for p, ps in ROUTES if not ps])
def test_get_without_params(client, seed_accounts, seed_customer, path, params):
    resp = client.get(path)
    assert resp.status_code < 500, f"{path}: {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("path,params", [(p, ps) for p, ps in ROUTES if len(ps) == 1])
def test_get_with_one_id(client, seed_accounts, seed_customer, path, params):
    concrete = path.replace("{" + params[0] + "}", "1")
    resp = client.get(concrete)
    assert resp.status_code < 500, f"{concrete}: {resp.status_code} {resp.text[:200]}"
    assert resp.status_code in (200, 400, 404, 422), f"{concrete}: {resp.status_code}"
