# Fix GST PDF alignment and download filename behavior

## Summary
Correct the GST101A PDF export so numeric values align cleanly in the PDF's
comb-style amount boxes and downloaded files use a stable GST-specific
filename instead of a generic browser default.

## Key Changes
- Replace fragile GST amount-box form filling with deterministic overlay text
  placement aligned to fixed comb cells.
- Tighten the GST export response/download path so the download action uses a
  real filename from the server.
- Adjust GST header field placement where current overlay coordinates clip or
  crowd the printed values.

## Test Plan
- Add a unit test for the GST comb-cell placement helper.
- Update GST report tests to assert the PDF response filename header.
- Update the GST report JS test to assert the download action uses
  `API.download(...)`.
- Re-render the GST PDF and visually inspect the output PNGs.
- Run focused Python/JS tests plus `git diff --check`.

## Defaults
- GST PDF downloads should be named `GST101A_<start>_<end>.pdf`.
- Amounts should render by explicit overlay positioning, not viewer-dependent
  AcroForm comb appearances.
