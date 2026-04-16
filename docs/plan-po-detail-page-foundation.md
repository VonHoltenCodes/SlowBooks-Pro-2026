# Plan: build the purchase-order detail page foundation

## Summary
Implement the first slice of the PO upgrade by replacing the modal-based editor with a dedicated purchase-order detail screen that uses the current persisted PO shape. This slice should improve structure and usability without yet widening the PO schema.

## Key Changes
- Add dedicated frontend routes/state for creating and editing purchase orders on a full page.
- Change the PO list so `+ New PO` and row `Edit` open the detail screen instead of the modal.
- Build a document-style PO detail layout using the fields already supported today:
  - vendor
  - date raised
  - delivery date
  - order number/status display
  - delivery address (ship_to)
  - notes/instructions
  - current GST-enabled line grid
  - totals summary
- Keep existing save, email, PDF, and convert-to-bill behavior intact.

## Test Plan
- Add a focused JS test for list-to-detail navigation and PO detail rendering.
- Verify add-line/item-selection/totals behavior on the full-page screen.
- Run targeted PO JS tests and `git diff --check`.

## Constraints
- No schema migration in this slice.
- Do not add new persisted PO fields yet; defer reference/attention/telephone/account-backed lines/discounts to later slices.
