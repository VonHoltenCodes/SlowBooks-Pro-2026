# Jinja Autoescape Hardening Slice

## Summary
Port the upstream Jinja2 XSS fix into this branch by enabling template autoescaping anywhere we render HTML from repository-controlled Jinja environments.

## Key Changes
- Add regression tests first for invoice email rendering and PDF HTML rendering with attacker-controlled strings.
- Enable Jinja autoescaping for HTML/XML templates in `app/services/pdf_service.py`.
- Enable the same protection in `app/services/email_service.py`, which uses the same unsafe `Environment(...)` pattern locally.
- Keep existing formatting filters and document output behavior unchanged aside from escaping unsafe HTML input.

## Test Plan
- Add failing tests that assert raw `<script>`/HTML payloads are escaped in rendered email/PDF HTML.
- Re-run targeted tests, full Python suite, JS tests, syntax checks, and `git diff --check`.
- Write an explicit security review note for the slice before commit.

## Defaults
- Prefer the smallest patch that matches upstream intent.
- No new dependencies.
- Escape HTML by default for template-rendered documents unless a future template deliberately opts out with Jinja-safe markup.
