# Payroll Address Cleanup Slice

## Summary
Finish the deferred NZ payroll address cleanup by exposing the existing employee address fields through the payroll API and UI using NZ-first labels, without renaming storage fields or changing filing export formats.

## Key Changes
- Add employee address fields to payroll create/update/response schemas.
- Surface employee address inputs in the employee modal using NZ labels `Region` and `Postcode`.
- Preserve backend/storage compatibility by keeping `state` and `zip` field names in the model/API.
- Verify starter/leaver filing exports still work with populated employee address data, without changing export format.

## Test Plan
- Add failing backend tests for employee address create/update/response round-trip.
- Add failing frontend tests for employee modal address labels/fields.
- Re-run payroll/export-related checks plus full Python/JS verification, syntax checks, and `git diff --check`.

## Defaults
- No migration or deeper schema renaming in this slice.
- No employee country field is added.
- Filing exports are regression-checked only; their wire shape stays unchanged.
