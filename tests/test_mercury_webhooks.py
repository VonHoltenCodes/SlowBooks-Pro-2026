"""Bank balance webhooks authenticate publicly and merge safely across deliveries."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.models.banking import BankTransaction
from app.models.settings import Settings
from app.models.transactions import Transaction
from app.services import mercury_balances as balances
from app.services.settings_service import get_setting_raw
from tests.test_mercury import (
    ACCOUNT,
    SECOND_ACCOUNT,
    bank as bank,
    transport as transport,
    connect_and_map,
)

SECRET = "test-mercury-webhook-signing-secret"
EVENT_ID = "20000000-0000-4000-8000-000000000001"


def event(**changes):
    return {
        "id": EVENT_ID,
        "resourceType": "checkingAccount",
        "resourceId": ACCOUNT,
        "operationType": "update",
        "resourceVersion": 4,
        "occurredAt": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
        "changedPaths": ["currentBalance", "availableBalance", "inFlightBalance"],
        "mergePatch": {
            "currentBalance": 7000.01,
            "availableBalance": 6900.01,
            "inFlightBalance": 100,
        },
        **changes,
    }


def signed(body, secret=SECRET, timestamp=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "Mercury-Signature": f"t={timestamp},v1={digest}",
        "Content-Type": "application/json",
    }


def deliver(client, payload, **kwargs):
    body = json.dumps(payload).encode()
    return client.post(
        balances.WEBHOOK_PATH, content=body, headers=signed(body, **kwargs)
    )


@pytest.fixture
def configured(client, bank, transport):
    connect_and_map(client, bank)
    response = client.post(
        "/api/mercury/webhook-settings", json={"signing_secret": SECRET}
    )
    assert response.status_code == 200
    return client


def test_public_signed_webhook_updates_bank_balances_without_posting(
    configured, unauthed_client, db_session, transport
):
    _, requests = transport
    before = len(requests)
    response = deliver(unauthed_client, event())
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "updated"}
    status = configured.get("/api/mercury/status").json()
    account = status["accounts"][0]
    assert account["balance"] == "7000.01"
    assert account["available_balance"] == "6900.01"
    assert account["in_flight_balance"] == "100"
    assert "_balance_state" not in account
    assert status["webhook"]["last_received"]
    assert db_session.query(Transaction).count() == 0
    assert db_session.query(BankTransaction).count() == 0
    assert len(requests) == before  # delivery acknowledges without an API round trip


def test_duplicate_and_out_of_order_partial_events(configured, unauthed_client):
    first = event()
    assert deliver(unauthed_client, first).json()["status"] == "updated"
    assert deliver(unauthed_client, first).json()["status"] == "ignored"
    assert (
        deliver(
            unauthed_client,
            event(resourceVersion=3, id="20000000-0000-4000-8000-000000000003"),
        ).json()["status"]
        == "ignored"
    )
    # Version 6 only changes available. A delayed v5 current-balance change
    # still applies because v6 carries no replacement for that field.
    newer = event(
        resourceVersion=6,
        id="20000000-0000-4000-8000-000000000006",
        mergePatch={"availableBalance": 8000},
    )
    older = event(
        resourceVersion=5,
        id="20000000-0000-4000-8000-000000000005",
        mergePatch={"currentBalance": 9000, "availableBalance": 7500},
    )
    assert deliver(unauthed_client, newer).json()["status"] == "updated"
    assert deliver(unauthed_client, older).json()["status"] == "updated"
    account = configured.get("/api/mercury/status").json()["accounts"][0]
    assert account["balance"] == "9000"
    assert account["available_balance"] == "8000"


@pytest.mark.parametrize("offset", [-301, 301])
def test_expired_and_future_signatures_are_rejected(
    configured, unauthed_client, offset
):
    response = deliver(unauthed_client, event(), timestamp=int(time.time()) + offset)
    assert response.status_code == 401
    assert (
        configured.get("/api/mercury/status").json()["accounts"][0]["balance"]
        == "2500.25"
    )


def test_tampered_unsigned_and_wrong_secret_bodies_are_rejected(
    configured, unauthed_client
):
    body = json.dumps(event()).encode()
    assert unauthed_client.post(balances.WEBHOOK_PATH, content=body).status_code == 401
    assert (
        unauthed_client.post(
            balances.WEBHOOK_PATH, content=body + b" ", headers=signed(body)
        ).status_code
        == 401
    )
    assert deliver(unauthed_client, event(), secret="wrong-key").status_code == 401
    assert (
        configured.get("/api/mercury/status").json()["webhook"]["last_received"] == ""
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"resourceVersion": True},
        {"resourceVersion": 0},
        {"occurredAt": "2026-01-01"},
        {"mergePatch": {"currentBalance": 700, "availableBalance": None}},
        {"mergePatch": {"currentBalance": "NaN"}},
        {"id": "not-an-id"},
    ],
)
def test_invalid_signed_payloads_change_nothing(configured, unauthed_client, changes):
    assert deliver(unauthed_client, event(**changes)).status_code == 400
    assert (
        configured.get("/api/mercury/status").json()["accounts"][0]["balance"]
        == "2500.25"
    )


def test_oversized_and_unreadable_bodies(configured, unauthed_client):
    for body, expected in [(b"{" * 65537, 413), (b"not json", 400)]:
        assert (
            unauthed_client.post(
                balances.WEBHOOK_PATH, content=body, headers=signed(body)
            ).status_code
            == expected
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"resourceId": SECOND_ACCOUNT},
        {"resourceType": "transaction"},
        {"operationType": "create"},
    ],
)
def test_unrelated_events_acknowledge_without_changing_balances(
    configured, unauthed_client, changes
):
    response = deliver(unauthed_client, event(**changes))
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert (
        configured.get("/api/mercury/status").json()["accounts"][0]["balance"]
        == "2500.25"
    )


def test_secret_is_encrypted_redacted_and_disable_revokes_delivery(
    configured, unauthed_client, db_session
):
    row = db_session.query(Settings).filter_by(key="mercury_webhook_secret").one()
    assert row.value.startswith("fernet:")
    assert SECRET not in row.value
    assert get_setting_raw(db_session, "mercury_webhook_secret") == SECRET
    assert SECRET not in configured.get("/api/settings").text
    assert SECRET not in configured.get("/api/mercury/status").text
    assert configured.delete("/api/mercury/webhook-settings").status_code == 200
    assert deliver(unauthed_client, event()).status_code == 401


def test_snapshot_merge_does_not_roll_back_a_webhook_or_a_newer_poll():
    initial = [
        {
            "id": ACCOUNT,
            "balance": "10",
            "available_balance": "9",
            "_snapshot_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    accounts = balances.merge_snapshots([], initial)
    change = event(
        occurredAt="2026-01-01T00:00:02+00:00", mergePatch={"currentBalance": 20}
    )
    assert balances.apply_event(accounts, change) == "updated"
    # A poll started before the event and returned after it.
    early_poll = [
        {
            **initial[0],
            "balance": "10",
            "available_balance": "8",
            "_snapshot_at": "2026-01-01T00:00:01+00:00",
        }
    ]
    merged = balances.merge_snapshots(accounts, early_poll)
    assert merged[0]["balance"] == "20"
    assert merged[0]["available_balance"] == "8"
    late_poll = [
        {**initial[0], "balance": "30", "_snapshot_at": "2026-01-01T00:00:03+00:00"}
    ]
    latest = balances.merge_snapshots(merged, late_poll)
    assert balances.merge_snapshots(latest, early_poll)[0]["balance"] == "30"
    assert balances.apply_event(latest, change) == "ignored"
    # Even a higher version cannot roll back a newer authoritative poll.
    assert balances.apply_event(latest, {**change, "resourceVersion": 5}) == "ignored"


def test_refresh_preserves_a_webhook_that_arrives_during_its_snapshot_window(
    configured, unauthed_client
):
    assert deliver(unauthed_client, event()).json()["status"] == "updated"
    assert configured.post("/api/mercury/refresh").status_code == 200
    assert (
        configured.get("/api/mercury/status").json()["accounts"][0]["balance"]
        == "7000.01"
    )
