# State withholding — coverage and parameters

Every state and the District of Columbia resolves to a payroll engine. Washington, California,
New York and Oregon have dedicated engines; the other 47 are driven by the table in
`app/services/state_tax/tables.py` through `TableEngine` (annualized percentage method:
wages − standard deduction − exemptions → flat rate or brackets → ÷ pay periods + extra).

**Figures are the 2026 published values**, verified 2026-09-03 against the state publications
and the compilations named in each row (Tax Foundation's 2026 rates and brackets; the 2026 SUI
wage-base chart), simplified to the percentage-method structure. States that only publish
wage-bracket tables are modelled from the formula equivalent. Rates change every January (and
some mid-year — Utah moved to 4.45% on 2026-06-01): update `tables.py` and regenerate this file
together. **Verify before filing.**

Employee inputs the engines read (Employees → form): **State W-4 allowances**,
**extra state withholding** per period, an **elected rate** (Arizona A-4), and a flat
**local tax rate** in percent where the state requires one (Indiana and Maryland counties,
Ohio cities and school districts, Pennsylvania municipalities, Michigan cities). Local
jurisdictions are not modelled individually.

The `GET /api/payroll/states` catalog returns the same information.

| State | Method | Deductions / exemptions | Other items (employee / employer) | SUTA base | Year | Source |
|---|---|---|---|---|---|---|
| **AK** Alaska | No wage income tax | none | AK employee unemployment insurance 0.50% to 54,200 | 54,200 | 2026 | Alaska DOL Employment Security Tax; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **AL** Alabama | Progressive 2.00%–5.00% (3 brackets) Standard deduction phases down with income; the maximum is used. | std ded 3,000 / 8,500; base exemption 1,500 / 3,000; 1,000 per allowance | — | 8,000 | 2026 | Alabama Withholding Tax Tables and Instructions; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **AR** Arkansas | Progressive 2.00%–3.90% (2 brackets) Arkansas gives a $29 per-exemption credit rather than an exemption amount; not modelled. | std ded 2,470 / 4,940 | — | 7,000 | 2026 | Arkansas Withholding Tax Formula Method (2026 std deduction $2,470); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **AZ** Arizona | Employee-elected rate (default 2.0%) Employee elects a rate on Form A-4 (0.5%–3.5%); 2.0% is the default when none is on file. Set employee.state_rate_override. The 2.5% flat income tax is what the election approximates. | none | — | 8,000 | 2026 | Arizona Form A-4; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **CA** California | Progressive (DE 44 Method B) + SDI — dedicated engine | see engine | see engine | 7,000 | 2026-approximate | app/services/state_tax/ca.py |
| **CO** Colorado | Flat 4.40% Employers with 9 or fewer employees owe no employer FAMLI share. | std ded 16,100 / 32,200 | CO FAMLI (employee) 0.44% to 176,100; CO FAMLI (employer) 0.44% to 176,100 | 30,600 | 2026 | Colorado DR 1098 / DR 0004; FAMLI 0.88% for 2026 split 50/50 (famli.colorado.gov); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **CT** Connecticut | Progressive 2.00%–6.99% (7 brackets) Personal exemption phases out above $30k/$48k; the full amount is used. Withholding codes A–F map to filing status here. | base exemption 15,000 / 24,000 | CT Paid Leave 0.50% to 176,100 | 27,000 | 2026 | Connecticut Circular CT; CT Paid Leave 0.5% (ctpaidleave.org); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **DC** District of Columbia | Progressive 4.00%–10.75% (7 brackets) | std ded 16,100 / 32,200 | DC Paid Family Leave (employer) 0.75% | 9,000 | 2026 | DC OTR withholding instructions (FR-230); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **DE** Delaware | Progressive 0.00%–6.60% (7 brackets) Delaware gives a $110 per-exemption credit; not modelled. | std ded 3,250 / 6,500 | DE Paid Leave (employee) 0.40% to 176,100; DE Paid Leave (employer) 0.40% to 176,100 | 14,500 | 2026 | Delaware Withholding Tax Tables; Paid Leave 0.8% (employer may pass up to half to employees); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **FL** Florida | No wage income tax | none | — | 7,000 | 2026 | Florida DOR reemployment tax; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **GA** Georgia | Flat 4.99% Allowances = dependent allowances on Form G-4 ($5,000 each). | std ded 15,000 / 30,000; 5,000 per allowance | — | 9,500 | 2026 | Georgia Employer's Tax Guide, revised 2026 (4.99%); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **HI** Hawaii | Progressive 1.40%–11.00% (12 brackets) TDI employee share is capped weekly; modelled as an annual wage base ($1,500/week × 52). | std ded 4,400 / 8,800; 1,144 per allowance | HI Temporary Disability Insurance 0.50% to 78,000 | 64,500 | 2026 | Hawaii Booklet A; TDI 0.5% of weekly wages, max $7.50/week in 2026 (DLIR); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **IA** Iowa | Flat 3.80% | std ded 16,100 / 32,200 | — | 20,400 | 2026 | Iowa Withholding Formula (3.8% flat); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **ID** Idaho | Flat 5.30% | std ded 16,100 / 32,200 | — | 58,300 | 2026 | Idaho Table for Percentage Computation Method; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **IL** Illinois | Flat 4.95% Allowances = IL-W-4 line 1 count × $2,925 (line 2 additional allowances are $1,000 each — add them as allowances at the reduced value or use extra withholding). | 2,925 per allowance | — | 14,250 | 2026 | Illinois Booklet IL-700-T (2026: $2,925 exemption allowance); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **IN** Indiana | Flat 2.95% County income tax (0.5%–3%) is required: set employee.local_tax_rate to the county rate. | 1,000 per allowance | — | 9,500 | 2026 | Indiana Departmental Notice #1 (2026: 2.95%); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **KS** Kansas | Progressive 5.20%–5.58% (2 brackets) | std ded 3,605 / 8,240; base exemption 9,160 / 18,320; 2,320 per allowance | — | 15,100 | 2026 | Kansas Withholding Tax Guide (KW-100); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **KY** Kentucky | Flat 3.50% | std ded 3,360 / 3,360 | — | 12,000 | 2026 | Kentucky Withholding Tax Formula (2026: 3.5% per HB 1, std deduction $3,360); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **LA** Louisiana | Flat 3.00% | std ded 12,875 / 25,750 | — | 7,000 | 2026 | Louisiana R-1300 (3% flat); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MA** Massachusetts | Flat 5.00% 4% surtax on income over $1M is not withheld here. Employers under 25 employees owe no employer PFML share (0.46% total). | base exemption 4,400 / 8,800 | MA PFML (employee) 0.46% to 176,100; MA PFML (employer) 0.42% to 176,100 | 15,000 | 2026 | Massachusetts Circular M; PFML 0.88% for 2026, 25+ employees (mass.gov); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MD** Maryland | Progressive 2.00%–6.50% (10 brackets) County tax (2.25%–3.2%) is required — set employee.local_tax_rate. Standard deduction is 15% of wages within a range; the maximum is used. | std ded 3,350 / 6,700; 3,200 per allowance | — | 8,500 | 2026 | Maryland Employer Withholding Guide (2026: new 6.25% / 6.5% brackets, std deduction $3,350/$6,700); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **ME** Maine | Progressive 5.80%–7.15% (3 brackets) | std ded 15,300 / 30,600; 5,300 per allowance | — | 12,000 | 2026 | Maine Revenue Services 2026 Withholding Tables (exemption $5,300; std deduction $15,300/$30,600); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MI** Michigan | Flat 4.25% Cities with an income tax (Detroit, Grand Rapids…) — use employee.local_tax_rate. | 5,900 per allowance | — | 9,500 | 2026 | Michigan Income Tax Withholding Guide (2026 exemption $5,900); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MN** Minnesota | Progressive 5.35%–9.85% (4 brackets) Small employers pay a reduced 0.66% total Paid Leave premium. | std ded 15,300 / 30,600; 5,300 per allowance | MN Paid Leave (employee) 0.44% to 176,100; MN Paid Leave (employer) 0.44% to 176,100 | 44,000 | 2026 | Minnesota Income Tax Withholding Instruction Booklet; Paid Leave 0.88% from 2026-01-01 split 50/50 (pl.mn.gov); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MO** Missouri | Progressive 0.00%–4.70% (8 brackets) | std ded 16,100 / 32,200 | — | 9,000 | 2026 | Missouri Employer's Tax Guide (Form MO-W-4); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MS** Mississippi | Progressive 0.00%–4.00% (2 brackets) | std ded 2,300 / 4,600; base exemption 6,000 / 12,000; 1,500 per allowance | — | 14,000 | 2026 | Mississippi Withholding Tax Tables (2026: 4.0%, stepping toward 3% by 2030); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **MT** Montana | Progressive 4.70%–5.65% (2 brackets) | std ded 16,100 / 32,200 | — | 47,300 | 2026 | Montana Withholding Tax Guide (2026: 5.65% top rate above $47,500; 5.4% in 2027); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NC** North Carolina | Flat 3.99% | std ded 12,750 / 25,500 | — | 34,200 | 2026 | NC-30 Withholding Tables (2026: 3.99%); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **ND** North Dakota | Progressive 0.00%–2.50% (3 brackets) | std ded 16,100 / 32,200 | — | 46,600 | 2026 | North Dakota Income Tax Withholding Rates and Instructions; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NE** Nebraska | Progressive 2.46%–4.55% (3 brackets) Nebraska's $176 per-exemption credit is not modelled. | std ded 8,850 / 17,700 | — | 9,000 | 2026 | Nebraska Circular EN (2026: 4.55% top rate, 3.99% by 2027); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) (base $9,000; $24,000 for the highest-rate employers) |
| **NH** New Hampshire | No wage income tax No tax on wages (interest & dividends tax repealed 2025). | none | — | 14,000 | 2026 | NH Employment Security; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NJ** New Jersey | Progressive 1.40%–10.75% (7 brackets) Rate Table A for single, B for married/head of household. | 1,000 per allowance | NJ unemployment + workforce (employee) 0.43% to 44,800; NJ disability insurance (employee) 0.19% to 171,100; NJ family leave insurance 0.23% to 171,100 | 44,800 | 2026 | New Jersey NJ-WT (Rate Tables A / B); NJDOL 2026 rates: UI/WF wage base $44,800 (max $190.40), TDI 0.19% + FLI 0.23% on $171,100; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NM** New Mexico | Progressive 1.50%–5.90% (6 brackets) | std ded 16,100 / 32,200 | — | 34,800 | 2026 | New Mexico FYI-104; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NV** Nevada | No wage income tax | none | — | 43,700 | 2026 | Nevada DETR; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **NY** New York | Progressive (NYS-50-T) + SDI/PFL — dedicated engine | see engine | see engine | 12,800 | 2026-approximate | app/services/state_tax/ny.py |
| **OH** Ohio | Progressive 0.00%–2.75% (2 brackets) Municipal income tax (RITA / CCA cities) and school district tax — use employee.local_tax_rate. | 2,400 per allowance | — | 9,000 | 2026 | Ohio Employer Withholding Tables (2026: flat 2.75% above $26,050); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **OK** Oklahoma | Progressive 0.00%–4.50% (4 brackets) | std ded 6,350 / 12,700; 1,000 per allowance | — | 25,000 | 2026 | Oklahoma Income Tax Withholding Tables (2026: brackets collapsed to three, 4.5% top); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **OR** Oregon | Progressive + statewide transit tax — dedicated engine | see engine | see engine | 54,300 | 2026-approximate | app/services/state_tax/oregon.py |
| **PA** Pennsylvania | Flat 3.07% No deductions or exemptions. Local EIT (municipal + school) — use employee.local_tax_rate. | none | PA employee unemployment contribution 0.07% | 10,000 | 2026 | PA Employer Withholding Guide (REV-415); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **RI** Rhode Island | Progressive 3.75%–5.99% (3 brackets) Brackets are the same for every filing status. Deduction/exemption phase out at high income; full amounts used. | std ded 11,200 / 22,400; 5,250 per allowance | RI Temporary Disability Insurance 1.10% to 100,000 | 30,800 | 2026 | Rhode Island Withholding Tax Booklet; RI DLT 2026: TDI 1.1% on $100,000, UI base $30,800; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets |
| **SC** South Carolina | Progressive 0.00%–6.00% (3 brackets) Top rate is legislated to step; check mid-year. | std ded 16,100 / 32,200; 4,930 per allowance | — | 14,000 | 2026 | South Carolina Withholding Tax Tables (WH-1603F); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **SD** South Dakota | No wage income tax | none | — | 15,000 | 2026 | SD DOL; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **TN** Tennessee | No wage income tax | none | — | 7,000 | 2026 | TN DOL; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) (2026 base expected, not confirmed) |
| **TX** Texas | No wage income tax | none | — | 9,000 | 2026 | TWC; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **UT** Utah | Flat 4.45% Utah's withholding allowance credit is not modelled; the flat rate applies to taxable wages. | none | — | 50,700 | 2026 | Utah Publication 14 (4.45% for pay periods from 2026-06-01; 4.5% before); 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **VA** Virginia | Progressive 2.00%–5.75% (4 brackets) | std ded 8,750 / 17,500; 930 per allowance | — | 8,000 | 2026 | Virginia Employer Withholding Instructions (2026 std deduction $8,750/$17,500); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **VT** Vermont | Progressive 3.35%–8.75% (4 brackets) | std ded 7,650 / 15,300; 5,300 per allowance | — | 15,400 | 2026 | Vermont Income Tax Withholding Instructions; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **WA** Washington | No income tax; PFML, WA Cares, L&I — dedicated engine | see engine | see engine | 72,800 | 2026-approximate | app/services/state_tax/wa.py |
| **WI** Wisconsin | Progressive 3.50%–7.65% (4 brackets) Standard deduction phases out with income; the maximum is used. | std ded 13,960 / 25,840; 700 per allowance | — | 14,000 | 2026 | Wisconsin Publication W-166 (2026 std deduction $13,960/$25,840); Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **WV** West Virginia | Progressive 2.22%–4.82% (5 brackets) | 2,000 per allowance | — | 9,500 | 2026 | West Virginia Employer's Withholding Tax Tables; Tax Foundation, 2026 State Individual Income Tax Rates and Brackets; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |
| **WY** Wyoming | No wage income tax | none | — | 33,800 | 2026 | WY DWS; 2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02) |

