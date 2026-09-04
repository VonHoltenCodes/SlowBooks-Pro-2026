# Accessibility (ADA / WCAG 2.1 AA) — design notes (brainstorm, 2026-08-13)

Status: **SHIPPED in v2.8.0** (2026-09-04). This is the audit that scoped the accessibility pass; the public statement, known gaps and how to report barriers are in [`docs/accessibility.md`](../accessibility.md). Kept for the record of what was found and why.


Status: DESIGN + audit findings. Target is WCAG 2.1 AA — the de facto
court standard for private software under ADA Title III (there is no
codified regulatory standard; AA is what courts reference).

## Legal framing (important, decided)

- **Never claim compliance.** No certifying body exists; a false claim
  adds misrepresentation on top of any access barrier. The defensible
  posture is what competitors use: "strive to conform" / "committed to"
  language, paired with real fixes and a contact path.
- Fixing the audit list ≠ compliance. Frame all public statements
  accordingly (site accessibility page, README).

## SPA audit findings (6 concrete issues)

1. Table headers missing `scope` attributes
2. `toast()` has no `aria-live` region — notifications invisible to
   screen readers
3. Modals lack a focus trap and `role="dialog"` / `aria-modal`
4. Reconciliation diff communicates state by color only (needs icon/text)
5. `--gray-400` text fails AA contrast
6. Icon-only delete buttons need `aria-label`

## The bigger gap: PDFs

- WeasyPrint output is untagged by default — W-2s, 1099s, and paystubs
  are currently invisible to screen readers. Investigate WeasyPrint's
  PDF/UA support and heading/structure tagging.
- 15 Jinja templates are missing the `lang` attribute.

## Sequencing suggestion

SPA fixes are small and testable (axe-core or manual NVDA passes);
PDF tagging is the research-shaped chunk. Ship the six SPA fixes +
lang attributes first; PDF/UA second; public accessibility statement
("striving toward WCAG 2.1 AA") last, once the first two are real.
