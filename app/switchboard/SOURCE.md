# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect Claude (Settings, Connections, #1087) holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in the private SlowBooks-Pro-MCP repository, package `sbplive`;
the relay server (`relay/`) is not copied.

- Source: SlowBooks-Pro-MCP, `sbplive/`
- Commit: 38869e1f0cbc640226b1bdac0d2e3a345dde8e22 (main, 2026-11-19,
  "relay/account-less")
- Standard library only; nothing is added to the requirements.

## The files

The modules `line.py` imports, directly or through `server.py` (and
`remote.py`, whose session table `server.Mcp` uses):

`__init__.py`, `account.py`, `client.py`, `config.py`, `line.py`, `logs.py`,
`operations.py`, `protocol.py`, `registry.py`, `remote.py`, `server.py`,
`store.py`, `tools.py`, `ws.py`

Not copied: the OAuth door (`oauth*.py`, `app_signin.py`), the companion's
window, CLI, autostart and host setup, and `probe.py`.

## The two changes, and nothing else

1. A two-line header naming the source file.
2. `from sbplive ...` / `import sbplive` read `app.switchboard` (import
   lines only; the logger's name, the user agent and the sentences keep
   `sbplive`).

Everything SlowBooks Pro needs on top (where the link is kept, the token,
the company, the thread) is in `app/services/switchboard_link.py`, never in
these files.

## Syncing

    python scripts/sync_switchboard.py ~/Development/SlowBooks-Pro-MCP
    python scripts/sync_switchboard.py ~/Development/SlowBooks-Pro-MCP --check

then put the new commit above. `tests/test_switchboard_vendored_1087.py`
compares the copies with a checkout when one is present
(`SLOWBOOKS_SWITCHBOARD_SOURCE`, else `~/Development/SlowBooks-Pro-MCP`) and
skips that comparison where there is none, as in CI.
