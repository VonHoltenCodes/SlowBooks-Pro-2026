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
# FIGURES ARE THE 2025 PUBLISHED VALUES (2026 where the state has released
# them), simplified to the percentage-method structure. States that publish
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
_add(
    StateSpec(
        "AK",
        "Alaska",
        "none",
        D(51700),
        employee_items=(
            OtherItem("AK employee unemployment insurance", D("0.005"), D(51700)),
        ),
        source="Alaska DOL Employment Security Tax",
    )
)
_add(StateSpec("FL", "Florida", "none", D(7000), source="Florida DOR reemployment tax"))
_add(StateSpec("NV", "Nevada", "none", D(41800), source="Nevada DETR"))
_add(
    StateSpec(
        "NH",
        "New Hampshire",
        "none",
        D(14000),
        source="NH Employment Security",
        notes="No tax on wages (interest & dividends tax repealed 2025).",
    )
)
_add(
    StateSpec(
        "SD", "South Dakota", "none", D(15000), source="SD DOL reemployment assistance"
    )
)
_add(StateSpec("TN", "Tennessee", "none", D(7000), source="TN DOL"))
_add(StateSpec("TX", "Texas", "none", D(9000), source="TWC"))
_add(StateSpec("WY", "Wyoming", "none", D(32400), source="WY DWS"))

