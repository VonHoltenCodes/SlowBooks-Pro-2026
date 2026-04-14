# Spec: System Account Role Admin Workflow

## Scope
Implement an admin surface for the existing system-account role mapping contract.

## Required roles
- accounts receivable
- accounts payable
- GST control
- undeposited funds
- default sales income
- default expense
- default bank
- wages expense
- employer KiwiSaver expense
- PAYE payable
- KiwiSaver payable
- ESCT payable
- child support payable
- payroll clearing

## Read model
Each role row must expose:
- role key
- human label
- short description
- required account type
- status: `configured`, `fallback`, or `missing`
- explicit configured account, if present
- currently resolved account without forcing new account creation
- whether runtime still has auto-create behavior for that role

## Write rules
- Only known roles may be updated.
- Assigned accounts must exist, be active, and match the role's required account type.
- `account_id = null` clears the explicit mapping and returns the role to fallback resolution.
- This slice must not remove legacy fallback or auto-create behavior already relied on by runtime posting flows.

## UI rules
- The workflow lives on the Chart of Accounts page.
- The role section appears before the account table.
- Assignment choices are limited to active accounts of the required type.
- Fallback and missing states must be visible so operators know when runtime is still relying on heuristics.
