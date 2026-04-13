# SlowBooks NZ Fork Policy

`nz-localization` is the authoritative product branch for SlowBooks NZ. Treat it as a New Zealand-localized product fork, not as a light patch set over the US-shaped upstream codebase.

## Branch Roles

- `nz-localization` is the canonical branch for New Zealand behavior, compliance, UI copy, reports, demo data, and tests.
- `main` and any external upstream remain reference branches for generic bug fixes, infrastructure work, and reusable UI improvements.
- Do not treat US tax, payroll, reporting, address, seed-data, or import/export behavior from upstream as the desired baseline for this branch.

## Upstream Sync Rules

- Review upstream changes before merging or cherry-picking them into `nz-localization`.
- Prefer selective cherry-picks for security fixes, generic bug fixes, test improvements, and UI polish that do not conflict with NZ behavior.
- Avoid blind merges of accounting, tax, payroll, reports, seed data, import/export, or settings changes.
- If an upstream change touches a localized surface, re-run the NZ localization tests and update `docs/localization_summary.md` when behavior changes.

## Do Not Restore US Behavior

Future maintainers and agents must not reintroduce US-specific behavior without an explicit product decision. In particular, do not restore or prioritize Schedule C, IRS terminology, US sales tax assumptions, Federal/State withholding, Social Security, Medicare, SSN, EIN, ZIP/State labels, IRS Pub 583 seed data, or US payroll calculations on `nz-localization`.

NZ GST, reporting, address, payroll, seed data, and compliance behavior override upstream US assumptions on this branch.
