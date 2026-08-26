# Receipt / Document Intake — design notes

Status: DESIGN, revised 2026-08-25. Nothing here is committed product
behavior yet. Revision: an outside contributor proposed a lightweight
in-core Tesseract tier, and it fits — the "standalone repo" rule below
existed to keep heavy vision dependencies out of the frozen installers,
and Tesseract doesn't carry any.

## Three-tier strategy

- **Tier 1 (this repo)**: vendor order-history importers — big-box
  vendors (Amazon Business, Home Depot Pro, Lowe's Pro, Grainger,
  Uline) expose structured CSV/order exports. Use those wherever they
  exist: 100% reliable vs. OCR's confidence games, SKU-level line
  items. Reuses the proven dialect pattern (migration engine / bank CSV
  importer): dedup on order number, line items → bills/cc_charges,
  match against SimpleFIN bank feed. Start every dialect from a REAL
  export file (MYOB lesson).
- **Tier 2 (this repo, NEW)**: lightweight local OCR via the system
  Tesseract binary. Ground rules that make it mergeable:
  - **Never bundled — policy, not a phase.** The tesseract binary is
    the USER'S install, like their PostgreSQL or their browser:
    Docker/native installs add the system package (`apt install
    tesseract-ocr`), Windows/macOS users install it themselves if they
    want the feature. It does not go into the signed installer or the
    notarized DMG — that would make its CVEs and release cadence ours
    (owner decision, 2026-08-25; see also the EasyAmp bundled-native-
    dep signature saga for why). If field demand ever reopens this, it
    reopens as its own project, not as scope creep on a PR.
  - **Optional, graceful degrade**: detect the binary at runtime
    (PATH + well-known install locations on Windows); when absent,
    the endpoint returns a clear "install Tesseract to enable
    scanning" message — the app must run exactly as today without it.
  - **Zero new Python dependencies**: call the binary directly with
    `subprocess` — pytesseract is only a subprocess wrapper, and
    requirements.txt is deliberately hard to add to (every pin
    carries CVE rationale). PDF pages rasterize via the poppler tools
    the PDF stack already uses. Pillow is already present.
  - **Deterministic parsing first**: raw OCR text → regex/anchor
    extraction for date, total, vendor. The existing BYOK AI layer
    (`app/services/ai_service.py`, 7 providers incl. self-hosted) is
    the OPTIONAL enhancement for messy documents — never a bundled
    model, never required, off by default like all AI here.
  - **One document type first**: receipts → pre-fill the Sales Receipt
    / Bill form (UI: a "Scan" button that uploads and populates the
    form; the operator always reviews before saving). Checks, bank
    statements, and 1099s are follow-ons — statements especially
    should push people to OFX/CSV/SimpleFIN first, OCR as last resort.
  - Endpoint shape: `POST /api/ocr/receipt` (multipart), auth'd like
    everything else; tests must skip cleanly when tesseract is absent
    and CI installs it (ubuntu: `apt-get install tesseract-ocr`).
- **Tier 3 (standalone repo, later)**: heavyweight OCR for the long
  tail — thermal receipts, batch capture. PaddleOCR-class accuracy,
  anchor-based template extraction (invoice2data-style: field positions
  relative to anchor text, never absolute pixels), correction UI where
  operator fixes update the template. Standalone keeps torch/CUDA
  weight out of this repo's clean footprint. Cloud OCR strictly opt-in.

## Honest framing (unchanged)

Template learning is anchor matching plus operator feedback — say
that. No "AI-powered" claims for deterministic extraction; the AI
label belongs only to the BYOK path when the operator turns it on.

## Integration seam (unchanged)

Structured JSON over the local API with a scoped bookkeeper token
(shipped v2.5.1). Attachments module links the source image/PDF to the
created document, so every scanned entry keeps its evidence.
