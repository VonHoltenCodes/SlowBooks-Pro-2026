# Jinja Autoescape Hardening Specification

## Goal
Prevent template-rendered document/email HTML from interpreting attacker-controlled fields as executable or structural HTML by enabling Jinja autoescaping in local HTML template environments.

## Required Behavior
- Invoice email rendering must escape untrusted customer/document fields by default.
- PDF HTML rendering must escape untrusted document/customer/vendor/payroll fields by default before WeasyPrint consumes the markup.
- Existing formatting filters (`currency`, `fdate`) and normal document rendering paths must continue to work.
- The change must cover the local services that still construct Jinja environments without autoescaping.

## Constraints
- Keep the change localized to existing Jinja environment setup plus regression tests.
- No new dependencies or broad template rewrites.
- Preserve explicit HTML structure authored in templates while escaping interpolated data values.

## Verification
- Backend regression tests proving HTML/script payloads are escaped in invoice email and invoice PDF output.
- Full Python suite, JS tests, syntax checks, explicit security review, and `git diff --check`.
