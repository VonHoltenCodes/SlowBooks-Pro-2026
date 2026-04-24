#!/bin/bash
set -e

echo "Slowbooks Pro 2026 — Starting up..."

# Wait for PostgreSQL (max 30 seconds)
echo "Waiting for PostgreSQL..."
PG_WAIT=0
until pg_isready -h "${PGHOST:-postgres}" -p "${PGPORT:-5432}" -U "${PGUSER:-bookkeeper}" -q; do
    PG_WAIT=$((PG_WAIT + 1))
    if [ "$PG_WAIT" -ge 30 ]; then
        echo "ERROR: PostgreSQL did not become ready within 30 seconds."
        exit 1
    fi
    sleep 1
done
echo "PostgreSQL is ready."

# Run migrations
echo "Running database migrations..."
alembic upgrade head

# Seed chart of accounts (idempotent — skips if accounts exist)
echo "Seeding database..."
python scripts/seed_database.py

echo "Starting Slowbooks Pro 2026 on port ${APP_PORT:-3001}..."
# Multi-worker production mode.
# uvloop + httptools come from uvicorn[standard], explicit for clarity.
# APP_WORKERS defaults to 2 (tunable via docker-compose env or .env).
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${APP_PORT:-3001}" \
    --workers "${APP_WORKERS:-2}" \
    --loop uvloop \
    --http httptools \
    --access-log
