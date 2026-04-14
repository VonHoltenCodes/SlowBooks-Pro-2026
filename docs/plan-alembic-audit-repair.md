# Alembic Audit + Repair Slice

## Summary
Review the repo's Alembic workflow end to end, fix the migration/bootstrap path for new and existing databases, and verify that the current NZ schema changes are represented by the checked-in revision chain.

## Key Changes
- Audit `alembic/versions` for a valid single-head chain and keep that chain authoritative for schema creation.
- Replace any production database bootstrap paths that still rely on `Base.metadata.create_all(...)` with the canonical migration + seed workflow.
- Centralize the bootstrap sequence used by Docker startup and multi-company database creation.
- Add regression coverage for migration-chain integrity and company database bootstrap behavior.
- Update operator/developer docs so local install, Docker, and company creation reference the same verified schema bootstrap flow.

## Test Plan
- Add a failing test proving company database creation runs the migration bootstrap workflow rather than raw `create_all`.
- Add a migration-integrity test that asserts exactly one Alembic head and a continuous revision chain.
- Re-run targeted Python unit tests, JS checks, `py_compile`, and `git diff --check`.

## Defaults
- Use forward-only fixes; do not rewrite or squash existing revisions.
- Keep the current NZ schema shape unchanged unless the audit finds an actual missing migration step.
- Seed/bootstrap remains NZ-first and should keep populating the default chart and system-account settings.
