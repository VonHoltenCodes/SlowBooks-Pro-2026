# INSTALL.md

Detailed installation and first-run guide for **Slowbooks Pro 2026**.

Use this guide if you want to:
- run the app with the bundled Docker + Postgres stack
- connect the app to an external PostgreSQL instance
- run the app directly with a local Python environment

For the product overview and feature list, see `README.md`.

---

## 1. Prerequisites

### Docker path
- Docker with Compose support

### Local Python path
- Python 3.10+
- PostgreSQL 12+
- PostgreSQL client tools if you want backup/restore support (`pg_dump`, `pg_restore`)

### Runtime dependency note
This repo uses:
- FastAPI + Uvicorn
- PostgreSQL + SQLAlchemy + Alembic
- WeasyPrint / ReportLab / pypdf for PDF generation

If you are not using Docker, ensure your local environment can support those Python dependencies.

---

## 2. Self-contained Docker install (recommended)

This is the easiest path for local development.

### Steps

```bash
cp .env.example .env
docker compose up --build
```

### What happens on startup
The app container will:
1. wait for PostgreSQL to become reachable
2. run `python scripts/bootstrap_database.py` (Alembic upgrade + NZ seed)
3. start the app with Uvicorn

### Result
- app: **http://localhost:3001**
- database: bundled `postgres:18`

### Local bind mounts
The default compose stack bind-mounts:
- the repo source into the app container
- PostgreSQL data to `./data/postgres`

---

## 3. Docker with external PostgreSQL

If you already have a PostgreSQL server you want to use, edit `.env`.

### Option A — use `DATABASE_URL`

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

If `DATABASE_URL` is set, it takes precedence over the `POSTGRES_*` variables.

### Option B — use full Postgres settings

```env
DATABASE_URL=
POSTGRES_HOST=db.example.com
POSTGRES_PORT=5432
POSTGRES_DB=bookkeeper
POSTGRES_USER=bookkeeper
POSTGRES_PASSWORD=secret
POSTGRES_SSLMODE=require
```

### Start only the app service

```bash
docker compose up --build app
```

This keeps the app container but skips the bundled Postgres container.

---

## 4. Local Python install

### Steps

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then configure database access in `.env`.

### Local Postgres example

For a local database server, either:
- set `DATABASE_URL` directly, or
- leave `DATABASE_URL` blank and set:
  - `POSTGRES_HOST=localhost`
  - `POSTGRES_PORT=5432`
  - `POSTGRES_DB=bookkeeper`
  - `POSTGRES_USER=bookkeeper`
  - `POSTGRES_PASSWORD=bookkeeper`
  - `POSTGRES_SSLMODE=disable`

If you need to create the DB/user manually:

```bash
sudo -u postgres psql -c "CREATE USER bookkeeper WITH PASSWORD 'bookkeeper'"
sudo -u postgres psql -c "CREATE DATABASE bookkeeper OWNER bookkeeper"
```

### Bootstrap the database

```bash
python3 scripts/bootstrap_database.py
```

### Start the app

```bash
python3 run.py
```

Open:
- **http://localhost:3001**

---

## 5. Environment variable reference

### Database resolution precedence
1. `DATABASE_URL` if non-empty
2. otherwise build the connection string from the `POSTGRES_*` variables

### Supported DB variables

```env
DATABASE_URL=
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=bookkeeper
POSTGRES_USER=bookkeeper
POSTGRES_PASSWORD=bookkeeper
POSTGRES_SSLMODE=disable
```

### App variables

```env
APP_HOST=0.0.0.0
APP_PORT=3001
APP_DEBUG=true
```

---

## 6. First-run verification

After startup, verify:
- the app loads at `http://localhost:3001`
- the database schema exists
- seeded chart of accounts exists
- PDF-generating features can run in your environment

Good smoke checks:
- open the app home/dashboard
- create or view a PDF-backed document path if available
- confirm the chart of accounts is populated

---

## 7. Backups

The repo includes:

```bash
./scripts/backup.sh
```

Notes:
- it uses `POSTGRES_*` values from `.env` when present
- it expects PostgreSQL client tools such as `pg_dump`
- default backup location is `~/bookkeeper-backups`

---

## 8. Troubleshooting

### Docker command not found
Install Docker/Compose locally, or use the local Python install path instead.

### App cannot connect to Postgres
Check:
- `DATABASE_URL`
- or the `POSTGRES_*` values
- host reachability / port mapping
- DB user/password
- `sslmode` requirements for external DBs

### Migrations fail
Check:
- DB connectivity
- whether the target DB already exists
- whether the configured user has schema permissions

### PDF features fail outside Docker
Your local OS may be missing native libraries needed by WeasyPrint/PDF generation. The Docker path is the easiest way to avoid local dependency drift.

### Backups fail
Make sure `pg_dump` / `pg_restore` are installed and reachable in `PATH`.
