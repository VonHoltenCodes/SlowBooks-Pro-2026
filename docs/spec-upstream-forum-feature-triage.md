# Spec: upstream forum feature triage for NZ todo

## Inputs
- Upstream commit: `934244242d3a1a2802ba76de80f59f8a942c2c5e`
- Current NZ branch summary: `docs/localization_summary.md`

## Expected output
- `docs/localization_summary.md` explicitly records which upstream forum features are still relevant to SlowBooks NZ.
- The remaining-todo list gains follow-up items for relevant missing features only.
- The doc also makes clear why excluded items were not added (already covered, superseded by GST-specific behavior, or not a NZ priority).

## Acceptance criteria
- NZ-relevant missing features are discoverable from the remaining-todo section without rereading the upstream commit.
- The wording does not suggest reintroducing US-specific sales-tax workflows on the NZ branch.
- No code paths or runtime behavior change in this slice.
