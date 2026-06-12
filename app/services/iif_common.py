# ============================================================================
# IIF type correspondences shared by iif_import.py and iif_export.py.
#
# The two directions are deliberately asymmetric: QB2003's ACCNTTYPE
# vocabulary is finer-grained than our six AccountType enums, so export
# picks a specific IIF type from the account-number range while import
# collapses every IIF type back to the nearest enum. Keeping both
# directions in one module (plus tests/test_interchange_roundtrip.py)
# is what stops them drifting apart.
#
# Known-lossy round trips (inherent to the IIF vocabulary, not bugs):
#   ItemType.MATERIAL exports as PART and re-imports as ItemType.PRODUCT.
# ============================================================================

from app.models.accounts import Account, AccountType
from app.models.items import Item, ItemType

IIF_TO_ACCOUNT_TYPE = {
    "BANK": AccountType.ASSET,
    "AR": AccountType.ASSET,
    "OCASSET": AccountType.ASSET,
    "OASSET": AccountType.ASSET,
    "FIXASSET": AccountType.ASSET,
    "AP": AccountType.LIABILITY,
    "OCLIAB": AccountType.LIABILITY,
    "LTLIAB": AccountType.LIABILITY,
    "EQUITY": AccountType.EQUITY,
    "INC": AccountType.INCOME,
    "EXP": AccountType.EXPENSE,
    "COGS": AccountType.COGS,
    # Additional QB types mapped to closest Slowbooks equivalent
    "EXINC": AccountType.INCOME,
    "EXEXP": AccountType.EXPENSE,
    "NONPOSTING": AccountType.ASSET,
}

IIF_TO_ITEM_TYPE = {
    "SERV": ItemType.SERVICE,
    "PART": ItemType.PRODUCT,
    "OTHC": ItemType.LABOR,
    "INVENTORY": ItemType.PRODUCT,
    "NON-INVENTORY": ItemType.MATERIAL,
}


def account_to_iif_type(acct: Account) -> str:
    """Map Slowbooks AccountType + account_number to IIF ACCNTTYPE.

    QB2003 has finer-grained account types than our 6 enums.
    We use account_number ranges to distinguish sub-types within
    each Slowbooks category.
    """
    num = int(acct.account_number or "0")
    atype = acct.account_type.value

    if atype == "asset":
        if 1000 <= num <= 1099:
            return "BANK"
        if num == 1100:
            return "AR"
        if num < 1500:
            return "OCASSET"
        if num < 2000:
            return "FIXASSET"
        return "OASSET"

    if atype == "liability":
        if num == 2000:
            return "AP"
        if num < 2500:
            return "OCLIAB"
        return "LTLIAB"

    return {
        "equity": "EQUITY",
        "income": "INC",
        "expense": "EXP",
        "cogs": "COGS",
    }[atype]


def item_to_iif_type(item: Item) -> str:
    """Map Slowbooks ItemType to IIF INVITEMTYPE."""
    return {
        "service": "SERV",
        "product": "PART",
        "material": "PART",
        "labor": "OTHC",
    }[item.item_type.value]
