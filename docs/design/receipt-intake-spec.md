# Receipt / Document Intake — v1 implementation spec

Status: SPEC (2026-08-25). Companion to [docs/design/receipt-intake.md](receipt-intake.md)
(Tier 2, "lightweight local OCR"). This document turns the design notes into an
implementable contract: endpoints, data flow, field mapping, UI behavior,
security, and tests. No code changes are made yet.

---

## 1. Goal

A bookkeeper scans or photographs a paper receipt, clicks **Scan Receipt** on the
**Enter Sales Receipt** or **Enter Bill** modal, and the form is pre-filled with
whatever the OCR can read (date, merchant, total, tax). The operator **always
reviews before saving**. The saved document keeps the scanned image as an
attachment (evidence trail).

v1 is **deterministic-only** — regex/anchor extraction over Tesseract output.
The BYOK AI layer (`app/services/ai_service.py`) is explicitly **out of scope**
for v1 (confirmed; follow-up).

## 2. Confirmed decisions (from interview)

| Topic | Decision |
|---|---|
| Button scope | Both forms: Enter Sales Receipt + Enter Bill |
| Receipt direction | **No** sale/expense auto-detection — fill whichever form the operator opened |
| PDF input | **Supported in v1**, rasterized via **poppler-utils** (`pdftoppm`/`pdfinfo`, same user-install pattern as Tesseract) |
| Line items | **Single total line** (qty 1, rate = total, description = merchant); operator splits afterward |
| Tax | **Per form**: Sales Receipt → detect-and-split (subtotal line + tax rate); Bill → grand total as the line, tax noted in Notes |
| Vendor/customer | **Exact match auto-select** (case-insensitive); otherwise show detected name for manual pick from the dropdown |
| File lifecycle | **Server temp intake bucket** with TTL cleanup; attach to the saved document after save |
| Missing fields | **Blank + notice** — form marks the scan "partial", defaults date to today, highlights what's missing |
| Total detection | **Anchor-first, largest-amount fallback**; low-confidence picks flagged |
| AI | **Not in v1** |
| Tesseract absent | Scan button **disabled with tooltip** + status row in Settings; app runs exactly as today |
| Tests | **Both**: mock-based unit tests always run; integration tests with generated fixture images skip when the binary is absent; **CI installs `tesseract-ocr`** |
| Language | Tesseract **auto-detect** over a fixed allowlist (`eng spa fra deu por ita nld`); parsers stay English-anchored (numbers/dates still extract) |
| Processing | **Synchronous request with a timeout** (no background-job queue in the stack) |

## 3. Out of scope for v1

- AI-assisted parsing (BYOK or otherwise)
- Sale-vs-expense auto-classification
- Itemized line-item extraction from OCR text
- Checks, bank statements, 1099s, multi-document batch capture (Tier 3)
- Tesseract bundling into the signed Windows/macOS installers (design doc
  ground rule; system-binary install only)
- Multi-page PDFs: v1 OCRs **page 1 only** (receipts are one page); the note in
  the scan summary says so when the PDF has more pages
- Foreign-currency receipts (single-currency, `$`-anchored parsing)

## 4. Dependencies — ZERO new Python dependencies (owner decision)

