# Plan: upstream forum feature triage for NZ todo

## Goal
Review upstream commit `934244242d3a1a2802ba76de80f59f8a942c2c5e`, identify which of its ten forum-requested features matter for the NZ fork, and record only the NZ-relevant carryovers in the master todo.

## Steps
1. Compare the upstream feature list against the current NZ branch surfaces and already-completed NZ work.
2. Exclude items that are already covered, superseded by NZ-specific behavior, or poor fits for the NZ product direction.
3. Update `docs/localization_summary.md` with a concise triage note plus new remaining-todo entries for the relevant missing features.
4. Verify the doc diff for accuracy and run `git diff --check`.
