#!/bin/sh
set -eu

mkdir -p /app/backups /app/app/static/uploads

python - <<'PY'
import sys
import time
from sqlalchemy import create_engine, text
from app.config import DATABASE_URL

last_error = None
for attempt in range(60):
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"Database ready: {DATABASE_URL}")
        sys.exit(0)
    except Exception as exc:
        last_error = exc
        print(f"Waiting for database (attempt {attempt + 1}/60): {exc}")
        time.sleep(2)

raise SystemExit(f"Database not reachable after waiting: {last_error}")
PY

alembic upgrade head
python scripts/seed_database.py
exec python run.py
