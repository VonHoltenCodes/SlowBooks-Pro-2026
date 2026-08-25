# SlowBooks Pro Server Edition — setup guide

One download, two products: the same signed Windows build that runs as a
single-user desktop app can serve your whole office. Add a second user
and the deployment *becomes* Server Edition — no separate license, no
separate download, free either way.

## What you get

- One Windows PC hosts the books; everyone else uses a **browser** —
  nothing to install on the other computers.
- **Users and roles**: admin (everything), bookkeeper (daily books, no
  admin functions), read-only (reports and lookups).
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

1. Sign in as the admin → **Settings → Users**.
2. Add each person with a username, password, and role. The moment a
   second user exists, the login screen gains a username field and the
   header reads **SERVER EDITION**.
3. Roles are enforced server-side: a read-only user physically cannot
   post an entry; a bookkeeper cannot touch Settings, Users, backups, or
   migrations. The last active admin can never be locked out — the app
   refuses to demote or deactivate them.

## Honest limits (current release)

- **Plain HTTP** — run this on a network you trust (an office LAN behind
  your router). TLS support is planned; until then do not port-forward
  it to the internet.
- Comfortable for small teams (2–10 people). The database serializes
  writes; hundreds of concurrent users is not the design target.
- The update badge appears in-app as usual; updating means running the
  new installer on the host machine.

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
