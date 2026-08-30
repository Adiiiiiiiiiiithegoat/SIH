"""The endpoints, against CONTRACT.md, and the end-to-end path a report takes.

The end-to-end test is the one that matters: create a report, confirm it binds
to an edge, confirm severance recomputes, confirm the queue reorders.
"""
import io

import pytest

from app import config
from app.network import edge_id
from app.store import CONTRACT_FIELDS


# The 507 m span the sweep validated as genuinely severing. The first point is
# its western junction; the second is mid-span, where a second reporter of the
# same blockage would plausibly stand.
SEVERING_POINT = (13.01087, 74.81183)
SEVERING_MIDSPAN = (13.01158, 74.80965)
# A quiet through road far from it, used as the low-priority control.
QUIET_POINT = (13.0904, 74.7871)


def create(client, lat, lon, **extra):
    """Post a report the way the operator's map click does -- JSON, no photo."""
    body = {"lat": lat, "lon": lon, "asset_type": "road", **extra}
    r = client.post("/api/reports", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- contract shape -------------------------------------------------------
def test_report_record_is_exactly_the_contract_fields(client):
    report = create(client, *QUIET_POINT, state="unknown")
    assert set(report) == set(CONTRACT_FIELDS)


def test_contract_enumerations_are_honoured(client):
    road = create(client, *QUIET_POINT, state="impassable")
    assert road["state"] in ("passable", "impassable", "unknown")
    assert road["detection_mode"] in ("api", "model", "manual")
    assert road["status"] == "pending"

    building = create(client, 13.0224, 74.7900, asset_type="building", state="damaged")
    assert building["state"] in ("damaged", "not_damaged", "unknown")
    assert building["edge_id"] is None, "a building is not bound to a road"


def test_bad_asset_type_is_rejected(client):
    assert client.post("/api/reports",
                       json={"lat": 13.02, "lon": 74.79, "asset_type": "bridge"}
                       ).status_code == 422


def test_missing_coordinates_are_rejected(client):
    assert client.post("/api/reports", json={"asset_type": "road"}).status_code == 422


def test_get_reports_filters_by_status(client):
    a = create(client, *SEVERING_POINT, state="impassable")
    create(client, *QUIET_POINT, state="unknown")
    client.post(f"/api/reports/{a['id']}/status", json={"status": "resolved"})

    pending = client.get("/api/reports?status=pending").json()
    resolved = client.get("/api/reports?status=resolved").json()
    assert [r["id"] for r in resolved] == [a["id"]]
    assert a["id"] not in [r["id"] for r in pending]


def test_get_one_report_includes_the_binding_candidates(client):
    report = create(client, *SEVERING_POINT, state="impassable", gps_accuracy_m=40)
    full = client.get(f"/api/reports/{report['id']}").json()
    assert len(full["bind_candidates"]) > 1
    assert full["bind_candidates"][0]["edge_id"] == full["edge_id"]


def test_missing_report_is_a_404(client):
    assert client.get("/api/reports/9999").status_code == 404
    assert client.post("/api/reports/9999/status",
                       json={"status": "resolved"}).status_code == 404


def test_invalid_status_is_rejected(client):
    report = create(client, *QUIET_POINT)
    assert client.post(f"/api/reports/{report['id']}/status",
                       json={"status": "cleared"}).status_code == 422


# --- map layers -----------------------------------------------------------
def test_roads_returns_every_edge_with_state_and_coordinates(client, network):
    roads = client.get("/api/roads").json()
    assert len(roads) == len(network.spans)
    for road in roads[:50]:
        assert set(road) == {"edge_id", "coordinates", "state", "name", "highway", "length_m"}
        assert road["state"] in ("passable", "impassable", "unknown")
        assert len(road["coordinates"]) >= 2
        lat, lon = road["coordinates"][0]
        assert 12.9 < lat < 13.2 and 74.7 < lon < 74.9, "coordinates are (lat, lon)"
        # The map weights line thickness by class, so it must be a plain string.
        assert road["highway"] is None or isinstance(road["highway"], str)
    assert any(r["highway"] == "trunk" for r in roads), "the trunk network is classed"


def test_a_reported_road_shows_its_state_on_the_roads_layer(client):
    report = create(client, *SEVERING_POINT, state="impassable")
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "impassable"


def test_pockets_returns_polygons_and_population(client):
    create(client, *SEVERING_POINT, state="impassable")
    pockets = client.get("/api/pockets").json()
    assert len(pockets) == 1
    p = pockets[0]
    assert p["n_nodes"] == 5
    assert len(p["polygon"]) >= 4
    assert p["population_source"] in ("census", "raster", "missing")
    assert p["lost_facility"] == "Padmavathi Hospital, Dakshina Kannada"
    assert p["prior_access_km"] == pytest.approx(4.65, abs=0.01)


def test_no_blockages_means_no_pockets(client):
    create(client, *SEVERING_POINT, state="passable")
    assert client.get("/api/pockets").json() == []


# --- settings -------------------------------------------------------------
def test_settings_expose_the_locked_weights(client):
    s = client.get("/api/settings").json()
    assert s["detection_mode"] in ("api", "model", "manual")
    assert s["population_exponent"] == config.POPULATION_EXPONENT == 0.4
    assert s["disconnect_penalty"] == config.DISCONNECT_PENALTY == 300
    assert s["assumed_speed_kmh"] == config.ASSUMED_SPEED_KMH == 35


def test_settings_can_be_changed_live_and_rescore_the_queue(client):
    original = config.DISCONNECT_PENALTY
    try:
        report = create(client, *SEVERING_POINT, state="impassable")
        before = client.get(f"/api/reports/{report['id']}").json()["priority_score"]
        client.post("/api/settings", json={"disconnect_penalty": 600})
        after = client.get(f"/api/reports/{report['id']}").json()["priority_score"]
        assert after == pytest.approx(before * 2, rel=1e-3)
    finally:
        config.DISCONNECT_PENALTY = original
        client.post("/api/settings", json={"disconnect_penalty": original})


def test_bad_detection_mode_is_rejected(client):
    assert client.post("/api/settings",
                       json={"detection_mode": "psychic"}).status_code == 422


# --- manual reports and overrides ----------------------------------------
def test_a_manual_report_is_a_normal_record(client):
    """An operator's map click must produce the same shape as a photo report --
    detection_mode 'manual', confidence 1.0, and the same severance path."""
    report = create(client, *SEVERING_POINT, state="impassable")
    assert report["detection_mode"] == "manual"
    assert report["confidence"] == 1.0
    assert report["edge_id"] is not None
    assert report["priority_score"] > 0
    assert "Severs" in report["priority_reason"]
    assert len(client.get("/api/pockets").json()) == 1


def test_a_photo_report_goes_through_the_detection_stub(client):
    """No real detector yet, so the stub decides the state -- and the record
    must carry the mode it ran in."""
    files = {"image": ("road.jpg", io.BytesIO(b"not-a-real-jpeg"), "image/jpeg")}
    r = client.post("/api/reports", files=files,
                    data={"lat": str(SEVERING_POINT[0]), "lon": str(SEVERING_POINT[1]),
                          "gps_accuracy_m": "", "asset_type": "road"})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["detection_mode"] == config.DETECTION_MODE
    assert report["confidence"] == config.STUB_CONFIDENCE
    assert report["gps_accuracy_m"] is None, "an empty form field is absent, not 0"
    # Absolute, not "/uploads/...": the dashboard drops image_path straight into
    # <img src>, which resolves against the PAGE origin. On a separate frontend
    # dev server a relative path 404s on every thumbnail.
    assert report["image_path"].startswith("http://testserver/uploads/")
    assert client.get(report["image_path"]).status_code == 200, "the image must fetch"


def test_an_operator_can_override_the_edge_binding(client, network):
    report = create(client, *SEVERING_POINT, state="impassable", gps_accuracy_m=40)
    other = next(c["edge_id"] for c in
                 client.get(f"/api/reports/{report['id']}").json()["bind_candidates"]
                 if c["edge_id"] != report["edge_id"])

    r = client.post(f"/api/reports/{report['id']}/edge", json={"edge_id": other})
    assert r.status_code == 200
    assert r.json()["edge_id"] == other
    assert client.get("/api/pockets").json() == [], \
        "moving the report off the severing span must un-sever the pocket"


def test_overriding_to_a_nonexistent_edge_is_rejected(client):
    report = create(client, *SEVERING_POINT, state="impassable")
    assert client.post(f"/api/reports/{report['id']}/edge",
                       json={"edge_id": "1-2"}).status_code == 422


# --- corroboration --------------------------------------------------------
def test_reports_on_the_same_edge_increment_n_reports_on_all_of_them(client):
    first = create(client, *SEVERING_POINT, state="impassable")
    assert first["n_reports"] == 1

    second = create(client, *SEVERING_MIDSPAN, state="impassable")
    assert second["edge_id"] == first["edge_id"]

    reports = {r["id"]: r for r in client.get("/api/reports").json()}
    assert reports[first["id"]]["n_reports"] == 2, "the earlier report must update too"
    assert reports[second["id"]]["n_reports"] == 2
    assert "2 corroborating reports" in reports[first["id"]]["priority_reason"]


def test_rejecting_a_report_removes_its_blockage(client):
    report = create(client, *SEVERING_POINT, state="impassable")
    assert len(client.get("/api/pockets").json()) == 1
    client.post(f"/api/reports/{report['id']}/status", json={"status": "rejected"})
    assert client.get("/api/pockets").json() == []


def test_accepting_a_report_raises_its_priority(client):
    """Accepting a report -- crews are on it -- is an operator vouching for it,
    and outranks any number of citizen reports."""
    report = create(client, *SEVERING_POINT, state="impassable")
    before = report["priority_score"]
    after = client.post(f"/api/reports/{report['id']}/status",
                        json={"status": "in_progress"}).json()["priority_score"]
    assert after == pytest.approx(before * config.CONFIRMATION_BOOST, rel=1e-3)


def test_closing_a_case_clears_the_road_it_was_holding_shut(client):
    """The headline figures must move when an operator closes a case. Resolved
    means the work is done and the road is open, so the pocket it severed is
    reconnected and the report stops counting as an impassable road."""
    report = create(client, *SEVERING_POINT, state="impassable")
    assert len(client.get("/api/pockets").json()) == 1
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "impassable"

    closed = client.post(f"/api/reports/{report['id']}/status",
                         json={"status": "resolved"}).json()

    assert client.get("/api/pockets").json() == [], "areas cut off must drop to 0"
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "passable", \
        "roads impassable must drop -- the span is open again"
    assert closed["priority_score"] == 0.0
    assert "cleared" in closed["priority_reason"]
    assert "Severs" not in closed["priority_reason"], \
        "a closed case must not still claim to be severing anything"


def test_work_in_progress_still_holds_the_road_shut(client):
    """Crews being on it is not the road being open. Only closing clears it."""
    report = create(client, *SEVERING_POINT, state="impassable")
    client.post(f"/api/reports/{report['id']}/status", json={"status": "in_progress"})
    assert len(client.get("/api/pockets").json()) == 1
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "impassable"


def test_reopening_a_closed_case_restores_the_blockage(client):
    """The operator can be wrong about a road being clear, so closing must be
    reversible and the figures must come back."""
    report = create(client, *SEVERING_POINT, state="impassable")
    client.post(f"/api/reports/{report['id']}/status", json={"status": "resolved"})
    assert client.get("/api/pockets").json() == []

    reopened = client.post(f"/api/reports/{report['id']}/status",
                           json={"status": "pending"}).json()
    assert len(client.get("/api/pockets").json()) == 1
    assert reopened["priority_score"] > 0
    assert "Severs" in reopened["priority_reason"]


def test_a_closed_report_stops_corroborating_a_live_one(client):
    """A fixed blockage is not evidence the road is blocked now, so it must not
    inflate the corroboration count on a report that is still open."""
    first = create(client, *SEVERING_POINT, state="impassable")
    second = create(client, *SEVERING_MIDSPAN, state="impassable")
    assert client.get(f"/api/reports/{second['id']}").json()["n_reports"] == 2

    client.post(f"/api/reports/{first['id']}/status", json={"status": "resolved"})
    still_open = client.get(f"/api/reports/{second['id']}").json()
    assert still_open["n_reports"] == 1
    assert still_open["priority_score"] > 0, "the live report still blocks the road"
    edges = client.get("/api/pockets").json()[0]["severing_edges"]
    assert edges[0]["report_ids"] == [second["id"]], \
        "a closed report is no longer a cause of the pocket"


# --- end to end -----------------------------------------------------------
def test_end_to_end_a_new_report_binds_recomputes_and_reorders_the_queue(client):
    """The whole path in one test.

    Seed the queue with a harmless report, then add one on a span the sweep
    proved severing. The new report must bind to that span, severance must find
    the pocket, and the queue must put the new report on top.
    """
    quiet = create(client, *QUIET_POINT, state="impassable")
    queue = client.get("/api/reports").json()
    assert [r["id"] for r in queue] == [quiet["id"]]
    assert client.get("/api/pockets").json() == [], "nothing severed yet"

    severing = create(client, *SEVERING_POINT, state="impassable", gps_accuracy_m=15)

    # 1. it bound to the span the sweep validated
    from app.network import net
    n = net()
    expected = edge_id(n.nearest_node(13.01087, 74.81183),
                       n.nearest_node(13.01229, 74.80747))
    assert severing["edge_id"] == expected

    # 2. severance recomputed against the new blocked set
    pockets = client.get("/api/pockets").json()
    assert len(pockets) == 1
    assert pockets[0]["n_nodes"] == 5
    assert [e["edge_id"] for e in pockets[0]["severing_edges"]] == [expected]

    # 3. the queue reordered -- the severing report is now on top
    queue = client.get("/api/reports").json()
    assert queue[0]["id"] == severing["id"]
    assert queue[0]["priority_score"] > queue[1]["priority_score"]

    # 4. and it says why, in English an operator can act on
    assert queue[0]["priority_reason"].startswith("Severs a 5-node pocket near ")
    assert "Padmavathi Hospital" in queue[0]["priority_reason"]
    assert "prior access 4.65 km" in queue[0]["priority_reason"]


def test_end_to_end_resolving_the_top_report_keeps_the_picture_consistent(client):
    """An operator working the queue must not desynchronise reports from map."""
    create(client, *QUIET_POINT, state="impassable")
    top = create(client, *SEVERING_POINT, state="impassable")

    client.post(f"/api/reports/{top['id']}/status", json={"status": "rejected"})

    assert client.get("/api/pockets").json() == []
    queue = client.get("/api/reports").json()
    rejected = next(r for r in queue if r["id"] == top["id"])
    assert rejected["priority_score"] is None
    assert queue[-1]["id"] == top["id"], "a rejected report sinks to the bottom"
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[top["edge_id"]]["state"] == "passable"


def test_a_road_report_that_binds_to_nothing_says_so(client):
    """A report the binder could not place on any span was never assessed
    against the network. It must not claim the network absorbed it, and it must
    not score 0 -- 0 reads as "harmless", when the truth is "unassessed"."""
    report = create(client, 13.05, 74.60, state="impassable")   # out at sea
    assert report["edge_id"] is None
    assert report["priority_score"] is None, "unbound is unranked, not zero"
    assert "Not matched to a road span" in report["priority_reason"]
    assert "alternative routes remain" not in report["priority_reason"]


def test_a_caller_supplied_image_path_is_left_alone(client):
    """A manual report pointing at a frontend asset must not be rewritten into
    an API URL -- only our own /uploads/ paths are ours to rewrite."""
    report = create(client, *QUIET_POINT, state="impassable",
                    image_path="mocks/images/report-6.svg")
    assert report["image_path"] == "mocks/images/report-6.svg"


def test_pocket_severing_edges_link_back_to_the_reports_behind_them(client):
    """A pocket must name the reports that caused it, so an operator can go
    from a cut-off area straight to the photos."""
    first = create(client, *SEVERING_POINT, state="impassable")
    second = create(client, *SEVERING_MIDSPAN, state="impassable")

    pockets = client.get("/api/pockets").json()
    assert len(pockets) == 1
    edges = pockets[0]["severing_edges"]
    assert [e["edge_id"] for e in edges] == [first["edge_id"]]
    assert edges[0]["report_ids"] == [first["id"], second["id"]]


def test_a_rejected_report_stops_backing_the_pocket_it_caused(client):
    """Rejecting one of two reports leaves the pocket standing but must drop
    that report from the list of what is holding the road closed."""
    first = create(client, *SEVERING_POINT, state="impassable")
    second = create(client, *SEVERING_MIDSPAN, state="impassable")
    client.post(f"/api/reports/{first['id']}/status", json={"status": "rejected"})

    pockets = client.get("/api/pockets").json()
    assert len(pockets) == 1, "the second report still blocks the road"
    assert pockets[0]["severing_edges"][0]["report_ids"] == [second["id"]]


def test_an_unknown_state_report_does_not_claim_to_have_caused_a_pocket(client):
    """Only an impassable report blocks a road. An unassessed one on the same
    span must not appear as a cause."""
    blocking = create(client, *SEVERING_POINT, state="impassable")
    create(client, *SEVERING_MIDSPAN, state="unknown")
    pockets = client.get("/api/pockets").json()
    assert pockets[0]["severing_edges"][0]["report_ids"] == [blocking["id"]]


def test_pocket_record_matches_the_field_list_in_contract_md(client):
    """CONTRACT.md is the shared reference the frontend builds against, so a
    field added or renamed here must be reflected there in the same change."""
    import os, re
    from app import config

    doc = open(os.path.join(config.ROOT, "CONTRACT.md"), encoding="utf-8").read()
    section = doc.split("## Pocket record")[1].split("## Endpoints")[0]
    listed = [{f.strip() for f in block.replace("\n", " ").split(",") if f.strip()}
              for block in re.findall(r"```\n(.*?)\n```", section, re.S)]

    create(client, *SEVERING_POINT, state="impassable")
    pocket = client.get("/api/pockets").json()[0]
    assert set(pocket) == listed[0], "pocket fields differ from CONTRACT.md"
    assert set(pocket["severing_edges"][0]) == listed[1], \
        "severing_edges fields differ from CONTRACT.md"


# --- workflow and overrides ----------------------------------------------
def test_a_report_moves_through_the_whole_workflow(client):
    """pending -> in_progress -> resolved, and back again. The operator decides;
    no transition is one-way."""
    report = create(client, *SEVERING_POINT, state="impassable")
    for status in ("in_progress", "resolved", "in_progress", "pending"):
        r = client.post(f"/api/reports/{report['id']}/status", json={"status": status})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
        assert client.get(f"/api/reports/{report['id']}").json()["status"] == status


def test_in_progress_still_blocks_the_network(client):
    """Accepting a report does not clear the road. Only rejecting it does."""
    report = create(client, *SEVERING_POINT, state="impassable")
    client.post(f"/api/reports/{report['id']}/status", json={"status": "in_progress"})
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "impassable"
    assert len(client.get("/api/pockets").json()) == 1

    client.post(f"/api/reports/{report['id']}/status", json={"status": "rejected"})
    roads = {r["edge_id"]: r for r in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "passable"
    assert client.get("/api/pockets").json() == []


def test_accepting_a_report_raises_its_priority(client):
    """An operator accepting it is worth more than a citizen reporting it."""
    report = create(client, *SEVERING_POINT, state="impassable")
    before = client.get(f"/api/reports/{report['id']}").json()["priority_score"]
    client.post(f"/api/reports/{report['id']}/status", json={"status": "in_progress"})
    after = client.get(f"/api/reports/{report['id']}").json()["priority_score"]
    assert after > before


def test_an_operator_can_override_an_unknown_state(client):
    """The detector says unknown; the operator looking at the photo can say."""
    report = create(client, *SEVERING_POINT, state="unknown")
    assert report["priority_score"] is not None

    r = client.post(f"/api/reports/{report['id']}/state", json={"state": "impassable"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["state"] == "impassable"
    # A human assertion, not a model guess -- the record must say so.
    assert out["confidence"] == 1.0
    assert out["detection_mode"] == "manual"

    roads = {x["edge_id"]: x for x in client.get("/api/roads").json()}
    assert roads[report["edge_id"]]["state"] == "impassable"
    assert len(client.get("/api/pockets").json()) == 1


def test_state_override_is_checked_against_the_asset_type(client):
    road = create(client, *QUIET_POINT, state="unknown")
    assert client.post(f"/api/reports/{road['id']}/state",
                       json={"state": "damaged"}).status_code == 422
    building = create(client, 13.0224, 74.7900, asset_type="building", state="unknown")
    assert client.post(f"/api/reports/{building['id']}/state",
                       json={"state": "impassable"}).status_code == 422
    assert client.post("/api/reports/9999/state",
                       json={"state": "passable"}).status_code == 404


def test_bad_status_is_still_rejected(client):
    report = create(client, *QUIET_POINT)
    assert client.post(f"/api/reports/{report['id']}/status",
                       json={"status": "in progress"}).status_code == 422
