# ============================================================================
# State Tax package — per-state payroll withholding engine registry.
# ----------------------------------------------------------------------------
# get_engine() resolves a 2-letter state code (case-insensitive) to a concrete
# StateEngine. Dedicated engines exist for WA, CA, NY, and OR; any unknown or
# missing code falls back to a zero-rate GenericStateEngine so an unsupported
# state contributes no state income tax rather than a wrong guess.
#
# Engines are stateless, so the registry holds module-level singletons.
# ============================================================================

from app.services.state_tax.base import StateEngine, StateTaxResult
from app.services.state_tax.ca import CAEngine
from app.services.state_tax.generic import GenericStateEngine
from app.services.state_tax.ny import NYEngine
from app.services.state_tax.oregon import OregonEngine
from app.services.state_tax.wa import WAEngine

__all__ = [
    "StateEngine",
    "StateTaxResult",
    "GenericStateEngine",
    "WAEngine",
    "CAEngine",
    "NYEngine",
    "OregonEngine",
    "get_engine",
]

# Registry of dedicated engines, keyed by uppercase 2-letter state code.
_REGISTRY: dict[str, StateEngine] = {
    "WA": WAEngine(),
    "CA": CAEngine(),
    "NY": NYEngine(),
    "OR": OregonEngine(),
}

# Shared fallback for any state without a dedicated engine (flat_rate 0).
_GENERIC = GenericStateEngine()


def get_engine(state_code: str | None) -> StateEngine:
    """Return the payroll engine for a 2-letter state code (case-insensitive).

    Unknown or missing codes return a zero-rate GenericStateEngine.
    """
    if not state_code:
        return _GENERIC
    return _REGISTRY.get(state_code.strip().upper(), _GENERIC)