## Bracket schedules (single filer, annual, marginal)

- **AL**: 2% from 0; 4% from 500; 5% from 3,000
- **AR**: 2% from 0; 3.90% from 4,600
- **CT**: 2% from 0; 4.50% from 10,000; 5.50% from 50,000; 6% from 100,000; 6.50% from 200,000; 6.90% from 250,000; 6.99% from 500,000
- **DC**: 4% from 0; 6% from 10,000; 6.50% from 40,000; 8.50% from 60,000; 9.25% from 250,000; 9.75% from 500,000; 10.75% from 1,000,000
- **DE**: 0% from 0; 2.20% from 2,000; 3.90% from 5,000; 4.80% from 10,000; 5.20% from 20,000; 5.55% from 25,000; 6.60% from 60,000
- **HI**: 1.40% from 0; 3.20% from 9,600; 5.50% from 14,400; 6.40% from 19,200; 6.80% from 24,000; 7.20% from 36,000; 7.60% from 48,000; 7.90% from 125,000; 8.25% from 175,000; 9% from 225,000; 10% from 275,000; 11% from 325,000
- **KS**: 5.20% from 0; 5.58% from 23,000
- **MD**: 2% from 0; 3% from 1,000; 4% from 2,000; 4.75% from 3,000; 5% from 100,000; 5.25% from 125,000; 5.50% from 150,000; 5.75% from 250,000; 6.25% from 500,000; 6.50% from 1,000,000
- **ME**: 5.80% from 0; 6.75% from 27,400; 7.15% from 64,850
- **MN**: 5.35% from 0; 6.80% from 33,310; 7.85% from 109,430; 9.85% from 203,150
- **MO**: 0% from 0; 2% from 1,348; 2.50% from 2,696; 3% from 4,044; 3.50% from 5,392; 4% from 6,740; 4.50% from 8,088; 4.70% from 9,436
- **MS**: 0% from 0; 4% from 10,000
- **MT**: 4.70% from 0; 5.65% from 47,500
- **ND**: 0% from 0; 1.95% from 48,475; 2.50% from 244,825
- **NE**: 2.46% from 0; 3.51% from 4,130; 4.55% from 24,760
- **NJ**: 1.40% from 0; 1.75% from 20,000; 3.50% from 35,000; 5.53% from 40,000; 6.37% from 75,000; 8.97% from 500,000; 10.75% from 1,000,000
- **NM**: 1.50% from 0; 3.20% from 5,500; 4.30% from 16,500; 4.70% from 33,500; 4.90% from 66,500; 5.90% from 210,000
- **OH**: 0% from 0; 2.75% from 26,050
- **OK**: 0% from 0; 2.50% from 3,750; 3.50% from 4,900; 4.50% from 7,200
- **RI**: 3.75% from 0; 4.75% from 82,050; 5.99% from 186,450
- **SC**: 0% from 0; 3% from 3,640; 6% from 18,230
- **VA**: 2% from 0; 3% from 3,000; 5% from 5,000; 5.75% from 17,000
- **VT**: 3.35% from 0; 6.60% from 49,400; 7.60% from 119,700; 8.75% from 249,700
- **WI**: 3.50% from 0; 4.40% from 15,110; 5.30% from 51,950; 7.65% from 332,720
- **WV**: 2.22% from 0; 2.96% from 10,000; 3.33% from 25,000; 4.44% from 40,000; 4.82% from 60,000

Married / head-of-household schedules are in `tables.py` (married defaults to doubled thresholds where the state does that).
