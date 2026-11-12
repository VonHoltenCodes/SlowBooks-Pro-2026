"""A schedule of values line is in whole cents (#813).

PUT /sov took 33.335 and 66.665, stored them as 33.34 and 66.67, and a
100.00 contract's schedule came to 100.01 with a -0.01 balance. A third
decimal is refused with a sentence, as a line's figures are since #782,
and nothing is saved."""

from tests.test_change_orders import _contract, _job


def test_a_third_decimal_is_refused_and_nothing_is_saved(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=0)
    _contract(client, job["id"], contract_type="lump_sum", contract_amount=100)
    url = f"/api/jobs/{job['id']}/sov"
    before = client.get(url).json()
    r = client.put(
        url,
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": "33.335"},
                {"description": "Site", "kind": "work", "scheduled_value": 66.665},
            ]
        },
    )
    assert r.status_code == 422, r.text
    said = [e["message"] for e in r.json()["detail"]]
    assert any("A scheduled value is in whole cents; 33.335 isn't." in m for m in said)
    assert any("A scheduled value is in whole cents; 66.665 isn't." in m for m in said)
    assert client.get(url).json() == before
    # cents, and a deduct alternate's negative cents, still save
    r = client.put(
        url,
        json={
            "lines": [
                {"description": "Base", "kind": "work", "scheduled_value": "33.33"},
                {"description": "Site", "kind": "work", "scheduled_value": "66.67"},
                {"description": "Deduct", "kind": "work", "scheduled_value": "-0.10"},
            ]
        },
    )
    assert r.status_code == 200, r.text
