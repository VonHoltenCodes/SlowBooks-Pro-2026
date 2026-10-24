# SlowBooks Pro Server Edition — setup guide

One download, two products: the same signed Windows build that runs as a
single-user desktop app can serve your whole office. Add a second user
and the deployment *becomes* Server Edition — no separate license, no
separate download, free either way.

## What you get

- One Windows PC hosts the books; everyone else uses a **browser** —
  nothing to install on the other computers.
- **Users and roles**: admin (everything), bookkeeper (daily books, no
  admin functions), accountant (reads everything, the audit log included,
  and posts journal entries only — an outside accountant's sign-in, see
  [working-with-your-accountant.md](working-with-your-accountant.md)),
  read-only (reports and lookups). **HR and payroll are
  admin functions** — pay runs, pay stubs, W-2/941 forms, direct-deposit
  accounts, portal tokens, benefits, garnishments, onboarding paperwork and
  employee records are refused to the other three roles, reads included; the
  employee list stays visible to them as a directory (names, activity,
  work state) with pay, tax elections and addresses blanked. Time entries
  and time off remain daily books.
- Every change in the audit log says **who** made it.
- The company stays **one file** — backup is copy, undo is restore.

## Quick trial (no install, stops when you close it)

On the machine that will host:

```powershell
cd <folder containing SlowBooksPro.exe>
.\SlowBooksPro.exe --serve-lan
```

A popup shows the connect URLs (also written to `connect-urls.txt` in the
data folder). Allow the Windows Firewall prompt. From any other computer
on the network, browse to `http://<host-name>:3001`.

## Permanent install (starts with Windows, no login needed)

From an **elevated** PowerShell in the folder containing the exe:

```powershell
powershell -ExecutionPolicy Bypass -File _internal\scripts\windows\serveredition-install.ps1
```

