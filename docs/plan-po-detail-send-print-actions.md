# Plan: add send and print actions to the PO detail page

## Summary
Add purchase-order email and PDF actions directly to the purchase-order detail screen so users can send a PO to the selected vendor email address or open a printable PDF without returning to the list view.

## Key Changes
- Add detail-page action buttons next to the primary save action for purchase orders.
- For existing POs, expose:
  - email action using the existing purchase-order email endpoint/modal flow
  - print/PDF action using the existing purchase-order PDF endpoint
- For unsaved/new POs, show the actions in a disabled state so the workflow is visible but gated until the PO exists.
- Keep existing list-row email and convert-to-bill actions intact.

## Test Plan
- Add/extend JS tests covering detail-page action-button rendering for new and existing POs.
- Add/extend JS tests covering the print/PDF helper wiring.
- Run targeted PO JS tests and `git diff --check`.

## Constraints
- No schema migration in this slice.
- Reuse existing PO email/PDF backend endpoints rather than creating new transport flows.
- Keep the change isolated to PO detail-page ergonomics.
