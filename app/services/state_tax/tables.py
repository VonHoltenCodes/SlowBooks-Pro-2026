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
        "NV", "Nevada", "none", D(43700), year="2026", source=f"Nevada DETR; {SUI}"
    )
)
_add(
    StateSpec(
        "NH",
        "New Hampshire",
        "none",
        D(14000),
        year="2026",
        source=f"NH Employment Security; {SUI}",
        notes="No tax on wages (interest & dividends tax repealed 2025).",
    )
)
_add(
    StateSpec(
        "SD", "South Dakota", "none", D(15000), year="2026", source=f"SD DOL; {SUI}"
    )
)
_add(
    StateSpec(
        "TN",
        "Tennessee",
        "none",
        D(7000),
        year="2026",
        source=f"TN DOL; {SUI} (2026 base expected, not confirmed)",
    )
)
_add(StateSpec("TX", "Texas", "none", D(9000), year="2026", source=f"TWC; {SUI}"))
_add(StateSpec("WY", "Wyoming", "none", D(33800), year="2026", source=f"WY DWS; {SUI}"))

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
        flat_rate=D("0.0399"),
        std_deduction=_sd(12750, 25500, 19125),
        year="2026",
        source=f"NC-30 Withholding Tables (2026: 3.99%); {SUI}",
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
        source=f"PA Employer Withholding Guide (REV-415); {SUI}",
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
        source=f"Utah Publication 14 (4.45% for pay periods from 2026-06-01; 4.5% before); {SUI}",
        notes="Utah's withholding allowance credit is not modelled; the flat rate applies to taxable wages.",
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
            [(0, 2.46), (4130, 3.51), (24760, 4.55)],
            [(0, 2.46), (8250, 3.51), (49530, 4.55)],
            [(0, 2.46), (7700, 3.51), (39560, 4.55)],
        ),
        std_deduction=_sd(8850, 17700, 12990),
        year="2026",
        source=f"Nebraska Circular EN (2026: 4.55% top rate, 3.99% by 2027); {TF}; {SUI} (base $9,000; $24,000 for the highest-rate employers)",
        notes="Nebraska's $176 per-exemption credit is not modelled.",
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
                (0, 1.4),
                (20000, 1.75),
                (35000, 3.5),
                (40000, 5.525),
                (75000, 6.37),
                (500000, 8.97),
                (1000000, 10.75),
            ],
            [
                (0, 1.4),
                (20000, 1.75),
                (50000, 2.45),
                (70000, 3.5),
                (80000, 5.525),
                (150000, 6.37),
                (500000, 8.97),
                (1000000, 10.75),
            ],
            [
                (0, 1.4),
                (20000, 1.75),
                (50000, 2.45),
                (70000, 3.5),
                (80000, 5.525),
                (150000, 6.37),
                (500000, 8.97),
                (1000000, 10.75),
            ],
        ),
        exemption=D(1000),
        employee_items=(
            OtherItem("NJ unemployment + workforce (employee)", D("0.00425"), D(44800)),
            OtherItem("NJ disability insurance (employee)", D("0.0019"), D(171100)),
            OtherItem("NJ family leave insurance", D("0.0023"), D(171100)),
        ),
        year="2026",
        source=f"New Jersey NJ-WT (Rate Tables A / B); NJDOL 2026 rates: UI/WF wage base $44,800 (max $190.40), TDI 0.19% + FLI 0.23% on $171,100; {SUI}",
        notes="Rate Table A for single, B for married/head of household.",
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
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"New Mexico FYI-104; {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "ND",
        "North Dakota",
        "brackets",
        D(46600),
        brackets=_br(
            [(0, 0), (48475, 1.95), (244825, 2.5)],
            [(0, 0), (80975, 1.95), (298075, 2.5)],
            [(0, 0), (64700, 1.95), (244825, 2.5)],
        ),
        std_deduction=_sd(16100, 32200, 24150),
        year="2026",
        source=f"North Dakota Income Tax Withholding Rates and Instructions; {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "OH",
        "Ohio",
        "brackets",
        D(9000),
        brackets=_br([(0, 0), (26050, 2.75)], [(0, 0), (26050, 2.75)]),
        exemption=D(2400),
        year="2026",
        source=f"Ohio Employer Withholding Tables (2026: flat 2.75% above $26,050); {TF}; {SUI}",
        notes="Municipal income tax (RITA / CCA cities) and school district tax — use employee.local_tax_rate.",
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
        std_deduction=_sd(6350, 12700, 9350),
        exemption=D(1000),
        year="2026",
        source=f"Oklahoma Income Tax Withholding Tables (2026: brackets collapsed to three, 4.5% top); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "RI",
        "Rhode Island",
        "brackets",
        D(30800),
        brackets=_br([(0, 3.75), (82050, 4.75), (186450, 5.99)]),
        std_deduction=_sd(11200, 22400, 16800),
        exemption=D(5250),
        employee_items=(
            OtherItem("RI Temporary Disability Insurance", D("0.011"), D(100000)),
        ),
        year="2026",
        source=f"Rhode Island Withholding Tax Booklet; RI DLT 2026: TDI 1.1% on $100,000, UI base $30,800; {TF}",
        notes="Brackets are the same for every filing status. Deduction/exemption phase out at high income; full amounts used.",
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
        std_deduction=_sd(16100, 32200, 24150),
        exemption=D(4930),
        year="2026",
        source=f"South Carolina Withholding Tax Tables (WH-1603F); {TF}; {SUI}",
        notes="Top rate is legislated to step; check mid-year.",
    )
)
_add(
    StateSpec(
        "VT",
        "Vermont",
        "brackets",
        D(15400),
        brackets=_br(
            [(0, 3.35), (49400, 6.6), (119700, 7.6), (249700, 8.75)],
            [(0, 3.35), (82500, 6.6), (199450, 7.6), (304000, 8.75)],
            [(0, 3.35), (66200, 6.6), (171100, 7.6), (277000, 8.75)],
        ),
        std_deduction=_sd(7650, 15300, 11500),
        exemption=D(5300),
        year="2026",
        source=f"Vermont Income Tax Withholding Instructions; {TF}; {SUI}",
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
        std_deduction=_sd(8750, 17500, 8750),
        exemption=D(930),
        year="2026",
        source=f"Virginia Employer Withholding Instructions (2026 std deduction $8,750/$17,500); {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "WV",
        "West Virginia",
        "brackets",
        D(9500),
        brackets=_br(
            [(0, 2.22), (10000, 2.96), (25000, 3.33), (40000, 4.44), (60000, 4.82)],
            [(0, 2.22), (10000, 2.96), (25000, 3.33), (40000, 4.44), (60000, 4.82)],
        ),
        exemption=D(2000),
        year="2026",
        source=f"West Virginia Employer's Withholding Tax Tables; {TF}; {SUI}",
    )
)
_add(
    StateSpec(
        "WI",
        "Wisconsin",
        "brackets",
        D(14000),
        brackets=_br(
            [(0, 3.5), (15110, 4.4), (51950, 5.3), (332720, 7.65)],
            [(0, 3.5), (20150, 4.4), (69260, 5.3), (443630, 7.65)],
            [(0, 3.5), (15110, 4.4), (51950, 5.3), (332720, 7.65)],
        ),
        std_deduction=_sd(13960, 25840, 18000),
        exemption=D(700),
        year="2026",
        source=f"Wisconsin Publication W-166 (2026 std deduction $13,960/$25,840); {TF}; {SUI}",
        notes="Standard deduction phases out with income; the maximum is used.",
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
