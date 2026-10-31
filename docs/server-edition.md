# SlowBooks Pro Server Edition — setup guide

One download, two products: the same signed Windows build that runs as a
single-user desktop app can serve your whole office. Run it as a server and
the deployment *is* Server Edition — no separate license, no separate
download, free either way.

## What you get

- One Windows PC hosts the books; everyone else uses a **browser** —
  nothing to install on the other computers.
- **Users and roles**: administrator (everything), bookkeeper (daily books,
  no administrator functions), accountant (reads everything but payroll, HR
  and the employee records, posts journal entries, voids its own, attaches
  support to them and uses Reclassify Transactions — an outside accountant's
  sign-in, see [working-with-your-accountant.md](working-with-your-accountant.md)),
  read-only (reports and lookups). **HR and payroll are administrator
  functions** — pay runs, pay stubs, W-2/941 forms, direct-deposit accounts,
  portal links, benefits, garnishments, onboarding paperwork and employee
  records are refused to the other three roles, reads included. The
  employee list stays visible to bookkeeper and read-only sign-ins as a
  directory (names, activity, work state) with pay, tax elections and
  addresses blanked; an accountant's sign-in sees no employee records at
  all. Time entries and time off remain daily books.
- Every change in the audit log says **who** made it.
- The company stays **one file** — backup is copy, undo is restore.

## Quick trial (no install)

On the machine that will host, close the SlowBooks Pro window first (the
window and the server cannot share port 3001), then from a PowerShell in
the folder the installer put the app in:

```powershell
cd "C:\Program Files\SlowBooks Pro 2026"
.\SlowBooksPro.exe --serve-lan
```

A popup shows the connect URLs (also written to `connect-urls.txt` in the
data folder, `%LOCALAPPDATA%\SlowBooksPro\data`). Allow the Windows Firewall
prompt. From any other computer on the network, browse to
`http://<host-name>:3001`. It serves your own desktop books, as the signed-in
Windows user, and keeps serving after the popup and the PowerShell window are
closed: to stop it, end **SlowBooksPro.exe** in Task Manager, or sign out of
Windows. (Opening the SlowBooks Pro window while it serves is refused with
"already running".)

## Permanent install (starts with Windows, no login needed)

From an **elevated** PowerShell (right-click → Run as Administrator):

```powershell
cd "C:\Program Files\SlowBooks Pro 2026"
powershell -ExecutionPolicy Bypass -File _internal\scripts\windows\serveredition-install.ps1
```

The script:

