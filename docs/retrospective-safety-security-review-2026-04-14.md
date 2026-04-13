# Retrospective Safety & Security Review — 2026-04-14

## Scope
Completed NZ-localization slices reviewed retrospectively before further implementation:

1. `cc41344` — Preserve GST posting integrity during document lifecycle
2. `fa16426` — Support IRD GST returns for the NZ product fork
3. `c394790` — Retire Schedule C from the NZ product surface
4. `bae8ad4` — Replace US payroll setup with an NZ employee model

## Safety Verification Performed
### Repository-wide verification
- `.venv/bin/python -m unittest discover -s tests` → **49 tests passed**
- `for f in tests/*.js; do node "$f"; done` → **9 JS checks passed**
- `.venv/bin/python -m py_compile $(find app alembic -name '*.py' -print)` → **passed**
- `git diff --check` → **passed**

### Slice-specific behavior evidence
- GST posting/document lifecycle coverage present in `tests/test_document_gst_calculations.py`
- GST return/report and PDF coverage present in `tests/test_gst_return_report.py` and `tests/js_gst_return_report.test.js`
- Schedule C disablement coverage present in `tests/test_schedule_c_disabled.py` and `tests/js_tax_nav_disabled.test.js`
- NZ payroll model/placeholder coverage present in `tests/test_nz_payroll_data_model.py` and `tests/js_nz_payroll_ui.test.js`

## Security Review Method
Reviewed the changed route/service/model/migration/UI files for:
- secrets exposure
- injection risks (SQL/HTML/header/path)
- auth/access-control regressions introduced by the slices
- unsafe PDF generation/file handling
- sensitive payroll data handling
- placeholder/disablement behavior for incomplete compliance features

Additional spot checks:
- regex-based secrets scan over the repo
- pattern scan for raw SQL / dynamic execution / unsafe HTML sinks in the reviewed areas
- dependency-audit availability check (`pip-audit` is **not installed** in this environment)

## Findings

### CRITICAL
- None found in the reviewed slices.

### HIGH
- None found in the reviewed slices.

### MEDIUM
1. **Sensitive payroll data remains exposed through unauthenticated app surfaces**
   - Files: `app/routes/employees.py`, `app/models/payroll.py`, `app/schemas/payroll.py`, `app/main.py`
   - The NZ payroll slice introduced IRD number, tax code, KiwiSaver, student loan, child support, and ESCT fields. Those fields are stored in plaintext and returned by employee CRUD endpoints, while the app still runs as a wide-open local FastAPI surface with permissive CORS and no authentication/authorization boundary.
   - This is not a new injection bug, but it materially increases privacy risk now that the app stores employee tax identifiers.
   - Required operational constraint: keep SlowBooks NZ on trusted/local/private deployments until an auth + privacy hardening slice exists.

### LOW
1. **Payroll setup fields lack stricter server-side format validation**
   - Files: `app/schemas/payroll.py`, `app/routes/employees.py`
   - Pydantic currently enforces types, but not NZ-specific format/allowlist rules for fields such as `ird_number`, `tax_code`, and `pay_frequency` beyond current UI choices.
   - Impact is data-quality/privacy oriented rather than code-execution oriented.

2. **Verification output includes SQLite ResourceWarnings in tests**
   - Observed during `.venv/bin/python -m unittest discover -s tests`
   - This is a test-hygiene issue rather than a production vulnerability, but it weakens the signal quality of the verification process and should be cleaned up.

## Positive Security/Safety Outcomes
- No hardcoded credentials or private keys were found in the reviewed slices.
- Reviewed report and payroll code paths use SQLAlchemy ORM queries rather than user-concatenated SQL.
- The payroll run endpoints intentionally return `410 Gone`, preventing accidental use of incomplete PAYE logic.
- The retired Schedule C endpoints intentionally return `410 Gone`, reducing exposure of unsupported US tax workflows.
- GST PDF generation uses a fixed bundled template path (`app/forms/gst101a-2023.pdf`); reviewed code does not expose user-controlled filesystem paths.
- GST report UI escapes rendered values before inserting HTML, limiting XSS risk in the reviewed rendering paths.
- GST posting lifecycle changes preserve balanced journal behavior via shared posting/reversal helpers and retain closing-date checks where applicable.

## Coverage Gaps / Limitations
- `pip-audit` was unavailable, so no automated Python dependency CVE scan was run in this pass.
- This review focused on the completed NZ-localization slices above, not the entire historical codebase.
- The broader app trust model (no auth, permissive CORS) predates these slices, but now has higher impact because payroll PII exists.

## Recommended Follow-up Before/While Continuing NZ Work
1. Add an explicit **auth/privacy hardening slice** for employee/payroll data before introducing PAYE processing or payslips.
2. Add schema-level validation for payroll identifiers and enumerated fields.
3. Clean up the SQLite ResourceWarnings so verification output stays trustworthy.
4. Install and run `pip-audit` (or equivalent) as part of the standing safety/security checklist.

## Overall Assessment
- **Safety verification status:** PASS for the reviewed completed slices.
- **Security review status:** No CRITICAL/HIGH slice regressions found.
- **Residual risk:** MEDIUM, driven by sensitive payroll data living inside an application that still has no auth/privacy boundary.
