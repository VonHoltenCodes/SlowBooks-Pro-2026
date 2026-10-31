"""2026 state withholding tables, states A-M: one hand-derived check per
state whose figures changed, taken from the state's own 2026 publication
(named in each StateSpec.source).

Unless a test says otherwise the check is a $2,000 biweekly check, 26 pay
periods, single, no allowances: annual wages 52,000.
"""

from decimal import Decimal

from app.services.state_tax import get_engine
from app.services.state_tax.tables import STATES


def calc(code, gross=2000, status="single", periods=26, ytd=0, **kw):
    return get_engine(code).calculate(
        gross=Decimal(str(gross)),
        taxable=Decimal(str(gross)),
        ytd_gross=Decimal(str(ytd)),
        pay_periods=periods,
        hours=Decimal("80"),
        filing_status=status,
        wc_class_code=None,
        **kw,
    )


def test_alabama_floor_standard_deduction_and_personal_exemption():
    # Alabama formula (booklet rev. Jan 2026, p.6): GI 52,000 >= 35,500 -> std
    # deduction $2,500 (S); personal exemption $1,500; federal withholding
    # deduction not modelled. Taxable 48,000: 500 x 2% + 2,500 x 4% +
    # 45,000 x 5% = 10 + 100 + 2,250 = 2,360 / 26 = 90.77
    assert calc("AL").income_tax == Decimal("90.77")
    # Married: 52,000 - 5,000 - 3,000 = 44,000: 1,000 x 2% + 5,000 x 4% +
    # 38,000 x 5% = 20 + 200 + 1,900 = 2,120 / 26 = 81.54
    assert calc("AL", status="married").income_tax == Decimal("81.54")


def test_arkansas_hb1001_rates_retroactive_to_january():
    # DFA formula (posted 2026-05-29): 52,000 - 2,470 = 49,530 net taxable;
    # 3.70% x 49,530 - 367.16 = 1,465.45 (marginal form: 112 + 144 + 353.60
    # + 3.7% x 23,130 = 1,465.41 - the $0.04 is DFA's $5,599/$5,600 boundary)
    # / 26 = 56.36
    assert calc("AR").income_tax == Decimal("56.36")
    # Married gets the same $2,470 deduction.
    assert calc("AR", status="married").income_tax == Decimal("56.36")
    # Top table: $4,000 biweekly = 104,000 - 2,470 = 101,530 (>= 97,601):
    # 3.70% x 101,530 - 79.90 = 3,676.71 / 26 = 141.41
    assert calc("AR", gross=4000).income_tax == Decimal("141.41")
    # $29 credit per AR4EC exemption: 1,465.41 - 29.01 = 1,436.40 / 26 = 55.25
    assert calc("AR", state_allowances=1).income_tax == Decimal("55.25")


def test_colorado_dr1098_step_2a_allowance():
    # DR 1098 (10/21/25): 52,000 - 5,500 = 46,500 x 4.40% = 2,046 / 26 = 78.69
    assert calc("CO").income_tax == Decimal("78.69")
    # Married filing jointly: 52,000 - 11,000 = 41,000 x 4.40% = 1,804 / 26 = 69.38
    assert calc("CO", status="married").income_tax == Decimal("69.38")


def test_connecticut_tpg211_code_f_single():
    # TPG-211 (Rev. 12/25), code F, annualized salary 52,000: Table A
    # exemption 0 (over 44,000); Table B 200 + 4.5% x 40,000 + 5.5% x 2,000
    # = 2,110; Table C/D 0; Table E credit .10 -> 2,110 x 0.90 = 1,899.00
    # / 26 = 73.04
    assert calc("CT").income_tax == Decimal("73.04")
    # 50,000 (1,923.08 x 26 = 50,000.08): 200 + 4.5% x 40,000 = 2,000 x 0.90
    # = 1,800.00 / 26 = 69.23
    assert calc("CT", gross="1923.08").income_tax == Decimal("69.23")
    # Code C (married) at 48,000 (1,846.15 x 26 = 47,999.90): exemption
    # 24,000 -> 400 + 4.5% x 4,000 = 580 x (1 - .15) = 493.00 / 26 = 18.96
    assert calc("CT", gross="1846.15", status="married").income_tax == Decimal("18.96")
    assert calc("CT").employee_other == Decimal("10.00")  # CT Paid Leave 0.5%


def test_dc_no_standard_deduction_4300_per_allowance():
    # FR-230 / Notice 2022-08: no standard deduction. 52,000: 10,000 x 4% +
    # 30,000 x 6% + 12,000 x 6.5% = 400 + 1,800 + 780 = 2,980 / 26 = 114.62
    assert calc("DC").income_tax == Decimal("114.62")
    # One D-4 allowance ($4,300): 47,700 -> 400 + 1,800 + 7,700 x 6.5% = 2,700.50
    # / 26 = 103.87
    assert calc("DC", state_allowances=1).income_tax == Decimal("103.87")


