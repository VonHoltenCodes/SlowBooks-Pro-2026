# ============================================================================
# Journal models — balanced transactions + debit/credit lines.
# The core double-entry engine — every financial event passes through here.
# ============================================================================
# IMPORTANT: The CHECK constraint below rejects split lines with both debit
# AND credit nonzero — it guards against a real class of corruption bugs.
# Do not remove.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    Date,
    Numeric,
    DateTime,
    Text,
    ForeignKey,
    CheckConstraint,
    Index,
    func,
)
from sqlalchemy import event
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Session, relationship, with_loader_criteria

from app.database import Base

# The three values of transaction_lines.basis.
BASIS_BOTH = "both"
BASIS_ACCRUAL = "accrual"
BASIS_CASH = "cash"
BASES = (BASIS_BOTH, BASIS_ACCRUAL, BASIS_CASH)

# Session execution option naming the reading a query wants: "accrual"
# (the default when the option is absent), "cash", or "all" (every line,
# for a void copying the entry it reverses, or a repair).
LEDGER_BASIS_OPTION = "ledger_basis"
READ_ACCRUAL = "accrual"
READ_CASH = "cash"
READ_ALL = "all"


class Transaction(Base):
    __tablename__ = "transactions"
    # Documents' entries are found by their source (a payment's invoices,
    # the cash-basis backfill, a void): without this each lookup scans.
    __table_args__ = (Index("ix_transactions_source", "source_type", "source_id"),)

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    reference = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)  # memo line
    # The memo typed on an expense (#648), kept on its own. Its bank line
    # holds the memo or, with none typed, the payee (the register shows it),
    # so a memo that read like the payee was lost and a payee typed beside a
    # vendor came back as the memo. "" is an expense saved with no memo;
    # NULL is one saved before the column existed, whose memo is still read
    # off its bank line. Other documents leave it empty.
    memo = Column(Text, nullable=True)
    source_type = Column(String(50), nullable=True)
    source_id = Column(Integer, nullable=True)  # FK to the source record
    # An import's own key for the row it came from (#663): what a second
    # upload of QuickBooks Desktop's Transaction Detail by Account checks,
    # so the reference can hold QuickBooks' Num. NULL on everything else.
    source_ref = Column(String(100), nullable=True)
    # The other program's own word for the row, kept by the same import:
    # QuickBooks' Type ("Check", "Bill Pmt -Check", "General Journal"), which
    # the register's Type column shows as "Check (imported)". NULL on
    # everything else.
    source_kind = Column(String(50), nullable=True)
    # A partial entry (#663): one the same import posted from a file that
    # did not hold all of the transaction, its difference on Import
    # Differences. The file's debits less its credits, so what the entry
    # put on Import Differences is a credit of this much (a debit when it is
    # negative). A later file that holds the whole transaction is offered as
    # its replacement. NULL on every other entry.
    source_difference = Column(Numeric(15, 2), nullable=True)
    # Void-and-replace (#689): a posted expense, card charge or transfer with no edit of its own is
    # changed by voiding it and posting a new one; each names the other.
    # Plain ids, no foreign key: both rows always stay (a void keeps the
    # original). NULL on everything else.
    replaces_id = Column(Integer, nullable=True)
    replaced_by_id = Column(Integer, nullable=True)

    # Class tracking dimension (QB-style); NULL groups with Uncategorized
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    # Job-costing dimension (QB "Customer:Job"); NULL = no job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    # The vendor a register entry was paid to or refunded by (#491): a
    # line added from a bank feed names its payee's vendor here, so the
    # vendor's page and payments list include it. Documents (bills,
    # expenses, payments) carry their party on their own row, not here.
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)

    # An adjusting entry (#341): an accountant's correction, marked so the
    # owner can tell it from the daily books, listed by the Adjusting
    # Journal Entries report and left out of a statement on request.
    is_adjusting = Column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    # The sign-in that posted a journal entry (#340): shown on the entry,
    # and what lets an accountant void only what it posted. Documents'
    # postings leave it empty; the audit log names their author.
    posted_by = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lines = relationship(
        "TransactionLine",
        back_populates="transaction",
        cascade="all, delete-orphan",
        foreign_keys="TransactionLine.transaction_id",
    )


