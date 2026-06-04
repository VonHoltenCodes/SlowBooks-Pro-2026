# ============================================================================
# State income-tax reciprocity agreements.
# ----------------------------------------------------------------------------
# When an employee works in one state but lives in another and the two have a
# reciprocity agreement, income tax is withheld for the RESIDENCE state, not
# the work state. (Unemployment tax and disability/leave premiums still follow
# the work state — reciprocity covers income tax only.)
#
# This is a representative, not exhaustive, map of US agreements.
# ============================================================================

# work_state -> set of residence states with which it has an agreement.
RECIPROCITY: dict[str, set[str]] = {
    "IN": {"KY", "MI", "OH", "PA", "WI"},
    "KY": {"IL", "IN", "MI", "OH", "VA", "WV", "WI"},
    "MI": {"IL", "IN", "KY", "MN", "OH", "WI"},
    "OH": {"IN", "KY", "MI", "PA", "WV"},
    "PA": {"IN", "MD", "NJ", "OH", "VA", "WV"},
    "NJ": {"PA"},
    "IL": {"IA", "KY", "MI", "WI"},
    "IA": {"IL"},
    "WI": {"IL", "IN", "KY", "MI"},
    "MN": {"MI", "ND"},
    "ND": {"MN", "MT"},
    "MT": {"ND"},
    "MD": {"DC", "PA", "VA", "WV"},
    "VA": {"DC", "KY", "MD", "PA", "WV"},
    "WV": {"KY", "MD", "OH", "PA", "VA"},
    "DC": {"MD", "VA"},
}


def has_reciprocity(work_state: str | None, residence_state: str | None) -> bool:
    """True if the work / residence pair has an income-tax reciprocity agreement."""
    if not work_state or not residence_state:
        return False
    ws, rs = work_state.strip().upper(), residence_state.strip().upper()
    return rs in RECIPROCITY.get(ws, set())


def withholding_state(
    work_state: str | None, residence_state: str | None
) -> str | None:
    """Resolve which state's income tax should be withheld.

    Returns the residence state when a reciprocity agreement applies, otherwise
    the work state.
    """
    if not residence_state:
        return work_state
    if has_reciprocity(work_state, residence_state):
        return residence_state.strip().upper()
    return work_state
