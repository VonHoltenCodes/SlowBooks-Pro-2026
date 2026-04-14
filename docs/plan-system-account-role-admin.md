# System Account Role Admin Workflow Slice

## Summary
Add a dedicated admin workflow for viewing, validating, assigning, and clearing the settings-backed system account roles introduced by the decoupling slice.

## Key Changes
- Add a canonical backend role registry and read/update API for system account role mappings.
- Extend Chart of Accounts with a System Account Roles section that shows configured, fallback, and missing states.
- Allow admins to assign only active accounts of the required type or clear a mapping back to fallback resolution.
- Keep existing runtime fallback and auto-create behavior unchanged in this slice.

## Test Plan
- Add backend tests for role listing, validation, update, and clear behavior.
- Keep runtime regression coverage for payment, bill payment, and payroll flows.
- Run focused JS syntax checks plus repo Python tests for this slice.

## Defaults
- Manage role mappings inside the existing Chart of Accounts admin workflow.
- Clearing a role restores fallback behavior rather than deleting or mutating accounts.
