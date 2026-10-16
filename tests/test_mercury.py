"""Mercury feed: API pagination, credential protection, and bank-review integration."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.models.accounts import Account, AccountType
from app.models.bank_rules import BankRule
from app.models.banking import BankAccount, BankTransaction
from app.models.settings import Settings
from app.models.transactions import Transaction
from app.services import mercury_service as mercury
from app.services.settings_service import get_setting_raw, set_setting

ACCOUNT = "00000000-0000-4000-8000-000000000001"
SECOND_ACCOUNT = "00000000-0000-4000-8000-000000000002"
TXN = "10000000-0000-4000-8000-000000000001"
TOKEN = "mercury-test-token-not-a-real-credential"
ACCOUNT_DATA = {
    "id": ACCOUNT,
    "name": "Checking",
    "nickname": "My LLC",
    "type": "mercury",
    "status": "active",
    "currentBalance": 2500.25,
    "availableBalance": 2400.25,
    "accountNumber": "123456789012",
    "routingNumber": "123456789",
}


def transaction(**changes):
    return {
        "id": TXN,
        "accountId": ACCOUNT,
        "amount": "2500.25",
        "status": "sent",
        "createdAt": "2025-12-01T12:00:00Z",
        "postedAt": "2026-08-10T12:00:00Z",
        "counterpartyName": "Client LLC",
        "bankDescription": "ACH credit",
        "externalMemo": "Invoice 42",
        "note": "August consulting",
        **changes,
    }


@pytest.fixture
def transport(monkeypatch):
    original_client = httpx.Client
    requests = []
    responses = {}

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.mercury.com"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        answer = responses[request.url.path]
        return answer(request) if callable(answer) else httpx.Response(200, json=answer)

    def make_client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mercury.httpx, "Client", make_client)
    responses["/api/v1/accounts"] = {"accounts": [ACCOUNT_DATA], "page": {}}
    responses["/api/v1/transactions"] = {"transactions": [transaction()], "page": {}}
    return responses, requests


@pytest.fixture
def bank(db_session, seed_accounts):
    bank = BankAccount(
        name="Mercury LLC", bank_name="Mercury", account_id=seed_accounts["1000"].id
    )
    db_session.add(bank)
    db_session.commit()
    return bank


def connect_and_map(client, bank):
    response = client.post("/api/mercury/connect", json={"api_token": TOKEN})
    assert response.status_code == 200, response.text
    response = client.post("/api/mercury/map", json={"mapping": {ACCOUNT: bank.id}})
    assert response.status_code == 200, response.text


def test_accounts_paginate_and_keep_only_safe_display_fields(transport):
    responses, requests = transport

    def accounts(request):
        if not request.url.params.get("start_after"):
            return httpx.Response(
                200,
                json={"accounts": [ACCOUNT_DATA], "page": {"nextPage": SECOND_ACCOUNT}},
            )
        assert request.url.params["start_after"] == ACCOUNT
        return httpx.Response(
            200, json={"accounts": [{**ACCOUNT_DATA, "id": SECOND_ACCOUNT}], "page": {}}
        )

    responses["/api/v1/accounts"] = accounts
    data = mercury.fetch_accounts(TOKEN)
    assert len(data) == 2
    assert {k: v for k, v in data[0].items() if not k.startswith("_")} == {
        "id": ACCOUNT,
        "name": "My LLC",
        "last_four": "9012",
        "balance": "2500.25",
        "available_balance": "2400.25",
        "currency": "USD",
    }
    assert "123456789012" not in json.dumps(data)
    assert len(requests) == 2


def test_transactions_paginate_by_cursor_and_posting_date(transport):
    responses, requests = transport
    second = "10000000-0000-4000-8000-000000000002"

    def transactions(request):
        assert request.url.params["postedStart"] == "2026-08-01"
        assert request.url.params["accountId"] == ACCOUNT
        assert (
            "start" not in request.url.params
        )  # createdAt must not limit late postings
        if not request.url.params.get("start_after"):
            return httpx.Response(
                200,
                json={"transactions": [transaction()], "page": {"nextPage": second}},
            )
        assert request.url.params["start_after"] == TXN
        return httpx.Response(
            200,
            json={
                "transactions": [transaction(id=second, amount="-15.10")],
                "page": {},
            },
        )

    responses["/api/v1/transactions"] = transactions
    rows, warnings = mercury.to_import_rows(
        mercury.fetch_transactions(TOKEN, ACCOUNT, date(2026, 8, 1)), ACCOUNT
    )
    assert len(requests) == 2
    assert warnings == []
    assert [r["amount"] for r in rows] == [Decimal("2500.25"), Decimal("-15.10")]
    assert rows[0]["date"] == date(2026, 8, 10)
    assert rows[0]["memo"] == "ACH credit · Invoice 42 · August consulting"


@pytest.mark.parametrize("status", ["pending", "failed", "cancelled", "blocked"])
def test_unsettled_transactions_are_not_imported(status):
    assert mercury.to_import_rows([transaction(status=status)], ACCOUNT) == ([], [])


def test_reversals_warn_and_unposted_transactions_wait():
    rows, warnings = mercury.to_import_rows(
        [transaction(status="reversed"), transaction(postedAt=None)], ACCOUNT
    )
    assert rows == []
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "NaN"},
        {"amount": "Infinity"},
        {"amount": "1.001"},
        {"id": ""},
        {"postedAt": "invalid"},
        {"accountId": SECOND_ACCOUNT},
    ],
)
def test_invalid_transaction_aborts_instead_of_silently_losing_money(changes):
    with pytest.raises(mercury.MercuryError):
        mercury.to_import_rows([transaction(**changes)], ACCOUNT)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_remote_errors_do_not_echo_credentials_or_response_bodies(transport, status):
    responses, requests = transport
    responses["/api/v1/accounts"] = lambda req: httpx.Response(
        status, text=TOKEN, headers={"Location": "https://example.com/steal"}
    )
    with pytest.raises(mercury.MercuryError) as error:
        mercury.fetch_accounts(TOKEN)
    assert TOKEN not in str(error.value)
    assert len(requests) == 1


def test_bad_pagination_fails_without_looping(transport):
    responses, requests = transport
    responses["/api/v1/accounts"] = {
        "accounts": [ACCOUNT_DATA],
        "page": {"nextPage": ACCOUNT},
    }
    with pytest.raises(mercury.MercuryError, match="repeated"):
        mercury.fetch_accounts(TOKEN)
    assert len(requests) == 2


def test_connect_encrypts_and_redacts_token(client, db_session, transport):
    response = client.post("/api/mercury/connect", json={"api_token": TOKEN})
    assert response.status_code == 200
    stored = db_session.query(Settings).filter_by(key="mercury_api_token").one().value
    assert stored.startswith("fernet:") and TOKEN not in stored
    assert get_setting_raw(db_session, "mercury_api_token") == TOKEN
    for path in ["/api/settings", "/api/mercury/status"]:
        response = client.get(path)
        assert response.status_code == 200
        assert TOKEN not in response.text
        assert ACCOUNT_DATA["accountNumber"] not in response.text
        assert ACCOUNT_DATA["routingNumber"] not in response.text


def test_sync_imports_once_applies_rules_and_disconnect_keeps_rows(
    client, db_session, bank, transport
):
    revenue = Account(name="Consulting", account_type=AccountType.INCOME)
    db_session.add(revenue)
    db_session.flush()
    db_session.add(
        BankRule(
            name="Client",
            pattern="Client LLC",
            rule_type="contains",
            account_id=revenue.id,
            priority=10,
            is_active=True,
        )
    )
    db_session.commit()
    connect_and_map(client, bank)
    for imported, skipped in [(1, 0), (0, 1)]:
        response = client.post("/api/mercury/sync", json={"since": "2026-08-01"})
        assert response.status_code == 200, response.text
        assert response.json()["imported"] == imported
        assert response.json()["skipped"] == skipped
    row = db_session.query(BankTransaction).one()
    assert row.amount == Decimal("2500.25")
    assert row.import_source == "mercury"
    assert row.category_account_id == revenue.id
    assert row.match_status == "unmatched"
    assert row.transaction_id is None
    assert db_session.query(Transaction).count() == 0
    status = client.get("/api/mercury/status").json()
    assert status["since"] == "2026-08-01"
    assert status["last_sync"]
    assert client.post("/api/mercury/disconnect").status_code == 200
    assert db_session.query(BankTransaction).count() == 1
    assert not client.get("/api/mercury/status").json()["connected"]
    connect_and_map(client, bank)
    assert (
        client.post("/api/mercury/sync", json={"since": "2026-08-01"}).json()[
            "imported"
        ]
        == 0
    )


def test_later_page_failure_imports_nothing_and_preserves_last_sync(
    client, db_session, bank, transport
):
    responses, requests = transport
    connect_and_map(client, bank)
    set_setting(db_session, "mercury_last_sync", "2026-07-01T00:00:00Z")
    db_session.commit()

    def transactions(request):
        if request.url.params.get("start_after"):
            return httpx.Response(503, text=TOKEN)
        return httpx.Response(
            200, json={"transactions": [transaction()], "page": {"nextPage": TXN}}
        )

    responses["/api/v1/transactions"] = transactions
    response = client.post("/api/mercury/sync", json={"since": "2026-08-01"})
    assert response.status_code == 502
    assert TOKEN not in response.text
    assert db_session.query(BankTransaction).count() == 0
    assert get_setting_raw(db_session, "mercury_last_sync") == "2026-07-01T00:00:00Z"


def test_mapping_rejects_unknown_unlinked_and_remapped_accounts(
    client, db_session, bank, transport
):
    connect_and_map(client, bank)
    assert (
        client.post(
            "/api/mercury/map", json={"mapping": {SECOND_ACCOUNT: bank.id}}
        ).status_code
        == 400
    )
    unlinked = BankAccount(name="Unlinked")
    db_session.add(unlinked)
    db_session.commit()
    assert (
        client.post(
            "/api/mercury/map", json={"mapping": {ACCOUNT: unlinked.id}}
        ).status_code
        == 400
    )
    client.post("/api/mercury/sync", json={"since": "2026-08-01"})
    ledger = Account(
        name="Another bank", bank_kind="bank", account_type=AccountType.ASSET
    )
    db_session.add(ledger)
    db_session.flush()
    other = BankAccount(name="Another feed", account_id=ledger.id)
    db_session.add(other)
    db_session.commit()
    response = client.post("/api/mercury/map", json={"mapping": {ACCOUNT: other.id}})
    assert response.status_code == 400
    assert "original mapping" in response.text


def test_non_usd_books_and_future_start_are_rejected(
    client, db_session, bank, transport
):
    connect_and_map(client, bank)
    assert (
        client.post("/api/mercury/sync", json={"since": "2999-01-01"}).status_code
        == 400
    )
    set_setting(db_session, "home_currency", "EUR")
    db_session.commit()
    assert (
        client.post("/api/mercury/sync", json={"since": "2026-08-01"}).status_code
        == 400
    )


def test_missing_connection_and_mapping(client, transport):
    assert (
        client.post("/api/mercury/sync", json={"since": "2026-08-01"}).status_code
        == 400
    )
    client.post("/api/mercury/connect", json={"api_token": TOKEN})
    assert (
        client.post("/api/mercury/sync", json={"since": "2026-08-01"}).status_code
        == 400
    )


def test_readonly_and_bookkeeper_permissions(client, db_session, bank, transport):
    from tests.test_rbac_users import _mk_user, _login_as

    connect_and_map(client, bank)
    for role in ["bookkeeper", "readonly"]:
        _mk_user(db_session, role, "a-test-password-123", role)
        _login_as(client, role, "a-test-password-123")
        assert client.get("/api/mercury/status").status_code == 200
        assert (
            client.post("/api/mercury/connect", json={"api_token": TOKEN}).status_code
            == 403
        )
        assert client.post("/api/mercury/disconnect").status_code == 403
        assert (
            client.post(
                "/api/mercury/webhook-settings", json={"signing_secret": "test-secret"}
            ).status_code
            == 403
        )
        assert client.delete("/api/mercury/webhook-settings").status_code == 403
        response = client.post("/api/mercury/sync", json={"since": "2026-08-01"})
        assert response.status_code == (200 if role == "bookkeeper" else 403)
