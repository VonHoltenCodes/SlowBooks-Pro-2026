# Spec: Specs Folder Cleanup

## Goal
Retire the legacy `specs/` directory and consolidate all spec documents under `docs/`.

## Mapping
- `specs/nz-address-labels.md` -> `docs/spec-nz-address-labels.md`
- `specs/nz-gst-calculation-service.md` -> `docs/spec-nz-gst-calculation-service.md`
- `specs/nz-gst-domain-model.md` -> `docs/spec-nz-gst-domain-model.md`
- `specs/nz-gst-journal-posting.md` -> `docs/spec-nz-gst-journal-posting.md`
- `specs/nz-line-gst-storage.md` -> `docs/spec-nz-line-gst-storage.md`
- `specs/nz-localized-formatting.md` -> `docs/spec-nz-localized-formatting.md`

## Rules
- Preserve file contents exactly unless a path/name reference must be updated.
- Update repo references from old paths/names to new ones where needed.
- Remove the `specs/` directory only after it is empty.

## Validation
- No remaining `specs/` references in tracked docs/code.
- New `docs/spec-*.md` files present.
- `git diff --check` passes.
