# Plan: review upstream Phase 10 change and refresh NZ todo list

## Goal
Review upstream commit `80b4bc782954aba5cdb93503f817e0776dc652c1`, determine which features are relevant to the NZ branch, and record the actionable outcome in `docs/localization_summary.md`.

## Scope
- Inspect the upstream feature bundle and map each feature to current NZ branch behavior.
- Distinguish relevant carryovers from explicitly non-portable US-specific items.
- Add concise todo guidance for any still-relevant future work, including security or product-decision constraints where needed.

## Steps
1. Review the upstream commit summary/diff and compare it with the current NZ branch state.
2. Create a short NZ relevance summary covering implemented, relevant, gated, and not-relevant items.
3. Update the remaining-todo section so future work is grounded in this review.
4. Verify the documentation diff and keep the change limited to docs unless a small test/docs harness fix is required.

## Constraints
- Do not silently adopt US-specific tax/reporting behavior.
- Keep the output focused on NZ branch priorities and existing architecture/security constraints.
- No new dependencies or implementation changes in this slice.
