# Installation Guide

Three ways to run Slowbooks Pro 2026.

---

## Option 1: Docker (Windows, macOS, Linux)

**Recommended for Windows and macOS.** One command, no dependency headaches.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine + Docker Compose (Linux)

### Steps

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
cp .env.example .env        # optional — defaults work out of the box
docker compose up
```

Open **http://localhost:3001** in your browser.

### What happens on first run

1. PostgreSQL 16 starts and creates the `bookkeeper` database
2. Alembic runs all migrations (creates 35 tables)
3. Chart of Accounts is seeded (39 accounts)
4. Uvicorn starts serving the app on port 3001

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

- Python 3.12+
- PostgreSQL 16+
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
brew install postgresql@16 cairo pango gdk-pixbuf libffi

# Start PostgreSQL
brew services start postgresql@16

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
- **Native macOS:** `brew install postgresql@16`
