"""2026 withholding figures for the table-driven states N–W.

One hand-derived check per state whose figures changed, taken from the
state's own 2026 percentage-method publication (named in each spec's
`source`).  Each comment derives the annual tax from the published
formula; the assertion is the engine's per-period figure to the cent.
"""

from decimal import Decimal

from app.services.state_tax import get_engine
from app.services.state_tax.tables import STATES


def calc(code, gross=2000, status="single", periods=26, allowances=0, ytd=0):
    return get_engine(code).calculate(
        gross=Decimal(str(gross)),
        taxable=Decimal(str(gross)),
        ytd_gross=Decimal(str(ytd)),
        pay_periods=periods,
        hours=Decimal("80"),
        filing_status=status,
        wc_class_code=None,
        state_allowances=allowances,
    )


def test_north_carolina_nc30_annualized_method():
    # NC-30 (2026) p.19: 52,000 − 12,750 = 39,250 × 4.09% = 1,605.33 / 26
    assert calc("NC").income_tax == Decimal("61.74")
    # married uses the same $12,750, not double
    assert calc("NC", status="married").income_tax == Decimal("61.74")
    # one allowance = $2,500: (39,250 − 2,500) × 4.09% = 1,503.08 / 26
    assert calc("NC", allowances=1).income_tax == Decimal("57.81")


def test_north_dakota_section_2_tables_have_no_deduction_step():
    # 2026 booklet p.45, $4,000 biweekly = 104,000:
    # single (104,000 − 57,625) × 1.95% = 904.31 / 26
    assert calc("ND", gross=4000).income_tax == Decimal("34.78")
    # married (104,000 − 57,500) × 1.95% = 906.75 / 26
    assert calc("ND", gross=4000, status="married").income_tax == Decimal("34.88")
    # $2,000 biweekly single = 52,000 is inside the zero bracket
    assert calc("ND").income_tax == Decimal("0.00")


def test_nebraska_circular_en_table_7():
    # Circular EN 2026 Table 7, single, one allowance ($2,440):
    # 52,000 − 2,440 = 49,560 → 1,343.55 + 4.48% × (49,560 − 40,130) = 1,766.01 / 26
    assert calc("NE", allowances=1).income_tax == Decimal("67.92")
    # married, two allowances, $4,000 biweekly:
    # 104,000 − 4,880 = 99,120 → 2,932.22 + 4.60% × (99,120 − 82,920) = 3,677.42 / 26
    assert calc("NE", gross=4000, status="married", allowances=2).income_tax == Decimal(
        "141.44"
    )


def test_new_jersey_rate_tables_a_and_b():
    # Rate A annual: 795 + 6.1% × (52,000 − 40,000) = 1,527.00 / 26
    assert calc("NJ").income_tax == Decimal("58.73")
    # Rate B annual, $3,000 biweekly = 78,000: 1,440 + 3.9% × 8,000 = 1,752.00 / 26
    assert calc("NJ", gross=3000, status="married").income_tax == Decimal("67.38")
    assert calc("NJ", gross=3000, status="head_of_household").income_tax == Decimal(
        "67.38"
    )


def test_new_mexico_fyi_104_table_7():
    # FYI-104 (Rev. 11/2025) Table 7 single: 1,165.50 + 4.7% × (52,000 − 41,550) = 1,656.65 / 26
    assert calc("NM").income_tax == Decimal("63.72")


def test_ohio_percentage_method_from_august_2026():
    # Biweekly table (eff. 2026-08-01), one exemption: 2,000 − 25.00 = 1,975.00
    # → 16.03 + 2.990% × (1,975.00 − 1,001.92) = 45.12(5)
    assert calc("OH", allowances=1).income_tax == Decimal("45.13")
    # zero exemptions: 16.03 + 2.990% × (2,000 − 1,001.92) = 45.87
    assert calc("OH").income_tax == Decimal("45.87")


