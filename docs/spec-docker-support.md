# Spec: Docker Support

## Deliverables
- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- entrypoint script for DB wait + migrate + seed + app start
- expanded `.env.example`

## Runtime rules
- Compose default services: `app`, `postgres`
- Postgres image: `postgres:latest`
- App startup sequence: wait for DB -> `alembic upgrade head` -> `python scripts/seed_database.py` -> start uvicorn
- Use local bind mounts for repo source and Postgres data
- App config precedence: `DATABASE_URL` if non-empty, else compose/build URL from `POSTGRES_*`

## Env contract
- `DATABASE_URL`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_SSLMODE`
- existing app host/port/debug vars remain supported

## Validation
- `docker compose config` succeeds
- config resolution works both with `DATABASE_URL` and with `POSTGRES_*`
- Alembic uses the resolved DB target
- image contains system deps required for PDF generation and Postgres tooling
