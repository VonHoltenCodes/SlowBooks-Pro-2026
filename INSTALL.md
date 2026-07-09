# Installation Guide

Ways to run Slowbooks Pro 2026.

---

## Option 0: One-click desktop app (Windows)

**Easiest for a single user on Windows** who already has **Docker Desktop** and
**Python** installed. Runs Slowbooks in its own window — no browser tab, no
`.env` editing, no command line.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and **running** (its WSL2 backend is the default on Windows 10/11)
- Python 3 installed and on your `PATH` (`python --version` should work)

### Steps

1. Download / clone this repository.
2. Double-click **`Launch SlowBooks Pro.bat`**.

That's it. On first launch it will:

- create your `.env` and generate a strong `PAYROLL_ENCRYPTION_SECRET` for you
  (this is the value that causes the *"PAYROLL_ENCRYPTION_SECRET is the public
  dev default"* error when it's left unset — the launcher sets it correctly so
  you never see that error),
- install the small window component the first time,
- start the app in Docker, and
- open Slowbooks in a desktop window once it's ready.

To stop the app, double-click **`Stop SlowBooks Pro.bat`** (your data is kept
safe in Docker volumes for next time).

> First launch downloads and builds the container images, so it can take a few
> minutes. Later launches are quick.

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

> On Windows, **Option 0** does all of this for you (generates the secret,
> starts Docker, opens a desktop window) — prefer it unless you specifically
> want the manual Docker flow.

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
