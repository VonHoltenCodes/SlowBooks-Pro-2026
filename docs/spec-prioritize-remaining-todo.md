# Spec: prioritize remaining NZ todo order

## Required outcome
- `docs/localization_summary.md` presents a clear recommended execution order for the remaining NZ backlog.
- The ordering should reflect dependencies, security risk, and likely business value.

## Ordering rules
- Foundational/reporting work should come before optional automation layers.
- Product- or policy-gated items should stay after the capabilities they depend on.
- High-risk file-handling and templating features should remain late and explicitly gated by security requirements.
- Existing non-port decisions (for example, US-only 1099 tracking) must remain excluded.

## Verification
- Manual review of the revised ordering against the current backlog.
- `git diff --check` must remain clean.
