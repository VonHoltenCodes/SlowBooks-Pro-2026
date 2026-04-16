# Spec: review upstream Phase 10 change and refresh NZ todo list

## Required outcome
- `docs/localization_summary.md` records which parts of upstream commit `80b4bc782954aba5cdb93503f817e0776dc652c1` matter to SlowBooks NZ.
- The todo list clearly separates:
  - relevant NZ carryovers,
  - items requiring explicit policy/security/product decisions, and
  - upstream items that should not be ported.

## Content requirements
- Note already-covered overlap where the NZ branch has equivalent functionality or a better NZ-specific replacement.
- Add future todo items only for features that still make sense in an NZ-first product.
- Call out security-sensitive areas such as uploads and templating where future implementation must follow existing hardening standards.

## Verification
- Manual review of the upstream-to-NZ mapping against current repo state.
- `git diff --check` must remain clean.
