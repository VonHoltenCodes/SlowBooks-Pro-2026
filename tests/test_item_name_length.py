"""An item's name stops at the width of its column, on the server too
(explore 2.17.3 integration, I11).

The item form got maxlength="200"; the API took any length. SQLite stored
the overlong name as sent and PostgreSQL answered an opaque 500
(StringDataRightTruncation). A new item and a rename are refused past 200
characters with the plain sentence every 422 carries.
"""

from app.models.items import Item

SENTENCE = "Name must be 200 characters or fewer."


def _messages(r):
    assert r.status_code == 422, r.text
    return [e["message"] for e in r.json()["detail"]]


def test_the_limit_is_the_columns_width():
    assert Item.__table__.c.name.type.length == 200


def test_a_new_item_name_past_the_column_is_refused_in_a_sentence(client):
    r = client.post("/api/items", json={"name": "x" * 201, "item_type": "service"})
    assert _messages(r) == [SENTENCE]
    assert client.get("/api/items").json() == []

    r = client.post("/api/items", json={"name": "x" * 200, "item_type": "service"})
    assert r.status_code == 201, r.text


def test_a_rename_past_the_column_is_refused_and_the_name_kept(client):
    item = client.post(
        "/api/items", json={"name": "Design Hour", "item_type": "service"}
    ).json()
    r = client.put(f"/api/items/{item['id']}", json={"name": "y" * 201})
    assert _messages(r) == [SENTENCE]
    assert client.get(f"/api/items/{item['id']}").json()["name"] == "Design Hour"


def test_padding_does_not_count_against_the_limit(client):
    # trimmed first, as the name is stored
    r = client.post(
        "/api/items", json={"name": "  " + "z" * 200 + "  ", "item_type": "service"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "z" * 200
