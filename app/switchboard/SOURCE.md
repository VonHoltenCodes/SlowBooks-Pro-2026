# app/switchboard: SlowBooks Switchboard's line client, vendored

Connect to AI assistant (Settings, Connections, #1087; was "Connect Claude") holds the line to the
SlowBooks relay with SlowBooks Switchboard's own client, so the product
ships it instead of asking for a second program. The client is written
and tested in SlowBooks Switchboard's private source repository, package
`sbplive`; the relay server (`relay/`) is not copied.

- Source: SlowBooks Switchboard's source repository, `sbplive/`
- Commit: 247e20bb04f14858c647148ad7a439cd1b894b81 (branch multi-company,
  2026-11-21): setup takes only a token that ends with the administrator who
  made it (only `sbplive/cli.py` and `sbplive/probe.py` changed, which are
  not copied; the copies are as they were). Before it: c725852: setup given
  the address several companies are kept for connects Claude. Before it: 091dbe8: Switchboard's own store of several companies (sbplive
  add-books, which the product does not run) is used by a running
  connection only while it was added for the same address; of the copied
  files only `store.py` changed (its settings can be read from a given
  folder). Before it: 979edbe: a company leaves the connection only when SlowBooks Pro's
  gate turned its token away (X-SlowBooks-Token-Refused), never on a
  route's own 401; no tool sends anything but a GET under /api/auth/; the
  sentence for a refused company comes from the Books provider
  (`lost_sentence`); and Switchboard's own store (sbplive add-books, which
  the product does not run) keeps its companies for one address and only
  under tokens SlowBooks Pro ends with their maker. Before it: b7e258f
  (Switchboard's store of several companies), and 842aa6a: one connection
  can work several companies' books. Every
  tool takes a `books` argument, the handle `context` lists; a connection
  of several companies refuses a call that names none, an unknown handle,
  or a company's name or file, before anything is sent, and every result
  starts by naming the books. The registry takes a Books provider (one
  ApiClient still works as before); a client names its company in
  X-SlowBooks-Company, drops any other spelling of that header, and takes
  an answer only when X-SlowBooks-Company-Served names the same company.
  It carries main up to 9854f0e (the 1.0.0 public-export fixes: plain
  http:// only to a listed local network, an untaxed sales document
  answered as untaxed, reports without print pages).
  Before them: e5d2b2d (main, 2026-11-20,
  "sbplive/keep-the-apps-tax-note"): a sales document's own `tax_note`
  from SlowBooks Pro (a missing or expired exemption certificate) is kept,
  in front of the note about the rate, instead of being written over.
  Before it: 8bf0f93 ("sbplive/clearer-answers"): a DELETE's answer says "deactivated" or
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
| `client.py` | 372 |
| `config.py` | 392 |
| `line.py` | 1044 |
| `logs.py` | 78 |
| `operations.py` | 692 |
| `protocol.py` | 493 |
| `registry.py` | 708 |
| `remote.py` | 682 |
| `server.py` | 629 |
| `store.py` | 259 |
| `tools.py` | 1243 |
| `ws.py` | 589 |
| 14 files | 7803 |

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

Everything SlowBooks Pro needs on top (where the link is kept, each
company's token and handle, which companies are open here, the thread) is
in `app/services/switchboard_link.py`, never in these files: its
`LinkBooks` is the Books provider the line's registry is built on.

## Syncing

    python scripts/sync_switchboard.py <source checkout>
    python scripts/sync_switchboard.py <source checkout> --check

then put the new commit above. `tests/test_switchboard_vendored_1087.py`
compares the copies with a checkout when one is present
(`SLOWBOOKS_SWITCHBOARD_SOURCE`, else one found beside this checkout) and
skips that comparison where there is none, as in CI.
