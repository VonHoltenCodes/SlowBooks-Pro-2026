# ============================================================================
# A deposit's other lines (#792) — money on a Make Deposits slip that was
# never invoiced: a reimbursement, a vendor's refund, a rebate. Each line
# names the account it goes to, who it came from (a customer, a vendor or a
# name typed in), the check number, the method, its memo, class and job,
# and its amount.
#
# The posting is the deposit's own journal entry: DR bank for the whole
# deposit, CR Undeposited Funds for the payments it took, and one credit per
# line here (with the line's customer, class and job). These rows hold what
# the ledger line cannot — the vendor or typed name, the check number and
# the method — so the slip prints the line with the checks, and an edit
# reposts the entry from them.
# ============================================================================

from sqlalchemy import Column, ForeignKey, Integer, Numeric, String

from app.database import Base


class DepositOtherLine(Base):
    __tablename__ = "deposit_other_lines"

    id = Column(Integer, primary_key=True, index=True)
    deposit_transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False, default=0, server_default="0")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    # a name that is neither a customer nor a vendor, typed on the line
    received_from = Column(String(200), nullable=True)
    memo = Column(String(300), nullable=True)
    check_number = Column(String(50), nullable=True)
    method = Column(String(30), nullable=True)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
