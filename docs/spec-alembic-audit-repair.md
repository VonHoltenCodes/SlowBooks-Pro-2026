# Alembic Audit + Repair Specification

## Goal
Make Alembic the canonical schema/bootstrap path for this branch so fresh installs, Docker startup, and newly created company databases all converge on the same migrated + seeded NZ baseline.

## Required Behavior
- The checked-in Alembic revisions must form a single linear upgrade chain ending at one head.
- Production/runtime database bootstrap must run:
  1. Alembic upgrade to head
  2. NZ seed/bootstrap script
- Multi-company database creation must use that same bootstrap path after creating the new database.
- Documentation must describe one canonical bootstrap workflow instead of mixed `create_all` vs Alembic behavior.

## Implementation Constraints
- Do not change user-facing business behavior in this slice.
- Do not delete or rewrite historical migration files.
- If no model/schema drift is found, prefer tests + bootstrap-path repair over inventing extra migrations.
- Keep tests SQLite-friendly where possible; use static migration-chain validation when live Postgres migration execution is not practical in unit tests.

## Verification
- Unit test for company creation invoking the shared bootstrap flow with the new database URL.
- Unit/static test for migration graph integrity (single head, all `down_revision` links resolve).
- Targeted regression tests for chart-seed/bootstrap behavior still passing.
- Python/JS syntax and diff checks.