def test_oklahoma_head_of_household_uses_the_single_table():
    # OW-2 Table 7 single, one allowance: 52,000 − 1,000 = 51,000
    # → 109.25 + 4.5% × (51,000 − 13,550) = 1,794.50 / 26
    assert calc("OK", allowances=1).income_tax == Decimal("69.02")
    assert calc("OK", status="head_of_household", allowances=1).income_tax == Decimal(
        "69.02"
    )


def test_rhode_island_exemption_only_no_standard_deduction():
    # 2026 booklet p.8-9, one exemption: 52,000 − 1,000 = 51,000 × 3.75% = 1,912.50 / 26
    r = calc("RI", allowances=1)
    assert r.income_tax == Decimal("73.56")
    assert r.employee_other == Decimal("22.00")  # TDI 1.1% of 2,000


def test_south_carolina_wh_1603f_zero_allowances():
    # WH-1603F (Rev. 11/4/25), zero allowances → no deduction:
    # 437.70 + 6% × (52,000 − 18,230) = 2,463.90 / 26
    assert calc("SC").income_tax == Decimal("94.77")
    # one allowance = $5,000 (the 10%-of-wages deduction is a stated simplification)
    # 437.70 + 6% × (47,000 − 18,230) = 2,163.90 / 26
    assert calc("SC", allowances=1).income_tax == Decimal("83.23")


def test_utah_single_above_the_credit_phase_out_is_exact():
    # Pub 14 Schedule 7 single: 52,000 × 4.45% = 2,314.00;
    # credit 485 − 1.3% × (52,000 − 9,348) < 0 → 0; 2,314.00 / 26
    assert calc("UT").income_tax == Decimal("89.00")


def test_virginia_one_deduction_for_every_status():
    # Rev. 10/26 formula, married, E1 = 2:
    # 52,000 − [8,750 + 2 × 930] = 41,390 → 720 + 5.75% × 24,390 = 2,122.43 / 26
    assert calc("VA", status="married", allowances=2).income_tax == Decimal("81.63")
    assert calc("VA", status="married").income_tax == calc("VA").income_tax


def test_vermont_gb_1210_and_child_care_contribution():
    # GB-1210-2026 annual, single, one allowance ($5,400):
    # 52,000 − 5,400 = 46,600 → 3.35% × (46,600 − 3,925) = 1,429.61 / 26
    r = calc("VT", allowances=1)
    assert r.income_tax == Decimal("54.99")
    assert r.employer_other == Decimal("8.80")  # CCC 0.44% × 2,000, employer-paid
    assert r.employee_other == Decimal("0.00")
    # married, two allowances, $3,000 biweekly:
    # 78,000 − 10,800 = 67,200 → 3.35% × (67,200 − 11,775) = 1,856.74 / 26
    assert calc("VT", gross=3000, status="married", allowances=2).income_tax == Decimal(
        "71.41"
    )


def test_wisconsin_w166_alternate_method_at_50k():
    # W-166 alternate method, single, one exemption, annual wages 50,000
    # (the wage the fixed deduction is taken at): deduction 6,702 − 12% ×
    # (50,000 − 17,780) = 2,835.60; net 50,000 − 2,835.60 − 400 = 46,764.40
    # → 1,045.04 + 5.30% × (46,764.40 − 25,520) = 2,170.99 / 50
    assert calc("WI", gross=1000, periods=50, allowances=1).income_tax == Decimal(
        "43.42"
    )


def test_west_virginia_two_earner_table_after_sb_392():
    # IT-100.2.A (March 2026) two-earner Table 5, one exemption:
    # 52,000 − 2,000 = 50,000 → 1,462.88 + 4.58% × 5,000 = 1,691.88 / 26
    assert calc("WV", allowances=1).income_tax == Decimal("65.07")


def test_n_w_specs_cite_a_2026_publication():
    for (
        code
    ) in "NC ND NE NH NJ NM NV OH OK PA RI SC SD TN TX UT VA VT WI WV WY".split():
        spec = STATES[code]
        assert spec.year == "2026", code
        assert "2026" in spec.source and "Verified 2026-10-31" in spec.source, code
