# Spec: Prevent route imports from referencing missing service modules

## Deliverable
Ensure route modules do not import nonexistent `app.services.*` modules.

## Rules
- Add automated coverage that fails when a route imports a service module with
  no corresponding file in `app/services`.
- `app/routes/bank_import.py` must use the existing OFX import service module.
- Do not change the bank import endpoint contract beyond reconnecting it to the
  real implementation.
