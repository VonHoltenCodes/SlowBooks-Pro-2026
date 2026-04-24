"""Phase 11: duplicate detection on customer/vendor creation.

Verifies the fuzzy-match heuristic catches common variations (case, punctuation,
business suffixes) and returns a 409 with suggestions unless ?force=true.
"""
from app.services.duplicate_detection import normalize_name, similarity, find_duplicates


# -------- unit tests on the service --------


def test_normalize_strips_suffixes_and_punctuation():
    assert normalize_name("Acme, Inc.") == "acme"
    assert normalize_name("ACME LLC") == "acme"
    assert normalize_name("The Acme Company") == "acme"
    assert normalize_name("Acme Corp.") == "acme"


def test_exact_match_after_normalization():
    assert similarity("Acme Inc", "acme, LLC") == 1.0
    assert similarity("Bob's Shop", "Bob's Shop") == 1.0


def test_similar_but_not_identical():
    # One typo — should still be high
    s = similarity("Widget Supply Co", "Widgit Supply Co")
    assert 0.8 < s < 1.0


def test_totally_different_names_are_low():
    assert similarity("Acme Inc", "Zyzzyx Corporation") < 0.5


def test_find_duplicates_sorts_highest_first():
    class FakeRow:
        def __init__(self, id, name):
            self.id = id
            self.name = name

    rows = [
        FakeRow(1, "Widget Supply Co"),
        FakeRow(2, "Widgit Supply Co"),  # typo of above
        FakeRow(3, "Zyzzyx Corporation"),  # unrelated
    ]
    matches = find_duplicates("Widget Supply Company", rows, threshold=0.7)
    assert len(matches) >= 1
    assert matches[0]["similarity"] >= matches[-1]["similarity"]


# -------- integration: customer + vendor routes --------


def test_create_customer_warns_on_duplicate(client):
    client.post("/api/customers", json={"name": "Acme Corporation"})
    r = client.post("/api/customers", json={"name": "ACME Corp."})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["error"] == "possible_duplicate"
    assert len(body["duplicates"]) >= 1
    assert body["duplicates"][0]["name"] == "Acme Corporation"


def test_force_bypass_allows_creation_despite_duplicate(client):
    client.post("/api/customers", json={"name": "Acme Corporation"})
    r = client.post("/api/customers?force=true", json={"name": "ACME Corp"})
    assert r.status_code == 201, r.text


def test_totally_unique_name_creates_without_warning(client):
    client.post("/api/customers", json={"name": "Acme Corporation"})
    r = client.post("/api/customers", json={"name": "Zyzzyx Unrelated Business"})
    assert r.status_code == 201


def test_vendor_duplicate_detection_works_the_same(client):
    client.post("/api/vendors", json={"name": "Super Widgets Inc"})
    r = client.post("/api/vendors", json={"name": "Super Widgets LLC"})
    assert r.status_code == 409


def test_check_duplicate_endpoint_preview(client):
    """GET /api/customers/check-duplicate lets the UI warn before submit."""
    client.post("/api/customers", json={"name": "Acme Corporation"})
    r = client.get("/api/customers/check-duplicate?name=ACME Corp")
    assert r.status_code == 200
    assert len(r.json()["duplicates"]) >= 1
