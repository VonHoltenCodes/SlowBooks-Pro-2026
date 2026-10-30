"""Local MCP server: an agent works the books through the API (#655).

Run with ``python -m app.mcp``. Stdio transport, loopback HTTP to the
running app, an API token from a private file. Nothing here touches the
database or the models: every tool is a request the browser could make,
under the token's role, audited as ``token:<label>``.

Standard library only: no SDK ships with the app. The official MCP client
is a dev-only dependency that drives the server in tests. Reads are on by
default; the write tools are listed only with ``SLOWBOOKS_MCP_WRITES=1``.
"""
