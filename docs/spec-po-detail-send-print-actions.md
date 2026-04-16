# Spec: add send and print actions to the PO detail page

## Goal
Let users send or print a purchase order directly from the purchase-order detail screen instead of going back to the list first.

## Required Behavior
- The purchase-order detail screen shows action buttons adjacent to the main save action area.
- Existing purchase orders provide:
  - an email action that opens the existing PO email flow and targets the vendor email
  - a print/PDF action that opens the existing PO PDF document
- New unsaved purchase orders keep those actions visible but disabled until the PO has been created.
- Existing list-based PO email behavior remains available.

## Verification
- JS regression coverage for detail-page action rendering.
- JS regression coverage for the print/PDF action wiring.
- `git diff --check`.

## Assumptions
- This slice is a frontend workflow improvement only.
- Current backend PO email/PDF endpoints are already sufficient for the desired behavior.
