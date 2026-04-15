# Prevent route imports from referencing missing service modules

## Summary
Add regression coverage for route-to-service imports and rewire the bank import
route to the real OFX service implementation that already exists in the repo.

## Key Changes
- Add a test that scans route imports for `app.services.*` modules and verifies
  the referenced service files exist.
- Update the bank import route to use `app.services.ofx_import`.
- Preserve the current bank-import endpoint response shape expected by the UI.

## Test Plan
- Run `python -m unittest tests.test_route_dependency_imports tests.test_route_schema_imports tests.test_batch_payment_schema tests.test_docker_config`.
- Run `git diff --check`.

## Defaults
- Route modules should only import service modules that actually exist in
  `app/services`.