This registers a startup task (runs as SYSTEM before anyone logs in),
opens the firewall port, sets the data home to
`C:\ProgramData\SlowBooksPro` (machine-wide, not one user's profile),
starts the server, and prints your team's connect URLs. If you already
have desktop-mode books in `%LOCALAPPDATA%\SlowBooksPro`, the script
copies them (company files, encryption key, uploads, backups) into the
new data home the first time — your desktop copies are left untouched.

Run it from an **elevated** PowerShell (right-click → Run as
Administrator) and from the installed app's folder — a wrong location
stops with a "SlowBooksPro.exe not found" message before anything is
changed.

Undo it any time:

```powershell
powershell -ExecutionPolicy Bypass -File _internal\scripts\windows\serveredition-uninstall.ps1
```

Your books survive uninstall — the script never deletes data.

## Adding your team

On a multi-user install the sign-in screen lists the usernames, so a person picks
their name and types only their own password — the way desktop bookkeeping software
has always worked. Names only, never roles, and only before sign-in. Anyone who can
reach the port can read that list, which is one more reason Server Edition is for
trusted networks.

1. Sign in as the admin → **Settings → Users**.
2. Add each person with a username, password, and role. The moment a
   second user exists, the login screen gains a username field and the
   header reads **SERVER EDITION**.
3. Roles are enforced server-side: a read-only user physically cannot
   post an entry; an accountant can post and void their own journal
   entries and nothing else; a bookkeeper cannot touch Settings, Users,
   backups, or migrations, and cannot connect, remap or disconnect a bank
   feed (those store a credential and wire the books, so they are the
   administrator's) — but a bookkeeper does run the day-to-day syncs on a
   feed the administrator has connected. The last active admin can never be
   locked out — the app refuses to demote or deactivate them.
4. **Invite your accountant** (Settings → Users) makes a sign-in with a
   one-time password and shows what to send, once: the address, the username
   and the password, each with a Copy button of its own. They choose their own
   password at the first sign-in and nothing else opens until they do. Give the sign-in an end date if the
   engagement has one: past that day the sign-in is refused with the date,
   and you can extend it from the Users list, which also shows each
   person's last sign-in. An administrator's password reset works the same
   way: the new password is for one use.

## API tokens (programs, not people)

A program (an AI agent, the receipt service, an import tool, a script) signs
in with an API token instead of a password. An administrator makes one under
**Settings → API Tokens**: a label, a role and, if the program needs it,
**Can make backups**. The secret (`sbp_…`) is shown once and kept only as a
SHA-256 hash. The program sends it on every request as
`Authorization: Bearer sbp_…`; no session cookie and no CSRF token are
involved. Every change it makes is in the audit log as `token:<label>`.

What a token can do:

- Whatever its role allows: **bookkeeper** (daily books), **accountant**
  (reads everything, posts journal entries) or **read-only** (reports and
  lookups), exactly as for a person with that role. HR and payroll stay
  closed to all three.
- With **Can make backups** ticked: make a backup (`POST /api/backups`), list
  this company's backups (`GET /api/backups`) and download one
  (`GET /api/backups/download/<file>`), on top of its role. A backup is the
  whole company (every record, the sign-ins' password hashes, payroll and HR),
  so tick it only for a program you would hand the company file to. Only an
  administrator can tick or clear it, and the token list shows which tokens
  have it.

What a token cannot do, whatever its role or permission:

- Be an administrator. A new token is bookkeeper, accountant or read-only;
  settings, users, tokens, companies, migration imports and HR stay with a
  signed-in administrator.
- Restore a backup. Restoring replaces the whole company, so it is done by an
  administrator signed in to SlowBooks Pro (Settings → Backup / Restore, or
  `POST /api/backups/restore` from that session). A tool that resets the books
  between runs takes its backups with a token and leaves the restore to a
  person.
- Manage users or tokens, its own included.
- Come back after it is revoked. **Revoke** is final: the token answers
  `401` with "The API token "<label>" was revoked. Ask an administrator for a
  new one under Settings, API Tokens.", and it cannot be switched back on.
  Make a new token for a program that still needs one. A secret that matches
  no token is answered "Not authenticated", with nothing more.

Admin tokens made before 2.29.0 keep working as they did, with two changes:
they cannot restore a backup, and once revoked they stay revoked. No new
admin token can be made; revoke them and give each program a token with the
role it needs.

## Several companies on one server

One host serves every company in its list. A bookkeeper who keeps a small
business's books and a nonprofit's signs in to either from the same address,
and two people can work in two companies at the same time.

- **The sign-in screen asks which company.** It lists the companies by name
  (names only, like the usernames, and only on a Server Edition host), then the
  people in the chosen company.
- **Each company keeps its own people and roles.** A user lives in the company
  file, the way they always have. To give the bookkeeper both companies, add
  them under Settings → Users in each one, with whatever role each company
  gives them: admin in one and bookkeeper in the other is fine.
- **One sign-in, one click to switch.** Signing in also checks the same
  username and password against the other companies. The topbar then offers a
  company switch: each company that sign-in opens is one click away, with that
  company's role. A company where the person has another password, or no
  sign-in at all, is marked "(sign in)" and asks for its own password, with
  the username already chosen. A switch is recorded as a sign-in in that
  company's own log.
- **Everything else stays per company:** the audit log, backups (named for the
  company, and listed only in it), bank feeds, settings and the closing date.
  The company is still one file: backup is copy, undo is restore.
- **A new company** made under Companies → + New Company joins the list at
  once. The administrator who made it opens it (Companies → Open) and sets it
  up. Nobody else on the network can set up a company nobody has set up yet.
- **API tokens** belong to the company they were made in. A token's request
  names that company with an `X-SlowBooks-Company: <file>.db` header; without
  it, the request goes to the company the server was started for. A token
  is its own principal, not its maker's session: by design it outlives the
  deactivation of the user who made it, until it is revoked itself.
- **Starting the server** brings every company in the list up to this version
  before it serves any of them. A company that cannot be brought up (a failed
  migration, or a file encrypted under its own recovery key, which only opens
  at the SlowBooks Pro window) is named in `launcher.log` and is not served. To
  serve encrypted company files, set `COMPANY_FILE_ENCRYPTION_SECRET` on the
  host; every company is opened with it.

Customer pay links, the employee portal and payment-provider webhooks still
reach only the company the server was started for (the last one opened on the
host).

## Honest limits (current release)

- **Plain HTTP** — run this on a network you trust (an office LAN behind
  your router). TLS support is planned; until then do not port-forward
  it to the internet.
- Comfortable for small teams (2–10 people). The database serializes
  writes; hundreds of concurrent users is not the design target.
- The update badge appears in-app as usual: opening the app asks
  dl.slowbookspro.com for the latest version, which tells that host the
  install's IP address and version. Set `SLOWBOOKS_UPDATE_CHECK=0` in the
  host's SlowBooks `.env` file and restart to turn it off. Updating means
  running the new installer on the host machine.

## Troubleshooting

- **"Nothing happened" when running the exe** — the packaged exe has no
  console; output goes to `launcher.log` in the data folder, and
  `--serve-lan` shows its URLs in a popup + `connect-urls.txt`.
- **Double-clicking the exe shows a WebView2 error** — that's the
  *desktop window* needing the WebView2 runtime (the installer sets it
  up; the portable zip doesn't). `--serve-lan` doesn't need WebView2 at
  all — browsers on client machines are all it takes.
- **Other computers can't connect** — check the firewall rule (the
  install script creates it; the quick-trial path relies on you clicking
  Allow), and confirm host and clients are on the same network.
