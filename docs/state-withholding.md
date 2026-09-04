# State withholding — coverage and parameters

Every state and the District of Columbia resolves to a payroll engine. Washington, California,
New York and Oregon have dedicated engines; the other 47 are driven by the table in
`app/services/state_tax/tables.py` through `TableEngine` (annualized percentage method:
wages − standard deduction − exemptions → flat rate or brackets → ÷ pay periods + extra).

**Verify before filing.** Figures are the 2025 published values (2026 where released),
simplified to the percentage-method structure; states that only publish wage-bracket tables
are modelled from the formula equivalent. Each row names the publication to check. Rates
change every January (and some in July): update `tables.py` and this file together.

Employee inputs the engines read (Employees → form): **State W-4 allowances**,
**extra state withholding** per period, an **elected rate** (Arizona A-4), and a flat
**local tax rate** in percent where the state requires one (Indiana and Maryland counties,
Ohio cities and school districts, Pennsylvania municipalities, Michigan cities). Local
jurisdictions are not modelled individually.

The `GET /api/payroll/states` catalog returns the same information.

| State | Method | Deductions / exemptions | Other items (employee / employer) | SUTA base | Year | Source |
|---|---|---|---|---|---|---|
| **AK** Alaska | No wage income tax | none | AK employee unemployment insurance 0.50% to 51,700 | 51,700 | 2025 | Alaska DOL Employment Security Tax |
| **AL** Alabama | Progressive 2.00%–5.00% (3 brackets) Standard deduction phases down with income; the maximum is used. | std ded 3,000 / 8,500; base exemption 1,500 / 3,000; 1,000 per allowance | — | 8,000 | 2025 | Alabama Withholding Tax Tables and Instructions |
| **AR** Arkansas | Progressive 0.00%–3.90% (5 brackets) Arkansas gives a $29 per-exemption credit rather than an exemption amount; not modelled. | std ded 2,410 / 4,820 | — | 7,000 | 2025 | Arkansas Withholding Tax Formula Method |
| **AZ** Arizona | Employee-elected rate (default 2.0%) Employee elects a rate on Form A-4 (0.5%–3.5%); 2.0% is the default when none is on file. Set employee.state_rate_override. | none | — | 8,000 | 2025 | Arizona Form A-4 |
| **CA** California | Progressive (DE 44 Method B) + SDI — dedicated engine | see engine | see engine | 7,000 | 2026-approximate | app/services/state_tax/ca.py |
| **CO** Colorado | Flat 4.40% 4.4% statutory rate (a TABOR-triggered temporary cut applied in 2025). | std ded 15,000 / 30,000 | CO FAMLI (employee) 0.45% to 176,100; CO FAMLI (employer) 0.45% to 176,100 | 27,200 | 2025 | Colorado DR 1098 / DR 0004; FAMLI 0.9% split 50/50 |
| **CT** Connecticut | Progressive 2.00%–6.99% (7 brackets) Personal exemption phases out above $30k/$48k; the full amount is used. Withholding codes A–F map to filing status here. | base exemption 15,000 / 24,000 | CT Paid Leave 0.50% to 176,100 | 26,100 | 2025 | Connecticut Circular CT (Form CT-W4 withholding codes) |
| **DC** District of Columbia | Progressive 4.00%–10.75% (7 brackets) | std ded 15,000 / 30,000 | DC Paid Family Leave (employer) 0.75% | 9,000 | 2025 | DC Office of Tax and Revenue withholding instructions (FR-230) |
| **DE** Delaware | Progressive 0.00%–6.60% (7 brackets) Delaware gives a $110 per-exemption credit; not modelled. | std ded 3,250 / 6,500 | DE Paid Leave (employee) 0.40% to 176,100; DE Paid Leave (employer) 0.40% to 176,100 | 12,500 | 2025 | Delaware Withholding Tax Tables; Paid Leave 0.8% total from 2025 |
| **FL** Florida | No wage income tax | none | — | 7,000 | 2025 | Florida DOR reemployment tax |
| **GA** Georgia | Flat 5.19% Rate steps down 0.10%/yr toward 4.99%. | std ded 12,000 / 24,000; 4,000 per allowance | — | 9,500 | 2025 | Georgia Employer's Tax Guide (Form G-4) |
| **HI** Hawaii | Progressive 1.40%–11.00% (12 brackets) TDI employee share capped at 0.5% of weekly wages up to the statutory maximum; modelled as an annual base. | std ded 4,400 / 8,800; 1,144 per allowance | HI Temporary Disability Insurance 0.50% to 69,000 | 62,000 | 2025 | Hawaii Booklet A Employer's Tax Guide |
| **IA** Iowa | Flat 3.80% | std ded 15,000 / 30,000 | — | 39,500 | 2025 | Iowa Withholding Formula (3.8% flat from 2025) |
| **ID** Idaho | Flat 5.30% | std ded 15,000 / 30,000 | — | 55,300 | 2025 | Idaho Table for Percentage Computation Method |
| **IL** Illinois | Flat 4.95% Allowances = IL-W-4 line 1 count × $2,850 (line 2 additional allowances are $1,000 each — add them as allowances at the reduced value or use extra withholding). | 2,850 per allowance | — | 13,916 | 2025 | Illinois Booklet IL-700-T |
| **IN** Indiana | Flat 3.00% County income tax (0.5%–3%) is required: set employee.local_tax_rate to the county rate. Rate drops to 2.95% in 2026. | 1,000 per allowance | — | 9,500 | 2025 | Indiana Departmental Notice #1 |
| **KS** Kansas | Progressive 5.20%–5.58% (2 brackets) | std ded 3,605 / 8,240; base exemption 9,160 / 18,320; 2,320 per allowance | — | 14,000 | 2025 | Kansas Withholding Tax Guide (KW-100), 2024 tax reform |
| **KY** Kentucky | Flat 4.00% Rate is 3.5% for 2026 (HB 1). | std ded 3,270 / 3,270 | — | 11,700 | 2025 | Kentucky Withholding Tax Formula |
| **LA** Louisiana | Flat 3.00% | std ded 12,500 / 25,000 | — | 7,700 | 2025 | Louisiana R-1300 / Act 11 of 2024 (3% flat from 2025) |
| **MA** Massachusetts | Flat 5.00% 4% surtax on income over $1M is not withheld here. | base exemption 4,400 / 8,800 | MA PFML (employee) 0.46% to 176,100; MA PFML (employer) 0.42% to 176,100 | 15,000 | 2025 | Massachusetts Circular M; PFML 0.88% (25+ employees) |
| **MD** Maryland | Progressive 2.00%–5.75% (8 brackets) County tax (2.25%–3.2%) is required — set employee.local_tax_rate. Standard deduction is 15% of wages within $1,800–$2,800 ($3,650–$5,450 joint); the maximum is used. | std ded 2,800 / 5,450; 3,200 per allowance | — | 8,500 | 2025 | Maryland Employer Withholding Guide |
| **ME** Maine | Progressive 5.80%–7.15% (3 brackets) | std ded 15,000 / 30,000; 5,150 per allowance | — | 12,000 | 2025 | Maine Withholding Tables for Individual Income Tax |
| **MI** Michigan | Flat 4.25% Cities with an income tax (Detroit, Grand Rapids…) — use employee.local_tax_rate. | 5,800 per allowance | — | 9,500 | 2025 | Michigan Income Tax Withholding Guide (Form MI-W4) |
| **MN** Minnesota | Progressive 5.35%–9.85% (4 brackets) | std ded 14,950 / 29,900; 5,050 per allowance | MN Paid Leave (employee) 0.44% to 176,100; MN Paid Leave (employer) 0.44% to 176,100 | 43,000 | 2025 / PFML 2026 | Minnesota Income Tax Withholding Instruction Booklet; Paid Leave premiums from 1 Jan 2026 |
| **MO** Missouri | Progressive 0.00%–4.70% (8 brackets) | std ded 15,000 / 30,000 | — | 9,500 | 2025 | Missouri Employer's Tax Guide (Form MO-W-4) |
| **MS** Mississippi | Progressive 0.00%–4.40% (2 brackets) 4.0% in 2026. | std ded 2,300 / 4,600; base exemption 6,000 / 12,000; 1,500 per allowance | — | 14,000 | 2025 | Mississippi Withholding Tax Tables |
| **MT** Montana | Progressive 4.70%–5.90% (2 brackets) | std ded 15,000 / 30,000 | — | 45,100 | 2025 | Montana Withholding Tax Guide (Form MW-4), 2024 simplification |
| **NC** North Carolina | Flat 4.25% 3.99% from 2026. | std ded 12,750 / 25,500 | — | 32,600 | 2025 | NC-30 Withholding Tables |
| **ND** North Dakota | Progressive 0.00%–2.50% (3 brackets) | std ded 15,000 / 30,000 | — | 45,100 | 2025 | North Dakota Income Tax Withholding Rates and Instructions |
| **NE** Nebraska | Progressive 2.46%–5.20% (4 brackets) Nebraska's $157 per-exemption credit is not modelled. | std ded 8,600 / 17,200 | — | 9,000 | 2025 | Nebraska Circular EN |
| **NH** New Hampshire | No wage income tax No tax on wages (interest & dividends tax repealed 2025). | none | — | 14,000 | 2025 | NH Employment Security |
| **NJ** New Jersey | Progressive 1.40%–10.75% (7 brackets) Rate Table A for single, B for married/head of household. | 1,000 per allowance | NJ unemployment insurance (employee) 0.38% to 43,300; NJ disability insurance (employee) 0.23% to 43,300; NJ family leave insurance 0.33% to 43,300 | 43,300 | 2025 | New Jersey NJ-WT (Rate Tables A / B) |
| **NM** New Mexico | Progressive 1.50%–5.90% (6 brackets) | std ded 15,000 / 30,000 | — | 33,200 | 2025 | New Mexico FYI-104 Withholding Tax |
| **NV** Nevada | No wage income tax | none | — | 41,800 | 2025 | Nevada DETR |
| **NY** New York | Progressive (NYS-50-T) + SDI/PFL — dedicated engine | see engine | see engine | 12,800 | 2026-approximate | app/services/state_tax/ny.py |
| **OH** Ohio | Progressive 0.00%–3.50% (3 brackets) Municipal income tax (RITA / CCA cities) and school district tax — use employee.local_tax_rate. 2.75% flat from 2026. | 2,400 per allowance | — | 9,000 | 2025 | Ohio Employer Withholding Tables |
| **OK** Oklahoma | Progressive 0.25%–4.75% (6 brackets) | std ded 6,350 / 12,700; 1,000 per allowance | — | 27,000 | 2025 | Oklahoma Income Tax Withholding Tables (Packet OW-2) |
| **OR** Oregon | Progressive + statewide transit tax — dedicated engine | see engine | see engine | 54,300 | 2026-approximate | app/services/state_tax/oregon.py |
| **PA** Pennsylvania | Flat 3.07% No deductions or exemptions. Local EIT (municipal + school) — use employee.local_tax_rate. | none | PA employee unemployment contribution 0.07% | 10,000 | 2025 | PA Employer Withholding Guide (REV-415) |
| **RI** Rhode Island | Progressive 3.75%–5.99% (3 brackets) Brackets are the same for every filing status. Deduction/exemption phase out above ~$260k; full amounts used. | std ded 10,900 / 21,800; 5,100 per allowance | RI Temporary Disability Insurance 1.30% to 89,200 | 29,800 | 2025 | Rhode Island Withholding Tax Booklet |
| **SC** South Carolina | Progressive 0.00%–6.20% (3 brackets) Top rate steps down toward 6%. | std ded 15,000 / 30,000; 4,790 per allowance | — | 14,000 | 2025 | South Carolina Withholding Tax Tables (WH-1603F) |
| **SD** South Dakota | No wage income tax | none | — | 15,000 | 2025 | SD DOL reemployment assistance |
| **TN** Tennessee | No wage income tax | none | — | 7,000 | 2025 | TN DOL |
| **TX** Texas | No wage income tax | none | — | 9,000 | 2025 | TWC |
| **UT** Utah | Flat 4.50% Utah's withholding allowance credit is not modelled; the flat rate applies to taxable wages. | none | — | 48,900 | 2025 | Utah Publication 14 |
| **VA** Virginia | Progressive 2.00%–5.75% (4 brackets) | std ded 8,500 / 17,000; 930 per allowance | — | 8,000 | 2025 | Virginia Employer Withholding Instructions (Form VA-4) |
| **VT** Vermont | Progressive 3.35%–8.75% (4 brackets) | std ded 7,400 / 14,850; 5,100 per allowance | — | 14,800 | 2025 | Vermont Income Tax Withholding Instructions, Tables and Charts |
| **WA** Washington | No income tax; PFML, WA Cares, L&I — dedicated engine | see engine | see engine | 72,800 | 2026-approximate | app/services/state_tax/wa.py |
| **WI** Wisconsin | Progressive 3.50%–7.65% (4 brackets) Standard deduction phases out with income; the maximum is used. | std ded 13,930 / 25,790; 700 per allowance | — | 14,000 | 2025 | Wisconsin Publication W-166 |
| **WV** West Virginia | Progressive 2.22%–4.82% (5 brackets) | 2,000 per allowance | — | 9,500 | 2025 | West Virginia Employer's Withholding Tax Tables (2025 rate reduction) |
| **WY** Wyoming | No wage income tax | none | — | 32,400 | 2025 | WY DWS |