# --- Flat-rate states ----------------------------------------------------------
_add(
    StateSpec(
        "AZ",
        "Arizona",
        "flat",
        D(8000),
        default_rate=D("0.020"),
        source="Arizona Form A-4",
        notes="Employee elects a rate on Form A-4 (0.5%–3.5%); 2.0% is the default when none is on file. Set employee.state_rate_override.",
    )
)
_add(
    StateSpec(
        "CO",
        "Colorado",
        "flat",
        D(27200),
        flat_rate=D("0.044"),
        std_deduction=_sd(15000, 30000, 22500),
        employee_items=(OtherItem("CO FAMLI (employee)", D("0.0045"), SS_BASE),),
        employer_items=(OtherItem("CO FAMLI (employer)", D("0.0045"), SS_BASE),),
        source="Colorado DR 1098 / DR 0004; FAMLI 0.9% split 50/50",
        notes="4.4% statutory rate (a TABOR-triggered temporary cut applied in 2025).",
    )
)
_add(
    StateSpec(
        "GA",
        "Georgia",
        "flat",
        D(9500),
        flat_rate=D("0.0519"),
        std_deduction=_sd(12000, 24000, 12000),
        exemption=D(4000),
        source="Georgia Employer's Tax Guide (Form G-4)",
        notes="Rate steps down 0.10%/yr toward 4.99%.",
    )
)
_add(
    StateSpec(
        "ID",
        "Idaho",
        "flat",
        D(55300),
        flat_rate=D("0.053"),
        std_deduction=_sd(15000, 30000, 22500),
        source="Idaho Table for Percentage Computation Method",
    )
)
_add(
    StateSpec(
        "IL",
        "Illinois",
        "flat",
        D(13916),
        flat_rate=D("0.0495"),
        exemption=D(2850),
        source="Illinois Booklet IL-700-T",
        notes="Allowances = IL-W-4 line 1 count × $2,850 (line 2 additional allowances are $1,000 each — add them as allowances at the reduced value or use extra withholding).",
    )
)
_add(
    StateSpec(
        "IN",
        "Indiana",
        "flat",
        D(9500),
        flat_rate=D("0.030"),
        exemption=D(1000),
        source="Indiana Departmental Notice #1",
        notes="County income tax (0.5%–3%) is required: set employee.local_tax_rate to the county rate. Rate drops to 2.95% in 2026.",
    )
)
_add(
    StateSpec(
        "IA",
        "Iowa",
        "flat",
        D(39500),
        flat_rate=D("0.038"),
        std_deduction=_sd(15000, 30000, 22500),
        source="Iowa Withholding Formula (3.8% flat from 2025)",
    )
)
_add(
    StateSpec(
        "KY",
        "Kentucky",
        "flat",
        D(11700),
        flat_rate=D("0.040"),
        std_deduction=_sd(3270, 3270, 3270),
        source="Kentucky Withholding Tax Formula",
        notes="Rate is 3.5% for 2026 (HB 1).",
    )
)
_add(
    StateSpec(
        "LA",
        "Louisiana",
        "flat",
        D(7700),
        flat_rate=D("0.030"),
        std_deduction=_sd(12500, 25000, 12500),
        source="Louisiana R-1300 / Act 11 of 2024 (3% flat from 2025)",
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
        source="Massachusetts Circular M; PFML 0.88% (25+ employees)",
        notes="4% surtax on income over $1M is not withheld here.",
    )
)
_add(
    StateSpec(
        "MI",
        "Michigan",
        "flat",
        D(9500),
        flat_rate=D("0.0425"),
        exemption=D(5800),
        source="Michigan Income Tax Withholding Guide (Form MI-W4)",
        notes="Cities with an income tax (Detroit, Grand Rapids…) — use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "NC",
        "North Carolina",
        "flat",
        D(32600),
        flat_rate=D("0.0425"),
        std_deduction=_sd(12750, 25500, 19125),
        source="NC-30 Withholding Tables",
        notes="3.99% from 2026.",
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
        source="PA Employer Withholding Guide (REV-415)",
        notes="No deductions or exemptions. Local EIT (municipal + school) — use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "UT",
        "Utah",
        "flat",
        D(48900),
        flat_rate=D("0.045"),
        source="Utah Publication 14",
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
        source="Alabama Withholding Tax Tables and Instructions",
        notes="Standard deduction phases down with income; the maximum is used.",
    )
)
_add(
    StateSpec(
        "AR",
        "Arkansas",
        "brackets",
        D(7000),
        brackets=_br(
            [(0, 0), (5500, 2), (10900, 3), (15600, 3.4), (26000, 3.9)],
            [(0, 0), (5500, 2), (10900, 3), (15600, 3.4), (26000, 3.9)],
        ),
        std_deduction=_sd(2410, 4820, 2410),
        source="Arkansas Withholding Tax Formula Method",
        notes="Arkansas gives a $29 per-exemption credit rather than an exemption amount; not modelled.",
    )
)
_add(
    StateSpec(
        "CT",
        "Connecticut",
        "brackets",
        D(26100),
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
        source="Connecticut Circular CT (Form CT-W4 withholding codes)",
        notes="Personal exemption phases out above $30k/$48k; the full amount is used. Withholding codes A–F map to filing status here.",
    )
)
_add(
    StateSpec(
        "DE",
        "Delaware",
        "brackets",
        D(12500),
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
        source="Delaware Withholding Tax Tables; Paid Leave 0.8% total from 2025",
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
        std_deduction=_sd(15000, 30000, 22500),
        employer_items=(
            OtherItem("DC Paid Family Leave (employer)", D("0.0075"), None),
        ),
        source="DC Office of Tax and Revenue withholding instructions (FR-230)",
    )
)
_add(
    StateSpec(
        "HI",
        "Hawaii",
        "brackets",
        D(62000),
        brackets=_br(
            [
                (0, 1.4),
                (2400, 3.2),
                (4800, 5.5),
                (9600, 6.4),
                (14400, 6.8),
                (19200, 7.2),
                (24000, 7.6),
                (36000, 7.9),
                (48000, 8.25),
                (150000, 9),
                (175000, 10),
                (200000, 11),
            ]
        ),
        std_deduction=_sd(4400, 8800, 6424),
        exemption=D(1144),
        employee_items=(
            OtherItem("HI Temporary Disability Insurance", D("0.005"), D(69000)),
        ),
        source="Hawaii Booklet A Employer's Tax Guide",
        notes="TDI employee share capped at 0.5% of weekly wages up to the statutory maximum; modelled as an annual base.",
    )
)
_add(
    StateSpec(
        "KS",
        "Kansas",
        "brackets",
        D(14000),
        brackets=_br([(0, 5.2), (23000, 5.58)], [(0, 5.2), (46000, 5.58)]),
        std_deduction=_sd(3605, 8240, 6180),
        base_exemption=_sd(9160, 18320, 9160),
        exemption=D(2320),
        source="Kansas Withholding Tax Guide (KW-100), 2024 tax reform",
    )
)
_add(
    StateSpec(
        "ME",
        "Maine",
        "brackets",
        D(12000),
        brackets=_br(
            [(0, 5.8), (26050, 6.75), (61600, 7.15)],
            [(0, 5.8), (52100, 6.75), (123250, 7.15)],
            [(0, 5.8), (39050, 6.75), (92450, 7.15)],
        ),
        std_deduction=_sd(15000, 30000, 22500),
        exemption=D(5150),
        source="Maine Withholding Tables for Individual Income Tax",
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
            ],
        ),
        std_deduction=_sd(2800, 5450, 2800),
        exemption=D(3200),
        source="Maryland Employer Withholding Guide",
        notes="County tax (2.25%–3.2%) is required — set employee.local_tax_rate. Standard deduction is 15% of wages within $1,800–$2,800 ($3,650–$5,450 joint); the maximum is used.",
    )
)
_add(
    StateSpec(
        "MN",
        "Minnesota",
        "brackets",
        D(43000),
        brackets=_br(
            [(0, 5.35), (32570, 6.8), (106990, 7.85), (198630, 9.85)],
            [(0, 5.35), (47620, 6.8), (189180, 7.85), (330410, 9.85)],
            [(0, 5.35), (40100, 6.8), (161130, 7.85), (264050, 9.85)],
        ),
        std_deduction=_sd(14950, 29900, 22500),
        exemption=D(5050),
        employee_items=(OtherItem("MN Paid Leave (employee)", D("0.0044"), SS_BASE),),
        employer_items=(OtherItem("MN Paid Leave (employer)", D("0.0044"), SS_BASE),),
        source="Minnesota Income Tax Withholding Instruction Booklet; Paid Leave premiums from 1 Jan 2026",
        year="2025 / PFML 2026",
    )
)
_add(
    StateSpec(
        "MS",
        "Mississippi",
        "brackets",
        D(14000),
        brackets=_br([(0, 0), (10000, 4.4)], [(0, 0), (10000, 4.4)]),
        std_deduction=_sd(2300, 4600, 3400),
        base_exemption=_sd(6000, 12000, 9500),
        exemption=D(1500),
        source="Mississippi Withholding Tax Tables",
        notes="4.0% in 2026.",
    )
)
_add(
    StateSpec(
        "MO",
        "Missouri",
        "brackets",
        D(9500),
        brackets=_br(
            [
                (0, 0),
                (1313, 2),
                (2626, 2.5),
                (3939, 3),
                (5252, 3.5),
                (6565, 4),
                (7878, 4.5),
                (9191, 4.7),
            ],
            [
                (0, 0),
                (1313, 2),
                (2626, 2.5),
                (3939, 3),
                (5252, 3.5),
                (6565, 4),
                (7878, 4.5),
                (9191, 4.7),
            ],
        ),
        std_deduction=_sd(15000, 30000, 22500),
        source="Missouri Employer's Tax Guide (Form MO-W-4)",
    )
)
_add(
    StateSpec(
        "MT",
        "Montana",
        "brackets",
        D(45100),
        brackets=_br(
            [(0, 4.7), (21100, 5.9)], [(0, 4.7), (42200, 5.9)], [(0, 4.7), (31650, 5.9)]
        ),
        std_deduction=_sd(15000, 30000, 22500),
        source="Montana Withholding Tax Guide (Form MW-4), 2024 simplification",
    )
)
_add(
    StateSpec(
        "NE",
        "Nebraska",
        "brackets",
        D(9000),
        brackets=_br(
            [(0, 2.46), (3970, 3.51), (23820, 5.01), (38390, 5.2)],
            [(0, 2.46), (7940, 3.51), (47640, 5.01), (76780, 5.2)],
            [(0, 2.46), (7410, 3.51), (38050, 5.01), (56810, 5.2)],
        ),
        std_deduction=_sd(8600, 17200, 12600),
        source="Nebraska Circular EN",
        notes="Nebraska's $157 per-exemption credit is not modelled.",
    )
)
_add(
    StateSpec(
        "NJ",
        "New Jersey",
        "brackets",
        D(43300),
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
            OtherItem("NJ unemployment insurance (employee)", D("0.003825"), D(43300)),
            OtherItem("NJ disability insurance (employee)", D("0.0023"), D(43300)),
            OtherItem("NJ family leave insurance", D("0.0033"), D(43300)),
        ),
        source="New Jersey NJ-WT (Rate Tables A / B)",
        notes="Rate Table A for single, B for married/head of household.",
    )
)
_add(
    StateSpec(
        "NM",
        "New Mexico",
        "brackets",
        D(33200),
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
        std_deduction=_sd(15000, 30000, 22500),
        source="New Mexico FYI-104 Withholding Tax",
    )
)
_add(
    StateSpec(
        "ND",
        "North Dakota",
        "brackets",
        D(45100),
        brackets=_br(
            [(0, 0), (48475, 1.95), (244825, 2.5)],
            [(0, 0), (80975, 1.95), (445600, 2.5)],
            [(0, 0), (64700, 1.95), (244825, 2.5)],
        ),
        std_deduction=_sd(15000, 30000, 22500),
        source="North Dakota Income Tax Withholding Rates and Instructions",
    )
)
_add(
    StateSpec(
        "OH",
        "Ohio",
        "brackets",
        D(9000),
        brackets=_br(
            [(0, 0), (26050, 2.75), (100000, 3.5)],
            [(0, 0), (26050, 2.75), (100000, 3.5)],
        ),
        exemption=D(2400),
        source="Ohio Employer Withholding Tables",
        notes="Municipal income tax (RITA / CCA cities) and school district tax — use employee.local_tax_rate. 2.75% flat from 2026.",
    )
)
_add(
    StateSpec(
        "OK",
        "Oklahoma",
        "brackets",
        D(27000),
        brackets=_br(
            [
                (0, 0.25),
                (1000, 0.75),
                (2500, 1.75),
                (3750, 2.75),
                (4900, 3.75),
                (7200, 4.75),
            ],
            [
                (0, 0.25),
                (2000, 0.75),
                (5000, 1.75),
                (7500, 2.75),
                (9800, 3.75),
                (12200, 4.75),
            ],
        ),
        std_deduction=_sd(6350, 12700, 9350),
        exemption=D(1000),
        source="Oklahoma Income Tax Withholding Tables (Packet OW-2)",
    )
)
_add(
    StateSpec(
        "RI",
        "Rhode Island",
        "brackets",
        D(29800),
        brackets=_br([(0, 3.75), (79900, 4.75), (181650, 5.99)]),
        std_deduction=_sd(10900, 21800, 16350),
        exemption=D(5100),
        employee_items=(
            OtherItem("RI Temporary Disability Insurance", D("0.013"), D(89200)),
        ),
        source="Rhode Island Withholding Tax Booklet",
        notes="Brackets are the same for every filing status. Deduction/exemption phase out above ~$260k; full amounts used.",
    )
)
_add(
    StateSpec(
        "SC",
        "South Carolina",
        "brackets",
        D(14000),
        brackets=_br(
            [(0, 0), (3560, 3), (17830, 6.2)], [(0, 0), (3560, 3), (17830, 6.2)]
        ),
        std_deduction=_sd(15000, 30000, 22500),
        exemption=D(4790),
        source="South Carolina Withholding Tax Tables (WH-1603F)",
        notes="Top rate steps down toward 6%.",
    )
)
_add(
    StateSpec(
        "VT",
        "Vermont",
        "brackets",
        D(14800),
        brackets=_br(
            [(0, 3.35), (47900, 6.6), (116000, 7.6), (242000, 8.75)],
            [(0, 3.35), (79950, 6.6), (193300, 7.6), (294600, 8.75)],
            [(0, 3.35), (64200, 6.6), (165800, 7.6), (268500, 8.75)],
        ),
        std_deduction=_sd(7400, 14850, 11100),
        exemption=D(5100),
        source="Vermont Income Tax Withholding Instructions, Tables and Charts",
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
        std_deduction=_sd(8500, 17000, 8500),
        exemption=D(930),
        source="Virginia Employer Withholding Instructions (Form VA-4)",
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
        source="West Virginia Employer's Withholding Tax Tables (2025 rate reduction)",
    )
)
_add(
    StateSpec(
        "WI",
        "Wisconsin",
        "brackets",
        D(14000),
        brackets=_br(
            [(0, 3.5), (14680, 4.4), (29370, 5.3), (323290, 7.65)],
            [(0, 3.5), (19580, 4.4), (39150, 5.3), (431060, 7.65)],
            [(0, 3.5), (14680, 4.4), (29370, 5.3), (323290, 7.65)],
        ),
        std_deduction=_sd(13930, 25790, 18000),
        exemption=D(700),
        source="Wisconsin Publication W-166",
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
