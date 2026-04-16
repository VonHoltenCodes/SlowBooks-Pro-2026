# Security Review — Backup Service Startup Syntax Fix (2026-04-16)

## Scope Reviewed
- `app/services/backup_service.py`
- `app/routes/backups.py`
- `tests/test_backup_path_hardening.py`
- `tests/test_admin_rbac_protection.py`

## Review Focus
- Whether restoring `restore_backup()` also preserves the managed-backup path boundary
- Whether invalid filenames still fail before filesystem or subprocess access
- Whether the startup fix changes backup route security behavior beyond removing the syntax error

## Findings
1. **The syntax repair preserves the existing path-validation boundary**
   - `restore_backup()` still routes all caller-supplied filenames through `resolve_backup_path()`.
   - Invalid filenames still return a 400-style failure result before `pg_restore` is invoked.

2. **Missing files still fail safely**
   - The repaired function still returns a 404-style failure when the resolved backup file is absent.
   - No new fallback path or unsafe path concatenation was introduced.

3. **The slice is narrow and startup-focused**
   - The only behavioral change is restoring a valid implementation for the already-hardened restore path.
   - Backup route RBAC and download/list behavior were not widened.

## Residual Risks
- Live `pg_restore` execution against a real PostgreSQL instance was not exercised in this session.
- The function still relies on subprocess execution of PostgreSQL client tools by design; this review covers path/input safety, not tool availability.

## Verification
- `.venv/bin/python -m py_compile app/services/backup_service.py app/routes/backups.py`
- `.venv/bin/python -m unittest tests.test_backup_path_hardening tests.test_admin_rbac_protection`
- `git diff --check`

## Conclusion
- No new CRITICAL/HIGH issues identified.
- The fix restores application startup while preserving the prior backup-path hardening guarantees.
