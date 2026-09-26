"""Live QBO progress survives refreshes without locking the accounting DB."""

from threading import Event
from pathlib import Path
import time

from fastapi import HTTPException
import pytest
from quickbooks.exceptions import GeneralException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_sqlite_tuning
from app.models.accounts import Account, AccountType
from app.models.audit import AuditLog
from app.routes import qbo as qbo_routes
from app.services import (
    qbo_import,
    qbo_import_runs as runs,
    qbo_progress,
    qbo_service,
    storage,
)
from app.services.audit import register_audit_hooks


@pytest.fixture(autouse=True)
def private_log_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "backups_root", lambda: tmp_path / "private")


@pytest.fixture
def run_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'company.db'}")
    enable_sqlite_tuning(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    register_audit_hooks(factory)
    with factory() as db:
        yield db
    engine.dispose()


def _finished(store):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = store.latest()
        if snapshot["run"] and snapshot["run"]["status"] not in runs.ACTIVE:
            return snapshot
        time.sleep(0.01)
    pytest.fail("Import worker did not finish")


def test_item_progress_is_visible_before_commit_and_worker_keeps_audit_identity(
    run_db, monkeypatch
):
    entered, release = Event(), Event()

    @qbo_progress.stage("accounts")
    def importer(db):
        qbo_progress.emit("query", "Fetching Accounts page 1")
        qbo_progress.emit("fetch", "Fetched 1 account", fetched=1)
        qbo_progress.item("101", "Supplies")
        db.add(Account(name="Supplies", account_type=AccountType.EXPENSE))
        db.flush()
        qbo_progress.created()
        entered.set()
        assert release.wait(3)
        return {"imported": 1, "errors": []}

    monkeypatch.setattr(qbo_import, "import_accounts", importer)
    store = runs.store_for(run_db)
    accepted = runs.start_run(run_db, ["accounts"], "michelle")
    try:
        assert entered.wait(2)
        live = store.latest()
        assert live["run"]["run_id"] == accepted["run_id"]
        assert live["run"]["status"] == "running"
        assert live["run"]["counters"]["pending"] == 1
        assert live["run"]["counters"]["imported"] == 0
        assert live["run"]["counters"]["fetched"] == 1
        assert any(
            row["action"] == "create" and row["item_id"] == "101"
            for row in live["events"]
        )
    finally:
        release.set()
    final = _finished(store)
    assert final["run"]["status"] == "completed"
    assert final["run"]["counters"]["imported"] == 1
    assert final["run"]["counters"]["pending"] == 0
    assert [row["action"] for row in final["events"]].index("create") < [
        row["action"] for row in final["events"]
    ].index("commit")
    run_db.expire_all()
    assert run_db.query(Account).filter_by(name="Supplies").count() == 1
    assert (
        run_db.query(AuditLog).filter_by(table_name="accounts").one().username
        == "michelle"
    )


def test_query_wait_does_not_fake_progress(run_db, monkeypatch):
    entered, release = Event(), Event()

    @qbo_progress.stage("accounts")
    def importer(db):
        qbo_progress.emit("query", "Waiting for QBO Accounts page 1")
        entered.set()
        assert release.wait(3)
        return {"imported": 0, "errors": []}

    monkeypatch.setattr(qbo_import, "import_accounts", importer)
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")
    try:
        assert entered.wait(2)
        first = store.latest()
        second = store.latest()
        assert first["run"]["last_progress_at"] == second["run"]["last_progress_at"]
        assert first["run"]["current_step"] == "Waiting for QBO Accounts page 1"
        assert second["server_time"] >= first["server_time"]
        assert store.latest(first["run"]["last_sequence"])["events"] == []
    finally:
        release.set()
    _finished(store)


def test_worker_does_not_reuse_request_session(run_db, monkeypatch):
    request_session = run_db

    @qbo_progress.stage("accounts")
    def importer(db):
        assert db is not request_session
        assert db.info["acting_username"] == "eric"
        return {"imported": 0, "errors": []}

    monkeypatch.setattr(qbo_import, "import_accounts", importer)
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")
    request_session.close()
    assert _finished(store)["run"]["status"] == "completed"


def test_failed_worker_rolls_back_and_preserves_error_code(run_db, monkeypatch):
    @qbo_progress.stage("accounts")
    def importer(db):
        qbo_progress.item("bad", "Bad record")
        db.add(Account(name="Should roll back", account_type=AccountType.EXPENSE))
        db.flush()
        qbo_progress.created()
        raise RuntimeError("private-password-must-not-be-exposed")

    monkeypatch.setattr(qbo_import, "import_accounts", importer)
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")
    result = _finished(store)
    assert result["run"]["status"] == "failed"
    assert result["run"]["counters"]["imported"] == 0
    assert result["run"]["counters"]["pending"] == 0
    assert result["run"]["result"]["accounts"] == 0
    assert result["run"]["result"]["errors"][0]["code"] == "IMPORT_FAILED"
    assert any(row["code"] == "IMPORT_ROLLED_BACK" for row in result["events"])
    assert any(row["code"] == "IMPORT_FAILED" for row in result["events"])
    assert "private-password" not in str(result)
    assert run_db.query(Account).count() == 0


def test_nested_rollback_keeps_previously_pending_work(run_db):
    store = runs.store_for(run_db)
    state = store.reserve(["accounts", "journal_entries"], "eric")
    with runs._run_context(run_db, store, state) as reporter:
        reporter.begin_entity("accounts")
        qbo_progress.item("1")
        run_db.add(Account(name="Kept", account_type=AccountType.ASSET))
        run_db.flush()
        qbo_progress.created()
        reporter.end_entity({"imported": 1, "errors": []})
        reporter.begin_entity("journal_entries")
        with pytest.raises(RuntimeError):
            with run_db.begin_nested():
                qbo_progress.item("2")
                run_db.add(
                    Account(name="Rolled back", account_type=AccountType.EXPENSE)
                )
                run_db.flush()
                qbo_progress.created()
                raise RuntimeError("rollback savepoint")
        reporter.end_entity(
            {
                "imported": 0,
                "errors": [{"entity": "journal_entries", "message": "Posting failed"}],
            }
        )
        run_db.commit()
    snapshot = store.latest()
    assert snapshot["run"]["status"] == "completed_with_errors"
    assert snapshot["run"]["counters"]["imported"] == 1
    assert snapshot["run"]["counters"]["pending"] == 0
    assert snapshot["run"]["result"]["accounts"] == 1
    assert snapshot["run"]["result"]["journal_entries"] == 0
    assert run_db.query(Account).one().name == "Kept"


def test_later_failure_does_not_report_earlier_uncommitted_items_as_imported(run_db):
    store = runs.store_for(run_db)
    state = store.reserve(["accounts", "journal_entries"], "eric")
    with pytest.raises(RuntimeError):
        with runs._run_context(run_db, store, state) as reporter:
            reporter.begin_entity("accounts")
            qbo_progress.item("1")
            run_db.add(Account(name="Uncommitted", account_type=AccountType.ASSET))
            run_db.flush()
            qbo_progress.created()
            reporter.end_entity({"imported": 1, "errors": []})
            reporter.begin_entity("journal_entries")
            raise RuntimeError("Failure before final commit")
    snapshot = store.latest()
    assert snapshot["run"]["status"] == "failed"
    assert snapshot["run"]["counters"]["imported"] == 0
    assert snapshot["run"]["result"]["accounts"] == 0
    assert run_db.query(Account).count() == 0


def test_commit_during_entity_does_not_double_count_items(run_db):
    store = runs.store_for(run_db)
    state = store.reserve(["accounts"], "eric")
    with runs._run_context(run_db, store, state) as reporter:
        reporter.begin_entity("accounts")
        for qbo_id in ["1", "2"]:
            qbo_progress.item(qbo_id)
            run_db.add(
                Account(name=f"Account {qbo_id}", account_type=AccountType.ASSET)
            )
            run_db.flush()
            qbo_progress.created()
            run_db.commit()
        reporter.end_entity({"imported": 2, "errors": []})
        run_db.commit()
    snapshot = store.latest()
    assert snapshot["run"]["counters"]["imported"] == 2
    assert snapshot["run"]["counters"]["pending"] == 0
    assert snapshot["run"]["result"]["accounts"] == 2


def test_actual_importer_logs_qbo_error_code(run_db, monkeypatch):
    class Client:
        def query(self, query):
            raise GeneralException("private QBO response", 610, "private payload")

    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: Client())
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")
    snapshot = _finished(store)
    assert snapshot["run"]["status"] == "completed_with_errors"
    assert any(row["code"] == "610" for row in snapshot["events"])
    assert "private payload" not in str(snapshot)
    assert "private QBO response" not in str(snapshot)


