# Specs Folder Cleanup

## Summary
Move the legacy markdown files from `specs/` into `docs/`, standardize them under the `docs/spec-<slice>.md` naming convention, update any references, and remove the now-empty `specs/` folder.

## Key Changes
- Create canonical `docs/spec-*.md` filenames for the six legacy spec files currently under `specs/`.
- Update any repo references that still point to old paths or names.
- Remove `specs/` once empty.

## Test Plan
- Verify old files are gone from `specs/`.
- Verify new files exist in `docs/`.
- Run `git diff --check` and targeted reference search for stale `specs/` paths.

## Defaults
- Preserve document content; this is an organization/naming cleanup only.
