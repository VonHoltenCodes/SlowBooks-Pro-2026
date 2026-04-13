# Security Review — Docker Support (2026-04-14)

## Scope
Reviewed the Docker/runtime slice changes in:
- `Dockerfile`
- `docker-compose.yml`
- `scripts/docker-entrypoint.sh`
- `.env.example`
- `app/config.py`
- `alembic/env.py`
- `app/services/backup_service.py`
- `app/services/company_service.py`
- `scripts/backup.sh`

## Checks performed
- Verified config precedence uses `DATABASE_URL` first and falls back to full `POSTGRES_*` settings.
- Reviewed DB wait/migrate/seed startup logic for obvious command-injection and secret-handling issues.
- Reviewed backup/company-service DB URL parsing for external-Postgres compatibility and sslmode support.
- Ran Python tests, JS checks, shell syntax checks, `py_compile`, and `git diff --check`.
- Confirmed Docker CLI is not installed in this environment, so `docker compose config` / live container startup could not be executed here.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **Docker/compose runtime was not exercised in this environment**
   - The files were statically reviewed and syntax-checked where possible, but no live `docker compose up` validation could be run because Docker CLI is unavailable here.

### LOW
1. **Default compose exposes Postgres port to the host**
   - This is useful for local development, but operators who do not want host exposure should remove or override that mapping.
2. **Default entrypoint auto-migrates and seeds on startup**
   - Appropriate for a self-contained dev stack, but should not be treated as a production deployment pattern.

## Positive controls
- `DATABASE_URL` can override the self-contained stack cleanly for external Postgres.
- Alembic now uses the resolved runtime DB target rather than a fixed local URL.
- Backup tooling now respects host/port/password/sslmode from the env model.
- Multi-company DB URL generation preserves query settings like sslmode.
- No secrets are baked into the image; runtime credentials come from env.

## Overall assessment
- **No CRITICAL/HIGH regressions found in the reviewed Docker-support slice.**
- **Residual risk is MEDIUM** because live compose/container execution could not be validated in this environment.