def test_delaware_credit_as_exemption():
    # Employer's Guide s.17: 52,000 - 3,250 = 48,750; tax = 1,001 + 5.55% x
    # 23,750 = 2,319.125 / 26 = 89.20
    assert calc("DE").income_tax == Decimal("89.20")
    # One allowance = $110 credit: 2,319.125 - 110 = 2,209.125 / 26 = 84.97;
    # the $2,000 exemption gives 1,001 + 5.55% x 21,750 = 2,208.125 / 26 = 84.93
    assert calc("DE", state_allowances=1).income_tax == Decimal("84.93")


def test_hawaii_lump_sum_allowance_and_7_9_top_withholding_rate():
    # Booklet A (Rev. 2025) Appendix Part 1: 52,000 - 4,350 = 47,650;
    # 134.40 + 153.60 + 264 + 307.20 + 816 + 7.2% x 11,650 (838.80) = 2,514.00
    # / 26 = 96.69 (the booklet's rounded cumulative "$1,675 plus 7.2%" gives
    # 2,513.80 / 26 = 96.68)
    assert calc("HI").income_tax == Decimal("96.69")
    # Top withholding rate is 7.9%: 20,000 biweekly = 520,000 - 4,350 =
    # 515,650 -> 8,391 + 7.9% x 390,650 = 39,252.35 (booklet cumulative;
    # exact marginal 8,391.20 + 30,861.35 = 39,252.55) / 26 = 1,509.71
    assert calc("HI", gross=20000).income_tax == Decimal("1509.71")
    # TDI: 0.5% of 2,000 = 10.00; capped at $1,500.21 x 52 = 78,010.92
    assert calc("HI").employee_other == Decimal("10.00")
    assert calc("HI", ytd=77000).employee_other == Decimal("5.05")


def test_iowa_2026_formula_deductions_and_40_dollar_allowance_credit():
    # IDR formula eff. 2026-01-01: 'Other' D = 13,000: (52,000 - 13,000) x
    # 3.80% = 1,482 / 26 = 57.00
    assert calc("IA").income_tax == Decimal("57.00")
    # Head of household D = 19,500: 32,500 x 3.80% = 1,235 / 26 = 47.50
    assert calc("IA", status="head_of_household").income_tax == Decimal("47.50")
    # MFJ, spouse no earned income D = 26,000: 26,000 x 3.80% = 988 / 26 = 38.00
    assert calc("IA", status="married").income_tax == Decimal("38.00")
    # Publication Example 1: biweekly 2,100 'Other', $40 allowance:
    # (2,100 - 500) x 3.80% = 60.80 - 40 / 26 = 59.26
    assert calc("IA", gross=2100, state_allowances=1).income_tax == Decimal("59.26")


def test_idaho_head_of_household_uses_single_table():
    # EPB00744 (07-23-2026): single incl. HOH 5.3% over 16,100:
    # (52,000 - 16,100) x 5.3% = 1,902.70 / 26 = 73.18
    assert calc("ID", status="head_of_household").income_tax == Decimal("73.18")
    assert calc("ID").income_tax == Decimal("73.18")
    # Married 5.3% over 32,200: 19,800 x 5.3% = 1,049.40 / 26 = 40.36
    assert calc("ID", status="married").income_tax == Decimal("40.36")


def test_kansas_head_of_household_allowance():
    # KW-100 Table 7: HOH allowance 9,160 + 2,320 = 11,480; wages after
    # allowance 40,520 -> 1,196 + 5.58% x (40,520 - 26,605) = 1,196 + 776.457
    # = 1,972.457 / 26 = 75.86
    assert calc("KS", status="head_of_household").income_tax == Decimal("75.86")


def test_louisiana_head_of_household_deduction():
    # RIB 26-005: HOH standard deduction 25,750: (52,000 - 25,750) x 3% =
    # 787.50 / 26 = 30.29
    assert calc("LA", status="head_of_household").income_tax == Decimal("30.29")
    assert calc("LA").income_tax == Decimal("45.14")  # (52,000 - 12,875) x 3% / 26


