# ============================================================================
# State Tax package — per-state payroll withholding engine registry.
# ----------------------------------------------------------------------------
# get_engine() resolves a 2-letter state code (case-insensitive) to a concrete
# StateEngine. Dedicated engines exist for WA, CA, NY, and OR; every other
# state and DC is a TableEngine driven by tables.py. An unknown code (a typo,
# a territory) falls back to a zero-rate GenericStateEngine so it contributes
# no state income tax rather than a wrong guess.
#
# Engines are stateless, so the registry holds module-level singletons, one
# set per year SlowBooks keeps tables for (#818): get_engine(code, year)
# returns the engine with that year's figures. Washington, California and
# Oregon keep 2025's and 2026's; every other state keeps 2026's only, and a
# run dated 2025 there uses them and says so (state_tables_year).
# ============================================================================

from app.services.state_tax.base import StateEngine, StateTaxResult
from app.services.state_tax.ca import CAEngine
from app.services.state_tax.generic import GenericStateEngine
from app.services.state_tax.ny import NYEngine
from app.services.state_tax.oregon import OregonEngine
from app.services.state_tax.table_engine import TableEngine, describe
from app.services.state_tax.tables import DEDICATED, STATES
from app.services.state_tax.wa import WAEngine

__all__ = [
    "StateEngine",
    "StateTaxResult",
    "GenericStateEngine",
    "WAEngine",
    "CAEngine",
    "NYEngine",
    "OregonEngine",
    "TableEngine",
    "get_engine",
    "state_name",
    "state_tables_year",
    "list_states",
    "is_supported",
]

# Registry of dedicated engines, keyed by uppercase 2-letter state code: the
# newest year's figures.
_REGISTRY: dict[str, StateEngine] = {
    "WA": WAEngine(),
    "CA": CAEngine(),
    "NY": NYEngine(),
    "OR": OregonEngine(),
}
for _code, _spec in STATES.items():
    _REGISTRY.setdefault(_code, TableEngine(_spec))

# The newest year the state tables are for, and the engines of each earlier
# year a state keeps its own figures for.
NEWEST_YEAR = 2026
_BY_YEAR: dict[int, dict[str, StateEngine]] = {
    2025: {"WA": WAEngine(2025), "CA": CAEngine(2025), "OR": OregonEngine(2025)},
    NEWEST_YEAR: _REGISTRY,
}

# Shared fallback for any state without a dedicated engine (flat_rate 0).
_GENERIC = GenericStateEngine()


def state_tables_year(state_code: str | None, year: int | None) -> int | None:
    """The year whose figures a run dated in `year` is figured with for the
    state: `year` itself when SlowBooks keeps the state's tables for it,
    else the nearest year it keeps them for. None for a blank or unknown
    code (no figures to be dated)."""
    code = (state_code or "").strip().upper()
    if code not in _REGISTRY:
        return None
    if year is None:
        return NEWEST_YEAR
    kept = sorted(y for y, reg in _BY_YEAR.items() if code in reg)
    if year in kept:
        return year
    return min(kept, key=lambda y: (abs(y - year), -y))


def state_name(state_code: str) -> str:
    """A state's name ("Texas"), or the code itself when unknown."""
    code = (state_code or "").strip().upper()
    if code in STATES:
        return STATES[code].name
    if code in DEDICATED:
        return DEDICATED[code][0]
    return code


def get_engine(state_code: str | None, year: int | None = None) -> StateEngine:
    """Return the payroll engine for a 2-letter state code (case-insensitive)
    with `year`'s figures (the nearest year kept for the state; the newest
    when None).

    Unknown or missing codes return a zero-rate GenericStateEngine.
    """
    if not state_code:
        return _GENERIC
    code = state_code.strip().upper()
    if code not in _REGISTRY:
        return _GENERIC
    return _BY_YEAR[state_tables_year(code, year)][code]


def is_supported(state_code: str | None) -> bool:
    return bool(state_code) and state_code.strip().upper() in _REGISTRY


def list_states() -> list[dict]:
    """Catalog of every supported jurisdiction: method, summary, deductions,
    other items, SUTA base, the publication the figures came from."""
    out = []
    for code in sorted(_REGISTRY):
        if code in STATES:
            out.append(describe(STATES[code]))
        else:
            name, base, summary, year, source = DEDICATED[code]
            out.append(
                {
                    "code": code,
                    "name": name,
                    "method": "dedicated",
                    "summary": summary,
                    "suta_wage_base": float(base),
                    "uses_local_rate": False,
                    "uses_rate_election": False,
                    "year": year,
                    "source": source,
                    "notes": f"Dedicated engine: app/services/state_tax/{'oregon' if code == 'OR' else code.lower()}.py.",
                }
            )
    return out
