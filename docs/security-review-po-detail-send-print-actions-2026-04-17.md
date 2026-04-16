# Security Review: PO Detail Send/Print Actions — 2026-04-17

## Scope
Reviewed the purchase-order detail-page send/print slice changes in:
- `app/static/js/purchase_orders.js`
- `tests/js_purchase_orders_detail.test.js`
- `docs/plan-po-detail-send-print-actions.md`
- `docs/spec-po-detail-send-print-actions.md`

## Questions reviewed
1. Does the slice widen who can trigger purchase-order email or PDF generation?
2. Does it introduce new client-side data exfiltration or unsafe document URL handling?
3. Does it bypass existing purchase-order permission checks?

## Findings
1. **No new backend capability was introduced**
   - The slice reuses the existing `POST /api/purchase-orders/{id}/email` and `GET /api/purchase-orders/{id}/pdf` endpoints.
   - No new transport, attachment, or file-generation code path was added.

2. **Existing permission boundaries remain intact**
   - The UI only adds buttons to existing detail-page workflows.
   - Server-side auth remains enforced by the existing purchase-order routes: `purchasing.manage` for email and `purchasing.view` for PDF.

3. **No untrusted URL or filename input added**
   - The PDF action uses the existing `API.open()` helper against a fixed same-origin API path.
   - The client-side fallback filename is derived from the in-memory PO number only and does not affect server-side file access.

## Residual risk
- The broader repo trust model and existing purchase-order email capability remain unchanged; any user who already has access to those endpoints can still send or open POs.
- Vendor email correctness still depends on existing vendor master data quality; this slice does not change recipient validation rules.

## Verdict
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW**, because this is a UI-only exposure of already-protected backend capabilities.
