# Security Review — Alembic Audit + Repair (2026-04-14)

## Scope Reviewed
- `app/services/company_service.py`
- `scripts/bootstrap_database.py`
- `scripts/docker-entrypoint.sh`
- operator docs for database bootstrap

## Review Focus
- Shared bootstrap command execution
- Database creation/bootstrap failure handling
- Drift between documented and runtime schema initialization paths

## Findings
1. **Bootstrap path is now centralized instead of split across mixed code paths**
   - Docker startup and multi-company database creation now use the same `bootstrap_database.py` flow.
   - This reduces the risk of a new company DB missing Alembic-applied schema changes or NZ seed settings.

2. **Failure handling is safer than the old `create_all` path**
   - If company bootstrap fails after the database is created, the service now rolls back the registry transaction and attempts to drop the just-created database.
   - This reduces orphaned partially initialized company databases.

3. **Subprocess execution remains constrained to repo-owned commands**
   - The shared bootstrap script only executes the Alembic console script and the checked-in `scripts/seed_database.py` file with a controlled `DATABASE_URL` override.
   - No user-provided shell fragments are introduced in this slice.

## Residual Risks
- Company database names are still interpolated into `CREATE DATABASE` / `DROP DATABASE` SQL statements. Existing behavior assumes trusted admin operators and PostgreSQL-safe names; stricter identifier validation remains a future hardening option.
- Live end-to-end Postgres migration execution was not exercised in this environment because the local Python 3.14 environment could not install `psycopg2-binary` from wheel and attempted a source build requiring `pg_config`.

## Conclusion
- No new CRITICAL/HIGH security issues identified in this slice.
- Residual risk is **LOW to MEDIUM** and primarily operational/admin-input hardening rather than application-user exposure.
