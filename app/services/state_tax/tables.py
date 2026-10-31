# ============================================================================
# State withholding parameters — every state + DC, as data.
# ----------------------------------------------------------------------------
# One StateSpec per jurisdiction drives TableEngine (table_engine.py):
#
#   method      none      no wage income tax (AK FL NV NH SD TN TX WY; WA has
#                         its own engine for PFML / Cares / L&I)
#               flat      one rate on annual taxable wages after deductions
#               brackets  progressive marginal schedule per filing status
#   std_deduction   annual amount subtracted per filing status
#   exemption       annual amount per state W-4 allowance (employee.state_allowances)
#   base_exemption  annual amount every employee gets regardless of allowances
#   employee_items / employer_items
#                   flat-rate "other" lines (SDI, TDI, PFML, UI) with an annual
#                   wage-base cap (None = no cap)
#   default_rate    AZ-style elected percentage when the employee sets none
#   suta_wage_base  employer SUTA taxable wage base
#
# FIGURES ARE THE 2026 PUBLISHED VALUES (verified 2026-09-03 against the
# state publications and the Tax Foundation / SUI-base compilations named in
# each `source`), simplified to the percentage-method structure. States that publish
# only wage-bracket tables are modelled from their formula equivalent.
# Verify against each state's current withholding publication (named in
# `source`) before relying on these for actual filings — see
# docs/state-withholding.md. Local taxes (county / city / school district)
# are NOT modelled per jurisdiction; employee.local_tax_rate applies a flat
# local percentage where one is needed (IN counties, MD counties, OH cities,
# PA municipalities, MI cities, KY/AL occupational taxes ...).
# ============================================================================

from dataclasses import dataclass, field
from decimal import Decimal


def D(v) -> Decimal:
    return Decimal(str(v))


@dataclass(frozen=True)
class OtherItem:
    label: str
    rate: Decimal  # of gross
    wage_base: Decimal | None = None  # annual cap on wages the rate applies to


@dataclass(frozen=True)
class StateSpec:
    code: str
    name: str
    method: str  # none | flat | brackets
    suta_wage_base: Decimal
    flat_rate: Decimal = Decimal("0")
    # {"single": [(lower, rate), ...], "married": [...], "head_of_household": [...]}
    brackets: dict = field(default_factory=dict)
    std_deduction: dict = field(default_factory=dict)  # per filing status, annual
    exemption: Decimal = Decimal("0")  # per allowance, annual
    base_exemption: dict = field(default_factory=dict)  # per filing status, annual
    employee_items: tuple = ()
    employer_items: tuple = ()
    default_rate: Decimal | None = None  # employee-elected % states (AZ)
    year: str = "2025"
    source: str = ""
    notes: str = ""


def _sd(single, married, hoh=None):
    return {
        "single": D(single),
        "married": D(married),
        "head_of_household": D(hoh if hoh is not None else single),
    }


def _br(single, married=None, hoh=None):
    """Brackets as [(lower, rate%)]; married defaults to doubled single thresholds."""

    def conv(rows):
        return [(D(lo), D(r) / Decimal("100")) for lo, r in rows]

    s = conv(single)
    m = conv(married) if married else [(lo * 2, r) for lo, r in s]
    h = conv(hoh) if hoh else s
    return {"single": s, "married": m, "head_of_household": h}


# Social Security wage base — several PFML programs cap at it.
SS_BASE = D(176100)

STATES: dict[str, StateSpec] = {}


def _add(spec: StateSpec):
    STATES[spec.code] = spec


# --- No wage income tax ------------------------------------------------------
TF = "Tax Foundation, 2026 State Individual Income Tax Rates and Brackets"
SUI = "2026 SUI wage bases (Nextep chart of state releases as of 2026-01-02)"

_add(
    StateSpec(
        "AK",
        "Alaska",
        "none",
        D(54200),
        employee_items=(
            OtherItem("AK employee unemployment insurance", D("0.005"), D(54200)),
        ),
        year="2026",
        source=f"Alaska DOL Employment Security Tax; {SUI}",
    )
)
_add(
    StateSpec(
        "FL",
        "Florida",
        "none",
        D(7000),
        year="2026",
        source=f"Florida DOR reemployment tax; {SUI}",
    )
)
_add(
    StateSpec(
        "NV",
        "Nevada",
        "none",
        D(43700),
        year="2026",
        source="Nevada DETR Employment Security Division, UI Tax (detr.nv.gov/Page/UI_Tax): 2026 taxable wage base $43,700 ($45,400 for 2027). Verified 2026-10-31.",
    )
)
_add(
    StateSpec(
        "NH",
        "New Hampshire",
        "none",
        D(14000),
        year="2026",
        source="NH Employment Security, Employer Claims & Taxes (nhes.nh.gov/services/employers/claimtax.htm): taxable wage base $14,000 (statutory, RSA 282-A:4). Verified 2026-10-31.",
        notes="No tax on wages (interest & dividends tax repealed 2025).",
    )
)
_add(
    StateSpec(
        "SD",
        "South Dakota",
        "none",
        D(15000),
        year="2026",
        source="SD Department of Labor & Regulation, Reemployment Assistance for businesses (dlr.sd.gov/ra/businesses): 2026 taxable wage base $15,000; 2026 rate notices mailed 2025-10-31. Verified 2026-10-31.",
        notes="SD added a 0.08% employer administrative fee on taxable wages for 2026 (not modelled).",
    )
)
_add(
    StateSpec(
        "TN",
        "Tennessee",
        "none",
        D(7000),
        year="2026",
        source="TN Department of Labor & Workforce Development, UI Tax Rates (tn.gov/workforce/employers/.../unemployment-insurance-tax/tax-rates.html): taxable wage base $7,000 for calendar 2026 (the base moves with the trust-fund balance, so re-check each January). Verified 2026-10-31.",
    )
)
_add(
    StateSpec(
        "TX",
        "Texas",
        "none",
        D(9000),
        year="2026",
        source="Texas Workforce Commission, Your Tax Rates (twc.texas.gov/programs/unemployment-tax/your-tax-rates): taxable wage base $9,000 per employee per year. Verified 2026-10-31.",
    )
)
_add(
    StateSpec(
        "WY",
        "Wyoming",
        "none",
        D(33800),
        year="2026",
        source="Wyoming DWS, Unemployment Taxable Wage Base (dws.wyo.gov/dws-division/unemployment-insurance/wyui/unemployment-taxable-wage-base/): 2026 $33,800; 2027 $34,900. Verified 2026-10-31.",
    )
)