def test_actual_importer_logs_duplicate_skips(run_db, monkeypatch):
    class Client:
        def query(self, query):
            return {
                "QueryResponse": {
                    "Account": (
                        []
                        if "Active = false" in query
                        else [
                            {
                                "Id": "1",
                                "Name": "Supplies",
                                "AccountType": "Expense",
                                "Active": True,
                            }
                        ]
                    )
                }
            }

    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: Client())
    store = runs.store_for(run_db)
    runs.start_run(run_db, ["accounts"], "eric")
    first = _finished(store)
    assert first["run"]["counters"]["imported"] == 1
    runs.start_run(run_db, ["accounts"], "eric")
    second = _finished(store)
    assert second["run"]["run_id"] != first["run"]["run_id"]
    assert second["run"]["counters"]["imported"] == 0
    assert second["run"]["counters"]["skipped"] == 1
    assert any(
        row["action"] == "skip" and row["item_id"] == "1" for row in second["events"]
    )
    assert run_db.query(Account).count() == 1


def test_only_latest_log_is_retained_and_can_be_reopened(run_db):
    store = runs.store_for(run_db)
    first = store.reserve(["accounts"], "eric")
    first["status"] = "completed"
    store.publish(first, "finish", "Completed")
    second = store.reserve(["journal_entries"], "michelle")
    # Reopening the same sidecar restores persisted rows without the worker object.
    path = Path(store.connection.execute("PRAGMA database_list").fetchone()[2])
    reopened = runs.ImportStore(path)
    snapshot = reopened.latest()
    assert snapshot["run"]["run_id"] == second["run_id"]
    assert snapshot["run"]["started_by"] == "michelle"
    assert len(snapshot["events"]) == 1
    assert "journal_entries" in snapshot["events"][0]["message"]
    reopened.connection.close()


