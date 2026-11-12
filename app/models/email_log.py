# ============================================================================
# Email Log — tracks all emails sent from the system
# Feature 8: Invoice email delivery via SMTP
# ============================================================================

from sqlalchemy import Column, Integer, String, DateTime, Text, func

from app.database import Base


class EmailLog(Base):
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(50), nullable=False)  # invoice, estimate, statement
    entity_id = Column(Integer, nullable=False)
    recipient = Column(String(200), nullable=False)
    # Copies on the send (#611): the Cc addresses as sent, and the Bcc
    # addresses so the log says who was blind-copied even though the mail
    # itself never does. Comma-separated, null when none. Text, not a sized
    # String: the mail has already gone out by the time the row is written,
    # so a list the column cannot hold would error a send that succeeded.
    cc = Column(Text, nullable=True)
    bcc = Column(Text, nullable=True)
    subject = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False)  # sent, failed
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    # Who sent it (#894): the acting name the audit log writes, stamped on
    # every new row by the audit hooks' before_flush (app.services.audit).
    # NULL on rows from before 2.32.0.
    username = Column(String(100), nullable=True)
