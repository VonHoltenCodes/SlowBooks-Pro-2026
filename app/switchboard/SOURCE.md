# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect to AI assistant (Settings, Connections, #1087; was "Connect Claude") holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in SlowBooks Switchboard's private source repository, package
`sbplive`; the relay server (`relay/`) is not copied.

- Source: SlowBooks Switchboard's source repository, `sbplive/`
- Commit: 8bf0f93bb6e9734b8419a08b2fc7b1cfb0f1fb0c (main, 2026-11-19,
  "sbplive/clearer-answers"): a DELETE's answer says "deactivated" or
  "deleted" as the app answered; a payment's answer names the account the
  money went to (Undeposited Funds when no deposit account was given); an
  expense is asked about first and, without the account it was paid from,
  is not sent and names the account the form would start at; a new item
  without accounts lists what its sales and purchases post to. It carries
  "sbplive/sales-tax-default" and "sbplive/defaulted-no-ids" (a sales
  document sent without a tax rate takes the company's Default Tax Rate,
  as the app's form does, and says so; every create lists `left_blank` and
  `defaulted`) and "sbplive/connector-icon" (serverInfo reads title
  "SlowBooks Pro", websiteUrl and icons; through the relay the icons are
  the relay's own files, which the line points at). Before them: f5da708
  ("relay/forget"), with "sbplive/annotations", "relay/steady-line" and
  "sbplive/ask-first".
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
| `line.py` | 1030 |
| `logs.py` | 78 |
| `operations.py` | 684 |
| `protocol.py` | 493 |
| `registry.py` | 401 |
| `remote.py` | 682 |
| `server.py` | 615 |
| `store.py` | 170 |
| `tools.py` | 1011 |
| `ws.py` | 589 |
| 14 files | 6803 |

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
