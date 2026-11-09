# ============================================================================
# A bank account's check details (#739): what blank check stock needs that
# pre-printed stock carries — the bank's name and city, the fractional
# routing number and the routing and account numbers of the MICR line. One
# row per bank account, made the first time they are entered. The account
# number is encrypted at rest like the ACH company account; its last four
# are kept in the clear for the page.
# ============================================================================

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func

from app.database import Base


class BankCheckDetails(Base):
    __tablename__ = "bank_check_details"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True
    )
    bank_name = Column(String(100), nullable=False, default="", server_default="")
    bank_city_state = Column(String(100), nullable=False, default="", server_default="")
    fractional_routing = Column(
        String(30), nullable=False, default="", server_default=""
    )
    routing_number = Column(String(9), nullable=False, default="", server_default="")
    account_number_enc = Column(String(255), nullable=True)
    account_last_four = Column(String(4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
