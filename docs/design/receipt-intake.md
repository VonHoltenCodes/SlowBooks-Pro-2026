# Receipt / Purchase Intake — design notes (brainstorm, 2026-08-13)

Status: DESIGN. Nothing here is committed product behavior yet.

## Two-tier strategy (decided)

- **Tier 1 (this repo)**: vendor order-history importers — big-box vendors
  (Amazon Business, Home Depot Pro, Lowe's Pro, Grainger, Uline) expose
  structured CSV/order exports. Use those wherever they exist: 100%
  reliable vs. OCR's confidence games, SKU-level line items. Reuses the
  proven dialect pattern (migration engine / bank CSV importer): dedup on
  order number, line items → bills/cc_charges, match against SimpleFIN
  bank feed. Start every dialect from a REAL export file (MYOB lesson).
- **Tier 2 (standalone repo, merged later)**: OCR for the long tail —
  paper/thermal receipts. Standalone keeps torch/CUDA/vision deps out of
  this repo's clean footprint and frozen installers. Also reusable for
  sub/vendor invoice capture later.

## Tier 2 technical direction

- **Anchor-based template extraction, NOT ML** (invoice2data-style):
  store field positions relative to anchor text (e.g., the "TOTAL"
  label), never absolute pixel coords — survives variable receipt length.
- **PaddleOCR over Tesseract** for thermal receipts; cloud OCR
  (Textract / Azure) strictly opt-in for higher accuracy — local-first
  is the brand.
- **Correction UI is the real "self-adapting" mechanism**: flag
  low-confidence fields; operator corrections update the template.
  Be honest in framing — this is template learning, not ML. No
  "AI-powered" claims for what is anchor matching plus feedback.

## Integration seam

Structured JSON over the local API with a scoped bookkeeper token
(shipped v2.5.1) — the receipt service is the first real token customer.
Attachments module links the source image/CSV to the created document.
