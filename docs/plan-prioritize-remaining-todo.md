# Plan: prioritize remaining NZ todo order

## Goal
Turn the current remaining-todo list into an explicit recommended execution order for SlowBooks NZ so future work follows dependency, risk, and product value rather than arrival order.

## Scope
- Review the current remaining todo entries in `docs/localization_summary.md`.
- Group work by foundations, near-term accounting value, policy-gated work, and high-risk work.
- Record a concise prioritized sequence in the todo documentation.

## Steps
1. Re-read the current remaining todo list and identify dependency relationships.
2. Create a priority order that pulls forward foundational/reporting work and pushes policy-gated or high-risk features later.
3. Update `docs/localization_summary.md` with the ordered list and short rationale cues.
4. Verify the docs-only diff and leave unrelated working-tree changes untouched.

## Constraints
- Do not re-scope or implement features in this slice.
- Preserve NZ-first/security guidance already recorded for uploads, templating, and policy-sensitive automation.
- No new dependencies.
