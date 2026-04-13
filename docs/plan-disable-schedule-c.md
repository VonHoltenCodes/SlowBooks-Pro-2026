# Disable Schedule C For SlowBooks NZ

## Summary
Remove Schedule C as an active NZ-facing feature and disable its API endpoints. This is a de-US-localization slice only; it does not add IR3, IR10, or accountant export yet.

## Key Changes
- Return HTTP 410 from `/api/tax/schedule-c` and `/api/tax/schedule-c/csv` with NZ-specific explanatory text.
- Remove the `#/tax` route and the Tax Reports nav item from the app shell.
- Leave dormant tax mapping storage code in place for now; do not invent NZ income-tax semantics in this slice.
- Update README and localization docs so Schedule C is no longer presented as an active feature on `nz-localization`.

## Test Plan
- Backend tests for both Schedule C endpoints returning 410.
- Frontend tests proving `#/tax` and the Tax Reports nav item are absent.
- Verify with Python unit tests, relevant Node tests, `node --check app/static/js/app.js`, and `git diff --check`.

## Assumptions
- GST Return remains the only active NZ tax/compliance workflow.
- No migration is required.
- NZ income-tax export direction remains a later decision.