class TransactionLine(Base):
    __tablename__ = "transaction_lines"
    __table_args__ = (
        # A line must be exactly one of debit-only or credit-only.
        CheckConstraint(
            "(debit >= 0 AND credit = 0 AND debit > 0) OR (debit = 0 AND credit >= 0 AND credit > 0)",
            name="ck_debit_or_credit",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    debit = Column(Numeric(15, 2), default=0, nullable=False)
    credit = Column(Numeric(15, 2), default=0, nullable=False)
    description = Column(String(300), nullable=True)  # split memo, 0x18
    # Per-line job / class; NULL falls back to the transaction header
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    # The name on a register line (#606): the donor or customer a line of a
    # bank entry came from. A label, not money.
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    cost_type = Column(String(20), nullable=True)  # for roll-ups when no code
    # Nonprofit function (program | management | fundraising); defaulted
    # from the class at posting time, NULL = not yet allocated
    function = Column(String(20), nullable=True)
    # Cost billable to the job's customer; set when it is pulled onto an invoice
    is_billable = Column(Boolean, nullable=False, default=False)
    billed_invoice_line_id = Column(
        Integer, ForeignKey("invoice_lines.id"), nullable=True
    )
    # Bank register: a line on a bank/credit-card account is "cleared" when
    # it matches a statement line or was ticked in a reconciliation, and it
    # carries the reconciliation that closed it (then it can't be voided).
    cleared = Column(Boolean, nullable=False, default=False)
    reconciliation_id = Column(Integer, ForeignKey("reconciliations.id"), nullable=True)
    # Undeposited Funds: on a payment's money-in line, the deposit (its
    # journal entry) that took it to the bank; NULL = not deposited yet, or
    # deposited before deposits kept their list (see routes/deposits.py).
    deposit_transaction_id = Column(
        Integer, ForeignKey("transactions.id"), nullable=True, index=True
    )
    # Which basis the line counts on (#483, docs/design/cash-basis.md):
    # "both" (the default: the figure is the same either way), "accrual"
    # (an invoice, bill, credit memo... and their voids) or "cash" (the
    # income / expense recognition a payment writes). The accrual view is
    # both + accrual, the cash view both + cash; see LEDGER_BASIS_OPTION.
    basis = Column(
        String(10), nullable=False, default=BASIS_BOTH, server_default=BASIS_BOTH
    )
    # On a cash recognition line: the invoice or bill it recognizes, so an
    # edit of that document can find and rewrite it (#483). Plain integers,
    # no foreign key: a line is history and must outlive its document.
    recognizes_invoice_id = Column(Integer, nullable=True, index=True)
    recognizes_bill_id = Column(Integer, nullable=True, index=True)

    transaction = relationship(
        "Transaction", back_populates="lines", foreign_keys=[transaction_id]
    )
    account = relationship("Account", back_populates="transaction_lines")


def read_basis_of(options: dict) -> str:
    """The reading a statement's execution options ask for; accrual when
    they say nothing."""
    value = options.get(LEDGER_BASIS_OPTION) if options else None
    return value if value in (READ_ACCRUAL, READ_CASH, READ_ALL) else READ_ACCRUAL


@event.listens_for(Session, "do_orm_execute")
def _ledger_reads_one_basis(state) -> None:
    """Every ORM select that touches a ledger line reads one basis (#483).

    Without an execution option the reading is accrual — both + accrual —
    so each of the hundred-odd query sites that sum TransactionLine keeps
    the figures it had before cash lines existed. A report that wants the
    cash basis passes ``execution_options(ledger_basis="cash")``; a path
    that needs every line (a void reversing a payment's entry) passes
    "all". The criteria rides into joins, column sums, subqueries and
    relationship loads alike (with_loader_criteria), which a filter at
    each site could not promise."""
    if not state.is_select:
        return
    reading = read_basis_of(state.execution_options)
    if reading == READ_ALL:
        return
    hidden = BASIS_CASH if reading == READ_ACCRUAL else BASIS_ACCRUAL
    state.statement = state.statement.options(
        with_loader_criteria(
            TransactionLine, lambda cls: cls.basis != hidden, include_aliases=True
        )
    )
