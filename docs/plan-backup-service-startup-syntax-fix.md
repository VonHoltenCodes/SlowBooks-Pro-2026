# Plan: fix backup_service startup syntax regression

## Summary
Repair the malformed `restore_backup()` implementation in `app/services/backup_service.py` that currently raises a SyntaxError during app import and blocks Uvicorn startup.

## Key Changes
- Restore a syntactically valid `restore_backup()` control flow.
- Keep the existing backup-path hardening behavior intact: invalid filenames must still fail before subprocess access, and missing files must still return safe errors.
- Avoid changing unrelated backup route behavior or broader backup semantics.

## Test Plan
- Re-run `py_compile` on `app/services/backup_service.py`.
- Run targeted backup hardening tests.
- Run route/importability checks and `git diff --check`.

## Constraints
- Minimal diff only; preserve the existing security boundary around managed backup filenames.
- No new dependencies.
