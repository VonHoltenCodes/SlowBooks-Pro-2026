"""Phase 11: Saved Reports CRUD."""


def test_create_and_list_saved_report(client):
    r = client.post("/api/saved-reports", json={
        "name": "Q1 P&L",
        "report_type": "profit_loss",
        "parameters": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Q1 P&L"
    assert body["parameters"]["start_date"] == "2026-01-01"

    list_r = client.get("/api/saved-reports")
    assert list_r.status_code == 200
    names = [row["name"] for row in list_r.json()]
    assert "Q1 P&L" in names


def test_update_saved_report(client):
    r = client.post("/api/saved-reports", json={
        "name": "Old Name",
        "report_type": "balance_sheet",
        "parameters": {},
    })
    report_id = r.json()["id"]

    r = client.put(f"/api/saved-reports/{report_id}", json={
        "name": "New Name",
        "parameters": {"as_of_date": "2026-12-31"},
    })
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"
    assert r.json()["parameters"]["as_of_date"] == "2026-12-31"


def test_delete_saved_report(client):
    r = client.post("/api/saved-reports", json={
        "name": "Disposable",
        "report_type": "ar_aging",
        "parameters": {},
    })
    report_id = r.json()["id"]

    r = client.delete(f"/api/saved-reports/{report_id}")
    assert r.status_code == 200

    r = client.get(f"/api/saved-reports/{report_id}")
    assert r.status_code == 404


def test_reject_unknown_report_type(client):
    r = client.post("/api/saved-reports", json={
        "name": "Bad",
        "report_type": "not_a_real_report",
        "parameters": {},
    })
    assert r.status_code == 400
    assert "Unknown report_type" in r.json()["detail"]


def test_filter_by_report_type(client):
    client.post("/api/saved-reports", json={"name": "P&L 1", "report_type": "profit_loss"})
    client.post("/api/saved-reports", json={"name": "BS 1", "report_type": "balance_sheet"})

    r = client.get("/api/saved-reports?report_type=profit_loss")
    assert r.status_code == 200
    types = {row["report_type"] for row in r.json()}
    assert types == {"profit_loss"}
