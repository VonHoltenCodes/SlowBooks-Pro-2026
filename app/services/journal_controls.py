# ============================================================================
# Journal-style entries stay off Accounts Payable and Accounts Receivable
# (#897).
#
# A/P's balance is the sum of the open bills, A/R's the sum of the open
# invoices: the agings, the vendor and customer balances, Pay Bills and
# Receive Payments read those documents. A job cost entry or a hand journal
# entry that puts a line on either control account moves the ledger and none
# of them, so the account stops tying to its subledger (Books Check, #893,
# found one: a job cost entry crediting A/P with no vendor behind it). The
# usual reason is month-end, a subcontractor's bill not in yet: that is an
# accrual, and 2010 Accrued Job Costs is its home now.
#
# What a person enters is refused, create and edit, window and API: a job
# cost entry (and an allocation), a manual journal entry, a memorized
# journal entry, in the shape #785 gave purchase lines. An edit that leaves a
# line on the control account it was already saved on goes through (the
# entries from before 2.32 are left exactly as they are). The imports (IIF,
# QuickBooks Online, the QuickBooks ledger reports) keep accepting such
# lines: QuickBooks allows them when they name the vendor or customer, and
# the agings read a named one as a payable or receivable item
# (services/control_journal_lines.py).
# ============================================================================

from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.services import control_accounts

JOB_COST = "job_cost"
JOURNAL = "journal"


def refusal_sentence(
    db: Session, account: Account, label: str, kind: str = JOURNAL
) -> str:
    """The sentence refusing `account` on a journal-style line, or "" when
    the account is neither Accounts Receivable nor Accounts Payable. `label`
    starts it ("Line 2"); the account is named as the chart names it (with
    its number when the company uses account numbers)."""
    from app.services.account_label import label_for

    which = control_accounts.is_receivable_or_payable(account)
    if which is None:
        return ""
    named = label_for(account, db)
    if which == control_accounts.PAYABLE:
        sentence = (
            f"{label} can't post to {named}, which holds vendor bills; enter "
            "the cost as a bill (Enter Bill, coded to the job) so it ages, "
            "shows on the vendor and can be paid."
        )
        if kind == JOB_COST:
            sentence += (
                " Until the bill arrives, credit "
                f"{control_accounts.ACCRUED_JOB_COSTS} Accrued Job Costs "
                "instead; the entry reverses itself on its Reverses on date."
            )
        return sentence
    return (
        f"{label} can't post to {named}, which holds customer invoices; bill "
        "the customer with Create Invoice, or credit them with a credit memo, "
        "so it ages, shows on the customer and can be collected."
    )


def refuse_control_line(
    db: Session,
    account: Optional[Account],
    line_no: int,
    kind: str = JOURNAL,
    kept: Iterable[int] = (),
    label: Optional[str] = None,
) -> None:
    """Refuse (422) a journal-style line on Accounts Receivable or Accounts
    Payable, unless the account is one of `kept` (the accounts an edited
    entry's lines were already saved on). `label` starts the sentence in
    place of "Line N" ("The allocation")."""
    if account is None or account.id in set(kept):
        return
    sentence = refusal_sentence(db, account, label or f"Line {line_no}", kind)
    if not sentence:
        return
    from app.services.terminology import terms_from_db

    raise HTTPException(status_code=422, detail=terms_from_db(db).text(sentence))


def kept_control_accounts(account_ids: Iterable[Optional[int]]) -> frozenset:
    """The account ids an existing entry's lines sit on, for an edit's
    `kept`: a line already on a control account may stay there."""
    return frozenset(i for i in account_ids if i)
