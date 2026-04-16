# Spec: add chart-template loader buttons to Settings demo data

## Goal
Let an admin load either the Xero sample default chart of accounts or the MAS chart of accounts from Settings without manually editing accounts, while preventing unsafe chart replacement on ledgers that already have dependent business data.

## Required Behavior
- Settings → Demo Data shows two new actions:
  - Load Xero Sample Default Chart
  - Load MAS Chart of Accounts
- Each action calls a protected settings endpoint with a template key.
- The backend must:
  - validate the template key
  - refuse the action when ledger/business data already exists
  - clear the existing chart on a clean ledger
  - seed the chosen template accounts
  - repopulate system-account role settings to matching accounts in that template
- The existing NZ demo-data action remains unchanged.

## Interface Changes
- Add a new settings POST endpoint for chart-template loading.
- Add chart-template seed definitions plus system-role number mappings for each template.

## Verification
- Backend permission/success/rejection tests.
- Settings UI test covering the new buttons.
- `git diff --check`.

## Assumptions
- The current NZ/Xero-derived chart remains the default for fresh databases; the new buttons are explicit admin actions.
- MAS is implemented as an alternate built-in chart template shipped in-repo.
