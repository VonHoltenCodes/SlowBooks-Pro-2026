# Spec: upgrade purchase orders to a Xero-style detail screen

## Goal
Bring purchase orders up to a full document-style workflow by replacing the current modal editor with a dedicated detail screen and supporting the key purchasing fields shown in the Xero sample.

## Required Behavior
- Users can open a dedicated screen for:
  - creating a new purchase order
  - viewing/editing an existing purchase order
- The PO header supports:
  - vendor/contact
  - date raised
  - delivery date
  - order number
  - reference
  - delivery address
  - attention
  - telephone
  - delivery instructions
  - tax mode
- The PO line grid supports:
  - item
  - description
  - qty
  - price
  - discount
  - account
  - tax rate
  - tax amount
  - amount
- The totals panel clearly shows subtotal, tax, and total.
- Existing PO email, PDF, and convert-to-bill flows continue to work with the expanded PO shape.

## Interface Changes
- Add frontend PO detail routes/screens instead of relying on the modal.
- Extend PO schemas/models with the new header fields and the missing line-level fields (`account_id`, discount field, explicit tax-mode support).
- Update PO PDF output to display the relevant added metadata.

## Verification
- Backend tests for PO create/update and bill-conversion behavior with the expanded fields.
- Frontend tests for list-to-detail navigation and PO detail interactions.
- `git diff --check`.

## Assumptions
- We want Xero-like capability and workflow, not an exact UI clone.
- A full-page PO experience is preferred over continuing to expand the modal.
- Both item-based and direct-account purchasing lines are desirable in this product branch.
