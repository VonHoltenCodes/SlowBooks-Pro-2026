# Security Review — Jinja Autoescape Hardening (2026-04-16)

## Scope Reviewed
- `app/services/pdf_service.py`
- `app/services/email_service.py`
- `app/templates/*.html`
- `tests/test_email_formatting.py`
- `tests/test_pdf_service_formatting.py`

## Review Focus
- Whether HTML-generating Jinja environments still render untrusted fields without escaping
- Whether the upstream XSS fix was applied to the local equivalents that still used unsafe `Environment(...)` defaults
- Whether existing document formatting behavior remains intact after enabling autoescaping

## Findings
1. **HTML template rendering is now escaped by default**
   - Both local Jinja environments that render HTML documents/emails now enable autoescaping.
   - This blocks attacker-controlled names, notes, terms, and line descriptions from being interpreted as live HTML/JS.

2. **Regression coverage now proves the dangerous payloads are escaped**
   - Email and PDF rendering tests assert that representative `<script>`, `<img onerror>`, `<svg onload>`, and inline HTML payloads are emitted as escaped text rather than executable markup.

3. **Formatting helpers still behave normally**
   - Existing date/currency formatting tests continue to pass, indicating the change did not break the expected localized rendering path.

## Residual Risks
- Future HTML templates could still reintroduce XSS if they explicitly opt out via Jinja `|safe` or inject pre-sanitized HTML without review.
- I also had to update `tests/js_gst_settlement_ui.test.js` to the current `ReportsPage.renderGstReturnSummary()` API so the existing JS suite could pass; that was a stale test harness issue, not part of the XSS fix itself.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW**, provided future template changes avoid unsafe `|safe` usage without explicit review.
