# ============================================================================
# OFX/QFX Import Service — parse bank feeds from OFX/QFX files
# Feature 18: Import, dedup by FITID, preview → confirm → auto-match
# ============================================================================

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction
from app.services.bank_rules_engine import apply_bank_rules


def parse_ofx(content: str) -> list[dict]:
    """Parse OFX/QFX file content into a list of transaction dicts.
    Uses ofxparse if available, falls back to simple regex parsing."""
    transactions = []

    try:
        from ofxparse import OfxParser
        from io import BytesIO

        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        ofx = OfxParser.parse(BytesIO(content_bytes))

        for account in ofx.accounts:
            for txn in account.statement.transactions:
                transactions.append(
                    {
                        "fitid": txn.id,
                        "date": (
                            txn.date.date() if hasattr(txn.date, "date") else txn.date
                        ),
                        "amount": Decimal(str(txn.amount)),
                        "payee": txn.payee or txn.memo or "",
                        "memo": txn.memo or "",
                        "type": txn.type or "",
                    }
                )
    except ImportError:
        # Fallback: simple OFX text parser
        import re

        txn_blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", content, re.DOTALL)
        for block in txn_blocks:
            fitid = _extract_tag(block, "FITID")
            dt = _extract_tag(block, "DTPOSTED")
            amt = _extract_tag(block, "TRNAMT")
            name = _extract_tag(block, "NAME")
            memo = _extract_tag(block, "MEMO")

            if dt and amt:
                txn_date = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
                transactions.append(
                    {
                        "fitid": fitid or "",
                        "date": txn_date,
                        "amount": Decimal(amt),
                        "payee": name or "",
                        "memo": memo or "",
                        "type": "",
                    }
                )

    return transactions


def _extract_tag(block: str, tag: str) -> str:
    """Extract value from OFX tag."""
    import re

    match = re.search(rf"<{tag}>([^<\n]+)", block)
    return match.group(1).strip() if match else ""


def import_transactions(
    db: Session,
    bank_account_id: int,
    transactions: list[dict],
    import_source: str = "ofx",
) -> dict:
    """Import parsed transactions, deduplicating by FITID.

    Shared by the OFX upload path and the SimpleFIN feed (which passes
    import_source="simplefin" and SimpleFIN transaction ids as fitids).
    """
    imported = 0
    skipped = 0

    for txn in transactions:
        fitid = txn.get("fitid", "")

        # Dedup by FITID
        if fitid:
            existing = (
                db.query(BankTransaction)
                .filter(
                    BankTransaction.bank_account_id == bank_account_id,
                    BankTransaction.import_id == fitid,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

        bt = BankTransaction(
            bank_account_id=bank_account_id,
            date=txn["date"],
            amount=txn["amount"],
            payee=txn.get("payee", ""),
            description=txn.get("memo", ""),
            import_id=fitid,
            import_source=import_source,
            match_status="unmatched",
        )
        db.add(bt)
        imported += 1

    db.commit()

    # Auto-apply bank rules to newly imported transactions
    if imported > 0:
        apply_bank_rules(db, bank_account_id)

    return {"imported": imported, "skipped": skipped, "total": len(transactions)}
