# Spec: build the purchase-order detail page foundation

## Goal
Replace the current modal PO editor with a dedicated page-based purchase-order editing experience using the fields the backend already supports.

## Required Behavior
- Purchase order list actions must navigate to a dedicated PO detail screen for new/edit flows.
- The PO detail screen must expose the currently supported fields in a clearer purchasing-document layout:
  - vendor
  - date
  - expected/delivery date
  - order number
  - status (for existing POs)
  - ship_to as delivery address
  - notes/delivery instructions
  - line items
  - subtotal/tax/total summary
- Saving an existing or new PO must continue using the current create/update endpoints.
- Email, PDF, and convert-to-bill actions remain available from the list.

## Verification
- JS regression test for PO route/navigation and detail rendering.
- Existing PO-related UI behavior remains intact.
- `git diff --check`.

## Assumptions
- This slice is a UI/workflow foundation only.
- New PO header fields such as reference, attention, telephone, tax mode, account-backed lines, and discounts are deferred to later slices.