**Owner feedback (2026-08-25):** Tesseract is the user's install, permanently
(never bundled into the signed installers — no CVE/release tracking for a
binary the project doesn't manage); skip pytesseract and call the `tesseract`
binary directly via `subprocess`; zero new Python dependencies is the target
(`requirements.txt` is deliberately hard to add to).

Consequences, all implemented:

- **No `pytesseract`, no `pypdfium2`, no `Pillow`** in `requirements.txt`.
  (Pillow is *not* a pre-existing dependency — that claim was checked; the
  line in requirements.txt was ours from the first draft and is reverted.)
- **Tesseract called directly**: `subprocess.run(["tesseract", "stdin",
  "stdout", "--psm", "3", "-l", lang], input=image_bytes, timeout=20)`.
  Leptonica decodes PNG/JPEG/WebP/TIFF natively, so no Python image library
  is needed on our side either.
- **PDFs via poppler-utils** (`pdftoppm` + `pdfinfo`) — the design doc's
  original "poppler tools" suggestion. Same philosophy as Tesseract: a
  user-installed system binary (`sudo apt-get install poppler-utils`),
  detected at runtime, graceful "install poppler-utils to scan PDFs" message
  when absent (images keep working). Rasterizes page 1 at 300 DPI
  (200 DPI poppler renders lost the amount column on one ground-truth PDF;
  300 DPI is clean and receipt pages are small, so it's fast).
- `tesseract-ocr` install: `apt-get install tesseract-ocr` (Ubuntu/Debian),
  `brew install tesseract` (macOS), Windows installer (Settings UI link).
  Docker image: add `tesseract-ocr` + `poppler-utils` to the Dockerfile.

NOTE: this matches the owner's latest revision of `docs/design/receipt-intake.md`
(2026-08-25, on the `feat/receipt-intake` branch — main has no design doc):
"call the tesseract binary directly with subprocess (no pytesseract, zero new
Python deps)" and "never bundled — policy, not a phase." The design doc still
says "the poppler tools the PDF stack already uses" (the repo only *generates*
PDFs via WeasyPrint — nothing rasterized before this) and "Pillow is already
present" (it isn't, and doesn't need to be — the direct-subprocess design uses
no Python image library). Both are immaterial to the implementation; flagged
so the rationale isn't built on them.

## 5. Backend

### 5.1 New files

- `app/routes/ocr.py` — `APIRouter(prefix="/api/ocr", tags=["ocr"])`
- `app/services/ocr_service.py` — pipeline + deterministic parsers
- `app/schemas/ocr.py` — request/response models
- `tests/test_ocr.py` — unit tests (mock at the subprocess boundary)
- `tests/test_ocr_integration.py` — generated-fixture tests (skip when binary absent)
- Register `ocr.router` in `app/main.py` (phase-appropriate import block)

### 5.2 Endpoints

#### `GET /api/ocr/status` — Tesseract availability

Used by the frontend to enable/disable Scan buttons and by the Settings page.

```json
{
  "available": true,
  "version": "5.3.0",
  "languages": ["eng", "spa"]
}
```

- `available` — `shutil.which("tesseract")` non-None. `version` from
  `tesseract --version` (cached briefly, e.g. 60s, to avoid spawning a process
  per keystroke); `null` when absent. `languages` from
  `tesseract --list-langs` intersected with the allowlist; `null` when absent.
- Cheap, rate-limit: none or very generous. Auth: same as everything else
  (session or bearer token; bookkeeper+).

#### `POST /api/ocr/receipt` — scan + extract (multipart)

Request: `multipart/form-data`, field `file` (`UploadFile`). Accepted
content-types: `image/png`, `image/jpeg`, `image/webp`, `application/pdf`.
Size cap via `app/services/upload_limits.py::read_limited` —
`MAX_IMPORT_BYTES` (20 MB) is fine (scans are far smaller). Reject anything
else with 400, mirroring `app/routes/attachments.py` messaging style.

Behavior:

1. If `tesseract` is unavailable → **200** with
   `{"ocr_available": false, "message": "Tesseract is not installed. Install it
   to enable scanning (Ubuntu: apt-get install tesseract-ocr; macOS: brew
   install tesseract; Windows: see Settings)."}` — the UI shows the message.
   (200 + flag rather than an error status: the Scan button may be clicked in a
   race with a fresh install, and the frontend treats this as informational.)
2. Images → raw bytes straight to Tesseract's stdin (leptonica decodes
   PNG/JPEG/WebP natively). PDFs → `pdftoppm` rasterize **page 1** at 200 DPI
   → PNG bytes. Multi-page PDFs proceed with page 1 and the response notes
   `"multi_page": true` (page count from `pdfinfo`).
