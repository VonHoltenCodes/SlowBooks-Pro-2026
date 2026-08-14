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

When a new version is released, the footer of the app shows an
**Update available** notice — download the new installer and run it over
the old install; your companies and settings are kept.

### Working with multiple companies

This install manages companies the way QuickBooks Desktop does: each company
is its own file, stored under `%LOCALAPPDATA%\SlowBooksPro\data\companies\`.
Every time you open SlowBooks Pro you're asked which company to open (or to
create a new one). To switch companies, close the app and open it again.

### Stopping the app

Just close the window — the server shuts down with it. If something ever gets
stuck, end the **SlowBooksPro** process from Task Manager.

### Troubleshooting

The app runs with no console window — quietly in the background like any
other desktop app. If something goes wrong before the app window can open, a
small popup explains it, and full details are written to
`%LOCALAPPDATA%\SlowBooksPro\data\launcher.log`.

### Backups

Backups created from the Settings UI are simply snapshots of the open
company's `.db` file (stored in the app's `backups` folder). You can also copy
the company files in `%LOCALAPPDATA%\SlowBooksPro\data\companies\` anywhere
you like while the app is closed — each file is a complete, self-contained
company.

### Tradeoffs versus Option 1 (Docker + PostgreSQL)

This path is **single-user, single-machine**: no multi-user client portal
serving other people, no concurrent access. In exchange, there's nothing to
administer — no `pg_dump`, no containers, no background services.

---

## Option 0A: macOS desktop app

**Recommended for Apple Silicon Macs running macOS 14 or newer.** No Docker,
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

This path has the same single-user tradeoffs as the Windows desktop app. Intel
Macs should use Docker or the developer-oriented native install below until a
separately tested Intel build is available.

---

## Option 1: Docker (Windows, macOS, Linux)

**Recommended for servers, Intel Macs, and multi-user setups.** One command,
no dependency headaches.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine + Docker Compose (Linux)

### Steps

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
cp .env.example .env

# Set a strong encryption secret for employee bank PII. The app refuses to
# start against Postgres with the shipped dev default, so this is required:
#   Linux/macOS:  openssl rand -base64 32
#   any OS:       python -c "import secrets; print(secrets.token_urlsafe(32))"
# Put the result on the PAYROLL_ENCRYPTION_SECRET= line in .env.

docker compose up
```

Open **http://localhost:3001** in your browser.

> On Windows, **Option 0** avoids all of this (no Docker at all, secret
> generated for you, opens a desktop window) — prefer it unless you
> specifically want a multi-user Docker + PostgreSQL server.

### What happens on first run

1. PostgreSQL 17 starts and creates the `bookkeeper` database
2. Alembic runs all migrations (creates 55 tables)
3. Chart of Accounts is seeded (50 accounts — Contractor template, includes the payroll-liability accounts needed for pay-run processing)
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
# Edit .env if your database credentials differ.
#
# For a local development install, also set:
#     APP_DEBUG=true
# Leaving APP_DEBUG=false (the production default) turns on FORCE_HTTPS,
# which 307-redirects http://localhost:3001 to https://localhost:3001 —
# and a native install serves no TLS on that port, so the browser gets a
# connection error. Production deployments should keep APP_DEBUG=false and
# terminate TLS at a reverse proxy in front of the app.

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
brew install postgresql@17 cairo pango gdk-pixbuf libffi

# Start PostgreSQL
brew services start postgresql@17

# Create database
createuser bookkeeper -P    # password: bookkeeper
createdb bookkeeper -O bookkeeper

# Clone and install
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
pip install -r requirements.txt

# Set up and run
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