def test_restart_marks_unfinished_log_interrupted(run_db, monkeypatch):
    store = runs.store_for(run_db)
    state = store.reserve(["accounts"], "eric")
    monkeypatch.setattr(runs, "_BOOT_ID", "new-server-process")
    snapshot = store.latest()
    assert snapshot["run"]["run_id"] == state["run_id"]
    assert snapshot["run"]["status"] == "interrupted"
    assert snapshot["events"][-1]["code"] == "IMPORT_INTERRUPTED"
    assert store.latest()["run"]["last_sequence"] == snapshot["run"]["last_sequence"]
    assert store.reserve(["accounts"], "eric")["run_id"] != state["run_id"]


def test_event_pagination_and_cursors(run_db):
    store = runs.store_for(run_db)
    state = store.reserve(["accounts"], "eric")
    for number in range(505):
        store.publish(state, "skip", "Duplicate", item_id=str(number))
    first = store.latest()
    assert len(first["events"]) == 500
    assert first["has_more"] is True
    second = store.latest(first["events"][-1]["sequence"])
    assert len(second["events"]) == 6
    assert second["has_more"] is False
    assert second["events"][0]["sequence"] == 501


def test_company_logs_are_isolated(run_db, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'different-company.db'}")
    with sessionmaker(bind=engine)() as other_db:
        first = runs.store_for(run_db)
        other = runs.store_for(other_db)
        first.reserve(["accounts"], "eric")
        assert other.latest()["run"] is None
        assert other.reserve(["items"], "michelle")["entities"] == ["items"]
        assert first.latest()["run"]["entities"] == ["accounts"]
    engine.dispose()


def test_concurrent_starts_do_not_replace_live_log(run_db):
    store = runs.store_for(run_db)
    first = store.reserve(["accounts"], "eric")
    with pytest.raises(HTTPException) as rejected:
        store.reserve(["items"], "michelle")
    assert rejected.value.status_code == 409
    assert rejected.value.detail["run_id"] == first["run_id"]
    assert store.latest()["run"]["run_id"] == first["run_id"]


