"""Trade packages (#550): CRUD, the section mapping (replace), the 400 a
non-section code gets, and the defaults the construction setup seeds once."""

from app.services.cost_code_catalog import TRADE_PACKAGES


def _section(client, code, name="Section"):
    r = client.post("/api/cost-codes", json={"code": code, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


# ── CRUD + mapping ─────────────────────────────────────────────────────────


def test_trade_package_crud_and_section_mapping(client):
    a = _section(client, "03 30 00", "Cast-in-Place Concrete")
    b = _section(client, "03 20 00", "Concrete Reinforcing")
    c = _section(client, "03 10 00", "Concrete Forming")

    created = client.post(
        "/api/trade-packages",
        json={"name": "Concrete", "section_ids": [a["id"], b["id"]]},
    )
    assert created.status_code == 201, created.text
    pkg = created.json()
    assert pkg["name"] == "Concrete"
    assert pkg["is_active"] is True
    # sections come back ordered by code, with ids and labels
    assert [s["code"] for s in pkg["sections"]] == ["03 20 00", "03 30 00"]
    assert set(pkg["section_ids"]) == {a["id"], b["id"]}

    # name clash is refused
    assert (
        client.post("/api/trade-packages", json={"name": "concrete"}).status_code == 409
    )

    # PUT replaces the mapping and edits the fields
    upd = client.put(
        f"/api/trade-packages/{pkg['id']}",
        json={"notes": "GC self-perform", "section_ids": [c["id"]]},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["notes"] == "GC self-perform"
    assert [s["code"] for s in upd.json()["sections"]] == ["03 10 00"]

    # a PUT without section_ids leaves the mapping alone
    upd2 = client.put(f"/api/trade-packages/{pkg['id']}", json={"sort_order": 5})
    assert [s["code"] for s in upd2.json()["sections"]] == ["03 10 00"]
    assert upd2.json()["sort_order"] == 5

    # list is ordered by sort_order then name
    client.post("/api/trade-packages", json={"name": "Masonry", "sort_order": 1})
    names = [p["name"] for p in client.get("/api/trade-packages").json()]
    assert names == ["Masonry", "Concrete"]

    # DELETE just deletes (nothing references a package yet)
    assert client.delete(f"/api/trade-packages/{pkg['id']}").status_code == 200
    assert "Concrete" not in {
        p["name"] for p in client.get("/api/trade-packages").json()
    }


def test_non_section_cost_code_is_rejected(client):
    division = _section(client, "03", "Concrete")  # a division, not a section
    assert division["level"] == "division"
    r = client.post(
        "/api/trade-packages",
        json={"name": "Bad", "section_ids": [division["id"]]},
    )
    assert r.status_code == 400, r.text
    assert "not a section" in r.json()["detail"]

    custom = _section(client, "L-100", "Rough carpentry labor")  # custom, level null
    r2 = client.post(
        "/api/trade-packages",
        json={"name": "Bad2", "section_ids": [custom["id"]]},
    )
    assert r2.status_code == 400, r2.text


# ── Setup seeds the defaults once ──────────────────────────────────────────


def test_setup_seeds_defaults_once_and_maps_present_sections(client, seed_accounts):
    client.post("/api/construction/setup")
    pkgs = client.get("/api/trade-packages").json()
    assert len(pkgs) == len(TRADE_PACKAGES)
    concrete = next(p for p in pkgs if p["name"] == "Concrete")
    assert {s["code"] for s in concrete["sections"]} == {
        "03 10 00",
        "03 20 00",
        "03 30 00",
        "03 40 00",
    }

    # a second setup does not re-seed
    client.post("/api/construction/setup")
    assert len(client.get("/api/trade-packages").json()) == len(TRADE_PACKAGES)


def test_setup_seeds_no_packages_for_a_custom_chart_without_sections(
    client, seed_accounts
):
    # a company on its own numeric chart (no standard sections) gets no
    # empty default packages
    client.post("/api/cost-codes", json={"code": "1000", "name": "Direct Costs"})
    client.post("/api/construction/setup")
    assert client.get("/api/trade-packages").json() == []


def test_setup_maps_only_the_sections_present(client, seed_accounts):
    # a company that already owns a (partial) chart: setup leaves the chart
    # alone, so only the sections it has map into the default packages.
    client.post("/api/cost-codes", json={"code": "03 30 00", "name": "Concrete"})
    client.post("/api/construction/setup")
    pkgs = {p["name"]: p for p in client.get("/api/trade-packages").json()}
    # the chart has only "03 30 00"; Concrete maps that one and skips the rest
    assert {s["code"] for s in pkgs["Concrete"]["sections"]} == {"03 30 00"}
    # a package whose sections the chart lacks seeds empty, not errored
    assert pkgs["HVAC"]["sections"] == []


def test_fences_and_gates_ship_as_sections_and_a_package(client, seed_accounts):
    # a fence sub is a common trade: its sections sit under division 32 and
    # its own package bids them
    client.post("/api/construction/setup")
    codes = {c["code"]: c for c in client.get("/api/cost-codes").json()}
    fence = codes["32 31 00"]
    assert fence["name"] == "Fences and Gates"
    assert fence["level"] == "section" and fence["parent_code"] == "32"
    assert codes["32 31 29"]["name"] == "Wood Fences and Gates"
    pkgs = {p["name"]: p for p in client.get("/api/trade-packages").json()}
    assert {s["code"] for s in pkgs["Fences and Gates"]["sections"]} == {
        "32 31 00",
        "32 31 11",
        "32 31 13",
        "32 31 19",
        "32 31 23",
        "32 31 29",
    }
