<!--
PR template. Delete sections that don't apply, but please fill in
something for each that does. Bullet points are fine; this isn't an essay.
-->

## Summary

<!-- One or two sentences. What does this PR change, and why? -->

## Changes

<!-- Bullet list of the user-visible or developer-visible changes.
     Reviewers should be able to skim this and know what to look for. -->

-
-
-

## Test plan

<!-- How did you verify it works? Be specific. -->

- [ ] `pytest tests/ -q` passes locally
- [ ] `black --check app/ tests/` passes
- [ ] `ruff check app/ tests/` passes
- [ ] Manually verified the user flow described above
- [ ] Tested in dark mode (UI changes only)

## Screenshots / output

<!-- For UI changes, a before/after screenshot. For PDFs or reports,
     attach a sample rendering. Skip if not applicable. -->

## Security implications

<!-- If this touches auth, encryption, the portal flow, file uploads,
     subprocess calls, or the startup checks, describe the threat model
     change. Otherwise write "None — pure feature work" or similar. -->

## Database changes

<!-- Did you add or modify a SQLAlchemy model? -->

- [ ] No schema changes
- [ ] New table / new columns only (handled by `Base.metadata.create_all`)
- [ ] Existing-table change — Alembic migration added under `migrations/versions/`

## Documentation

- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `README.md` updated if the feature surface changed
- [ ] `docs/` updated if behavior or threat model changed
- [ ] `docs/todo.md` items moved to CHANGELOG or deleted as appropriate

## Related issues

<!-- Fixes #123, Refs #45, etc. -->

---

<!-- For PRs authored via Claude Code on the web, the session URL ends up
     in the merge commit automatically — no need to add it here. -->
