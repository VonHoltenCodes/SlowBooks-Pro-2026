# Plan: upgrade purchase orders to a Xero-style detail screen

## Summary
Replace the current modal-based purchase-order editor with a dedicated purchase-order detail page and expand the PO data model so the workflow better matches the Xero sample: richer header metadata, a more capable line grid, clearer totals, and a proper purchasing-document layout.

## Key Changes
- Replace modal-only PO editing with dedicated new/edit PO detail routes and screens.
- Expand PO header fields to support:
  - reference
  - delivery address / ship-to
  - attention
  - telephone
  - delivery instructions
  - explicit tax mode (inclusive/exclusive)
- Expand PO lines to support:
  - item-backed rows
  - account-backed rows
  - discount
  - tax/tax amount visibility in the grid
- Upgrade totals/status presentation and update PO PDF output to include useful new header metadata.
- Preserve existing list, email, PDF, and convert-to-bill flows while adapting them to the richer PO shape.

## Test Plan
- Backend tests for create/update serialization of new PO fields and lines.
- Frontend tests for PO list navigation, detail-page rendering, save/update flows, and live totals behavior.
- Regression coverage for PO email/PDF and convert-to-bill behavior.
- `git diff --check` plus targeted/full suites as appropriate for the implementation slice.

## Constraints
- Keep this as a capability/UX upgrade, not a visual pixel clone of Xero.
- Do not bundle unrelated chart-template, inventory, or vendor-contact overhaul work into this slice.
- Use a migration for any new persisted PO/PO-line fields.