# --- Flat-rate states ----------------------------------------------------------
_add(
    StateSpec(
        "AZ",
        "Arizona",
        "flat",
        D(8000),
        default_rate=D("0.020"),
        year="2026",
        source=f"Arizona Form A-4; {TF}; {SUI}",
        notes="Employee elects a rate on Form A-4 (0.5%–3.5%); 2.0% is the default when none is on file. Set employee.state_rate_override. The 2.5% flat income tax is what the election approximates.",
    )
)
_add(
    StateSpec(
        "CO",
        "Colorado",
        "flat",
        D(30600),
        flat_rate=D("0.044"),
        std_deduction=_sd(16100, 32200, 24150),
        employee_items=(OtherItem("CO FAMLI (employee)", D("0.0044"), SS_BASE),),
        employer_items=(OtherItem("CO FAMLI (employer)", D("0.0044"), SS_BASE),),
        year="2026",
        source=f"Colorado DR 1098 / DR 0004; FAMLI 0.88% for 2026 split 50/50 (famli.colorado.gov); {TF}; {SUI}",
        notes="Employers with 9 or fewer employees owe no employer FAMLI share.",
    )
)
_add(
    StateSpec(
        "GA",
        "Georgia",
        "flat",
        D(9500),
        flat_rate=D("0.0499"),
        std_deduction=_sd(15000, 30000, 15000),
        exemption=D(5000),
        year="2026",
        source=f"Georgia Employer's Tax Guide, revised 2026 (4.99%); {SUI}",
        notes="Allowances = dependent allowances on Form G-4 ($5,000 each).",
    )
)
_add(
    StateSpec(
        "ID",
        "Idaho",
        "flat",
        D(58300),
        flat_rate=D("0.053"),
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"Idaho Table for Percentage Computation Method; {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "IL",
        "Illinois",
        "flat",
        D(14250),
        flat_rate=D("0.0495"),
        exemption=D(2925),
        year="2026",
        source=f"Illinois Booklet IL-700-T (2026: $2,925 exemption allowance); {SUI}",
        notes="Allowances = IL-W-4 line 1 count × $2,925 (line 2 additional allowances are $1,000 each — add them as allowances at the reduced value or use extra withholding).",
    )
)
_add(
    StateSpec(
        "IN",
        "Indiana",
        "flat",
        D(9500),
        flat_rate=D("0.0295"),
        exemption=D(1000),
        year="2026",
        source=f"Indiana Departmental Notice #1 (2026: 2.95%); {SUI}",
        notes="County income tax (0.5%–3%) is required: set employee.local_tax_rate to the county rate.",
    )
)
_add(
    StateSpec(
        "IA",
        "Iowa",
        "flat",
        D(20400),
        flat_rate=D("0.038"),
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"Iowa Withholding Formula (3.8% flat); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "KY",
        "Kentucky",
        "flat",
        D(12000),
        flat_rate=D("0.035"),
        std_deduction=_sd(3360, 3360, 3360),
        year="2026",
        source=f"Kentucky Withholding Tax Formula (2026: 3.5% per HB 1, std deduction $3,360); {SUI}",
    )
)
_add(
    StateSpec(
        "LA",
        "Louisiana",
        "flat",
        D(7000),
        flat_rate=D("0.030"),
        std_deduction=_sd(12875, 25750, 12875),
        year="2026",
        source=f"Louisiana R-1300 (3% flat); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "MA",
        "Massachusetts",
        "flat",
        D(15000),
        flat_rate=D("0.050"),
        base_exemption=_sd(4400, 8800, 6800),
        employee_items=(OtherItem("MA PFML (employee)", D("0.0046"), SS_BASE),),
        employer_items=(OtherItem("MA PFML (employer)", D("0.0042"), SS_BASE),),
        year="2026",
        source=f"Massachusetts Circular M; PFML 0.88% for 2026, 25+ employees (mass.gov); {SUI}",
        notes="4% surtax on income over $1M is not withheld here. Employers under 25 employees owe no employer PFML share (0.46% total).",
    )
)
_add(
    StateSpec(
        "MI",
        "Michigan",
        "flat",
        D(9500),
        flat_rate=D("0.0425"),
        exemption=D(5900),
        year="2026",
        source=f"Michigan Income Tax Withholding Guide (2026 exemption $5,900); {SUI}",
        notes="Cities with an income tax (Detroit, Grand Rapids…) — use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "NC",
        "North Carolina",
        "flat",
        D(34200),
        flat_rate=D("0.0409"),
        std_deduction=_sd(12750, 12750, 19125),
        exemption=D(2500),
        year="2026",
        source="NC-30 2026 Income Tax Withholding Tables and Instructions for Employers (Web 11-25), Annualized Method p.19: 3.99% income tax rate + 0.1% = 4.09% withholding rate; N.C. standard deduction $12,750 (single, married, surviving spouse) / $19,125 (head of household); $2,500 per allowance (ncdor.gov/income-tax-withholding-tables-and-instructions-employers/open). SUI: NC DES 2026 taxable wage base $34,200 (des.nc.gov). Verified 2026-10-31.",
        notes="The withholding rate is 0.1 point above the 3.99% income tax rate by design (NC-30). Married uses the single deduction, not double. 2027: tax rate drops to 3.49% (S.L. 2026-41); expect a 3.59% NC-30 rate.",
    )
)
_add(
    StateSpec(
        "PA",
        "Pennsylvania",
        "flat",
        D(10000),
        flat_rate=D("0.0307"),
        employee_items=(
            OtherItem("PA employee unemployment contribution", D("0.0007"), None),
        ),
        year="2026",
        source="PA Department of Revenue, Employer Withholding (pa.gov/agencies/revenue/resources/tax-types-and-information/employer-withholding): flat 3.07% of compensation, no deductions or exemptions; Employer Withholding Guide REV-415. PA L&I UC Yearly Tax Highlights (pa.gov/agencies/dli/resources/for-employers-uc-tax-services/yearly-tax-highlights): 2026 employee UC withholding 0.07% of all gross wages (no cap); employer taxable wage base $10,000. Verified 2026-10-31.",
        notes="No deductions or exemptions. Local EIT (municipal + school) — use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "UT",
        "Utah",
        "flat",
        D(50700),
        flat_rate=D("0.0445"),
        year="2026",
        source="Utah Publication 14, Withholding Tax Guide (Rev. 4/26, effective for pay periods beginning on or after 2026-06-01), Schedule 7 Annual p.10: 4.45% of taxable wages minus a credit (files.tax.utah.gov/tax/forms/pubs/pub-14.pdf). SUI: Utah DWS, 'During 2026, the taxable wage base is $50,700.00' (jobs.utah.gov/ui/employer/Public/Questions/TaxRates.aspx). Verified 2026-10-31.",
        notes="SIMPLIFIED: Pub 14 subtracts a credit of $485 (single) / $970 (married) that phases out at 1.3% of annual wages over $9,348 / $18,696. The credit is not modelled: exact for single filers earning $46,656 or more and married filers earning $93,311 or more; below that the engine over-withholds by the remaining credit (at most $485 single / $970 married a year, e.g. married $50,000 → $563). Pay periods before 2026-06-01 used 4.5% (SB 60 made 4.45% retroactive for the tax year).",
    )
)

