# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect to AI assistant (Settings, Connections, #1087; was "Connect Claude") holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in SlowBooks Switchboard's private source repository, package
`sbplive`; the relay server (`relay/`) is not copied.

- Source: SlowBooks Switchboard's source repository, `sbplive/`
- Commit: f5da708e224cfc0882d79d8b1fca71c7257de5d7 (main, 2026-11-19,
  "relay/forget": Disconnect has the relay delete its record of these
  books, POST /v1/forget, with the relay name kept for the manual
  fallback). It carries "sbplive/annotations" (every tool that writes the books marked destructive; a clear 5xx
  sentence), "relay/steady-line" (a ping every 15 s, a silent line given up
  after 35 s, and "replay") and "sbplive/ask-first" (a create with little on
  it held back until the assistant asked; left_blank on every create).
  Before it: c0ab2c3
- Standard library only; nothing is added to the requirements.

## The files

The modules `line.py` imports, directly or through `server.py` (and
`remote.py`, whose session table `server.Mcp` uses):

| File | Lines (copy) |
|---|---:|
| `__init__.py` | 16 |
| `account.py` | 606 |
| `client.py` | 223 |
| `config.py` | 205 |
| `line.py` | 1019 |
| `logs.py` | 78 |
| `operations.py` | 649 |
| `protocol.py` | 389 |
| `registry.py` | 401 |
| `remote.py` | 682 |
| `server.py` | 581 |
| `store.py` | 170 |
| `tools.py` | 612 |
| `ws.py` | 589 |
| 14 files | 6220 |

Not copied: the self-hosted HTTP door's runner (`serve_http.py`, which
shows its connector secret on first run), the OAuth door (`oauth*.py`,
`app_signin.py`), the companion's window, CLI, autostart and host setup,
and `probe.py`.

## The three changes, and nothing else

1. A two-line header naming the source file.
2. The blocks between `# >>> private` and `# <<< private` are left out, as
   Switchboard's own public export leaves them out.
3. `from sbplive ...` / `import sbplive` read `app.switchboard` (import
   lines only; the logger's name, the user agent and the sentences keep
   `sbplive`).

Everything SlowBooks Pro needs on top (where the link is kept, the token,
the company, the thread) is in `app/services/switchboard_link.py`, never in
these files.

## Syncing

    python scripts/sync_switchboard.py <source checkout>
    python scripts/sync_switchboard.py <source checkout> --check

then put the new commit above. `tests/test_switchboard_vendored_1087.py`
compares the copies with a checkout when one is present
(`SLOWBOOKS_SWITCHBOARD_SOURCE`, else one found beside this checkout) and
skips that comparison where there is none, as in CI.
