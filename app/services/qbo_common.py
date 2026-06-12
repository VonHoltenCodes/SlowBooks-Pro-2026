# ============================================================================
# QBO type correspondences and QBOMapping helpers shared by qbo_import.py
# and qbo_export.py (both previously carried private copies).
#
# Import collapses QBO's account-type vocabulary onto our six AccountType
# enums; export emits one representative (qbo_type, qbo_subtype) pair per
# enum. tests/test_interchange_roundtrip.py asserts the two directions
# stay consistent.
#
# Known-lossy round trips (inherent to the QBO vocabulary, not bugs):
#   ItemType.LABOR exports as "Service" and re-imports as ItemType.SERVICE.
# ============================================================================

from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.models.items import ItemType
from app.models.qbo_mapping import QBOMapping

QBO_TO_ACCOUNT_TYPE = {
    "Bank": AccountType.ASSET,
    "Accounts Receivable": AccountType.ASSET,
    "Other Current Asset": AccountType.ASSET,
    "Fixed Asset": AccountType.ASSET,
    "Other Asset": AccountType.ASSET,
    "Accounts Payable": AccountType.LIABILITY,
    "Credit Card": AccountType.LIABILITY,
    "Other Current Liability": AccountType.LIABILITY,
    "Long Term Liability": AccountType.LIABILITY,
    "Equity": AccountType.EQUITY,
    "Income": AccountType.INCOME,
    "Other Income": AccountType.INCOME,
    "Expense": AccountType.EXPENSE,
    "Other Expense": AccountType.EXPENSE,
    "Cost of Goods Sold": AccountType.COGS,
}

# AccountType -> (QBO AccountType, QBO AccountSubType)
ACCOUNT_TYPE_TO_QBO = {
    AccountType.ASSET: ("Other Current Asset", "Other Current Asset"),
    AccountType.LIABILITY: ("Other Current Liability", "Other Current Liability"),
    AccountType.EQUITY: ("Equity", "Opening Balance Equity"),
    AccountType.INCOME: ("Income", "Sales of Product Income"),
    AccountType.EXPENSE: ("Expense", "Other Miscellaneous Service Cost"),
    AccountType.COGS: ("Cost of Goods Sold", "Supplies and Materials - COGS"),
}

QBO_TO_ITEM_TYPE = {
    "Service": ItemType.SERVICE,
    "Inventory": ItemType.PRODUCT,
    "Group": ItemType.PRODUCT,
    "NonInventory": ItemType.MATERIAL,
}

ITEM_TYPE_TO_QBO = {
    ItemType.SERVICE: "Service",
    ItemType.PRODUCT: "Inventory",
    ItemType.MATERIAL: "NonInventory",
    ItemType.LABOR: "Service",
}


def get_mapping_by_slowbooks_id(
    db: Session, entity_type: str, slowbooks_id: int
) -> QBOMapping:
    """Look up existing mapping by Slowbooks ID."""
    return (
        db.query(QBOMapping)
        .filter(
            QBOMapping.entity_type == entity_type,
            QBOMapping.slowbooks_id == slowbooks_id,
        )
        .first()
    )


def get_mapping_by_qbo_id(db: Session, entity_type: str, qbo_id: str) -> QBOMapping:
    """Look up existing mapping by QBO ID."""
    return (
        db.query(QBOMapping)
        .filter(
            QBOMapping.entity_type == entity_type,
            QBOMapping.qbo_id == str(qbo_id),
        )
        .first()
    )


def create_mapping(
    db: Session,
    entity_type: str,
    slowbooks_id: int,
    qbo_id: str,
    sync_token: str = None,
):
    """Create a new QBO <-> Slowbooks mapping."""
    m = QBOMapping(
        entity_type=entity_type,
        slowbooks_id=slowbooks_id,
        qbo_id=str(qbo_id),
        qbo_sync_token=sync_token,
    )
    db.add(m)
