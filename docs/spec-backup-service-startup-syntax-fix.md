# Spec: fix backup_service startup syntax regression

## Goal
Make `app/services/backup_service.py` importable again by repairing the broken `restore_backup()` function while preserving the current path-validation hardening.

## Required Behavior
- `restore_backup()` must return a 400-style failure result for invalid filenames.
- It must return a 404-style failure result when the resolved backup file does not exist.
- It must invoke `pg_restore` only for validated, existing backup paths.
- The module must load cleanly so app startup no longer fails on import.

## Verification
- `python -m py_compile app/services/backup_service.py`
- `tests.test_backup_path_hardening`
- route/import checks plus `git diff --check`

## Assumptions
- The duplicated/garbled lines inside `restore_backup()` are accidental corruption, not intentional behavior.
