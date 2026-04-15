# Spec: Clean up RBAC override permission labels

## Deliverable
Update the RBAC user-management UI so permission overrides display readable
labels instead of dotted permission keys.

## Rules
- The auth UI must not show raw dotted permission keys in the role override
  display surfaces.
- Permission checkbox values and saved payloads must remain the original keys.
- Tests must fail if the auth UI regresses to rendering dotted notation in the
  relevant user-management views.
