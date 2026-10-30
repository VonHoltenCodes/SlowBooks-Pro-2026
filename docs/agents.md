# Agents: the local MCP server

SlowBooks Pro ships a small MCP server so an AI agent (Claude Code, Codex,
Claude Desktop) can work your books. It runs on your machine over stdio,
talks to the running app on loopback with an API token, and reaches the API
only: it adds nothing to the books and has no database access of its own.
It is the standard library only, so there is nothing to install.

## Setup

1. Under Settings, API Tokens, make a token. Use a **read-only** token
   unless the agent must post entries; the API enforces the role. The token
   is shown once, with these steps.
2. Save the token in a private file (one line, starting with `sbp_`). Type
   the command, paste the token, then press Ctrl-D, so the token never
   enters your shell history:

   ```
   (umask 077; cat > ~/.slowbooks-token)
   ```

   The parentheses keep the private umask to that one command.

   PowerShell on Windows, into the app's data folder:

   ```
   New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\SlowBooksPro\data" | Out-Null
   Set-Content -Encoding ascii "$env:LOCALAPPDATA\SlowBooksPro\data\mcp-token" (Read-Host 'Token')
   ```

   `Read-Host` shows the token on screen as you paste it; clear the window
   after. Keep the file out of a folder that syncs to the cloud (OneDrive's
   Desktop or Documents, or iCloud Drive's "Desktop & Documents" on macOS):
   a synced token has left the machine. The default `~/.slowbooks-token` is
   outside both.

3. Start the app, then connect the agent. The server is `app/mcp` in the
   checkout; run it as `python -m app.mcp` from the checkout, or from
   anywhere by the file's full path with the Python that runs the app:

   ```
   /path/to/SlowBooks-Pro-2026/.venv/bin/python /path/to/SlowBooks-Pro-2026/app/mcp/__main__.py
   ```

**Claude Code** (macOS and Linux)

```
claude mcp add slowbooks -e SLOWBOOKS_TOKEN_FILE=$HOME/.slowbooks-token -- /path/to/SlowBooks-Pro-2026/.venv/bin/python /path/to/SlowBooks-Pro-2026/app/mcp/__main__.py
```

PowerShell on Windows, with the token in the app's data folder (in cmd.exe
write the path as `%LOCALAPPDATA%\SlowBooksPro\data\mcp-token`):

```
claude mcp add slowbooks -e SLOWBOOKS_TOKEN_FILE="$env:LOCALAPPDATA\SlowBooksPro\data\mcp-token" -- C:\path\to\SlowBooks-Pro-2026\.venv\Scripts\python.exe C:\path\to\SlowBooks-Pro-2026\app\mcp\__main__.py
```

Add `-e SLOWBOOKS_MCP_WRITES=1` to let the agent post entries (with a
bookkeeper token).

**Codex** takes the same server in `~/.codex/config.toml`:

```toml
[mcp_servers.slowbooks]
command = "/path/to/SlowBooks-Pro-2026/.venv/bin/python"
args = ["/path/to/SlowBooks-Pro-2026/app/mcp/__main__.py"]
env = { SLOWBOOKS_TOKEN_FILE = "/home/you/.slowbooks-token" }
```

**Claude Desktop** takes it in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "slowbooks": {
      "command": "/path/to/SlowBooks-Pro-2026/.venv/bin/python",
      "args": ["/path/to/SlowBooks-Pro-2026/app/mcp/__main__.py"],
      "env": { "SLOWBOOKS_TOKEN_FILE": "/home/you/.slowbooks-token" }
    }
  }
}
```

Use full paths in these files; they do not expand `~` and do not run from
the checkout.

**Desktop install.** The installed program with `--mcp` starts the same
server (use its full path; it is not on PATH: on Windows
`"C:\Program Files\SlowBooks Pro 2026\SlowBooksPro.exe" --mcp`, on macOS
`/Applications/SlowBooks\ Pro.app/Contents/MacOS/SlowBooksPro --mcp`, on
Linux `~/.local/bin/SlowBooksPro` or wherever the AppImage or binary was
put: the file you run to start the app). It finds the app on the port in
your `.env` (else 3001) and reads the token from `mcp-token` in the data
directory:

| Platform | Default token file |
|----------|--------------------|
| Windows | `%LOCALAPPDATA%\SlowBooksPro\data\mcp-token` |
| macOS | `~/Library/Application Support/SlowBooksPro/data/mcp-token` |
| Linux | `~/.slowbookspro/data/mcp-token` |

`--mcp` needs the app already running. It does not open a company, migrate
or start a server.

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `SLOWBOOKS_TOKEN_FILE` | none (required) | File holding the token, one line |
| `SLOWBOOKS_URL` | `http://127.0.0.1:3001` | The running app; loopback `http://` only |
| `SLOWBOOKS_MCP_WRITES` | off | `1` lists the write tools |
| `SLOWBOOKS_TIMEOUT_SECONDS` | `120` | Per-request timeout, 1 to 3600 |

The token file is read once, when the server starts. After you revoke a
token and write a new one, restart the server (in Claude Code, reconnect
with `/mcp` or restart the session).

The app's API description is cached for the server's lifetime, so after
upgrading SlowBooks Pro, restart the server.

With `SlowBooksPro --mcp` and neither `--port` nor a `.env`, the server
looks on port 3001 and sends the token to whatever answers there. If the
app runs elsewhere, set the port (`--port`, or `SLOWBOOKS_URL`).

The token comes from a file and nowhere else, so it never shows in a process
list or an agent's config. On macOS and Linux the file must not be readable
by others (`chmod 600`). On Windows it must be inside your user profile
folder, which only your account can read; a file PowerShell wrote with a
byte-order mark or in UTF-16 is read as it is.

