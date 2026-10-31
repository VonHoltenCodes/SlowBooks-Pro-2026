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
        source="Alaska DOL Employment Security Tax, 2026 Industry / Experience Rates (taxable wage base $54,200; employee rate 0.50%), https://labor.alaska.gov/estax/2026-experience-rates.html",
    )
)
_add(
    StateSpec(
        "FL",
        "Florida",
        "none",
        D(7000),
        year="2026",
        source="Florida DOR reemployment tax, taxable wage base $7,000 (s. 443.1217 F.S.; floridarevenue.com/taxes/taxesfees/Pages/rt_rate.aspx); Equifax 2026 state UI tax guide (2026-03-26)",
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
        source="Arizona DOR Form A-4 (2026) and azdor.gov/businesses-arizona/withholding-tax: employee elects 0.5%-3.5% of gross taxable wages, 2.0% when no A-4 is on file; SUI base $8,000 (Arizona DES; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Employee elects a rate on Form A-4 (0.5%-3.5% in half-point steps); 2.0% is the default when none is on file. Set employee.state_rate_override. There are no deductions or allowances. The 2.5% flat income tax is what the election approximates.",
    )
)
_add(
    StateSpec(
        "CO",
        "Colorado",
        "flat",
        D(30600),
        flat_rate=D("0.044"),
        std_deduction=_sd(5500, 11000, 5500),
        employee_items=(OtherItem("CO FAMLI (employee)", D("0.0044"), SS_BASE),),
        employer_items=(OtherItem("CO FAMLI (employer)", D("0.0044"), SS_BASE),),
        year="2026",
        source="Colorado DR 1098 2026 Colorado Withholding Worksheet for Employers (rev. 10/21/25), Step 2a $11,000 married filing jointly / qualifying surviving spouse, $5,500 otherwise, Step 2c 4.40%, https://tax.colorado.gov/sites/default/files/documents/DR_1098_Colorado_Withholding_Worksheet_for_Employees.pdf; FAMLI 2026 premium 0.88% (0.44% employee / 0.44% employer) on wages to the Social Security cap, famli.colorado.gov premium & benefits calculator (0.86% from 2027); SUI base $30,600 (CDLE; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="The deduction is the DR 1098 Step 2a amount that applies when the employee has only a federal W-4 (head of household gets the $5,500 'otherwise' amount). An employee who files DR 0004 with a Line 2 amount should have that amount deducted instead; not modelled. Employers with 9 or fewer employees owe no employer FAMLI share.",
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
        source="Georgia Employer's Withholding Tax Guide 2026 (revised September 2026), What's New + Table E/F percentage method: 4.99% (HB 463, retroactive to 2026-01-01; withhold at 5.19% for pay dates before 2026-05-11), standard deduction $30,000 MFJ / $15,000 single, HOH, MFS, dependent allowance $5,000, https://dor.georgia.gov/document/document/2026-employers-tax-guide-updated-june-2026/download; SUI base $9,500 (Georgia DOL; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Allowances = dependent allowances on Form G-4 ($5,000 each). Married couples who both work use the $15,000 column-3 deduction each; the engine's married status gives the $30,000 one-earner amount. Pay dates before 2026-05-11 were withheld at 5.19%.",
    )
)
_add(
    StateSpec(
        "ID",
        "Idaho",
        "flat",
        D(58300),
        flat_rate=D("0.053"),
        std_deduction=_sd(16100, 32200, 16100),
        year="2026",
        source="Idaho EPB00744 Table for Percentage Computation Method of Withholding (07-23-2026): single including head of household 5.3% over $16,100, married 5.3% over $32,200, https://tax.idaho.gov/wp-content/uploads/pubs/EPB00744/EPB00744_07-23-2026.pdf; Idaho Child Tax Credit Allowance Table = $0 (credit sunset, Idaho Code 63-3029L); SUI base $58,300 (IDOL Administrative Order 682, 2025-11-06)",
        notes="Head of household uses the single table. Idaho W-4 allowances are worth $0 in 2026 (the child tax credit allowance sunset), so employee.state_allowances has no effect.",
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
        source="Illinois Booklet IL-700-T, effective 2026-01-01: 4.95%, $2,925 per IL-W-4 Line 1 allowance, $1,000 per Line 2 allowance, https://tax.illinois.gov/content/dam/soi/en/web/tax/forms/withholding/documents/currentyear/il-700-t.pdf; SUI base $14,250 (IDES; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Allowances = IL-W-4 line 1 count x $2,925 (line 2 additional allowances are $1,000 each - add them as allowances at the reduced value or use extra withholding).",
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
        source="Indiana Departmental Notice #1 (R47 / 10-26, effective 2026-10-01): 2.95% for 2026, $1,000 per WH-4 line 5 personal exemption, $1,500 per dependent (lines 6-7), $3,000 per adopted child, county rates; https://www.in.gov/dor/files/dn01.pdf (2.90% for 2027); SUI base $9,500 (Indiana DWD; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="County income tax (0.5%-3%) is required: set employee.local_tax_rate to the county rate. Dependent exemptions are $1,500 each (count a dependent as 1.5 allowances) and adopted children $3,000.",
    )
)
_add(
    StateSpec(
        "IA",
        "Iowa",
        "flat",
        D(20400),
        flat_rate=D("0.038"),
        std_deduction=_sd(13000, 26000, 19500),
        exemption=D("1052.63"),
        year="2026",
        source="Iowa Department of Revenue, Iowa Withholding Formula effective 2026-01-01 (released November 2025): D = $13,000 'Other' / MFJ with a working spouse, $19,500 head of household, $26,000 MFJ spouse without earned income or QSS; rate 3.80%; allowances are a dollar credit (old IA W-4: $40 per allowance), https://revenue.iowa.gov/media/53/download; SUI base $20,400 (Iowa Workforce Development, SF 607; $21,100 for 2027)",
        notes="The married deduction is the $26,000 one-earner amount (the form's default when the spouse-income question is blank); a two-earner couple should use $13,000 (treat as single). Iowa allowances are a credit against tax, not an exemption: $1,052.63 per allowance x 3.8% = the $40 credit per old-style allowance; a 2024+ IA W-4 states the credit in dollars, so enter its total / $40 as allowances.",
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
        source="Kentucky DOR 2026 Withholding Tax Formula, 42A003 (TCF)(10-2025): standard deduction $3,360, 3.5% flat, https://revenue.ky.gov/Forms/2026%20Withholding%20Formula.pdf; SUI base $12,000 (Kentucky Office of Unemployment Insurance; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="No allowances. Employees with more than one W-2 may need an extra $117.60 a year withheld (the deduction is taken once).",
    )
)
_add(
    StateSpec(
        "LA",
        "Louisiana",
        "flat",
        D(7000),
        flat_rate=D("0.030"),
        std_deduction=_sd(12875, 25750, 25750),
        year="2026",
        source="Louisiana Revenue Information Bulletin 26-005 (2026-01-12) and Emergency Rule LAC 61:I.1501 effective 2026-01-01: 3% flat; standard deduction $12,875 single and MFS, $25,750 MFJ, QSS and head of household, https://dam.ldr.la.gov/lawspolicies/RIB%2026-005-Updated%20Withholding%20Tables.pdf; SUI base $7,000 (Louisiana Workforce Commission; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Head of household gets the $25,750 deduction, the same as married filing jointly.",
    )
)
_add(
    StateSpec(
        "MA",
        "Massachusetts",
        "brackets",
        D(15000),
        brackets=_br([(0, 5), (1107750, 9)], [(0, 5), (1107750, 9)]),
        std_deduction=_sd(2000, 2000, 2000),
        base_exemption=_sd(4400, 8400, 6800),
        exemption=D(1000),
        employee_items=(OtherItem("MA PFML (employee)", D("0.0046"), SS_BASE),),
        employer_items=(OtherItem("MA PFML (employer)", D("0.0042"), SS_BASE),),
        year="2026",
        source="Massachusetts Circular M (Rev. 12/25), Percentage Methods for Wages Paid from January 1, 2026, p.12: FICA/Medicare/retirement deduction up to $2,000; exemption factors $4,400 (claiming 1) or $1,000 x number claimed + $3,400 (a claimed spouse counts as 4); 5% to $1,107,750 and 9% above (4% surtax); head of household -$120 of tax; https://www.mass.gov/doc/massachusetts-circular-m-income-tax-withholding-tables-at-50-effective-january-1-2026/download; PFML 2026 0.88% for 25+ covered individuals = employee 0.46% (0.18% family + 0.28% medical) / employer 0.42%, wages to the Social Security cap, mass.gov PFML employer contribution rates; SUI base $15,000 (mass.gov UI contribution rates)",
        notes="The $2,000 FICA deduction is modelled at its cap (reached at $26,144 of wages; below that it over-deducts by up to $2,000, i.e. up to $100 of tax). base_exemption assumes the employee claims 1 (single/HOH) or self + spouse = 5 exemptions (married, $8,400); each allowance here is one more dependent ($1,000). An M-4 claiming '0' gets no exemption - not modelled. Head of household: the $120 annual tax reduction is modelled as $2,400 more exemption ($120 / 5%). Employers under 25 covered individuals owe no employer PFML share (0.46% total).",
    )
)
_add(
    StateSpec(
        "MI",
        "Michigan",
        "flat",
        D(9000),
        flat_rate=D("0.0425"),
        exemption=D(5900),
        year="2026",
        source="Michigan Treasury Form 446, 2026 Michigan Income Tax Withholding Guide (Rev. 02-26): withholding rate 4.25%, personal exemption $5,900, https://www.michigan.gov/taxes/-/media/Project/Websites/taxes/Forms/SUW/TY2026/446_Withholding-Guide_2026.pdf (Treasury 2026-04-15: rate stays 4.25% for 2026); SUI base $9,000 for employers in good standing (Michigan UIA Employer Advisor, December 2025 / taxable wage base page; $9,500 for employers not in good standing)",
        notes="Cities with an income tax (Detroit, Grand Rapids...) - use employee.local_tax_rate. The SUI base is $9,500 for employers delinquent on reports or taxes (UIA Form 6354).",
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
        std_deduction=_sd(2500, 5000, 2500),
        base_exemption=_sd(1500, 3000, 3000),
        exemption=D(1000),
        year="2026",
        source="Alabama Withholding Tax Tables and Instructions for Employers (revised January 2026), Formula for Computing Alabama Withholding Tax p.6-7: standard deduction $3,000 S / $8,500 M / $5,200 H at gross income <= $25,999 phasing down to $2,500 / $5,000 / $2,500 at >= $35,500; federal withholding deduction; personal exemption $1,500 S/MS, $3,000 M/H; dependents $1,000 (<= $50,000), $500 (to $100,000), $300 above; 2%/4%/5% at $500/$3,000 (M: $1,000/$6,000), https://www.revenue.alabama.gov/wp-content/uploads/2026/01/whbooklet_0126.pdf; SUI base $8,000 (Alabama DOL; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Standard deduction uses the floor amounts that apply at gross income >= $35,500 ($2,500 / $5,000 / $2,500); below $26,000 the published amounts are $500 / $3,500 / $2,700 higher (up to $25 / $175 / $135 a year more tax here). The deduction for the employee's annualized federal income tax withholding (line 2B) cannot be modelled: this over-withholds 5% of federal withholding (about $250 a year at $60,000 single). Dependents are $1,000 each regardless of income here ($500 / $300 above $50,000 / $100,000 in the formula). An A-4 claiming '0' gets no personal exemption - this assumes S, M or H is claimed. Occupational taxes - use employee.local_tax_rate.",
    )
)
_add(
    StateSpec(
        "AR",
        "Arkansas",
        "brackets",
        D(7000),
        brackets=_br(
            [
                (0, 0),
                (5600, 2),
                (11200, 3),
                (16000, 3.4),
                (26400, 3.7),
                (94700, 13.6055),
                (97600, 3.7),
            ],
            [
                (0, 0),
                (5600, 2),
                (11200, 3),
                (16000, 3.4),
                (26400, 3.7),
                (94700, 13.6055),
                (97600, 3.7),
            ],
        ),
        std_deduction=_sd(2470, 2470, 2470),
        exemption=D(784),
        year="2026",
        source="Arkansas DFA Withholding Tax Formula Method, Effective 01/01/2026 (posted 2026-05-29 for Act 1 of 2026 / HB 1001, top rate 3.9% -> 3.7% retroactive to 2026-01-01): standard deduction $2,470 (every status); 0% to $5,599, 2% - $111.98 to $11,199, 3% - $223.97 to $15,999, 3.4% - $287.97 to $26,399, 3.7% - $367.16 to $94,700, adjustment stepping down $10 per $100 to 3.7% - $79.90 at $97,601+; $29 credit per AR4EC exemption, https://www.dfa.arkansas.gov/wp-content/uploads/Withholding-Tax-Formula.pdf; SUI base $7,000 (Arkansas DWS; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Rates in force now (retroactive to 2026-01-01; the October 2025 edition had 3.9% - $419.96 / $89.30). The DFA 'minus adjustment' schedule is expressed as marginal brackets: the $94,700-$97,600 band recaptures the lower rates at 13.6055% so that income above $97,600 is taxed at a flat 3.7% - $79.90 (exact at $97,600 and above, within $5 inside the band). The $29 per-exemption credit is modelled as a $784 exemption ($29 / 3.7%); it is worth $26.66 per exemption in the 3.4% bracket. DFA's $50-midrange lookup and whole-dollar rounding are not applied.",
    )
)
_add(
    StateSpec(
        "CT",
        "Connecticut",
        "brackets",
        D(27000),
        brackets=_br(
            [  # Withholding Code F (single) - TPG-211 fitted on gross annual wages
                (0, 0),
                (15000, 2.4083),
                (30000, 8.2518),
                (44000, 4.725),
                (50000, 4.95),
                (56500, 6.3286),
                (60000, 11.2056),
                (64500, 5.993),
                (100000, 6.0),
                (101500, 6.7143),
                (105000, 6.5),
                (150000, 6.05),
                (200000, 8.3),
                (250000, 8.7),
                (345000, 6.9581),
                (500000, 7.99),
                (540000, 6.99),
            ],
            [  # Withholding Code C (married, spouse not employed)
                (0, 0),
                (24000, 1.7),
                (44000, 3.825),
                (48000, 8.2261),
                (71000, 4.3648),
                (96000, 12.095),
                (100000, 13.5),
                (100500, 6.5),
                (145500, 5.5917),
                (200000, 6.0),
                (210000, 6.5),
                (300000, 6.05),
                (400000, 8.3),
                (500000, 8.7),
                (690000, 6.9581),
                (1000000, 7.99),
                (1080000, 6.99),
            ],
            [  # Withholding Code B (head of household)
                (0, 0),
                (19000, 1.6),
                (35000, 4.3583),
                (38000, 8.2264),
                (56000, 4.4378),
                (74000, 10.36),
                (78500, 7.1667),
                (80000, 6.4275),
                (114500, 5.5879),
                (160000, 6.0),
                (168000, 6.5),
                (240000, 6.05),
                (320000, 8.25),
                (400000, 8.65),
                (552000, 6.9565),
                (800000, 7.99),
                (864000, 6.99),
            ],
        ),
        employee_items=(OtherItem("CT Paid Leave", D("0.005"), SS_BASE),),
        year="2026",
        source="Connecticut DRS TPG-211, 2026 Withholding Calculation Rules (Rev. 12/25, 'unchanged from 2025'): Table A personal exemption ($15,000 code F phasing out $30,000-$44,000; $24,000 code C, $48,000-$71,000; $19,000 code B, $38,000-$56,000), Table B initial tax 2%/4.5%/5.5%/6%/6.5%/6.9%/6.99%, Table C 2% phase-out add-back, Table D tax recapture, Table E personal tax credit (75% down to 0 at $64,500 / $100,500 / $78,500), https://portal.ct.gov/-/media/drs/forms/2025/wth/tpg-211_1225.pdf; CT Paid Leave 0.5% of wages to the Social Security cap (ctpaidleave.org, rate kept at 0.5% for 2026); SUI base $27,000 (CT DOL; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="Connecticut has no percentage method: every table (exemption phase-out, add-back, recapture, credit) keys off annualized gross wages, so each withholding code is encoded here as an equivalent marginal schedule on gross wages with no separate exemption. single = code F, married = code C (spouse not employed), head_of_household = code B. Exact at the schedule's break points ($50,000, $52,000, $64,500, $100,000... single) and within one table step ($25-$90) elsewhere; up to $110 high below $30,000 where the credit varies. Codes A and D (two-earner married) are not available - use code F (single) for an A or D employee, which is within $20 of code A above $56,000.",
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
        exemption=D(2000),
        employee_items=(OtherItem("DE Paid Leave (employee)", D("0.004"), SS_BASE),),
        employer_items=(OtherItem("DE Paid Leave (employer)", D("0.004"), SS_BASE),),
        year="2026",
        source="Delaware Division of Revenue, Employer's Guide (Withholding Regulations and Employer's Duties), Section 17 Withholding Methods Based on Annualized Wages (page modified 2026-01-09): standard deduction $3,250 single / $6,500 MFJ / $3,250 MFS, Tax Computation Table effective 2025-01-01 (0 / 2.2% / 3.9% / 4.8% / 5.2% / 5.55% / 6.6% at $2,000 / $5,000 / $10,000 / $20,000 / $25,000 / $60,000), $110 credit per personal exemption, https://revenue.delaware.gov/employers-guide-withholding-regulations-employers-duties/; Delaware Paid Leave 0.8% for 25+ employees (0.4% medical + 0.32% parental + 0.08% caregiving), employer may deduct up to half, wages to the Social Security cap (DE DOL PFML actuarial review 2026-03-30); SUI base $14,500 (labor.delaware.gov employer services)",
        notes="The $110 per-exemption credit is modelled as a $2,000 exemption ($110 / 5.55%); it is $6 short per exemption in the 5.2% bracket and $22 over in the 6.6% bracket. Head of household uses the single amounts. Employers with 10-24 employees owe Paid Leave at 0.32% (parental only); under 10, none.",
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
        exemption=D(4300),
        employer_items=(
            OtherItem("DC Paid Family Leave (employer)", D("0.0075"), None),
        ),
        year="2026",
        source="DC OTR FR-230 Income Tax Withholding Instructions (Rev. 12/13/17): 'the standard deduction will not be used in the withholding calculation', one personal exemption allowance per D-4 allowance, https://otr.cfo.dc.gov/sites/default/files/dc/sites/otr/publication/attachments/2018%20FR-230_12.13.17.pdf; OTR Tax Notice 2022-08 (2022-10-17): use the current rate schedule and the federal allowance amount = $4,300 (IRS Publication 15-T 2026); rate schedule 4% / 6% / 6.5% / 8.5% / 9.25% / 9.75% / 10.75% at $10,000 / $40,000 / $60,000 / $250,000 / $500,000 / $1,000,000 (D.C. Code 47-1806.03, tax years 2022+); DC Paid Family Leave employer 0.75% of all wages (dcpaidfamilyleave.dc.gov); SUI base $9,000 (DOES; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="No standard deduction in withholding; $4,300 per D-4 allowance. The same schedule applies to every filing status.",
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
            ],
        ),
        std_deduction=_sd(4350, 4350, 4350),
        exemption=D(1144),
        employee_items=(
            OtherItem("HI Temporary Disability Insurance", D("0.005"), D("78010.92")),
        ),
        year="2026",
        source="Hawaii Booklet A, Employer's Tax Guide (Rev. 2025, for wages paid from 2026-01-01), Appendix Part 1 Annualized Income Tax Withholding: $1,144 per allowance, extra lump sum withholding allowance $4,350, withholding rates 1.4% to 7.9% at $9,600 / $14,400 / $19,200 / $24,000 / $36,000 / $48,000 / $125,000 (single incl. unmarried head of household; married doubled), https://files.hawaii.gov/tax/news/pubs/25BkltA.pdf; TDI 0.5% of weekly wages up to the 2026 maximum weekly wage base $1,500.21 (max $7.50/week), DLIR Disability Compensation Division notice 2025-12-10; SUI base $64,500 (DLIR Schedule C, labor.hawaii.gov/ui)",
        notes="The withholding table stops at 7.9%; the 8.25%-11% income-tax brackets are not used for withholding. Head of household uses the single table. TDI employee share is capped weekly ($7.50); modelled as an annual wage base ($1,500.21 x 52).",
    )
)
_add(
    StateSpec(
        "KS",
        "Kansas",
        "brackets",
        D(15100),
        brackets=_br([(0, 5.2), (23000, 5.58)], [(0, 5.2), (46000, 5.58)]),
        std_deduction=_sd(3605, 8240, 3605),
        base_exemption=_sd(9160, 18320, 11480),
        exemption=D(2320),
        year="2026",
        source="Kansas Withholding Tax Guide KW-100 (Rev. 10-24, tables for wages paid on or after 2024-07-01, still current for 2026 - Notice 26-11 of 2026-10-15: no rate reduction): personal exemption $9,160 ($18,320 MFJ), +$2,320 head of household, +$2,320 per dependent; Table 7 annual: single incl. HOH 0 to $3,605, 5.2% to $26,605, 5.58% above; married 0 to $8,240, 5.2% to $54,240, 5.58% above, https://www.ksrevenue.gov/pdf/kw100.pdf; SUI base $15,100 (Kansas DOL; Equifax 2026 state UI tax guide (2026-03-26))",
        notes="base_exemption grants the personal exemption automatically ($9,160; $18,320 married; $11,480 head of household = $9,160 + the $2,320 HOH allowance); employee.state_allowances are dependents at $2,320 each.",
    )
)
_add(
    StateSpec(
        "ME",
        "Maine",
        "brackets",
        D(12000),
        brackets=_br(
            [(0, 5.8), (27400, 6.75), (64850, 7.15), (1000000, 9.15)],
            [(0, 5.8), (54850, 6.75), (129750, 7.15), (1500000, 9.15)],
        ),
        std_deduction=_sd(12850, 28550, 12850),
        exemption=D(5300),
        employee_items=(
            OtherItem(
                "ME Paid Family and Medical Leave (employee)", D("0.005"), SS_BASE
            ),
        ),
        employer_items=(
            OtherItem(
                "ME Paid Family and Medical Leave (employer)", D("0.005"), SS_BASE
            ),
        ),
        year="2026",
        source="Maine Revenue Services, Withholding Tables for Individual Income Tax 2026, Revised August 2026 (LD 2212 surcharge and higher standard deduction), Percentage Method p.6: $5,300 per allowance; standard deduction $12,850 single (wages <= $102,250, phased out to $0 at $177,250) / $28,550 married (<= $204,550, $0 at $354,550); 5.8% / 6.75% / 7.15% at $27,400 / $64,850 (married $54,850 / $129,750); 2% surcharge on annualized income over $1,000,000 single / $1,500,000 married, https://www.maine.gov/revenue/sites/maine.gov.revenue/files/inline-files/26_wh_tab_instr_August2026.pdf; Maine PFML contribution 1% of wages for employers with 15+ employees, up to 0.5% deductible from employees, wages to the Social Security base (MDOL PFML Employer FAQ, June 2026; 26 M.R.S. 850-F); SUI base $12,000 (2026 Form ME UC-1)",
        notes="Head of household uses the single schedule (there is no HOH table). The standard-deduction phase-out above $102,250 / $204,550 is not modelled (high earners are under-withheld by up to $920 / $2,040 a year). The LD 2212 surcharge is the 9.15% bracket. Employers with fewer than 15 employees pay 0.5% PFML in total and may deduct all of it from employees - no employer line for them.",
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
                (0, 4.75),
                (100000, 5),
                (125000, 5.25),
                (150000, 5.5),
                (250000, 5.75),
                (500000, 6.25),
                (1000000, 6.5),
            ],
            [
                (0, 4.75),
                (150000, 5),
                (175000, 5.25),
                (225000, 5.5),
                (300000, 5.75),
                (600000, 6.25),
                (1200000, 6.5),
            ],
            [
                (0, 4.75),
                (150000, 5),
                (175000, 5.25),
                (225000, 5.5),
                (300000, 5.75),
                (600000, 6.25),
                (1200000, 6.5),
            ],
        ),
        std_deduction=_sd(3400, 3400, 3400),
        exemption=D(3200),
        year="2026",
        source="Maryland Employer Withholding Guide, effective January 2026: 'for the purpose of the percentage method calculation the Standard Deduction is $3,400'; one exemption $3,200 annual; percentage charts (a) married filing joint or head of household 4.75% to $150,000 then 5% / 5.25% / 5.5% / 5.75% / 6.25% / 6.5% at $175,000 / $225,000 / $300,000 / $600,000 / $1,200,000, (b) single incl. MFS 4.75% to $100,000 then at $125,000 / $150,000 / $250,000 / $500,000 / $1,000,000 (state share; the charts add the county rate), https://www.marylandcomptroller.gov/content/dam/mdcomp/tax/instructions/withholding/2026/withholding-guide.pdf; SUI base $8,500 (labor.maryland.gov UI trust fund points)",
        notes="County tax (2.25%-3.2%) is required - set employee.local_tax_rate. The withholding schedules disregard the 2%/3%/4% return rates below $3,000 (4.75% from the first dollar) and head of household uses the joint chart. Maryland applies the county rate to taxable income after the deduction and exemptions; the engine applies it to gross taxable wages, which over-withholds the local tax by ($3,400 + $3,200 x exemptions) x the county rate (about $200 a year at 3.2%). No withholding on wages under $5,000 a year. FAMLI contributions start 2027-01-01 (0.9%, 0.45/0.45).",
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
        ),
        std_deduction=_sd(4700, 14700, 4700),
        exemption=D(5300),
        employee_items=(OtherItem("MN Paid Leave (employee)", D("0.0044"), SS_BASE),),
        employer_items=(OtherItem("MN Paid Leave (employer)", D("0.0044"), SS_BASE),),
        year="2026",
        source="Minnesota Income Tax Withholding Instruction Booklet 2026, Computer Formula p.34 ('supersedes any formulas before Jan. 1, 2026'): $5,300 per allowance; single 5.35% from $4,700 to $38,010, 6.80% to $114,130 (+$1,782.09), 7.85% to $207,850 (+$6,958.25), 9.85% above (+$14,315.27); married 5.35% from $14,700 to $63,400, 6.80% to $208,180, 7.85% to $352,630, 9.85% above, https://www.revenue.state.mn.us/sites/default/files/2025-12/wh-inst-26.pdf; Paid Leave 2026 premium 0.88% (0.61% medical + 0.27% family), employer may deduct up to 0.44%, wages to the Social Security limit (Minn. Stat. 268B.14; DEED small-employer rate notice); SUI base $44,000 (uimn.org taxable wage base)",
        notes="The zero bracket ($4,700 single / $14,700 married) is carried as std_deduction. Form W-4MN has single and married only: head of household uses the single chart. Small employers (30 or fewer employees, average wage at or below 150% of the state average) pay 0.66% in total - the employer share drops to 0.22%, the employee share stays 0.44%.",
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
        source="Mississippi DOR Pub 89-700-25-1 Withholding Income Tax Tables and Employer Instructions (Rev. 07/25, file revised 2026-01-13, tables effective 2026-01-01): exemptions $6,000 single / $9,500 head of family / $12,000 married; standard deduction $2,300 / $3,400 / $4,600; tax year 2026 0% on the first $10,000 and 4.0% on the excess; dependents, age 65 and blindness $1,500 each, https://www.dor.ms.gov/sites/default/files/tax-forms/business/89700251revised1.13.2026.pdf (3.75% for 2027); SUI base $14,000 (MDES)",
        notes="Married = Tables C (spouse not employed: $12,000 exemption, $4,600 deduction). A two-earner spouse (Tables D) claims a share of the $12,000 and a $2,300 deduction - not modelled. An employee with no exemption certificate on file gets zero exemption in Mississippi; the engine always grants the base amount.",
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
        std_deduction=_sd(16100, 16100, 24150),
        year="2026",
        source="Missouri DOR 2026 Missouri Withholding Tax Formula: standard deduction $16,100 single / married and spouse works / MFS, $32,200 married and spouse does not work (MO W-4 line 1 box), $24,150 head of household; annual rates 0% to $1,348, then 2% / 2.5% / 3% / 3.5% / 4% / 4.5% per $1,348 step, 4.7% over $9,436, https://dor.mo.gov/forms/Withholding%20Formula_2026.pdf; SUI base $9,000 (labor.mo.gov/des/employers/tax-rates)",
        notes="Married uses the $16,100 'spouse works' deduction (the default unless the MO W-4 line 1 box is checked); an employee whose spouse does not work is entitled to $32,200 - not modelled (under-withholds them by nothing, over-withholds by up to $757 a year). No allowances. DOR rounds each bracket's tax to whole dollars; the engine does not.",
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
        source="Montana Employer and Information Agent Guide with 2026 Tax Tables, formula W = A + B(G - C) annual: single / MFS / MFJ both working 0% to $16,100, 4.7% to $63,600, 5.65% above; MFJ (MW-4 1b) 0% to $32,200, 4.7% to $127,200; head of household 0% to $24,150, 4.7% to $95,400, https://revenuefiles.mt.gov/files/Forms/Montana_Employer_and_Information_Agent_Guide_with_Tax_Tables.pdf (2027: 4.7% to $65,000 / $130,000 / $97,500, then 5.4%); SUI base $47,300 (uid.dli.mt.gov Rate Explanation)",
        notes="No allowances since the 2024 Form MW-4; the zero bracket is carried as std_deduction. Married = MW-4 box 1b (one earner); a married employee whose spouse also works marks 1a and uses the single column.",
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
