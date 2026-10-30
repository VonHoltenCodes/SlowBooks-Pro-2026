"""MCP server hardening: no proxy or redirect for the token, structured 409s,
path normalisation, override parameters, Windows token files."""

import http.server
import threading

import pytest

from app.mcp import config as mcp_config
from app.mcp import tools
from app.mcp.client import ApiClient, ApiError, normalise_path, urllib_transport
from app.mcp.config import Config, ConfigError, _read_token

TOKEN = "sbp_" + "a" * 20


def _serve(handler_fn):
    """A loopback server that records each request and answers via handler_fn."""
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            handler_fn(self)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def _ok(h):
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.end_headers()
    h.wfile.write(b'{"ok": true}')


def test_transport_ignores_proxy_env(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    # A no_proxy naming loopback (common on CI runners) would let the default
    # opener pass too; clear it so this test fails without ProxyHandler({}).
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    srv, seen = _serve(_ok)
    try:
        send = urllib_transport(
            Config(base_url=f"http://127.0.0.1:{srv.server_port}", token=TOKEN)
        )
        status, body = send("GET", "/api/x", None, None, None)
    finally:
        srv.shutdown()
    assert (status, body) == (200, {"ok": True})
    assert seen == [("/api/x", f"Bearer {TOKEN}")]


def test_transport_does_not_follow_redirects():
    def redirect(h):
        h.send_response(302)
        h.send_header("Location", "http://127.0.0.1:9/elsewhere")
        h.end_headers()

    srv, seen = _serve(redirect)
    try:
        send = urllib_transport(
            Config(base_url=f"http://127.0.0.1:{srv.server_port}", token=TOKEN)
        )
        with pytest.raises(ApiError) as exc:
            send("GET", "/api/x", None, None, None)
    finally:
        srv.shutdown()
    assert exc.value.status == 302 and "redirect" in exc.value.detail
    assert len(seen) == 1


def test_structured_409_keeps_payload():
    detail = {
        "code": "credit_limit",
        "message": "Over the limit.",
        "question": "Send anyway?",
        "limit": "100.00",
        "would_owe": "150.00",
    }
    api = ApiClient(lambda *a: (409, {"detail": detail}))
    with pytest.raises(ApiError) as exc:
        api.post("/api/invoices", {})
    assert str(exc.value) == "HTTP 409: Over the limit."
    assert exc.value.payload["would_owe"] == "150.00"


def test_plain_error_has_no_payload():
    api = ApiClient(lambda *a: (404, {"detail": "Nope"}))
    with pytest.raises(ApiError) as exc:
        api.get("/api/x")
    assert exc.value.payload is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/%74okens",
        "/api/customers/../tokens",
        "/api//tokens",
        "/api/customers/..%2F..%2Ftokens",
        "/api\\tokens",
        "/api/%252574okens",
        "/api/x\n",
    ],
)
def test_tricky_paths_are_refused(api, path):
    with pytest.raises(ApiError) as exc:
        api.get(path)
    assert exc.value.status == 403
    assert "does not reach that route" in exc.value.detail


def test_trailing_slash_still_works(api, seed_accounts):
    api.get("/api/customers/")
    api.get("/api/customers")
    assert normalise_path("/api/x/../tokens") == "/api/tokens"
    assert normalise_path("/openapi.json") == "/openapi.json"
    assert normalise_path("/other") == ""


def test_customer_and_vendor_force(api, seed_accounts):
    for create in (tools.create_customer, tools.create_vendor):
        create(api, {"name": "Dup Co"})
        with pytest.raises(ApiError) as exc:
            create(api, {"name": "Dup Co."})
        assert exc.value.status == 409 and "duplicates" in exc.value.payload
        assert create(api, {"name": "Dup Co."}, force=True)["id"]


def _expense(seed_accounts, number):
    return {
        "date": "2026-09-11",
        "amount": "25.00",
        "expense_account_id": seed_accounts["6000"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "reference": number,
    }


def test_expense_duplicate_check_number(api, seed_accounts, client):
    # the company setting is off by default
    client.put("/api/settings", json={"warn_duplicate_check_number": "true"})
    assert tools.create_expense(api, _expense(seed_accounts, "1001"))["id"]
    with pytest.raises(ApiError) as exc:
        tools.create_expense(api, _expense(seed_accounts, "1001"))
    assert exc.value.status == 409
    assert exc.value.payload["code"] == "duplicate_check_number"
    posted = tools.create_expense(
        api, _expense(seed_accounts, "1001"), allow_duplicate_check_number=True
    )
    assert posted["id"]


def _write_token(path, encoding):
    path.write_text(TOKEN + "\n", encoding=encoding)
    path.chmod(0o600)
    return str(path)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_token_file_encodings(tmp_path, encoding):
    assert _read_token(_write_token(tmp_path / "t", encoding)) == TOKEN


def test_windows_token_must_be_under_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    monkeypatch.setattr(mcp_config, "_windows", lambda: True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    inside_file = _write_token(profile / "mcp-token", "utf-8")
    outside_file = _write_token(outside / "mcp-token", "utf-8")
    assert _read_token(inside_file) == TOKEN
    with pytest.raises(ConfigError, match="user profile"):
        _read_token(outside_file)
