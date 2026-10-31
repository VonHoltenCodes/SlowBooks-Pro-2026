# ============================================================================
# Memorized transactions (#605): a bill or a journal entry kept whole, with
# a schedule. Entered by the scheduler on its date (auto-enter) or listed
# as due until someone presses Enter now (remind). Recurring invoices keep
# their own table (app/models/recurring.py); the Memorized transactions
# page lists both.
# ============================================================================

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.orm import relationship

from app.database import Base

KIND_BILL = "bill"
KIND_JOURNAL = "journal"
KINDS = (KIND_BILL, KIND_JOURNAL)

MODE_AUTO = "auto"
MODE_REMIND = "remind"
MODES = (MODE_AUTO, MODE_REMIND)


class MemorizedTransaction(Base):
    __tablename__ = "memorized_transactions"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(10), nullable=False, index=True)  # bill | journal
    name = Column(String(120), nullable=False)  # what the list calls it
    mode = Column(String(10), nullable=False, default=MODE_REMIND)  # auto | remind

    # The schedule: a unit and a count (monthly + 3 is quarterly's twin;
    # QuickBooks' "Every two weeks" is weekly + 2), the date the next entry
    # is for, and the day of the month it holds to, so a schedule begun on
    # the 31st is entered on the 28th of February and the 31st of March
    # rather than drifting to the 28th for good.
    frequency = Column(String(20), nullable=False)
    interval = Column(Integer, nullable=False, default=1)
    start_date = Column(Date, nullable=False)
    next_date = Column(Date, nullable=False, index=True)
    anchor_day = Column(Integer, nullable=False)
    end_date = Column(Date, nullable=True)
    occurrences_limit = Column(Integer, nullable=True)
    entered_count = Column(Integer, nullable=False, default=0)
    # Paused when False. A schedule past its end date or its last
    # occurrence has ended, which is read off those columns, not stored.
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())
    last_entered_on = Column(Date, nullable=True)
    # The bill (kind bill) or the transaction (kind journal) entered last.
    last_document_id = Column(Integer, nullable=True)

    # A bill's header
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)
    # Blank: dated the entry day with the vendor's initials, as Enter Bill
    # numbers a bill with no number. Set: used as it is, "-2", "-3"... when
    # that vendor already has one.
    bill_number = Column(String(100), nullable=True)
    terms = Column(String(50), nullable=True)
    # The list entry these terms named when the template was saved (#664):
    # each document it makes is tied to it. NULL for text that named none
    # then, read as the old parser read it, never the list.
    payment_term_id = Column(Integer, ForeignKey("payment_terms.id"), nullable=True)
    tax_rate = Column(Numeric(7, 6), nullable=False, default=0)
    notes = Column(Text, nullable=True)

    # A journal entry's header
    description = Column(Text, nullable=True)
    reference = Column(String(100), nullable=True)
    is_adjusting = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    basis = Column(String(10), nullable=True)  # both | accrual | cash

    # Both
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vendor = relationship("Vendor")
    lines = relationship(
        "MemorizedTransactionLine",
        back_populates="memorized",
        cascade="all, delete-orphan",
        order_by="MemorizedTransactionLine.line_order",
    )


class MemorizedTransactionLine(Base):
    __tablename__ = "memorized_transaction_lines"

    id = Column(Integer, primary_key=True, index=True)
    memorized_id = Column(
        Integer,
        ForeignKey("memorized_transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_order = Column(Integer, nullable=False, default=0)

    # Shared by both kinds
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    description = Column(Text, nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    cost_code_id = Column(Integer, ForeignKey("cost_codes.id"), nullable=True)
    cost_type = Column(String(20), nullable=True)
    function = Column(String(20), nullable=True)  # nonprofit function
    is_billable = Column(Boolean, nullable=False, default=False, server_default=false())

    # A bill line: an item, a quantity and a rate (an amount alone is
    # stored as 1 x amount, as Enter Bill stores it)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=True)
    quantity = Column(Numeric(12, 4), nullable=True)
    rate = Column(Numeric(17, 4), nullable=True)

    # A journal line: one side or the other
    debit = Column(Numeric(15, 2), nullable=True)
    credit = Column(Numeric(15, 2), nullable=True)

    memorized = relationship("MemorizedTransaction", back_populates="lines")
    account = relationship("Account")
    item = relationship("Item")