3. OCR: `subprocess.run(["tesseract", "stdin", "stdout", "--psm", "3",
   "-l", lang], input=<image bytes>, timeout=20)` — direct call, no wrapper
   package. Language from the allowlist intersection (see §5.5). `raw_text`
   returned for the operator's reference.
4. Run deterministic parsers (date, total, tax, merchant — §5.4) over
   `raw_text`.
5. Store the original file in the intake bucket (§5.3), keyed by a fresh
   `uuid4`. Return the extraction result + `intake_id`.

Response (200, tesseract present):

```json
{
  "ocr_available": true,
  "intake_id": "9f2c…",
  "merchant":       {"value": "HOME DEPOT #1234", "confidence": "high"},
  "date":           "2026-08-20",
  "date_is_default": false,
  "total":          "123.45",
  "total_confidence": "high",
  "subtotal":       "118.45",
  "tax":            "5.00",
  "tax_detected":   true,
  "language":       "eng",
  "multi_page":     false,
  "raw_text":       "…full OCR text…",
  "partial":        false,
  "partial_reasons": []
}
```

- `confidence`: `"high" | "low" | "missing"` for `merchant`; `"high" | "low"`
  for `total` (anchored vs fallback).
- `partial` is `true` when any required field is missing/low-confidence;
  `partial_reasons` carries human-readable strings the frontend shows in the
  scan summary bar (e.g. `"date not found — today's date used"`,
  `"total is low-confidence — verify the amount"`).
- `tax_detected`/`tax`/`subtotal`: `tax` is `null` when no separate tax line
  found. **The endpoint does not decide form mapping** — the frontend applies
  the per-form tax rule (§6.4). If `total` is missing entirely, the response
  still returns the intake + whatever was found; the frontend blocks nothing
  but the summary bar makes it obvious.
- Server-side bound: `subprocess.run(timeout=20)` on tesseract plus the
  request not hanging past ~30s; 504/422 on timeout is acceptable (frontend
  fetch timeout ~45s).

#### `POST /api/ocr/intake/{intake_id}/attach` — move scan to the saved document

```json
{"entity_type": "invoice" | "bill", "entity_id": 123}
```

Called by the frontend **after** the document is saved (see §6.5). Moves the
intake file into the attachments store as an `Attachment` row
(`entity_type`/`entity_id` per request), reusing the sanitization/whitelist
machinery from `app/routes/attachments.py` (extension + MIME checks against the
original filename recorded at intake). Deletes the intake entry. Returns the
`AttachmentResponse` (schema from `app/schemas/attachments.py`).

- Validates the intake exists and is not expired (410/404 otherwise), and that
  the entity exists (invoice id for sales receipts — a sales receipt *is* an
  invoice with `is_sales_receipt`, per `app/routes/sales_receipts.py`).
