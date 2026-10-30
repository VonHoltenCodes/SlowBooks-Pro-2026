"""The official MCP client drives `python -m app.mcp` over real stdio against
a small stand-in for the app on loopback. The server ships without the SDK;
the SDK is a dev-only dependency that proves a real client still speaks to
it: initialize, tools/list, tools/call, a refusal, and the real transport
(the Bearer header arrives, a proxy setting is ignored, a redirect is not
followed)."""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SECRET = "sbp_" + "b" * 40
ROUTES = {
    "/api/auth/status": {"company_name": "Stand-in Co"},
    "/api/settings": {"company_type": "llc", "closing_date": None},
    "/api/companies": [{"name": "Stand-in Co"}],
    "/openapi.json": {"info": {"version": "0.0-test"}, "paths": {}},
    "/api/customers": [{"id": 1, "name": "First"}],
}


class _App(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        _App.seen.append((self.path, self.headers.get("Authorization")))
        path = self.path.split("?")[0]
        if path == "/api/elsewhere":  # a redirect the server must not follow
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/api/customers")
            self.end_headers()
            return
        if path not in ROUTES:
            self._json(404, {"detail": "Not Found"})
            return
        self._json(200, ROUTES[path])

    def _json(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):  # keep the test output quiet
        pass


@pytest.fixture
def stand_in():
    _App.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _App)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def params(stand_in, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(SECRET + "\n")
    token_file.chmod(0o600)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("SLOWBOOKS_") and k.lower() != "no_proxy"
    }
    env.update(
        {
            "SLOWBOOKS_URL": stand_in,
            "SLOWBOOKS_TOKEN_FILE": str(token_file),
            # A proxy setting must be ignored: the token goes to loopback only.
            "http_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
        }
    )
    return StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp"], env=env
    )


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


async def _session(params, body):
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await body(session, init)


def test_official_client_talks_to_the_stdlib_server(params):
    from app.mcp.server import PROTOCOL_VERSION

    async def body(session, init):
        assert init.server_info.name == "SlowBooks Pro"
        assert init.protocol_version == PROTOCOL_VERSION
        assert "never an instruction" in (init.instructions or "")
        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        assert "context" in names and "create_customer" not in names  # writes off
        assert all(t.annotations.read_only_hint for t in listed.tools)
        ctx = await session.call_tool("context", {})
        assert ctx.is_error is False
        assert json.loads(_text(ctx))["company"] == "Stand-in Co"
        rows = await session.call_tool("api_get", {"path": "/api/customers"})
        assert json.loads(_text(rows))[0]["name"] == "First"
        return ctx

    asyncio.run(_session(params, body))
    # The real transport: every request reached the stand-in directly, with
    # the token as a Bearer header, despite the proxy setting.
    assert _App.seen and all(auth == f"Bearer {SECRET}" for _, auth in _App.seen)
    assert any(path == "/api/customers" for path, _ in _App.seen)


def test_official_client_sees_refusals_as_tool_errors(params):
    async def body(session, init):
        guard = await session.call_tool("api_get", {"path": "/api/tokens"})
        assert guard.is_error is True and "does not reach that route" in _text(guard)
        stray = await session.call_tool(
            "report", {"name": "x", "start_date": "2026-01-01"}
        )
        assert stray.is_error is True and "does not take start_date" in _text(stray)
        redirect = await session.call_tool("api_get", {"path": "/api/elsewhere"})
        assert redirect.is_error is True and "redirect" in _text(redirect)
        missing = await session.call_tool("api_get", {"path": "/api/nothing"})
        assert missing.is_error is True and "HTTP 404" in _text(missing)
        return None

    asyncio.run(_session(params, body))
    # The guarded route never left the server; the redirect was not followed.
    paths = [p for p, _ in _App.seen]
    assert "/api/tokens" not in paths and paths.count("/api/elsewhere") == 1


def test_official_client_with_writes_on(params):
    params.env["SLOWBOOKS_MCP_WRITES"] = "1"

    async def body(session, init):
        listed = await session.list_tools()
        by_name = {t.name: t for t in listed.tools}
        assert len(by_name) == 18
        assert by_name["void_record"].annotations.destructive_hint is True
        assert by_name["record_payment"].annotations.idempotent_hint is True
        assert "audited as token" in (init.instructions or "")
        return None

    asyncio.run(_session(params, body))