## Bracket schedules (single filer, annual, marginal)

- **AL**: 2% from 0; 4% from 500; 5% from 3,000
- **AR**: 0% from 0; 2% from 5,500; 3% from 10,900; 3.40% from 15,600; 3.90% from 26,000
- **CT**: 2% from 0; 4.50% from 10,000; 5.50% from 50,000; 6% from 100,000; 6.50% from 200,000; 6.90% from 250,000; 6.99% from 500,000
- **DC**: 4% from 0; 6% from 10,000; 6.50% from 40,000; 8.50% from 60,000; 9.25% from 250,000; 9.75% from 500,000; 10.75% from 1,000,000
- **DE**: 0% from 0; 2.20% from 2,000; 3.90% from 5,000; 4.80% from 10,000; 5.20% from 20,000; 5.55% from 25,000; 6.60% from 60,000
- **HI**: 1.40% from 0; 3.20% from 2,400; 5.50% from 4,800; 6.40% from 9,600; 6.80% from 14,400; 7.20% from 19,200; 7.60% from 24,000; 7.90% from 36,000; 8.25% from 48,000; 9% from 150,000; 10% from 175,000; 11% from 200,000
- **KS**: 5.20% from 0; 5.58% from 23,000
- **MD**: 2% from 0; 3% from 1,000; 4% from 2,000; 4.75% from 3,000; 5% from 100,000; 5.25% from 125,000; 5.50% from 150,000; 5.75% from 250,000
- **ME**: 5.80% from 0; 6.75% from 26,050; 7.15% from 61,600
- **MN**: 5.35% from 0; 6.80% from 32,570; 7.85% from 106,990; 9.85% from 198,630
- **MO**: 0% from 0; 2% from 1,313; 2.50% from 2,626; 3% from 3,939; 3.50% from 5,252; 4% from 6,565; 4.50% from 7,878; 4.70% from 9,191
- **MS**: 0% from 0; 4.40% from 10,000
- **MT**: 4.70% from 0; 5.90% from 21,100
- **ND**: 0% from 0; 1.95% from 48,475; 2.50% from 244,825
- **NE**: 2.46% from 0; 3.51% from 3,970; 5.01% from 23,820; 5.20% from 38,390
- **NJ**: 1.40% from 0; 1.75% from 20,000; 3.50% from 35,000; 5.53% from 40,000; 6.37% from 75,000; 8.97% from 500,000; 10.75% from 1,000,000
- **NM**: 1.50% from 0; 3.20% from 5,500; 4.30% from 16,500; 4.70% from 33,500; 4.90% from 66,500; 5.90% from 210,000
- **OH**: 0% from 0; 2.75% from 26,050; 3.50% from 100,000
- **OK**: 0.25% from 0; 0.75% from 1,000; 1.75% from 2,500; 2.75% from 3,750; 3.75% from 4,900; 4.75% from 7,200
- **RI**: 3.75% from 0; 4.75% from 79,900; 5.99% from 181,650
- **SC**: 0% from 0; 3% from 3,560; 6.20% from 17,830
- **VA**: 2% from 0; 3% from 3,000; 5% from 5,000; 5.75% from 17,000
- **VT**: 3.35% from 0; 6.60% from 47,900; 7.60% from 116,000; 8.75% from 242,000
- **WI**: 3.50% from 0; 4.40% from 14,680; 5.30% from 29,370; 7.65% from 323,290
- **WV**: 2.22% from 0; 2.96% from 10,000; 3.33% from 25,000; 4.44% from 40,000; 4.82% from 60,000

Married / head-of-household schedules are in `tables.py` (married defaults to doubled thresholds where the state does that).
