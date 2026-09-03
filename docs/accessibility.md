# Accessibility

SlowBooks Pro is built to be usable by everyone, including people who rely
on screen readers, keyboards, or high-contrast displays. We **strive to
conform to WCAG 2.1 Level AA**. We do not claim compliance — no certifying
body issues one — but we test against it, fix what we find, and treat a
barrier as a bug.

## What is in place (v2.8)

- Every data table declares its column headers (`scope="col"`).
- Icon-only buttons (remove a line, delete an attachment, close a dialog)
  carry accessible names.
- Notifications ("Invoice saved") announce through a polite live region.
- Dialogs are real dialogs: focus moves into them, Tab and Shift+Tab stay
  inside, Escape closes them, and focus returns to the control that opened
  them.
- State is never conveyed by colour alone (e.g. the reconciliation
  difference reads "Balanced" / "Out of balance").
- Muted text meets the 4.5:1 contrast ratio in both the light and dark
  themes.
- **Every PDF the app generates is tagged (PDF/UA-1)** and declares its
  language and title — invoices, statements, estimates, pay stubs, W-2s,
  1099s, Forms 940/941, checks, reports — so screen readers receive
  headings, tables and reading order rather than a flat image of text.

## What we know is still open

- The chart-of-accounts tree and some long entry forms could use landmark
  regions and skip links.
- Colour-coding on the dashboard charts (A/R aging) has text equivalents in
  the legend but not on the bars themselves.
- Keyboard-only drag ordering is not offered where a mouse drag exists
  (the dashboard uses arrow buttons instead).

## Tell us

If something in SlowBooks Pro is hard or impossible for you to use, open
an issue at https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/issues or
email support@slowbookspro.com and say which screen and which assistive
technology. Barriers are triaged as bugs.