def test_new_api_orders_entities_and_returns_immediately(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)

    def start(db, entities, actor, import_all=False):
        captured.update(entities=entities, actor=actor, import_all=import_all)
        return {"run_id": "test-run", "status": "queued"}

    monkeypatch.setattr(runs, "start_run", start)
    response = client.post(
        "/api/qbo/import-runs",
        json={"entities": ["journal_entries", "accounts", "accounts"]},
    )
    assert response.status_code == 202
    assert captured["entities"] == ["accounts", "journal_entries"]
    assert captured["import_all"] is False
    assert client.post("/api/qbo/import-runs", json={}).status_code == 202
    assert captured["import_all"] is True
    assert captured["entities"] == list(runs.ENTITY_ORDER)


def test_invalid_start_does_not_replace_log(client, db_session, monkeypatch):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    store = runs.store_for(db_session)
    first = store.reserve(["accounts"], "eric")
    for entities in [[], ["not-an-entity"]]:
        assert (
            client.post("/api/qbo/import-runs", json={"entities": entities}).status_code
            == 422
        )
    assert store.latest()["run"]["run_id"] == first["run_id"]


def test_latest_endpoint_reads_without_querying_qbo(client, db_session, monkeypatch):
    monkeypatch.setattr(
        qbo_service,
        "is_connected",
        lambda db: pytest.fail("Log polling queried QBO credentials"),
    )
    store = runs.store_for(db_session)
    state = store.reserve(["accounts"], "eric")
    response = client.get("/api/qbo/import-runs/latest")
    assert response.status_code == 200
    assert response.json()["run"]["run_id"] == state["run_id"]
    assert "owner" not in response.json()["run"]
    assert client.get("/api/qbo/import-runs/latest?after=1").json()["events"] == []


@pytest.mark.parametrize(
    "path, payload",
    [
        ("/api/qbo/import", None),
        ("/api/qbo/import/accounts", None),
        ("/api/qbo/import-runs", {"entities": ["accounts"]}),
    ],
)
def test_all_import_endpoints_share_concurrency_guard(
    client, db_session, monkeypatch, path, payload
):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    state = runs.store_for(db_session).reserve(["items"], "eric")
    response = client.post(path, json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["run_id"] == state["run_id"]


def test_log_start_requires_admin(client, monkeypatch):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)

    def denied(request):
        raise HTTPException(403, "Admin role required")

    monkeypatch.setattr(qbo_routes, "require_admin", denied)
    assert client.post("/api/qbo/import-runs", json={}).status_code == 403


def test_log_is_not_public(unauthed_client):
    assert unauthed_client.get("/api/qbo/import-runs/latest").status_code == 401


def test_fast_connection_status_does_not_query_provider(client, monkeypatch):
    monkeypatch.setattr(qbo_service, "is_connected", lambda db: True)
    monkeypatch.setattr(
        qbo_service, "get_all_qbo_settings", lambda db: {"qbo_realm_id": "123"}
    )
    monkeypatch.setattr(
        qbo_service,
        "get_company_name",
        lambda db: pytest.fail("Provider request would delay live log"),
    )
    assert (
        client.get("/api/qbo/status?include_company_name=false").json()["connected"]
        is True
    )


def test_delayed_batch_error_keeps_its_own_source_id_document_and_code(run_db):
    store = runs.store_for(run_db)
    state = store.reserve(["journal_entries"], "eric")
    with runs._run_context(run_db, store, state) as reporter:
        reporter.begin_entity("journal_entries")
        qbo_progress.item("11063", "9/1 Payroll")
        qbo_progress.validated()
        qbo_progress.item("11030", "8/1 Payroll")
        qbo_progress.validated()
        error = {
            "entity": "journal_entries",
            "qbo_id": "11063",
            "code": "IMPORT_POSTING_MISMATCH",
            "message": "Local transaction #7085: account QBO #115 / local #136 differs",
        }
        reporter.error(error)
        reporter.end_entity({"imported": 0, "errors": [error]})
    snapshot = store.latest()
    errors = [row for row in snapshot["events"] if row["level"] == "error"]
    assert len(errors) == 1
    assert errors[0]["item_id"] == "11063"
    assert errors[0]["item_label"] == "9/1 Payroll"
    assert errors[0]["code"] == "IMPORT_POSTING_MISMATCH"
    assert "#11063 (document 9/1 Payroll)" in errors[0]["message"]
    assert "#7085" in errors[0]["message"] and "#115" in errors[0]["message"]
    assert "8/1 Payroll" not in errors[0]["message"]
    assert snapshot["run"]["counters"]["errors"] == 1
