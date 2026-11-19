# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect Claude (Settings, Connections, #1087) holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in SlowBooks Switchboard's private source repository, package
`sbplive`; the relay server (`relay/`) is not copied.

- Source: SlowBooks Switchboard's source repository, `sbplive/`
- Commit: cb1e1bb2ebba484413f78a9e7f18b83acc964f4c (main, 2026-11-19,
  "sbplive/annotations": every tool that writes the books is marked
  destructive, and a server failure with nothing usable to say is one
  clear sentence, never its body). It carries "relay/steady-line" and
  "sbplive/ask-first" (a create with
  little on it is held back until the assistant asked the person; every
  create's answer says what was left blank) and the steadier line (a ping
  every 15 s, a silent line given up after 35 s, and "replay": a request
  the relay sends again after a drop is answered from what was kept and
  never run twice). Before it: c0ab2c3 (serve-http's runner in its own
  module, an explicit TLS 1.2 minimum, and the line read and written from
  one thread at a time)
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
| 14 files | 6158 |

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