def test_massachusetts_circular_m_percentage_method():
    # Circular M (Rev. 12/25) p.12: step 1 FICA deduction capped at 2,000;
    # step 2 claiming '1' = 4,400; 52,000 - 2,000 - 4,400 = 45,600 x 5% =
    # 2,280 / 26 = 87.69
    assert calc("MA").income_tax == Decimal("87.69")
    # Head of household: 2,280 - 120 = 2,160 / 26 = 83.08
    assert calc("MA", status="head_of_household").income_tax == Decimal("83.08")
    # Married, spouse claimed (counts as 4): 1,000 x 5 + 3,400 = 8,400:
    # 52,000 - 2,000 - 8,400 = 41,600 x 5% = 2,080 / 26 = 80.00
    assert calc("MA", status="married").income_tax == Decimal("80.00")
    # One dependent more: 1,000 x 2 + 3,400 = 5,400 -> 44,600 x 5% = 2,230 / 26 = 85.77
    assert calc("MA", state_allowances=1).income_tax == Decimal("85.77")
    # 4% surtax: 50,000 biweekly = 1,300,000 - 6,400 = 1,293,600;
    # 5% x 1,107,750 + 9% x 185,850 = 55,387.50 + 16,726.50 = 72,114 / 26 = 2,773.62
    assert calc("MA", gross=50000).income_tax == Decimal("2773.62")


def test_maryland_3400_deduction_and_475_from_the_first_dollar():
    # 2026 guide: std deduction 3,400 for every status, 4.75% from $0:
    # (52,000 - 3,400) x 4.75% = 2,308.50 / 26 = 88.79
    assert calc("MD").income_tax == Decimal("88.79")
    # Married, 2 exemptions: 52,000 - 3,400 - 6,400 = 42,200 x 4.75% =
    # 2,004.50 / 26 = 77.10
    assert calc("MD", status="married", state_allowances=2).income_tax == Decimal(
        "77.10"
    )
    # Head of household uses the joint chart: 6,000 biweekly = 156,000 - 3,400
    # = 152,600 -> 4.75% x 150,000 + 5% x 2,600 = 7,125 + 130 = 7,255 / 26 = 279.04
    assert calc("MD", gross=6000, status="head_of_household").income_tax == Decimal(
        "279.04"
    )


def test_maine_august_2026_deduction_and_pfml():
    # MRS tables revised August 2026: single std deduction 12,850 (wages
    # <= 102,250): 52,000 - 12,850 = 39,150 -> 5.8% x 27,400 (1,589.20; MRS
    # prints 1,589) + 6.75% x 11,750 (793.125) = 2,382.325 / 26 = 91.63
    assert calc("ME").income_tax == Decimal("91.63")
    # Married 28,550: 23,450 x 5.8% = 1,360.10 / 26 = 52.31
    assert calc("ME", status="married").income_tax == Decimal("52.31")
    # HOH = single schedule
    assert calc("ME", status="head_of_household").income_tax == Decimal("91.63")
    # PFML 1%: 0.5% employee / 0.5% employer on 2,000
    r = calc("ME")
    assert r.detail["ME Paid Family and Medical Leave (employee)"] == Decimal("10.00")
    assert r.detail["ME Paid Family and Medical Leave (employer)"] == Decimal("10.00")
    assert r.employer_other == Decimal("10.00")


def test_michigan_suta_base_for_employers_in_good_standing():
    assert STATES["MI"].suta_wage_base == Decimal("9000")
    # 4.25%, $5,900 exemption unchanged: (52,000 - 5,900) x 4.25% = 1,959.25 / 26 = 75.36
    assert calc("MI", state_allowances=1).income_tax == Decimal("75.36")


def test_minnesota_computer_formula_zero_bracket():
    # Booklet p.34 single: (52,000 - 38,010) x 6.80% + 1,782.09 = 951.32 +
    # 1,782.09 = 2,733.41 / 26 = 105.13
    assert calc("MN").income_tax == Decimal("105.13")
    # Married: (52,000 - 14,700) x 5.35% = 1,995.55 / 26 = 76.75
    assert calc("MN", status="married").income_tax == Decimal("76.75")
    # HOH uses the single chart
    assert calc("MN", status="head_of_household").income_tax == Decimal("105.13")


def test_missouri_married_spouse_works_deduction():
    # 2026 formula: married and spouse works 16,100: 52,000 - 16,100 = 35,900;
    # 1,348 x (2 + 2.5 + 3 + 3.5 + 4 + 4.5)% = 262.86 + 4.7% x 26,464 =
    # 1,243.81 -> 1,506.67 / 26 = 57.95 (DOR's whole-dollar steps: 1,506.81 / 26 = 57.95)
    assert calc("MO", status="married").income_tax == Decimal("57.95")
    assert calc("MO").income_tax == Decimal("57.95")
    # Head of household 24,150: 27,850 -> 262.86 + 4.7% x 18,414 = 1,128.32 / 26 = 43.40
    assert calc("MO", status="head_of_household").income_tax == Decimal("43.40")
