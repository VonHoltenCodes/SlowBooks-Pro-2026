"""GET /api/settings answers an ETag and a 304 on a matching If-None-Match
(#828).

The open window kept its copy of the settings for the whole session, so a
setting changed through the API (an agent, a script, another window) did not
show until someone pressed Save. The window now asks again on focus and on
each page it opens, sending the tag it holds; an unchanged answer is a bare
304, a changed one the new body with its new tag.
"""

from app.routes.settings import SECRET_PLACEHOLDER


def _get(client, tag=None):
    headers = {"If-None-Match": tag} if tag else {}
    return client.get("/api/settings", headers=headers)


def test_the_answer_carries_a_quoted_tag_that_stays_while_nothing_changes(client):
    first = _get(client)
    assert first.status_code == 200
    tag = first.headers.get("etag")
    assert tag and tag.startswith('"') and tag.endswith('"')
    assert _get(client).headers.get("etag") == tag


def test_the_tag_changes_when_a_setting_changes(client):
    before = _get(client).headers["etag"]
    r = client.put("/api/settings", json={"company_phone": "555-0142"})
    assert r.status_code == 200, r.text
    after = _get(client).headers["etag"]
    assert after != before
    # and back again: the tag follows the values, not a counter
    client.put("/api/settings", json={"company_phone": ""})
    client.put("/api/settings", json={"company_phone": "555-0142"})
    assert _get(client).headers["etag"] == after


def test_a_matching_tag_gets_304_with_no_body(client):
    tag = _get(client).headers["etag"]
    r = _get(client, tag)
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers.get("etag") == tag


def test_a_stale_tag_gets_200_with_the_new_body(client):
    stale = _get(client).headers["etag"]
    client.put("/api/settings", json={"company_name": "Lakeside Supply Test Co"})
    r = _get(client, stale)
    assert r.status_code == 200
    assert r.json()["company_name"] == "Lakeside Supply Test Co"
    assert r.headers["etag"] != stale
    assert _get(client, r.headers["etag"]).status_code == 304


def test_if_none_match_reads_a_list_a_weak_tag_and_a_star(client):
    tag = _get(client).headers["etag"]
    assert _get(client, f'"nope", {tag}').status_code == 304
    assert _get(client, f"W/{tag}").status_code == 304
    assert _get(client, "*").status_code == 304
    assert _get(client, '"nope"').status_code == 200


def test_the_tag_is_taken_over_the_redacted_body(client):
    # A secret's value is never in the body, so it is not in the tag either:
    # the tag says only "set" or "not set", as the body does.
    client.put("/api/settings", json={"smtp_password": "first-secret"})
    r = _get(client)
    assert r.json()["smtp_password"] == SECRET_PLACEHOLDER
    tag = r.headers["etag"]
    client.put("/api/settings", json={"smtp_password": "second-secret"})
    assert _get(client, tag).status_code == 304


def test_the_answer_is_still_revalidated_not_served_from_a_cache(client):
    r = _get(client)
    assert r.headers.get("cache-control") == "no-cache"