# --- Progressive states ---------------------------------------------------------
_add(
    StateSpec(
        "AL",
        "Alabama",
        "brackets",
        D(8000),
        brackets=_br([(0, 2), (500, 4), (3000, 5)], [(0, 2), (1000, 4), (6000, 5)]),
        std_deduction=_sd(3000, 8500, 5200),
        base_exemption=_sd(1500, 3000, 3000),
        exemption=D(1000),
        year="2026",
        source=f"Alabama Withholding Tax Tables and Instructions; {TF}; {SUI}",
        notes="Standard deduction phases down with income; the maximum is used.",
    )
)
_add(
    StateSpec(
        "AR",
        "Arkansas",
        "brackets",
        D(7000),
        brackets=_br([(0, 2), (4600, 3.9)], [(0, 2), (4600, 3.9)]),
        std_deduction=_sd(2470, 4940, 2470),
        year="2026",
        source=f"Arkansas Withholding Tax Formula Method (2026 std deduction $2,470); {TF}; {SUI}",
        notes="Arkansas gives a $29 per-exemption credit rather than an exemption amount; not modelled.",
    )
)
_add(
    StateSpec(
        "CT",
        "Connecticut",
        "brackets",
        D(27000),
        brackets=_br(
            [
                (0, 2),
                (10000, 4.5),
                (50000, 5.5),
                (100000, 6),
                (200000, 6.5),
                (250000, 6.9),
                (500000, 6.99),
            ],
            [
                (0, 2),
                (20000, 4.5),
                (100000, 5.5),
                (200000, 6),
                (400000, 6.5),
                (500000, 6.9),
                (1000000, 6.99),
            ],
            [
                (0, 2),
                (16000, 4.5),
                (80000, 5.5),
                (160000, 6),
                (320000, 6.5),
                (400000, 6.9),
                (800000, 6.99),
            ],
        ),
        base_exemption=_sd(15000, 24000, 19000),
        employee_items=(OtherItem("CT Paid Leave", D("0.005"), SS_BASE),),
        year="2026",
        source=f"Connecticut Circular CT; CT Paid Leave 0.5% (ctpaidleave.org); {TF}; {SUI}",
        notes="Personal exemption phases out above $30k/$48k; the full amount is used. Withholding codes A–F map to filing status here.",
    )
)
_add(
    StateSpec(
        "DE",
        "Delaware",
        "brackets",
        D(14500),
        brackets=_br(
            [
                (0, 0),
                (2000, 2.2),
                (5000, 3.9),
                (10000, 4.8),
                (20000, 5.2),
                (25000, 5.55),
                (60000, 6.6),
            ],
            [
                (0, 0),
                (2000, 2.2),
                (5000, 3.9),
                (10000, 4.8),
                (20000, 5.2),
                (25000, 5.55),
                (60000, 6.6),
            ],
        ),
        std_deduction=_sd(3250, 6500, 3250),
        employee_items=(OtherItem("DE Paid Leave (employee)", D("0.004"), SS_BASE),),
        employer_items=(OtherItem("DE Paid Leave (employer)", D("0.004"), SS_BASE),),
        year="2026",
        source=f"Delaware Withholding Tax Tables; Paid Leave 0.8% (employer may pass up to half to employees); {TF}; {SUI}",
        notes="Delaware gives a $110 per-exemption credit; not modelled.",
    )
)
_add(
    StateSpec(
        "DC",
        "District of Columbia",
        "brackets",
        D(9000),
        brackets=_br(
            [
                (0, 4),
                (10000, 6),
                (40000, 6.5),
                (60000, 8.5),
                (250000, 9.25),
                (500000, 9.75),
                (1000000, 10.75),
            ],
            [
                (0, 4),
                (10000, 6),
                (40000, 6.5),
                (60000, 8.5),
                (250000, 9.25),
                (500000, 9.75),
                (1000000, 10.75),
            ],
        ),
        std_deduction=_sd(16100, 32200, 24150),
        employer_items=(
            OtherItem("DC Paid Family Leave (employer)", D("0.0075"), None),
        ),
        year="2026",
        source=f"DC OTR withholding instructions (FR-230); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "HI",
        "Hawaii",
        "brackets",
        D(64500),
        brackets=_br(
            [
                (0, 1.4),
                (9600, 3.2),
                (14400, 5.5),
                (19200, 6.4),
                (24000, 6.8),
                (36000, 7.2),
                (48000, 7.6),
                (125000, 7.9),
                (175000, 8.25),
                (225000, 9),
                (275000, 10),
                (325000, 11),
            ],
            [
                (0, 1.4),
                (19200, 3.2),
                (28800, 5.5),
                (38400, 6.4),
                (48000, 6.8),
                (72000, 7.2),
                (96000, 7.6),
                (250000, 7.9),
                (350000, 8.25),
                (450000, 9),
                (550000, 10),
                (650000, 11),
            ],
        ),
        std_deduction=_sd(4400, 8800, 6424),
        exemption=D(1144),
        employee_items=(
            OtherItem("HI Temporary Disability Insurance", D("0.005"), D(78000)),
        ),
        year="2026",
        source=f"Hawaii Booklet A; TDI 0.5% of weekly wages, max $7.50/week in 2026 (DLIR); {TF}; {SUI}",
        notes="TDI employee share is capped weekly; modelled as an annual wage base ($1,500/week × 52).",
    )
)
_add(
    StateSpec(
        "KS",
        "Kansas",
        "brackets",
        D(15100),
        brackets=_br([(0, 5.2), (23000, 5.58)], [(0, 5.2), (46000, 5.58)]),
        std_deduction=_sd(3605, 8240, 6180),
        base_exemption=_sd(9160, 18320, 9160),
        exemption=D(2320),
        year="2026",
        source=f"Kansas Withholding Tax Guide (KW-100); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "ME",
        "Maine",
        "brackets",
        D(12000),
        brackets=_br(
            [(0, 5.8), (27400, 6.75), (64850, 7.15)],
            [(0, 5.8), (54850, 6.75), (129750, 7.15)],
            [(0, 5.8), (41100, 6.75), (97300, 7.15)],
        ),
        std_deduction=_sd(15300, 30600, 22950),
        exemption=D(5300),
        year="2026",
        source=f"Maine Revenue Services 2026 Withholding Tables (exemption $5,300; std deduction $15,300/$30,600); {SUI}",
    )
)
_add(
    StateSpec(
        "MD",
        "Maryland",
        "brackets",
        D(8500),
        brackets=_br(
            [
                (0, 2),
                (1000, 3),
                (2000, 4),
                (3000, 4.75),
                (100000, 5),
                (125000, 5.25),
                (150000, 5.5),
                (250000, 5.75),
                (500000, 6.25),
                (1000000, 6.5),
            ],
            [
                (0, 2),
                (1000, 3),
                (2000, 4),
                (3000, 4.75),
                (150000, 5),
                (175000, 5.25),
                (225000, 5.5),
                (300000, 5.75),
                (600000, 6.25),
                (1200000, 6.5),
            ],
        ),
        std_deduction=_sd(3350, 6700, 3350),
        exemption=D(3200),
        year="2026",
        source=f"Maryland Employer Withholding Guide (2026: new 6.25% / 6.5% brackets, std deduction $3,350/$6,700); {TF}; {SUI}",
        notes="County tax (2.25%–3.2%) is required — set employee.local_tax_rate. Standard deduction is 15% of wages within a range; the maximum is used.",
    )
)
_add(
    StateSpec(
        "MN",
        "Minnesota",
        "brackets",
        D(44000),
        brackets=_br(
            [(0, 5.35), (33310, 6.8), (109430, 7.85), (203150, 9.85)],
            [(0, 5.35), (48700, 6.8), (193480, 7.85), (337930, 9.85)],
            [(0, 5.35), (41010, 6.8), (164790, 7.85), (270060, 9.85)],
        ),
        std_deduction=_sd(15300, 30600, 22950),
        exemption=D(5300),
        employee_items=(OtherItem("MN Paid Leave (employee)", D("0.0044"), SS_BASE),),
        employer_items=(OtherItem("MN Paid Leave (employer)", D("0.0044"), SS_BASE),),
        year="2026",
        source=f"Minnesota Income Tax Withholding Instruction Booklet; Paid Leave 0.88% from 2026-01-01 split 50/50 (pl.mn.gov); {TF}; {SUI}",
        notes="Small employers pay a reduced 0.66% total Paid Leave premium.",
    )
)
_add(
    StateSpec(
        "MS",
        "Mississippi",
        "brackets",
        D(14000),
        brackets=_br([(0, 0), (10000, 4.0)], [(0, 0), (10000, 4.0)]),
        std_deduction=_sd(2300, 4600, 3400),
        base_exemption=_sd(6000, 12000, 9500),
        exemption=D(1500),
        year="2026",
        source=f"Mississippi Withholding Tax Tables (2026: 4.0%, stepping toward 3% by 2030); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "MO",
        "Missouri",
        "brackets",
        D(9000),
        brackets=_br(
            [
                (0, 0),
                (1348, 2),
                (2696, 2.5),
                (4044, 3),
                (5392, 3.5),
                (6740, 4),
                (8088, 4.5),
                (9436, 4.7),
            ],
            [
                (0, 0),
                (1348, 2),
                (2696, 2.5),
                (4044, 3),
                (5392, 3.5),
                (6740, 4),
                (8088, 4.5),
                (9436, 4.7),
            ],
        ),
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"Missouri Employer's Tax Guide (Form MO-W-4); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "MT",
        "Montana",
        "brackets",
        D(47300),
        brackets=_br(
            [(0, 4.7), (47500, 5.65)],
            [(0, 4.7), (95000, 5.65)],
            [(0, 4.7), (71250, 5.65)],
        ),
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"Montana Withholding Tax Guide (2026: 5.65% top rate above $47,500; 5.4% in 2027); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "NE",
        "Nebraska",
        "brackets",
        D(9000),
        brackets=_br(
            [
                (0, 0),
                (3430, 2.26),
                (6710, 3.22),
                (21810, 4.21),
                (31610, 4.35),
                (40130, 4.48),
                (75370, 4.60),
            ],
            [
                (0, 0),
                (8190, 2.26),
                (13010, 3.22),
                (32400, 4.21),
                (50400, 4.35),
                (62530, 4.48),
                (82920, 4.60),
            ],
        ),
        exemption=D(2440),
        year="2026",
        source="Nebraska Circular EN, Income Tax Withholding Percentage Method Tables for wages paid on or after 2026-01-01 (Rev. 11-2025), Table 7 Annual p.13 and Withholding Allowance Table p.9 ($2,440 per allowance) (revenue.nebraska.gov/sites/default/files/doc/business/Cir_En_2025/2026_percent.pdf, .../2026_instruct.pdf). SUI: NDOL 2026 taxable wage base $9,000 ($24,000 for category-20 employers). Verified 2026-10-31.",
        notes="Head of household uses the single table (Circular EN). The 1.5%-of-wages minimum-withholding rule for employees claiming many allowances is not modelled. 2027 Circular EN (Rev. 8-2026) is already published: $2,510 per allowance, top rate 4.03%.",
    )
)
_add(
    StateSpec(
        "NJ",
        "New Jersey",
        "brackets",
        D(44800),
        brackets=_br(
            [
                (0, 1.5),
                (20000, 2.0),
                (35000, 3.9),
                (40000, 6.1),
                (75000, 7.0),
                (500000, 9.9),
                (1000000, 11.8),
            ],
            [
                (0, 1.5),
                (20000, 2.0),
                (50000, 2.7),
                (70000, 3.9),
                (80000, 6.1),
                (150000, 7.0),
                (500000, 9.9),
                (1000000, 11.8),
            ],
            [
                (0, 1.5),
                (20000, 2.0),
                (50000, 2.7),
                (70000, 3.9),
                (80000, 6.1),
                (150000, 7.0),
                (500000, 9.9),
                (1000000, 11.8),
            ],
        ),
        exemption=D(1000),
        employee_items=(
            OtherItem("NJ unemployment + workforce (employee)", D("0.00425"), D(44800)),
            OtherItem("NJ disability insurance (employee)", D("0.0019"), D(171100)),
            OtherItem("NJ family leave insurance", D("0.0023"), D(171100)),
        ),
        year="2026",
        source="NJ Division of Taxation, Tables for Percentage Method of Withholding, Rate A and Rate B, Annual Payroll Period (applicable to wages paid on and after 2020-10-01; still the tables NJ-WT Sept 2025 directs to), $1,000 per allowance (nj.gov/treasury/taxation/pdf/NJWithholdingRateTablesPercentageMethod.pdf). NJDOL 2026 rates (nj.gov/labor/ea/employer-services/rate-info): worker UI 0.3825% + WF/SWF 0.0425% = 0.425% to $44,800; TDI 0.19% and FLI 0.23% to $171,100; employer UI base $44,800. Verified 2026-10-31.",
        notes="Withholding uses the NJ-W4 Rate Tables, not the income-tax-return schedule: Rate A (single / married filing separately) and Rate B (married filing jointly, head of household, surviving spouse). Rate tables C, D and E, which an employee may elect on the NJ-W4 for two-income households, are not modelled. 2027: TDI/FLI 0.24% each to $177,100; UI base $46,400.",
    )
)
_add(
    StateSpec(
        "NM",
        "New Mexico",
        "brackets",
        D(34800),
        brackets=_br(
            [
                (0, 1.5),
                (5500, 3.2),
                (16500, 4.3),
                (33500, 4.7),
                (66500, 4.9),
                (210000, 5.9),
            ],
            [
                (0, 1.5),
                (8000, 3.2),
                (25000, 4.3),
                (50000, 4.7),
                (100000, 4.9),
                (315000, 5.9),
            ],
            [
                (0, 1.5),
                (8000, 3.2),
                (25000, 4.3),
                (50000, 4.7),
                (100000, 4.9),
                (315000, 5.9),
            ],
        ),
        std_deduction=_sd(8050, 16100, 12075),
        year="2026",
        source="New Mexico FYI-104 New Mexico Withholding Tax (Rev. 11/2025, for wages paid on or after 2026-01-01), Table 7 Annual p.7: zero bracket to $8,050 single / $16,100 married / $12,075 head of household, then 1.5%–5.9% (tax.newmexico.gov → Withholding Tax Rates → FYI-104 2026). SUI: NM DWS 2026 taxable wage base $34,800 (dws.nm.gov). Verified 2026-10-31.",
        notes="The zero bracket is modelled as the standard deduction; the published brackets then start at $0. No per-allowance amount (federal 2020+ W-4).",
    )
)
_add(
    StateSpec(
        "ND",
        "North Dakota",
        "brackets",
        D(46600),
        brackets=_br(
            [(0, 0), (57625, 1.95), (258450, 2.5)],
            [(0, 0), (57500, 1.95), (168525, 2.5)],
            [(0, 0), (78475, 1.95), (289675, 2.5)],
        ),
        year="2026",
        source="North Dakota Income Tax Withholding Rates & Instructions for wages paid in 2026, Section 2 (Forms W-4 for 2020 and after), Annual Percentage Method Tables p.45: single 0% to $57,625, 1.95% to $258,450, 2.5% above; married filing jointly $57,500 / $168,525; head of household $78,475 / $289,675; no deduction or allowance step (tax.nd.gov/sites/www/files/documents/forms/individual/2026-iit/2026-income-tax-withholding-rates-booklet.pdf). SUI: Job Service ND 2026 taxable wage base $46,600. Verified 2026-10-31.",
        notes="Section 2 (2020+ federal W-4) has no allowances, so state_allowances has no effect. Section 1 (pre-2020 W-4s: $5,044 per allowance and lower thresholds) is not modelled.",
    )
)
_add(
    StateSpec(
        "OH",
        "Ohio",
        "brackets",
        D(9000),
        brackets=_br(
            [(0, 1.6), (26050, 2.99), (100000, 3.4)],
            [(0, 1.6), (26050, 2.99), (100000, 3.4)],
        ),
        exemption=D(650),
        year="2026",
        source="Ohio Department of Taxation, Employer Withholding Taxes - Percentage Method, effective 2026-08-01: $12.50 per exemption per week ($650 a year); 1.600% to $500.96/week ($26,050 a year), 2.990% to $1,923.08/week ($100,000), 3.400% above (dam.assets.ohio.gov/image/upload/tax.ohio.gov/employer_withholding/2026%20Withholding%20Tables/WHT_PercentageMethod_2026.pdf). SUI: ODJFS 2026 taxable wage base $9,000. Verified 2026-10-31.",
        notes="Figures in force now (from 2026-08-01). Pay dates 2026-01-01 to 2026-07-31 used the 2025-10-01 tables: 1.775% / 2.990% / 3.640% at the same breaks and the same $650 exemption. Ohio publishes weekly through monthly tables; the annual breaks are 52 × the weekly ones (within a dollar). 2.75% is only the supplemental-wage rate. Municipal income tax (RITA / CCA cities) and school district tax — use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "OK",
        "Oklahoma",
        "brackets",
        D(25000),
        brackets=_br(
            [(0, 0), (3750, 2.5), (4900, 3.5), (7200, 4.5)],
            [(0, 0), (7500, 2.5), (9800, 3.5), (14400, 4.5)],
        ),
        std_deduction=_sd(6350, 12700, 6350),
        exemption=D(1000),
        year="2026",
        source="Oklahoma Income Tax Withholding Tables, Packet OW-2 (Revised 11-2025, effective 2026-01-01), Table 7 Annual p.9: single 0% to $10,100, 2.5% to $11,250, 3.5% to $13,550, 4.5% above; married $20,200 / $22,500 / $27,100; $1,000 per allowance p.6 (oklahoma.gov/.../withholding-tables/WHTables-2026.pdf). SUI: OESC 2026 taxable wage base $25,000 ($24,400 for 2027). Verified 2026-10-31.",
        notes="Modelled as a $6,350 / $12,700 deduction plus brackets at 3,750 / 4,900 / 7,200 (× 2 married), which reproduces Table 7 exactly. OW-2 has single and married tables only; head of household uses single.",
    )
)
_add(
    StateSpec(
        "RI",
        "Rhode Island",
        "brackets",
        D(30800),
        brackets=_br([(0, 3.75), (82050, 4.75), (186450, 5.99)]),
        exemption=D(1000),
        employee_items=(
            OtherItem("RI Temporary Disability Insurance", D("0.011"), D(100000)),
        ),
        year="2026",
        source="Rhode Island 2026 Withholding Tax Booklet, Percentage Method for the period beginning on or after 2026-01-01 (updated 11/04/2025) p.8-9: $1,000 per withholding exemption, no standard deduction; 3.75% to $82,050, 4.75% to $186,450, 5.99% above, all filing statuses (tax.ri.gov/sites/g/files/xkgbur541/files/2025-12/2026%20Withholding%20Tax%20Booklet.pdf). RI DLT 2026 UI & TDI Quick Reference: TDI/TCI 1.1% of the first $100,000; UI taxable wage base $30,800 ($32,300 for the highest-rate employers). Verified 2026-10-31.",
        notes="Brackets are the same for every filing status. SIMPLIFIED: the exemption amount is $0 once annual wages exceed $290,800 (booklet p.8); that cut-off is not modelled, so employees above it are under-withheld by 5.99% × $1,000 per exemption claimed.",
    )
)
_add(
    StateSpec(
        "SC",
        "South Carolina",
        "brackets",
        D(14000),
        brackets=_br(
            [(0, 0), (3640, 3), (18230, 6.0)], [(0, 0), (3640, 3), (18230, 6.0)]
        ),
        exemption=D(5000),
        year="2026",
        source="SC DOR WH-1603F, Formula for Computing South Carolina 2026 Withholding Tax (Rev. 11/4/25): $5,000 per personal allowance; standard deduction $0 with zero allowances, otherwise 10% of gross wages up to $7,500; 0% to $3,640, 3% to $18,230, 6% above (dor.sc.gov/sites/dor/files/forms/WH1603F_2026.pdf). SUI: SC DEW 2026 taxable wage base $14,000. Verified 2026-10-31.",
        notes="SIMPLIFIED: WH-1603F's standard deduction (10% of annual wages, max $7,500, only when one or more allowances are claimed) depends on wages and is not modelled. Exact for employees claiming zero allowances; an employee claiming allowances is over-withheld by 6% of that deduction (up to $450 a year; $300 at $50,000). Act 110 of 2026 rewrote the 2026 income tax (1.99% / 5.21%) but SCDOR has not revised the withholding formula; this is the formula in force.",
    )
)
_add(
    StateSpec(
        "VT",
        "Vermont",
        "brackets",
        D(15400),
        brackets=_br(
            [(0, 0), (3925, 3.35), (54675, 6.6), (126775, 7.6), (260225, 8.75)],
            [(0, 0), (11775, 3.35), (96475, 6.6), (216525, 7.6), (323825, 8.75)],
        ),
        exemption=D(5400),
        employer_items=(
            OtherItem("VT Child Care Contribution (employer)", D("0.0044"), None),
        ),
        year="2026",
        source="Vermont GB-1210-2026, 2026 Income Tax Withholding Instructions, Tables, and Charts (effective 2026-01-01 to 2026-12-31), Annual Payrolls table p.6: $5,400 per withholding allowance; single 0% to $3,925, 3.35% to $54,675, 6.6% to $126,775, 7.6% to $260,225, 8.75% above; married $11,775 / $96,475 / $216,525 / $323,825; Child Care Contribution 0.44% employer payroll tax p.4 (tax.vermont.gov/sites/tax/files/documents/GB-1210-2026.pdf). SUI: VT DOL, taxable wage base $15,400 effective 2026-01-01 (labor.vermont.gov/node/5551). Verified 2026-10-31.",
        notes="GB-1210 has single and married tables only; head of household uses single. The Child Care Contribution is an employer tax on all wages subject to Vermont withholding (no cap); an employer may deduct up to a quarter of it (0.11%) from the employee — that optional employee share is not modelled.",
    )
)
_add(
    StateSpec(
        "VA",
        "Virginia",
        "brackets",
        D(8000),
        brackets=_br(
            [(0, 2), (3000, 3), (5000, 5), (17000, 5.75)],
            [(0, 2), (3000, 3), (5000, 5), (17000, 5.75)],
        ),
        std_deduction=_sd(8750, 8750, 8750),
        exemption=D(930),
        year="2026",
        source="Virginia Income Tax Withholding Guide for Employers (2614086 Rev. 10/26), Formula for Computing Tax to be Withheld p.21, effective for wages paid after 2025-07-01: T = (G × P) − [$8,750 + E1 × $930 + E2 × $800]; 2% to $3,000, 3% to $5,000, 5% to $17,000, 5.75% above (tax.virginia.gov/sites/default/files/vatax-pdf/employer-withholding-instructions.pdf). SUI: VEC taxable wage base $8,000 (statutory). Verified 2026-10-31.",
        notes="The withholding formula deducts one $8,750 amount for every employee regardless of filing status (the $17,500 married figure is the income-tax-return deduction, used only in the wage-bracket tables). E2 (age 65 and over / blind, $800 each) is not modelled — add those as allowances at $930 or use extra withholding. 2027: the deduction rises to $9,200 / $18,400 (2026 special-session budget).",
    )
)
_add(
    StateSpec(
        "WV",
        "West Virginia",
        "brackets",
        D(9500),
        brackets=_br(
            [(0, 2.11), (7500, 2.81), (18750, 3.16), (30000, 4.22), (45000, 4.58)],
            [(0, 2.11), (7500, 2.81), (18750, 3.16), (30000, 4.22), (45000, 4.58)],
        ),
        exemption=D(2000),
        year="2026",
        source="West Virginia IT-100.2.A, Tables for Percentage Method of Withholding (March 2026, rates after SB 392's 5% cut retroactive to 2026-01-01), Two Earner / Two or More Jobs Table 5 Annual p.2: $2,000 per exemption; 2.11% to $7,500, 2.81% to $18,750, 3.16% to $30,000, 4.22% to $45,000, 4.58% above (tax.wv.gov/Documents/Withholding/it100.2a.pdf). SUI: W. Va. Code §21A-1A-28 taxable wage base $9,500. Verified 2026-10-31.",
        notes="The two-earner table is the default for every employee (IT-104). The optional one-earner table (same rates at $10,000 / $25,000 / $40,000 / $60,000), for a single, head-of-household or one-income married employee who checks the box on IT-104 line 5, is not modelled: such employees are over-withheld by up to $156 a year.",
    )
)
_add(
    StateSpec(
        "WI",
        "Wisconsin",
        "brackets",
        D(14000),
        brackets=_br(
            [(0, 3.54), (12760, 4.65), (25520, 5.30), (280950, 7.65)],
            [(0, 3.54), (12760, 4.65), (25520, 5.30), (280950, 7.65)],
        ),
        std_deduction=_sd("2835.60", "4606.40", "2835.60"),
        exemption=D(400),
        year="2026",
        source="Wisconsin Publication W-166, Withholding Tax Guide (1/26), Alternate Method of Withholding Wisconsin Income Tax p.25-26 (effective for withholding periods beginning on or after 2022-01-01, unchanged for 2026): deduction $6,702 single / $9,461 married phasing down (see notes), $400 per exemption; 3.54% to $12,760, 4.65% to $25,520, 5.30% to $280,950, 7.65% above, all statuses (revenue.wi.gov/DOR%20Publications/pb166.pdf). SUI: WI DWD 2026 taxable wage base $14,000. Verified 2026-10-31.",
        notes="SIMPLIFIED: W-166's deduction depends on wages — single $6,702 below $17,780 in annual wages, less 12% of wages over $17,780, $0 from $73,630; married $9,461 below $25,727, less 20% of wages over $25,727, $0 from $73,032. The engine uses the deduction at $50,000 of annual wages ($2,835.60 single / $4,606.40 married), so it is exact there and off by 12% (single) or 20% (married) of the distance from $50,000 × the marginal rate elsewhere: $10,000 away = about $64 a year single / $106 married, and employees at or above $73,630 / $73,032 are under-withheld by $150 / $244 a year. Head of household uses the single figures.",
    )
)

# States with a dedicated engine (kept here only for the catalog / SUTA base)
DEDICATED = {
    "CA": (
        "California",
        D(7000),
        "Progressive (DE 44 Method B) + SDI — dedicated engine",
    ),
    "NY": ("New York", D(12800), "Progressive (NYS-50-T) + SDI/PFL — dedicated engine"),
    "OR": (
        "Oregon",
        D(54300),
        "Progressive + statewide transit tax — dedicated engine",
    ),
    "WA": (
        "Washington",
        D(72800),
        "No income tax; PFML, WA Cares, L&I — dedicated engine",
    ),
}

ALL_CODES = sorted(set(STATES) | set(DEDICATED))
