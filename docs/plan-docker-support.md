# Docker Support

## Summary
Add first-class Docker support with a self-contained default `docker-compose.yml` stack (app + latest Postgres) and env-driven support for external Postgres.

## Key Changes
- Add Dockerfile, docker-compose.yml, .dockerignore, and an app entrypoint script.
- Auto wait for DB, run Alembic, seed chart, then start uvicorn.
- Expand `.env.example` to full Postgres connection settings plus compose-local defaults.
- Make config/Alembic resolve DB from `DATABASE_URL` or `POSTGRES_*` vars.
- Ensure container system dependencies cover WeasyPrint/PDF, Postgres client, and existing runtime features.

## Test Plan
- Add config-resolution tests.
- Validate `docker compose config`.
- Validate app image imports/PDF deps and startup scripts.
- Run full repo checks and explicit deployment/runtime security review.

## Defaults
- Default to a self-contained local compose stack.
- External Postgres works by env override without code changes.
