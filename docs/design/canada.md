# Canadian support — design notes (PARKED)

Status: PARKED 2026-09-03. Trent asked for the full set (sales tax, payroll,
filings, conversion), heard the sizing, and parked it so the v2.8 stage stays
shippable. Nothing here is committed product behavior.

## The requirement

Seamless: a Canadian company must feel exactly like a US one does today. One
codebase, no forks, no per-dollar branching. Country is chosen once, at company
creation, and locked.

## Where Canada actually differs (the four seams)

1. **Tax on a line.** Today a document carries one tax rate and one payable
   account. Canada needs a **tax code with components** — GST + PST, HST alone,
   GST + QST — each to its own account, and tax paid on purchases is
   **recoverable** (input tax credits), so the return nets collected against
   paid. This ripples through every tax-bearing form and PDF (invoice, bill,
   expense, sales receipt, credit memo, PO). It is also a straight improvement
   for US users (state + county + city rates), so **build it first as a general
   feature** and lap it on US books before Canada leans on it.
2. **Payroll calculation.** CPP (basic exemption, YMPE, second ceiling / CPP2),
   EI (employee rate, employer × 1.4), federal + provincial withholding via the
   CRA T4127 formulas. A second engine beside the US one, selected by country;
   same pay-run flow. Testable against the CRA's PDOC calculator. Largest single
   piece — on the order of the whole US payroll module — and a standing
   January/July table-update commitment.
3. **Filings.** GST/HST return (GST34 lines), PD7A remittance, T4 slips + T4
   Summary (CRA XML), Record of Employment (ROE Web XML is fussy — its own
   medium piece).
4. **Words and seeds.** Province/state, postal code/ZIP, BN/EIN, SIN/SSN, CAD as
   home currency, a Canadian chart of accounts seeded at creation. A label
   dictionary and a second seed file.

## The switch

`company.country` (US | CA), set at company creation, immutable. It picks the
chart seed, the tax-code set, the payroll engine, the filing set and the label
dictionary. Nothing else asks. US companies see exactly what they see today.

## Sizing (against work already shipped)

| Piece | Size |
|---|---|
| Tax codes w/ recoverable components + GST return | ≈ jobs M1 + M2 together |
| Canadian payroll engine | ≈ the US payroll module (biggest piece) |
| T4 / T4 Summary / PD7A | ≈ jobs M3 |
| ROE | medium, fussy XML |
| QuickBooks Canada conversion (tax-code mapping) | small |

Whole thing ≈ two v2.7-sized stages; payroll is more than half.

## Conditions before unparking

- The general tax-code engine has shipped and been lapped on US books.
- A Canadian bookkeeper is lined up to lap payroll against a real remittance
  (the CRA calculator proves the math, not the workflow).
- Someone owns the twice-yearly table updates.

## Open questions

- Home currency: is CAD-as-home a settings flip today, or does multi-currency
  assume USD home? Check `settings` / `exchange_rate` handling before scoping.
- Quebec: QPP instead of CPP, QPIP, RL-1 slips — decide whether Quebec is in
  the first cut or explicitly not.
