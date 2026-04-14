# Payroll Filing Audit Model Specification

## Goal
Track payroll filing outputs as first-class records so employee/pay-run filing history, filing status, and changed-since-filing detection are part of the app rather than inferred from current source data alone.

## Required Behavior
- Add a filing-audit model covering `starter`, `leaver`, and `employment_information` filing types.
- Existing export routes must create new `generated` filing records and store a source snapshot/hash.
- New routes must list filing history and update a filing record to `filed` or `amended`.
- Re-exporting the same filing scope/type should supersede older `generated` records for that scope.
- History responses must report whether the current payroll/employee source data differs from the stored snapshot hash.
- Employee UI must show starter/leaver filing history/status; payroll UI must show Employment Information history/status for processed runs.
- Employee filing history uses employee-view/export permissions; Employment Information history uses payroll-view/export permissions.

## Constraints
- No external submission integration.
- No new dependencies.
- Keep the model/company scope and existing RBAC foundation intact.
- Snapshot comparison should use filing-relevant fields/settings only.

## Verification
- Backend tests for creation, superseding, status changes, and changed-since-filing detection.
- RBAC tests for viewing/updating filing-audit state.
- Frontend tests for employee/payroll filing status rendering and actions.
- Full Python/JS suites, syntax checks, and `git diff --check`.
