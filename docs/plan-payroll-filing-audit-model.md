# Payroll Filing Audit Model Slice

## Summary
Add a dedicated filing-status/audit model so payroll filing exports are no longer stateless downloads and the app can track generated, filed, amended, superseded, and changed-since-filing states.

## Key Changes
- Add a payroll filing audit model + migration for starter, leaver, and Employment Information exports.
- Persist audit/history records from existing export routes.
- Add history/status update endpoints for employee filing and pay-run filing.
- Show filing status/history indicators in employee and payroll UI.
- Keep the new records behind the existing payroll RBAC permissions.

## Test Plan
- Add failing backend tests first for record creation, status updates, superseding, and changed-since-filing detection.
- Add targeted RBAC and frontend status/history tests.
- Re-run full Python/JS verification, syntax checks, and `git diff --check`.

## Defaults
- Treat exports as `generated` first; users later mark them `filed` or `amended`.
- Compare filing-relevant source snapshots only, not unrelated row changes.
- Reuse current payroll permissions instead of inventing a second filing permission system.
