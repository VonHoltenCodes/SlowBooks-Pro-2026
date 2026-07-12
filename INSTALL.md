# Installation Guide

Ways to run Slowbooks Pro 2026.

---

## Option 0: One-click desktop app (Windows)

**Recommended for non-technical Windows users.** No Docker, no WSL2, no command
line — Slowbooks Pro runs as a normal desktop app in its own window (no
browser tab). Nothing needs to be installed beforehand; one file sets
everything up automatically.

### Steps

1. Download **[`Setup SlowBooks Pro.bat`](https://raw.githubusercontent.com/moshgrossman/SlowBooks-Pro-2026/main/Setup%20SlowBooks%20Pro.bat)**
   (right-click → Save As if your browser shows it as text).
2. Double-click it.
3. Approve the two prompts you'll see:
   - **Windows SmartScreen** ("Windows protected your PC") — click
     **More info → Run anyway**. This is expected for any unsigned downloaded
     script; it appears only the first time.
   - **User Account Control** (Administrator permission) — needed to install
     Python and a small PDF-rendering component system-wide.
4. Wait for setup to finish. The app opens by itself, and a **SlowBooks Pro**
   shortcut appears on your Desktop for next time.

### What it installs

- The Slowbooks Pro application itself (into `%LOCALAPPDATA%\SlowBooksPro`)
- Python 3.13 (the language runtime the app is written in)
- Microsoft WebView2, only if missing (the window component; already present
  on most Windows 10/11 machines)
- The GTK3 runtime (a small component used to generate PDF invoices and tax forms)

That's it — **no Docker, no WSL2, no database server**. Your books are stored
in ordinary files on your own machine.

### Working with multiple companies

This install manages companies the way QuickBooks Desktop does: each company
is its own file, stored under `%LOCALAPPDATA%\SlowBooksPro\data\companies\`.
Every time you open SlowBooks Pro you're asked which company to open (or to
create a new one). To switch companies, close the app and open it again.

### Stopping the app

Just close the window — the server shuts down with it. If something ever gets
stuck, run `Stop SlowBooks Pro.bat` in the app folder as a safety net.

### Troubleshooting

Day-to-day, the Desktop shortcut opens the app with no console window — it
runs quietly in the background like any other desktop app. If something goes
wrong before the app window can open, a small popup explains it, and full
details are written to `%LOCALAPPDATA%\SlowBooksPro\data\launcher.log`. For
live console output while troubleshooting something trickier, double-click
`Launch SlowBooks Pro.bat` inside the app folder instead of the shortcut.

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

## Option 1: Docker (Windows, macOS, Linux)

**Recommended for Windows and macOS.** One command, no dependency headaches.

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

- Python 3.13 (CI gates against 3.13; older 3.12 may work but isn't tested)
- PostgreSQL 17 (Docker image ships 17-alpine; older 16 still works for native installs)
- System libraries for WeasyPrint

### Steps

```bash
# Install system dependencies (Ubuntu/Debian/Pop!_OS)
sudo apt install -y postgresql libcairo2-dev libpango-1.0-0 \
    libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev

# Create database
sudo -u postgres createuser bookkeeper -P    # password: bookkeeper
sudo -u postgres createdb bookkeeper -O bookkeeper

# Clone and install
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env if your database credentials differ

# Run migrations and seed
alembic upgrade head
python scripts/seed_database.py

# Start the server
python run.py
```

Open **http://localhost:3001**.

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
