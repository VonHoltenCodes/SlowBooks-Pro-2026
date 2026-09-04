# ============================================================================
# TableEngine — one engine class for every state described in tables.py.
# ----------------------------------------------------------------------------
# Percentage method, annualized:
#   annual wages      = period taxable × pay periods
#   annual taxable    = wages − standard deduction(status) − base exemption(status)
#                       − allowances × exemption
#   annual tax        = flat rate × taxable   |   brackets(status)
#   period tax        = annual tax ÷ pay periods + extra withholding
# plus flat "other" items (SDI / TDI / PFML / UI) on gross up to their wage
# base, and an optional flat local rate on taxable wages.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q
from app.services.state_tax.base import StateEngine, StateTaxResult
from app.services.state_tax.tables import StateSpec

ZERO = Decimal("0")


def _d(v) -> Decimal:
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def tax_from_brackets(wage: Decimal, brackets) -> Decimal:
    """Progressive tax on `wage` given ascending (lower_bound, rate) pairs."""
    if wage <= 0:
        return ZERO
    tax = ZERO
    for i, (lower, rate) in enumerate(brackets):
        if wage <= lower:
            break
        upper = brackets[i + 1][0] if i + 1 < len(brackets) else None
        top = wage if upper is None else min(wage, upper)
        tax += (top - lower) * rate
        if upper is None or wage <= upper:
            break
    return tax


def _capped(gross: Decimal, ytd_gross: Decimal, base: Decimal | None) -> Decimal:
    if base is None:
        return gross
    if ytd_gross >= base:
        return ZERO
    return min(gross, base - ytd_gross)


class TableEngine(StateEngine):
    def __init__(self, spec: StateSpec):
        self.spec = spec
        self.state_code = spec.code
        self.suta_wage_base = spec.suta_wage_base

    def _status(self, filing_status: str) -> str:
        return (
            filing_status
            if filing_status in ("single", "married", "head_of_household")
            else "single"
        )

    def annual_taxable(
        self, annual_wages: Decimal, filing_status: str, allowances: int
    ) -> Decimal:
        s = self.spec
        fs = self._status(filing_status)
        taxable = annual_wages
        taxable -= _d(s.std_deduction.get(fs, s.std_deduction.get("single")))
        taxable -= _d(s.base_exemption.get(fs, s.base_exemption.get("single")))
        taxable -= _d(allowances) * _d(s.exemption)
        return max(ZERO, taxable)

    def income_tax_annual(
        self,
        annual_wages: Decimal,
        filing_status: str,
        allowances: int = 0,
        rate_override=None,
    ) -> Decimal:
        s = self.spec
        if s.method == "none":
            return ZERO
        taxable = self.annual_taxable(annual_wages, filing_status, allowances)
        if s.method == "flat":
            rate = (
                _d(rate_override)
                if rate_override is not None
                else (s.flat_rate or _d(s.default_rate))
            )
            return taxable * rate
        fs = self._status(filing_status)
        table = s.brackets.get(fs) or s.brackets.get("single")
        return tax_from_brackets(taxable, table)

    def calculate(
        self,
        *,
        gross: Decimal,
        taxable: Decimal,
        ytd_gross: Decimal,
        pay_periods: int,
        hours: Decimal,
        filing_status: str,
        wc_class_code: str | None,
        state_allowances: int = 0,
        state_extra_withholding=ZERO,
        state_rate_override=None,
        local_tax_rate=None,
        **_ignored,
    ) -> StateTaxResult:
        s = self.spec
        if gross <= 0:
            return StateTaxResult()
        detail: dict = {}
        income_tax = ZERO
        if s.method != "none" and taxable > 0:
            override = None
            if state_rate_override is not None and _d(state_rate_override) > 0:
                override = _d(state_rate_override) / Decimal("100")
            annual = self.income_tax_annual(
                taxable * pay_periods,
                filing_status,
                int(state_allowances or 0),
                override,
            )
            income_tax = _q(annual / pay_periods)
            income_tax = _q(max(ZERO, income_tax) + _d(state_extra_withholding))
            detail[f"{s.code} income tax"] = income_tax
        elif _d(state_extra_withholding) > 0:
            income_tax = _q(_d(state_extra_withholding))
            detail[f"{s.code} income tax"] = income_tax

        # Local (county / city / school district) — flat percent of taxable wages
        local = ZERO
        if local_tax_rate is not None and _d(local_tax_rate) > 0 and taxable > 0:
            local = _q(taxable * _d(local_tax_rate) / Decimal("100"))
            detail[f"{s.code} local income tax"] = local

        employee_other = local
        employer_other = ZERO
        for item in s.employee_items:
            amt = _q(_capped(gross, _d(ytd_gross), item.wage_base) * item.rate)
            if amt:
                employee_other += amt
                detail[item.label] = amt
        for item in s.employer_items:
            amt = _q(_capped(gross, _d(ytd_gross), item.wage_base) * item.rate)
            if amt:
                employer_other += amt
                detail[item.label] = amt

        return StateTaxResult(
            income_tax=income_tax,
            employee_other=_q(employee_other),
            employer_other=_q(employer_other),
            detail=detail,
        )


def describe(spec: StateSpec) -> dict:
    """Catalog entry for the API / docs."""

    def money(v):
        return float(v) if v is not None else None

    if spec.method == "none":
        summary = "No wage income tax"
    elif spec.method == "flat":
        if spec.flat_rate:
            summary = f"Flat {float(spec.flat_rate) * 100:.2f}%"
        else:
            summary = (
                f"Employee-elected rate (default {float(spec.default_rate) * 100:.1f}%)"
            )
    else:
        s = spec.brackets["single"]
        summary = f"Progressive {float(s[0][1]) * 100:.2f}%–{float(s[-1][1]) * 100:.2f}% ({len(s)} brackets)"
    return {
        "code": spec.code,
        "name": spec.name,
        "method": spec.method,
        "summary": summary,
        "flat_rate": (
            money(spec.flat_rate) if spec.method == "flat" and spec.flat_rate else None
        ),
        "brackets": {
            k: [(float(lo), float(r)) for lo, r in v] for k, v in spec.brackets.items()
        },
        "std_deduction": {k: money(v) for k, v in spec.std_deduction.items()},
        "base_exemption": {k: money(v) for k, v in spec.base_exemption.items()},
        "exemption_per_allowance": money(spec.exemption),
        "employee_items": [
            (i.label, float(i.rate), money(i.wage_base)) for i in spec.employee_items
        ],
        "employer_items": [
            (i.label, float(i.rate), money(i.wage_base)) for i in spec.employer_items
        ],
        "suta_wage_base": money(spec.suta_wage_base),
        "uses_local_rate": "local_tax_rate" in (spec.notes or ""),
        "uses_rate_election": spec.default_rate is not None,
        "year": spec.year,
        "source": spec.source,
        "notes": spec.notes,
    }
