"""Read-only Mercury bank feed. Only GET requests leave this module."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx

API_URL = "https://api.mercury.com/api/v1"
PAGE_SIZE = 1000


class MercuryError(Exception):
    """A message safe to show without including credentials or bank responses."""


def _id(value) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise MercuryError("Mercury returned an invalid account or transaction ID")


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) >= Decimal("10000000000000"):
            raise ValueError
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise MercuryError("Mercury returned an invalid amount")


def _pages(token: str, resource: str, params: dict) -> list[dict]:
    """Read all cursor pages from a fixed API host; never follow redirects."""
    rows, seen = [], set()
    query = {**params, "limit": PAGE_SIZE, "order": "asc"}
    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _ in range(1000):
                response = client.get(f"{API_URL}/{resource}", params=query)
                if response.status_code in (401, 403):
                    raise MercuryError(
                        "Mercury refused the API token. Check its read permissions or reconnect with a new read-only token."
                    )
                if response.status_code == 429:
                    raise MercuryError(
                        "Mercury's request limit is reached. Try syncing again later."
                    )
                if response.status_code != 200:
                    raise MercuryError(
                        "Mercury is unavailable right now. Try again later."
                    )
                try:
                    data = response.json(parse_float=Decimal)
                except ValueError:
                    raise MercuryError("Mercury returned an unreadable response")
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get(resource), list)
                    or not isinstance(data.get("page"), dict)
                ):
                    raise MercuryError("Mercury returned an unreadable response")
                batch = data[resource]
                for row in batch:
                    if not isinstance(row, dict):
                        raise MercuryError("Mercury returned an unreadable response")
                    row_id = _id(row.get("id"))
                    if row_id in seen:
                        raise MercuryError(
                            "Mercury repeated a page. Try syncing again."
                        )
                    seen.add(row_id)
                    rows.append(row)
                if not data["page"].get("nextPage"):
                    return rows
                if not batch:
                    raise MercuryError(
                        "Mercury returned an empty page with more results"
                    )
                # start_after is exclusive; the last row is our cursor.
                query["start_after"] = _id(batch[-1]["id"])
    except httpx.RequestError:
        raise MercuryError(
            "Could not reach Mercury right now. Try again later."
        ) from None
    raise MercuryError(
        "This sync exceeds the page limit. Choose a more recent start date."
    )


def fetch_accounts(token: str) -> list[dict]:
    """Keep display fields only, never routing or full account numbers."""
    accounts = []
    snapshot_at = datetime.now(timezone.utc).isoformat()
    for row in _pages(token, "accounts", {}):
        if row.get("type") != "mercury" or row.get("status") != "active":
            continue
        accounts.append(
            {
                "id": _id(row["id"]),
                "name": str(row.get("nickname") or row.get("name") or "Mercury")[:200],
                "last_four": str(row.get("accountNumber") or "")[-4:],
                "balance": str(_money(row.get("currentBalance"))),
                "available_balance": str(_money(row.get("availableBalance"))),
                "currency": "USD",
                "_snapshot_at": snapshot_at,
            }
        )
    return accounts


def fetch_transactions(token: str, account_id: str, since: date) -> list[dict]:
    # postedStart finds transactions created earlier but posted in this window.
    return _pages(
        token,
        "transactions",
        {
            "accountId": [_id(account_id)],
            "postedStart": since.isoformat(),
        },
    )


def to_import_rows(
    transactions: list[dict], account_id: str
) -> tuple[list[dict], list[str]]:
    """Posted USD movements become statement lines, not ledger postings."""
    rows, warnings = [], []
    for txn in transactions:
        if _id(txn.get("accountId")) != account_id:
            raise MercuryError("Mercury returned a transaction for a different account")
        if txn.get("status") == "reversed":
            warnings.append(
                "Mercury reports a reversed transaction. Review it in Mercury and reconcile any matching entry in SlowBooks."
            )
            continue
        if txn.get("status") != "sent":
            continue
        if not txn.get("postedAt"):
            continue
        try:
            posted = datetime.fromisoformat(txn["postedAt"]).date()
        except (ValueError, TypeError):
            raise MercuryError("Mercury returned an invalid posting date")
        rows.append(
            {
                "fitid": f"mercury:{account_id}:{_id(txn.get('id'))}",
                "date": posted,
                "amount": _money(txn.get("amount")),
                "payee": str(txn.get("counterpartyName") or "")[:200],
                "memo": " · ".join(
                    str(txn[k])
                    for k in ("bankDescription", "externalMemo", "note")
                    if txn.get(k)
                )[:500],
            }
        )
    return rows, list(dict.fromkeys(warnings))
