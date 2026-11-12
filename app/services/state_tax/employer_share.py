# ============================================================================
# The paid-leave employer share as the agency determined it (#825).
# ----------------------------------------------------------------------------
# Washington PFML charges the employer share from 50 employees working in
# Washington and Paid Leave Oregon from 25 employees; the pay run counts the
# company's active employees at the moment it is figured. Washington's ESD
# and Oregon's OED size an employer over their own period and tell it so
# once a year, so a run's snapshot can disagree with the agency's letter.
#
# A setting per state says which to follow: "yes" (the agency determined the
# share applies), "no" (it does not) or "headcount" (the run's count, the
# default and the behaviour before the setting). The run reads it, the stub
# and the run say which applied and why, and the run warns when the
# headcount and the setting disagree.
# ============================================================================

from dataclasses import dataclass

from sqlalchemy.orm import Session

FOLLOW_HEADCOUNT = "headcount"
SHARE_VALUES = frozenset({"yes", "no", FOLLOW_HEADCOUNT})

# The settings name shown on Settings → Payroll and in every sentence.
SETTING_LABEL = "Employer share applies (as the agency determined)"


@dataclass(frozen=True)
class LeaveProgram:
    state: str
    program: str
    agency: str
    minimum: int
    setting_key: str
    # what the headcount counts, for a sentence ("employees working in
    # Washington")
    counted: str


PROGRAMS = {
    "WA": LeaveProgram(
        state="WA",
        program="Washington PFML",
        agency="ESD",
        minimum=50,
        setting_key="payroll_wa_pfml_employer_share",
        counted="employees working in Washington",
    ),
    "OR": LeaveProgram(
        state="OR",
        program="Paid Leave Oregon",
        agency="OED",
        minimum=25,
        setting_key="payroll_or_paid_leave_employer_share",
        counted="employees",
    ),
}

SETTING_KEYS = {p.setting_key: p for p in PROGRAMS.values()}


def refusal(key: str) -> str:
    """The sentence a value other than yes, no or headcount is refused with."""
    p = SETTING_KEYS[key]
    return (
        f"{p.program}, {SETTING_LABEL}, must be yes ({p.agency} "
        "determined the employer share "
        "applies), no (it determined it does not) or headcount (follow the "
        f"headcount at each pay run: {p.minimum} or more {p.counted}). "
        "Nothing was saved."
    )


def setting_value(db: Session, state: str) -> str:
    """The company's setting for the state's program: yes, no or headcount.
    Unset (or anything else stored) is headcount, today's behaviour."""
    from app.services.settings_service import get_setting_raw

    p = PROGRAMS.get(state)
    if p is None:
        return FOLLOW_HEADCOUNT
    value = (get_setting_raw(db, p.setting_key) or "").strip().lower()
    return value if value in SHARE_VALUES else FOLLOW_HEADCOUNT


def _people(count: int, counted: str) -> str:
    if count == 1:
        return "1 " + counted.replace("employees", "employee", 1)
    return f"{count} {counted}"


@dataclass(frozen=True)
class ShareDecision:
    # None: the engine follows the headcount, exactly as before the setting
    applies: bool | None
    # the stub's (and the run's) sentence, None when the headcount decides
    note: str | None = None
    # the run's warning when the headcount and the setting disagree
    warning: str | None = None


def decide(state: str, value: str, count: int) -> ShareDecision:
    """Whether the state's employer share applies on this run, from the
    company's setting (``value``) and the run's headcount (``count``: every
    active employee for Oregon, the active employees working in Washington
    for Washington)."""
    p = PROGRAMS.get(state)
    if p is None or value not in ("yes", "no"):
        return ShareDecision(applies=None)
    applies = value == "yes"
    by_count = count >= p.minimum
    people = _people(count, p.counted)
    if applies:
        note = (
            f"{p.program} employer share charged: Settings → Payroll → "
            f"{SETTING_LABEL} says yes, as {p.agency} determined "
            f"({people} at this run)."
        )
    else:
        note = (
            f"{p.program} employer share not charged: Settings → Payroll → "
            f"{SETTING_LABEL} says no, as {p.agency} determined "
            f"({people} at this run)."
        )
    warning = None
    if applies != by_count:
        if applies:
            what = "was charged"
            why = f"fewer than the {p.minimum} the share starts at"
        else:
            what = "was not charged"
            why = f"{p.minimum} or more"
        warning = (
            f"{p.program}: the employer share {what} because Settings → "
            f"Payroll → {SETTING_LABEL} says {value}, though this run counts "
            f"{people}, {why}. If {p.agency}'s latest letter says otherwise, "
            "change the setting and run the stubs again."
        )
    return ShareDecision(applies=applies, note=note, warning=warning)