The server sends requests to the loopback address only, with its own
connection: a proxy setting in the environment is ignored and a redirect is
never followed, so the token cannot leave the machine.

## Protocol

One MCP protocol version is pinned (`2025-06-18`), negotiated at
`initialize`. A client that asks for another version is answered with ours
and a line on stderr says so; if the client then refuses it, the server
exits and the app and its REST API carry on. The server is opt-in: nothing
in the books depends on it.

## Tools

Reads (always listed):

| Tool | Does |
|------|------|
| `context` | Company and app facts to read before working |
| `api_schema` | How to call a route: methods, parameters and request body |
| `search` | Find records by name, number or amount across the books |
| `list_records` | List one kind of record. `filters` are the route's own query parameters (`api_schema` lists them; an unknown one is refused, not ignored); `limit` is 1 to 500 |
| `get_record` | One record by id |
| `list_reports` | The reports that can be run, with their parameters |
| `report` | Run a report by name (see `list_reports`) |
| `api_get` | Read-only GET of any other API route (see `api_schema`); the query goes in `params`, not in the path |

`list_records` answers `{"rows": [...], "shown": N, "total": M}` when more
rows exist than it returned, and a plain list otherwise. `search` adds
`"more"`, the count it left out, when a group was cut. `api_schema`'s prefix
listing says how many more routes match when it shows the first 20. A
result, an error included, is cut at 60,000 characters.

Record kinds: customers, vendors, items, accounts, invoices, bills,
expenses, journal_entries, payments, bill_payments, bank_transactions.

Writes (listed with `SLOWBOOKS_MCP_WRITES=1`). Each posts the data you give
it to the API as-is, so the API's own validation answers: a missing or
unknown field comes back as a sentence, and `api_schema` on the route lists
the fields. The tools are marked as writes, so a client such as Claude Code
asks you before running one.

| Tool | Does |
|------|------|
| `create_customer`, `create_vendor`, `create_item` | Master records. Customers and vendors take `force` for the possible-duplicate question |
| `create_invoice`, `create_bill` | Documents with lines. An invoice over the customer's credit limit is answered by `allow_over_credit_limit: true` in the data |
| `create_expense` | A paid expense, one amount or lines; `allow_duplicate_check_number` for the duplicate-check question |
| `create_journal_entry` | A manual journal entry; debits must equal credits |
| `record_payment`, `record_bill_payment` | Money in and out, applied to invoices or bills. Each takes a `key` (1 to 128 characters of letters, digits, `_ . : -`): the same key with the same data books once, the same key with other data is refused, so a retry after a lost answer can never pay twice |
| `void_record` | Void an invoice, bill, expense, journal entry, payment or bill payment with a reversing entry. A journal entry that is a document's posting is refused (void the document, which reverses its posting). Nothing is ever deleted |

A 409 that asks a question (a similar name, a used check number, a credit
limit) comes back whole, with the API's code and the facts it needs, and the
override is for you to answer, not the agent: the tools' instructions say
so. There is no generic write tool: a route gets a tool when a real use
shows up. When a write's answer is lost (a timeout, the app restarted), the
tool says so and what to do: retry a payment with the same key; check with
`list_records` or `search` before retrying anything else.

The text in your books (bank descriptions, memos, notes) reaches the agent
as data. The server's instructions tell it so, and to act only on what you
ask; a read-only token is the stronger guard.

## What a token can never do

No token can manage users or tokens, restore a backup or change the
company file, and the server refuses those routes before they are sent
(the path is decoded and normalised first, so an encoded spelling does not
get past it). The token's role limits the rest: the agent can do what that
role can. Every change is audited as `token:<label>`, using the label you
gave the token. Revoke it under Settings, API Tokens, and the agent is
locked out. `api_get` refuses the two reads that made a link on app versions
before 2.31.2 (`/api/payments/payment-link/{id}` and
`/api/employees/{id}/portal-token`), so a read tool never makes a link;
links are made in the app.

## Troubleshooting

- "SLOWBOOKS_TOKEN_FILE must name a file that holds the API token": the
  variable is unset. Set it to the token file's path.
- "The token file ... does not exist": check the path; use a full path in
  JSON configs.
- "The token file ... can be read or changed by other users": run
  `chmod 600` on it.
- The agent says the server is not configured: the setup sentence is in the
  server's instructions and in every tool's answer; fix it and restart the
  server (the client's reconnect).
- The agent is locked out after you replaced a token: the old one is still
  loaded. Restart the server.
- "On Windows the token file must be under your user profile folder": move
  it there, for example the app's data folder.
- "The token file ... must hold one line": the file holds the token and
  nothing else, and it starts with `sbp_`.
- "SLOWBOOKS_URL must be a loopback http:// address": use
  `http://127.0.0.1:<port>`. The server never talks to another machine.
- "No module named 'app'": `python -m app.mcp` was started outside the
  checkout. Use the full path to `app/mcp/__main__.py` as above.
- "The client asked for MCP protocol ...": the client and this server
  disagree on the protocol version. Update whichever is older.
- A tool you expect is missing: the write tools are listed only with
  `SLOWBOOKS_MCP_WRITES=1`.
- "Something other than SlowBooks Pro answered at ...": another program is
  on that port. Check `SLOWBOOKS_URL` and the app's port.
- "SlowBooks Pro is not answering at ... Nothing was sent.": the app is not
  running or is on another port.
- Tools answer with a refusal: the app is not running, the token is revoked,
  or its role does not allow that. The API's own sentence says which.
