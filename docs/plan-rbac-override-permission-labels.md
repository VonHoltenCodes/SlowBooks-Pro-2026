# Clean up RBAC override permission labels

## Summary
Replace dotted permission keys in the RBAC user-management UI with readable
labels so role templates, current overrides, and allow/deny override cards are
scannable by humans.

## Key Changes
- Add a frontend permission-label formatter in the auth UI.
- Use readable labels instead of raw keys in the role template summary, current
  user override summary, and allow/deny override cards.
- Keep the underlying permission keys unchanged for form values and API payloads.

## Test Plan
- Add a JS regression test that fails when the auth UI renders dotted permission
  keys in user-management output.
- Run the focused auth-page JS test and relevant RBAC UI tests.
- Run `git diff --check`.

## Defaults
- UI copy should show labels like `View Accounts` and `Manage Employees`, while
  the code continues to submit keys like `accounts.view` and `employees.manage`.