- stops any server that is running;
- sets the data home to `C:\ProgramData\SlowBooksPro` (machine-wide, not one
  user's profile): the books go in `C:\ProgramData\SlowBooksPro\data`
  (`companies\`, `companies.json`, `backups\`, `launcher.log`) and the file
  with the encryption and session keys is `C:\ProgramData\SlowBooksPro\.env`.
  **Keep that `.env` with your backups**: without it a restored company
  cannot decrypt its payroll bank numbers or stored credentials;
- copies your desktop books the first time, if the server has none yet:
  the company files, manifest, backups and `.env` from
  `%LOCALAPPDATA%\SlowBooksPro`. Your desktop copies are left untouched.
  (An install made with a version before 2.31.3 kept the books one folder
  up and the keys at `C:\ProgramData\.env`; the script moves them into
  place once, and says so);
- restricts the data home to SYSTEM and Administrators. Windows lets every
  local account read what is created under `C:\ProgramData` by default,
  which here would be every company file and the keys;
- opens TCP 3001 in Windows Firewall on **private and domain** networks
  only, so a laptop host on a network Windows calls Public does not serve the
  sign-in screen to it;
- registers the startup task **SlowBooksProServer**, which runs the server
  as SYSTEM before anyone logs in, with no time limit, on battery as well as
  mains, and restarted if it dies;
- starts the server and prints your team's connect URLs.

A wrong location stops with a "SlowBooksPro.exe not found" message before
anything is changed. Options: `-Port 3010` serves another port, `-DataDir`
puts the data home elsewhere, `-ExePath` names the exe when the script is not
run from the install folder.

**If this is a new company, set the administrator password at once**: open
`http://localhost:3001` on the host as soon as the script finishes. Until it
is set, the first browser to reach the server could set it.

**Using the host itself:** open `http://localhost:3001` in a browser. The
desktop shortcut refuses while the server runs ("already running"), which is
right: the server has the books.

Undo it any time:

```powershell
powershell -ExecutionPolicy Bypass -File _internal\scripts\windows\serveredition-uninstall.ps1
```

Your books and keys survive uninstall — the script never deletes data.

## Updating, stopping and starting

**Updating.** Run the new installer on the host. It stops the server to
replace the program files and, when it finds the SlowBooksProServer task,
starts it again at the end instead of offering to open the desktop window
(2.31.3; an older installer left the server stopped until the next reboot,
and the window it opened then held port 3001). Running the install script
again does the same and re-registers the task against the installed exe.

**Stopping and starting**, from an elevated prompt:

```powershell
schtasks /End /TN SlowBooksProServer
schtasks /Run /TN SlowBooksProServer
```

A setting for the server (`SLOWBOOKS_UPDATE_CHECK=0`,
`COMPANY_FILE_ENCRYPTION_SECRET`) goes in `C:\ProgramData\SlowBooksPro\.env`,
then stop and start the task; a per-user environment variable never reaches
the SYSTEM task.

## Adding your team

On a multi-user install the sign-in screen lists the usernames, so a person
picks their name and types only their own password — the way desktop
bookkeeping software has always worked. Names only, never roles, and only
before sign-in. Anyone who can reach the port can read that list, which is
one more reason Server Edition is for trusted networks.

1. Sign in as the administrator → **Settings → Users**.
2. Add each person with a username, password, and role. The sidebar reads
   **Server Edition** whenever the host serves the network (`--serve-lan`);
   the moment a second user exists, the sign-in screen also lists the
   usernames.
3. Roles are enforced server-side: a read-only user physically cannot
   post an entry; an accountant posts journal entries, voids their own,
   attaches support to them and reclassifies, and nothing else; a
   bookkeeper cannot touch Settings, Users, backups, or migrations, and
   cannot connect, remap or disconnect a bank feed (those store a
   credential and wire the books, so they are the administrator's) — but a
   bookkeeper does run the day-to-day syncs on a feed the administrator has
   connected. The last active administrator can never be locked out — the
   app refuses to demote or deactivate them.
4. **Invite your accountant** (Settings → Users) makes a sign-in with a
   one-time password and shows what to send, once: the address, the username
   and the password, each with a Copy button of its own. They choose their own
   password at the first sign-in and nothing else opens until they do. Give
   the sign-in an end date if the engagement has one: past that day the
   sign-in is refused with the date, and you can extend it from the Users
   list, which also shows each person's last sign-in. An administrator's
   password reset works the same way: the new password is for one use.

## API tokens (programs, not people)

A program (an AI agent, the receipt service, an import tool, a script) signs
in with an API token instead of a password. An administrator makes one under
**Settings → API Tokens**: a label, a role and, if the program needs it,
**Can make backups**. The secret (`sbp_…`) is shown once and kept only as a
SHA-256 hash, with its first ten characters as a hint in the list. The
program sends it on every request as `Authorization: Bearer sbp_…`; no
session cookie and no CSRF token are involved. Every change it makes is in
the audit log as `token:<label>`.

What a token can do:

- Whatever its role allows: **bookkeeper** (daily books), **accountant**
  (reads everything but payroll, HR and employee records; posts journal
  entries) or **read-only** (reports and lookups), exactly as for a person
  with that role. HR and payroll stay closed to all three.
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

An administrator token made before 2.29.0 is refused on every request with a
sentence that says to revoke it and make a bookkeeper, accountant or
read-only token for the program; Settings, API Tokens flags it in the list
until it is revoked. No new administrator token can be made.

## Backups

**Settings → Backup / Restore → Create Backup** writes the open company's
file to `C:\ProgramData\SlowBooksPro\data\backups` (or `%LOCALAPPDATA%\SlowBooksPro\data\backups`
for the quick trial); Download saves a copy where you choose. A program with
a **Can make backups** token takes them over the API. Backups are made on
demand only. Copy them off the machine together with
`C:\ProgramData\SlowBooksPro\.env`, which holds the keys the restored company
needs, and with the recovery key of any encrypted company.

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
  gives them: administrator in one and bookkeeper in the other is fine.
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
  serve encrypted company files, set `COMPANY_FILE_ENCRYPTION_SECRET` in the
  server's `.env` **before** turning encryption on; every company is opened
  with it. (Turned on without it, the company gets a recovery key instead,
  and the next server start refuses the file.)

Customer pay links, the employee portal and payment-provider webhooks still
reach only the company the server was started for (the last one opened on the
host).

## Reaching it from outside the office

Server Edition is **plain HTTP** on a trusted network. Do not port-forward
it to the internet: the sign-in page lists companies and usernames to
whoever reaches it, and the session cookie travels unencrypted.

- **A VPN** is the simple answer for an accountant or a person working from
  home: Tailscale, WireGuard or the office router's own VPN puts their
  computer on the office network, and nothing is exposed. This is the setup
  to recommend.
- **A tunnel with a sign-in in front** (a Cloudflare Tunnel with Cloudflare
  Access, or the like) also works: the tunnel's agent runs on the host and
  forwards to `http://localhost:3001`, and the access policy decides who gets
  through, by email. Two things to know: customer pay links, the employee
  portal and payment webhooks need a bypass in that policy or customers,
  employees and the payment provider are blocked; and the server still has
  no HTTPS of its own, so the cookie has no Secure flag and there is no HSTS.
  Follow the tunnel provider's own documentation for the steps.
- **A cloud server** running the Docker image behind a real certificate is
  the right answer when the books should live off-site: [cloud-hosting.md](cloud-hosting.md).

## Honest limits (current release)

- **Plain HTTP** — run this on a network you trust (an office LAN behind
  your router). TLS support is planned; until then see the section above.
- Comfortable for small teams (2–10 people). The database serializes
  writes; hundreds of concurrent users is not the design target.
- The update badge appears in-app as usual: opening the app asks
  dl.slowbookspro.com for the latest version, which tells that host the
  install's IP address and version. Set `SLOWBOOKS_UPDATE_CHECK=0` in
  `C:\ProgramData\SlowBooksPro\.env` (the quick trial's is
  `%LOCALAPPDATA%\SlowBooksPro\.env`) and stop and start the task to turn it
  off.
- `--bind <IP>` serves one interface only; `--bind 127.0.0.1` serves the host
  alone and turns Server Edition mode off (the sidebar no longer says so and
  the sign-in screen lists no companies). Keep the LAN bind and let the
  firewall rule decide who reaches it.

## Troubleshooting

- **"Nothing happened" when running the exe** — the packaged exe has no
  console; output goes to `launcher.log` in the data folder
  (`C:\ProgramData\SlowBooksPro\data` for the permanent install,
  `%LOCALAPPDATA%\SlowBooksPro\data` otherwise), and `--serve-lan` shows its
  URLs in a popup + `connect-urls.txt`.
- **The quick trial does nothing** — the SlowBooks Pro window is open and
  holds the port; `launcher.log` says "already running". Close the window
  and run it again.
- **Double-clicking the exe shows a WebView2 error** — that's the
  *desktop window* needing the WebView2 runtime, version 101.0.1210.39 or
  later (the installer sets it up or updates it; the portable zip doesn't).
  `--serve-lan` doesn't need WebView2 at all — browsers on client machines
  are all it takes.
- **Other computers can't connect** — the firewall rule covers private and
  domain networks; if Windows calls the office network Public, change the
  network's profile to Private (Settings → Network & internet → the network
  → Network profile type). Confirm host and clients are on the same network.
  The quick-trial path relies on you clicking Allow at the firewall prompt.
- **The server stops after the host sleeps or goes on battery** — an install
  made before 2.31.3 used Task Scheduler's defaults, which stop a task on
  battery; run the install script again to re-register the task with the
  current settings.
