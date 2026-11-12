# ============================================================================
# Stored files — every file a company keeps, inside its own database.
#
# The company logo, attachments on invoices, bills and the rest, employee
# documents (the W-4s and I-9s) and scanned receipts waiting to be attached
# all used to be written into one "uploads" folder that every company on a
# desktop install shared. Two companies' invoice #1 "receipt.pdf" were the
# same file, and so was every company's logo (2.18.0 gate: skytech, macbase1
# NEW-14). Now the bytes live here, one row per upload, so a company is one
# file again: a backup, a restore or a copy of it carries every file it has.
# ============================================================================

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    String,
    false,
    func,
)
from sqlalchemy.orm import deferred

from app.database import Base

KIND_LOGO = "logo"
KIND_ATTACHMENT = "attachment"
KIND_EMPLOYEE_DOCUMENT = "employee_document"
# A scanned receipt the person has not saved a document for yet. It becomes
# an attachment when they do, and is swept after a day when they don't.
KIND_RECEIPT_SCAN = "receipt_scan"
# A file dropped on a new bill, expense or card charge before its first save
# (#796). It becomes that document's attachment when the form saves, and is
# swept after a day when the form is abandoned.
KIND_PENDING_ATTACHMENT = "pending_attachment"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StoredFile(Base):
    __tablename__ = "stored_files"
    # An id is never reused (SQLite would hand a deleted row's id to the next
    # insert; PostgreSQL's sequence never does). A logo's address is its id,
    # so a replaced logo gets a new address, and an address or a pointer to a
    # deleted file can never come to name a different one.
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(30), nullable=False, index=True)
    # The name the file had when it was uploaded: what a list shows and what
    # a download is called. Two uploads of "W-4.pdf" are two rows.
    original_name = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=True)
    size = Column(Integer, nullable=False, default=0, server_default="0")
    sha256 = Column(String(64), nullable=True)
    # Deferred: a list of attachments reads their names, never their bytes.
    data = deferred(Column(LargeBinary, nullable=True))
    # The unguessable id a receipt scan is addressed by before any row points
    # at it (/api/ocr/intake/<token>/...). Cleared when it is attached.
    token = Column(String(32), nullable=True, unique=True)
    # The upgrade to 2.18.0 copied this file in from the shared folder
    # earlier versions used. Nothing there says which company wrote a file,
    # so every such copy is flagged and shown with a note.
    legacy_path = Column(String(500), nullable=True)
    from_shared_folder = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # The row pointed at a shared-folder file that was not there to copy.
    missing = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