- This separate endpoint keeps the core `sales-receipts`/`bills` create paths
  untouched. **Alternative considered**: adding an optional `intake_id` to
  `SalesReceiptCreate`/`BillCreate` so the server attaches during create —
  lower round-trips but touches core schemas/routes; **not chosen for v1**
  (flag if you'd prefer it).

#### `DELETE /api/ocr/intake/{intake_id}` — (optional, recommended)

Frontend calls on modal cancel/close to free the file immediately; the TTL
sweep (§5.3) is the safety net if the browser dies mid-flow.

### 5.3 Intake bucket

- Location: `storage.files_root() / "uploads" / "intake"` (inside the uploads
  tree so desktop data-dir behavior matches; **never mounted/served** — the
  `/static/uploads` mount only exposes `attachments/`).
- File name: `<uuid4>.<ext>`; original filename + MIME recorded in a sidecar
  JSON (or the DB) so the attachment row is faithful later.
- TTL: sweep deletes intake files older than **24h**. Sweep runs opportunistically
  on each `POST /api/ocr/receipt` (delete only what's expired, O(n) over the dir)
  and once at app startup.
- Hard cap: if the bucket exceeds **1 GB or 500 files**, evict oldest-first
  (defense against a forgotten client filling disk).
- Not company-scoped beyond the uploads root (desktop installs are
  single-company-per-data-dir anyway); acceptable for v1.

### 5.4 Deterministic parsers (`ocr_service.py`)

All parsers operate on `raw_text` lines; **no regex over untrusted input ever
reaches a subprocess** (see §5.6).

- **Date**: try common US-first formats (`MM/DD/YYYY`, `YYYY-MM-DD`, `MM/DD/YY`,
  `Mon D, YYYY`, `D Mon YYYY`) via `python-dateutil` (already a dependency) with
  US-first ordering; prefer the date nearest the top of the receipt (purchase
  date, not the card's valid-thru). Missing → `date_is_default: true` with
  today's date; flagged in `partial_reasons`.
- **Total**: anchor-first — find a line whose text matches
  `(total|amount due|balance due|grand total|tota l|sale total)` and take the
  first `$?[\d,]+\.\d{2}` on/near that line. Fallback: the **largest currency
  amount** anywhere in the text. Anchored → `total_confidence: "high"`,
  otherwise `"low"`.
- **Tax / subtotal**: a line anchored by `tax` (excluding `tax included` /
  `no tax`); subtotal line anchored by `subtotal`. Only surfaced when a separate
  tax figure exists; the frontend decides what to do with it.
- **Merchant**: the first several non-empty lines that are not a date, amount,
  or address-like line (heuristic: 2+ words, no `$`, not a pure number), most
  likely the header/merchant block. Truncate to ~60 chars.
- Method of payment (cash/card/check): **not** extracted in v1 — operator picks
  it on the form (keeps the parser honest).

### 5.5 Language handling

`tesseract --list-langs` intersected with the allowlist
`{"eng", "spa", "fra", "deu", "por", "ita", "nld"}`. If the intersection is
non-empty and contains more than `eng`, pass the whole intersection to
`--languages` (Tesseract picks per page); otherwise `eng`. The language is a
**server-side constant**, never user input (no injection surface). Parsers are
English-anchored, so a Spanish receipt still yields numbers/dates; merchant
text extracts as-is.

### 5.6 Security posture

- **Auth/RBAC**: session or bearer token; `POST /api/ocr/receipt`,
  `/attach`, and `DELETE` are writes — bookkeeper role is allowed (not in
  `_ADMIN_WRITE_PREFIXES` in `app/main.py`).
- **Rate limit**: `@limiter.limit("30/minute")` on `POST /api/ocr/receipt`
  (pattern: `app/routes/analytics.py`); lighter/none on status and attach.
- **Size cap**: `read_limited` (20 MB) — avoids unbounded `await file.read()`.
- **No shell injection**: tesseract/poppler are invoked via `subprocess.run`
  with explicit argv lists — **never `shell=True`**, no user-controlled
  arguments (image bytes go through stdin; the language comes from a
  server-side allowlist constant).
- **File safety**: intake filenames are server-generated UUIDs; original
  filename only recorded as metadata and re-validated through the existing
  attachment sanitizers at attach time (`_sanitize_filename`, extension/MIME
  whitelists in `app/routes/attachments.py`).
- **Content sniffing**: PDFs go through poppler (a corrupt PDF exits nonzero
  → rejected with a clear message, never shelled out to); corrupt images make
  Tesseract exit nonzero → surfaced as "OCR failed" rather than crashing.
  The 20s subprocess timeout bounds pathological inputs.

## 6. Frontend

### 6.1 Shared module: `app/static/js/ocr.js`

A small shared helper (`window.ScanHelper` or similar, following the existing
`BillsPage`/`SalesReceiptsPage` object style) used by both forms:

- `status()` — GET `/api/ocr/status`; result cached ~2 min.
- `scanButtonHtml(formId)` — returns the button + hidden file input + status
  line markup.
- `wire(formId, applyCallback)` — on modal open, checks status (disables
  button with tooltip when unavailable), handles file selection, shows
  spinner, POSTs the file, calls `applyCallback(result)`.
- `attachAfterSave(intakeId, entityType, entityId)` — POST `/attach` with
  best-effort error toast.
- Load order: add `<script src="/static/js/ocr.js">` before the page scripts in
  `index.html`.

### 6.2 Button placement (both modals)

- **`app/static/js/sales_receipts.js`** — `SalesReceiptsPage.showForm()`:
  render the Scan row at the top of the modal body, before the
  `<div class="form-grid">`.
- **`app/static/js/bills.js`** — `BillsPage.showForm()`: same position.
- Markup: `[📄 Scan Receipt]` primary-secondary button + hidden
  `<input type="file" accept="image/png,image/jpeg,image/webp,application/pdf">`.
  When `GET /api/ocr/status` says unavailable, the button is `disabled` with a
  `title`/tooltip: *"Tesseract OCR isn't installed — see Settings for install
  instructions."*

### 6.3 Scan flow

1. Operator clicks **Scan Receipt** → file picker → spinner state ("Scanning…")
   and the form fields are disabled/covered so nothing edits mid-scan.
2. `POST /api/ocr/receipt` (FormData). On `ocr_available: false`, show the
   message as a toast.
3. On success: call the form's `applyCallback` (§6.4), then render the scan
   summary bar above the form grid:
   - green: "Scan complete — review before saving"
   - amber, when `partial`: join `partial_reasons`, e.g.
     *"Partial: total is low-confidence — verify the amount."*
   - note multi-page: *"Receipt has N pages — first page scanned."*
4. Store `intake_id` on the page object (`SalesReceiptsPage._intakeId` /
   `BillsPage._intakeId`), cleared on successful attach, save-failure, or modal
   close (which fires `DELETE /api/ocr/intake/{id}` best-effort).

### 6.4 Prefill rules

Common (both forms):

- **Date**: `date` input ← extracted date (or today, flagged in the bar).
- **Merchant → party**: exact case-insensitive match against the already-loaded
  customers (sales receipt) / vendors (bill) lists (`SalesReceiptsPage._customers`,
  `BillsPage._vendors`). Match → auto-select the dropdown option. No match →
  show a hint line under the dropdown: *"Detected: HOME DEPOT #1234 — select
  from the list or add new"*, and prefill the **Quick Add** name field
  (both forms already have a quick-add box — sales receipt's
  `sr-new-customer-form`; bill's vendor quick-add) so the operator can create
  it in one click. Never auto-create.
- **Total → one line**: set the first line's `rate` to the total, `quantity`
  to 1, `description` to the merchant name (or "Receipt scan" when merchant is
  missing), `item_id` unset. Leave existing additional lines alone. Recalc
  totals via the page's existing recalc.

Per form:

- **Sales Receipt** (`SalesReceiptsPage`): if `tax_detected` and `subtotal` are
  present, set line `rate = subtotal` and `tax_rate = tax / subtotal × 100`
  (round to 2dp); otherwise line `rate = total` and leave `tax_rate` as the
  form's default. `method`, `check_number`, `reference`, `deposit_to_account_id`
  are **not** filled — operator picks.
- **Bill** (`BillsPage`): always line `rate = total` (amount owed includes
  tax). If `tax_detected`, append to the Notes field: `"Tax detected: $5.00"`.
  `bill_number` is **not** filled (never inferable from a receipt; stays
  required). Terms untouched.

### 6.5 Save → attach

- `SalesReceiptsPage.save` / `BillsPage.save` succeed → call
  `ScanHelper.attachAfterSave(intake_id, "invoice" | "bill", id)`; sales
  receipts attach as `entity_type: "invoice"` (the created record is an
  invoice, per the sales-receipts route). Success is silent; failure toasts
  *"Saved, but the scan image couldn't be attached — add it manually."* and
  the intake TTL reaps the file.
- The existing attachments UI (upload button per document) is unchanged; the
  scan attachment appears there like any other.

### 6.6 Settings page status

`app/static/js/settings.js` gains a **Receipt scanning** row: calls
`GET /api/ocr/status` and shows:

- *"Tesseract OCR is installed (v5.3.0)"* — enabled state
- *"Tesseract OCR is not installed — scanning is disabled."* + platform-specific
  install hint (Ubuntu `sudo apt-get install tesseract-ocr`; macOS
  `brew install tesseract`; Windows link to UB-Mannheim/tesseract or winget).

## 7. Tests

### 7.1 Unit (always run — `tests/test_ocr.py`)

- Mock `ocr_service.ocr_image_bytes` (the subprocess boundary) with canned
  OCR text; plus a **fake `tesseract` on PATH** test that exercises the real
  subprocess plumbing (stdin pipe, stdout parse, `--version`/`--list-langs`
  probing) without the real binary — so it runs in CI even when tesseract is
  absent.
- Parser coverage (the deterministic core, no binary needed):
  - date extraction (formats, US-first, missing → default + flag)
  - total: anchor-first (TOTAL/AMOUNT DUE/BALANCE DUE), fallback largest,
    low-confidence flag
  - tax/subtotal split vs tax-included
  - merchant extraction (header line, address-line exclusion)
- Endpoint tests with mocked service:
  - auth required; bookkeeper allowed
  - 400 on unsupported MIME/type; 413 on oversize (`read_limited`)
  - `ocr_available: false` path (monkeypatch `shutil.which` → None) returns the
    message, no crash
  - intake lifecycle: upload → attach → file moved to attachments store,
    intake row gone; attach 404 on unknown/expired intake; TTL sweep removes
    old files
  - rate limit applied (config already toggles `RATE_LIMIT_ENABLED`)

### 7.2 Integration (skip when binary absent — `tests/test_ocr_integration.py`)

- `pytest.mark.skipif(shutil.which("tesseract") is None, ...)` at module scope.
- Helper renders known-text receipts with **the repo's WeasyPrint** (or the
  checked-in ground-truth PDFs in `assets/sample-receipts/`) — deterministic
  fixtures; merchant header, date line, item lines, TOTAL + amount
  in-test (no binary blobs in the repo). Cover: happy path fields, tax split,
  low-resolution robustness, PDF input (poppler-utils path) with a tiny
  generated PDF.

### 7.3 CI

`.github/workflows/ci.yml` — in the `test` job's system-deps step, add
`tesseract-ocr` to the `sudo apt-get install` list (renaming the step to
"System deps (WeasyPrint + Tesseract)"). CI then always exercises the
integration tests; the skip guards local dev without the binary.

## 8. Docs & release notes

- `docs/features.md`: document `POST /api/ocr/receipt`, `/status`,
  `/intake/{id}/attach` under the API surface (the repo keeps endpoint docs
  there) + the feature blurb.
- `CHANGELOG.md` entry when merged.
- Settings page carries the install instructions (§6.6); no installer changes
  in v1 (design-doc ground rule).

## 9. Open questions / flags for the implementer

1. **Attach mechanism**: the separate `/attach` endpoint was chosen to avoid
   touching core create schemas. If the team prefers `intake_id` on
   `SalesReceiptCreate`/`BillCreate` instead, that's a sanctioned alternative
   (flag in review).
2. **Merchant confidence**: the header-line heuristic is the weakest parser —
   if real-world scans show poor merchant detection, prefer taking the *first
   line of the OCR text* (usually the logo/merchant name) over the
   address-exclusion heuristic. Test both against the generated fixtures.
3. **Tax rounding**: `tax / subtotal × 100` rounded to 2dp can drift the line
   total by cents vs the receipt; acceptable because the operator reviews, but
   note it in the scan bar when the recomputed total ≠ grand total.

## 10. Work breakdown (suggested order)

1. Zero-dep confirmation + CI installs `tesseract-ocr` and `poppler-utils`
2. `ocr_service.py` parsers + unit tests (mock)
3. `POST /api/ocr/receipt` + status endpoint + schemas + route registration
4. Intake bucket + TTL + attach/delete endpoints + tests
5. `ocr.js` helper + Scan button + prefill in `sales_receipts.js`
6. Same in `bills.js`
7. Settings-page status row
8. Integration tests + fixtures, docs, CHANGELOG
