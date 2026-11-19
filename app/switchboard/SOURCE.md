# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect Claude (Settings, Connections, #1087) holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in SlowBooks Switchboard's private source repository, package
`sbplive`; the relay server (`relay/`) is not copied.

- Source: SlowBooks Switchboard's source repository, `sbplive/`
- Commit: c0ab2c3ee0f1b3dbec50e44a0f619543fe1bca8d (main, 2026-11-19,
  "sbplive/serve-http-module": serve-http's runner in its own module, an
  explicit TLS 1.2 minimum; it carries "relay/line-concurrency", which
  stopped the line dropping with 1006 when one TLS socket was read and
  written from two threads at once)
- Standard library only; nothing is added to the requirements.

## The files

The modules `line.py` imports, directly or through `server.py` (and
`remote.py`, whose session table `server.Mcp` uses):

| File | Lines (copy) |
|---|---:|
| `__init__.py` | 16 |
| `account.py` | 544 |
| `client.py` | 223 |
| `config.py` | 205 |
| `line.py` | 973 |
| `logs.py` | 78 |
| `operations.py` | 628 |
| `protocol.py` | 389 |
| `registry.py` | 359 |
| `remote.py` | 682 |
| `server.py` | 558 |
| `store.py` | 170 |
| `tools.py` | 382 |
| `ws.py` | 589 |
| 14 files | 5796 |

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
