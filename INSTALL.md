# Installation Guide

Ways to run Slowbooks Pro 2026.

---

## Option 0: Windows installer

**Recommended for Windows users.** No Docker, no WSL2, no command line —
Slowbooks Pro runs as a normal desktop app in its own window (no browser
tab). The installer is digitally signed, so Windows shows a verified
publisher instead of a SmartScreen warning.

### Steps

1. Download **`SlowBooksPro-Setup-x64.exe`** from the
   [latest release](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest)
   (or from [slowbookspro.com](https://www.slowbookspro.com)).
2. Double-click it and follow the wizard. Everything installs into
   `Program Files`; a Start Menu entry (and optional Desktop shortcut) is
   created.
3. Launch **SlowBooks Pro 2026** — you'll be asked to create your first
   company, and the app opens in its own window.

The app is fully self-contained — **no Docker, no WSL2, no database server,
no Python install**. Your books are stored in ordinary files on your own
machine, under `%LOCALAPPDATA%\SlowBooksPro`, which upgrades and even
uninstalls leave untouched.

When a new version is released, the top of the sidebar shows an
**Update available** notice — download the new installer and run it over
the old install; your companies and settings are kept.

### Working with multiple companies

This install manages companies the way QuickBooks Desktop does: each company
is its own file, stored under `%LOCALAPPDATA%\SlowBooksPro\data\companies\`.
Every time you open SlowBooks Pro you're asked which company to open (or to
create a new one). To switch companies while it is open, use **Companies →
Switch company…**.

### Stopping the app

Just close the window — the server shuts down with it. If something ever gets
stuck, end the **SlowBooksPro** process from Task Manager.

### Troubleshooting

The app runs with no console window — quietly in the background like any
other desktop app. If something goes wrong before the app window can open, a
small popup explains it, and full details are written to
`%LOCALAPPDATA%\SlowBooksPro\data\launcher.log`.

### Microsoft Edge WebView2 (the app window)

The app's window is drawn by Microsoft's Edge WebView2 runtime, which
Windows 11 has and most Windows 10 machines do too. SlowBooks needs
**version 101.0.1210.39 or later**: an older runtime cannot draw the window
at all. The installer installs the runtime when it is missing and updates it
when it is older than that (it downloads it from Microsoft, so it needs the
internet for that step).

When you open SlowBooks on a computer whose runtime is missing or older (the
portable .zip never sets it up, and an offline computer, or one whose updates
are held back, can stay on an old one), the app does not open a blank
window. It says the runtime is missing, or which version it found and which
it needs; gives Microsoft's download page,
<https://developer.microsoft.com/microsoft-edge/webview2/>; and offers to
open SlowBooks in your web browser meanwhile. Install or update the runtime
from that page (the Evergreen Bootstrapper), or run the SlowBooks installer,
which does it for you, then open SlowBooks. `launcher.log` records the
version found at every start. And if the window does open but the app
cannot start in it, the page says so in place of a white screen, with the
error to send to support.

### Windows Defender "Controlled folder access"

Off by default on Windows 11, but some people (and most managed PCs) turn it
on. When it is on, Defender blocks any app it does not know from writing
into Documents, Downloads and the other protected folders — including a
OneDrive-redirected Documents — and a code signature earns no exception.
SlowBooks writes there in two places: **Save PDF / Save CSV** on reports go
to `Documents\SlowBooks Pro\Reports`, and **Save backup** copies the backup
to `Downloads`. If the write is refused, the app does not fail silently: the
PDF or CSV lands in `%LOCALAPPDATA%\SlowBooksPro\data\Reports` instead and
the notice says so (with a *Show in folder* button), and the backup notice
tells you it is still in the app's own `backups` folder. Defender's own toast
may or may not appear.

To let SlowBooks use your Documents and Downloads folders directly:
**Windows Security → Virus & threat protection → Ransomware protection →
Manage Controlled folder access → Allow an app through Controlled folder
access → Add an allowed app → Recently blocked apps**, and pick
`SlowBooksPro.exe` (or browse to `C:\Program Files\SlowBooks Pro 2026\`).

### Backups

Backups created from the Settings UI are simply snapshots of the open
company's `.db` file (stored in the app's `backups` folder). You can also copy
the company files in `%LOCALAPPDATA%\SlowBooksPro\data\companies\` anywhere
you like while the app is closed — each file is a complete company. Keep
`%LOCALAPPDATA%\SlowBooksPro\.env` with your copies: it holds the keys that
encrypt payroll bank numbers and saved credentials, which a restored company
needs. An encrypted company also has a `.db.keyring` beside it and a recovery
key you were shown once; keep that too.

### Tradeoffs versus Option 1 (Docker + PostgreSQL)

Out of the box this path is single-user on one machine, with nothing to
administer — no `pg_dump`, no containers, no background services. The same
install also serves an office over the LAN as **Server Edition**
([docs/server-edition.md](docs/server-edition.md)): browser sign-ins with
roles, an audit trail that names who changed what, and a startup task that
serves the books before anyone logs in. Docker and PostgreSQL are for Linux
servers, Intel Macs and a cloud host.

---

## Option 0A: macOS desktop app

**Recommended for Apple Silicon Macs running macOS 15 or newer.** No Docker,
Python, or database server is required.

1. Download `SlowBooksPro-<version>-macos-arm64.dmg` from the
   [latest release](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest).
2. Open the disk image and drag **SlowBooks Pro** to Applications.
3. Eject the image, then launch **SlowBooks Pro** from Applications.

The app is signed with an Apple Developer ID and notarized by Apple. Each
company is an ordinary SQLite file under
`~/Library/Application Support/SlowBooksPro/data/companies/`. Settings,
backups, uploads, and `launcher.log` live under the same `data` directory and
remain outside the app bundle during upgrades.

This path has the same single-user tradeoffs as the Windows desktop app. The
app is built for Apple Silicon only; Intel Macs use Docker or the
developer-oriented native install below.

---

## Option 1: Docker (Windows, macOS, Linux)

> Putting it on a cloud server for your own company? [docs/cloud-hosting.md](docs/cloud-hosting.md)
> is the whole recipe: one VPS, Docker, a proxy with a real certificate, backups off the box.

**Recommended for Linux servers and Intel Macs.** One command, no
dependency headaches. Note: multi-user over the LAN does **not** require
Docker — Server Edition runs from the signed Windows installer (see
[docs/server-edition.md](docs/server-edition.md)).

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine + Docker Compose (Linux)

### Steps

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
cp .env.example .env

# Set a strong encryption secret for employee bank PII. The app refuses to
# start against Postgres with the shipped dev default, an empty value or
# one shorter than 16 characters, so this is required:
#   Linux/macOS:  openssl rand -base64 32
#   any OS:       python -c "import secrets; print(secrets.token_urlsafe(32))"
# Put the result on the PAYROLL_ENCRYPTION_SECRET= line in .env.

docker compose up
```

Open **http://localhost:3001** in your browser.

> The compose stack is a **single-host** install: Postgres lives on the
> compose-internal network and the app serves plain HTTP on localhost.
> `docker-compose.yml` sets `SLOWBOOKS_PRIVATE_NETWORK=1`, which skips the
> two production startup checks for a TLS database connection and an HTTPS
> redirect, and `FORCE_HTTPS=false`, so http://localhost:3001 answers
> instead of redirecting. Leave `FORCE_HTTPS` unset in `.env` (a `true`
> there wins over the compose default and sends the browser to an https://
> address nothing answers). To serve beyond this host, use
> `docker-compose.prod.yml` behind a TLS proxy: [docs/cloud-hosting.md](docs/cloud-hosting.md)
> is the whole recipe, and [docs/tls-proxy-setup.md](docs/tls-proxy-setup.md)
> covers the proxy, `FORWARDED_ALLOW_IPS` and `RATE_LIMIT_STORAGE_URI`.

> On Windows, **Option 0** avoids all of this (no Docker at all, secret
> generated for you, opens a desktop window, and serves the office as Server
> Edition from the same install).

### What happens on first run

1. PostgreSQL 17 starts and creates the `bookkeeper` database
2. Alembic runs all migrations (the complete schema — no table depends on app startup)
3. Chart of Accounts is seeded (56 accounts, including the payroll-liability accounts and 6810 Depreciation Expense; a corporation's or partnership's chart adds its equity accounts) and a default Equipment asset type
4. Uvicorn starts serving the app on port 3001
5. On first visit, you'll be prompted to set an operator password (min 8 characters)

### Loading demo data

To populate the IRS Publication 583 mock data (Henry Brown's Auto Body Shop):

```bash
docker compose exec slowbooks python scripts/seed_irs_mock_data.py
```

### Stopping and restarting

```bash
docker compose down          # stop (data persists in volumes)
docker compose up            # restart
docker compose down -v       # stop AND delete all data
```

### Changing the port

Edit `.env`:
```
APP_PORT=8080
```
Then `docker compose up` — the app will be at http://localhost:8080.

### Network exposure (defaults are loopback-only)

By default `docker compose up` binds both published ports to `127.0.0.1`, so
the app (`3001`) and Postgres (`5433`) answer on **this host only**. That keeps
the dev database — which ships with the default `bookkeeper` password — off the
LAN. To reach either from another machine, set the matching bind variable in
`.env` to `0.0.0.0`:

```
APP_BIND=0.0.0.0        # expose the app (put a TLS proxy in front first)
POSTGRES_BIND=0.0.0.0   # expose Postgres (set a real POSTGRES_PASSWORD first)
```

A port Docker publishes is opened by Docker itself, around a host firewall
such as `ufw`; the bind address is what keeps it off the network.

For production, use `docker-compose.prod.yml`, which publishes neither service
and expects your own TLS-terminating reverse proxy in front.

### Allowing a different browser origin

CORS defaults to `http://localhost:APP_PORT` and `http://127.0.0.1:APP_PORT`.
If the UI is served from a different host (reverse proxy, LAN IP, etc.), set
`CORS_ALLOW_ORIGINS` in `.env` to a comma-separated allowlist:

```
CORS_ALLOW_ORIGINS=https://books.example.com,https://admin.example.com
```

### Backups

Backups created from the Settings UI are stored in a Docker volume. To copy them out:

```bash
docker compose cp slowbooks:/app/backups ./my-backups
```

---

## Option 2: Native Install (Linux)

**Best for Linux development.** Direct install, no containers.

### Prerequisites

- Python 3.13 (CI gates against 3.13; 3.12 is verified working)
- PostgreSQL 17 (Docker image ships 17-alpine; older 16 still works for native installs)
- System libraries for WeasyPrint

### Steps

```bash
# Install system dependencies (Ubuntu/Debian/Pop!_OS)
sudo apt install -y postgresql python3-venv libcairo2-dev libpango-1.0-0 \
    libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev

# Optional — receipt scanning (Tier 2 OCR). The feature degrades
# gracefully without these; the Scan Receipt button just stays disabled.
# poppler-utils turns PDF receipts into images on Linux only: the Windows
# and macOS apps render PDFs with the OS and need nothing extra.
sudo apt install -y tesseract-ocr poppler-utils
# Only if you want the desktop window (python3 desktop_launcher.py) rather
# than a browser: pywebview renders through WebKitGTK and needs the
# GObject-introspection typelib, which is a separate package from the
# library itself.
sudo apt install -y gir1.2-webkit2-4.1

# Create database
sudo -u postgres createuser bookkeeper -P    # password: bookkeeper
sudo -u postgres createdb bookkeeper -O bookkeeper

# Clone
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026

# Create a virtual environment and install into it.
# This is REQUIRED, not optional: Ubuntu 23.04+, Debian 12+, Pop!_OS 24.04
# and Fedora 38+ implement PEP 668, so a bare `pip install` into the system
# interpreter fails with "error: externally-managed-environment".
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env if your database credentials differ, and set these three:
#     APP_DEBUG=true                # a development install; see below
#     APP_HOST=127.0.0.1            # the example ships 0.0.0.0, the whole LAN
#     PAYROLL_ENCRYPTION_SECRET=    # paste the output of: openssl rand -base64 32
# The app refuses to start against PostgreSQL with that secret empty or
# shorter than 16 characters. Leave FORCE_HTTPS unset: with APP_DEBUG=true
# it defaults to false, and a true there 307-redirects http://localhost:3001
# to https://localhost:3001, where a native install serves no TLS.
# Production deployments keep APP_DEBUG=false (FORCE_HTTPS then defaults to
# true) and terminate TLS at a reverse proxy: docs/tls-proxy-setup.md.

# Run migrations and seed
alembic upgrade head
python scripts/seed_database.py

# Start the server
python run.py
```

Open **http://localhost:3001**.

> Every command above assumes the virtualenv is active (`source .venv/bin/activate`).
> Without activating it, use the explicit paths instead: `.venv/bin/alembic`,
> `.venv/bin/python`.

### Optional: Load demo data

```bash
python scripts/seed_irs_mock_data.py
```

---

## Option 3: Native Install (macOS)

Same as Linux but using Homebrew for system dependencies.

### Steps

```bash
# Install dependencies
brew install postgresql@17 python@3.13 cairo pango gdk-pixbuf libffi

# Start PostgreSQL. postgresql@17 is keg-only, so its commands are not on
# PATH until linked (or call them as $(brew --prefix postgresql@17)/bin/...).
brew services start postgresql@17
brew link --force postgresql@17

# Create database
createuser bookkeeper -P    # password: bookkeeper
createdb bookkeeper -O bookkeeper

# Clone and install into a virtual environment (Homebrew's Python refuses a
# bare pip install, PEP 668)
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# WeasyPrint (PDFs) finds Homebrew's pango and cairo through this; put it in
# your shell profile too
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"

# Set up and run: the same .env edits as the Linux install above
# (APP_DEBUG=true, APP_HOST=127.0.0.1, a real PAYROLL_ENCRYPTION_SECRET)
cp .env.example .env
alembic upgrade head
python scripts/seed_database.py
python run.py
```

---

## Troubleshooting

### WeasyPrint fails with "cannot load library" (macOS/Linux native)

WeasyPrint needs Cairo and Pango. Install them:

```bash
# Ubuntu/Debian
sudo apt install libcairo2-dev libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0

# macOS
brew install cairo pango gdk-pixbuf
```

If using Docker, this is handled automatically.

### Port 3001 already in use

Change the port in `.env`:
```
APP_PORT=3002
```

### Database connection refused

- **Docker:** Make sure `docker compose up` is running and postgres is healthy: `docker compose ps`
- **Native:** Make sure PostgreSQL is running: `sudo systemctl status postgresql`

### "pg_dump not found" when creating backups

- **Docker:** This is included in the container automatically.
- **Native Linux:** `sudo apt install postgresql-client`
- **Native macOS:** `brew install postgresql@17`
