# Payroll Address Cleanup Specification

## Goal
Make payroll employee addresses usable and NZ-consistent by exposing the already-stored employee address fields in the payroll API and UI.

## Required Behavior
- `EmployeeCreate`, `EmployeeUpdate`, and `EmployeeResponse` must include `address1`, `address2`, `city`, `state`, and `zip`.
- Employee create/edit UI must show address inputs with NZ-facing labels `Region` and `Postcode`.
- Saved employee address values must round-trip through create, update, and get/list responses.
- Starter/leaver filing exports must continue working when employee address fields are populated, but export format/content must not change in this slice.

## Constraints
- Keep storage/API compatibility with existing `state` and `zip` names.
- No new migrations or country field.
- Do not combine this cleanup with payroll filing format expansion or deeper schema renames.

## Verification
- Backend tests for employee address round-trip and non-regressed filing export.
- Frontend tests for employee modal labels and address fields.
- Full Python/JS suites, syntax checks, and `git diff --check`.
