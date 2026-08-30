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
    assert report["image_path"].startswith("/uploads/")
    assert report["gps_accuracy_m"] is None, "an empty form field is absent, not 0"


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


def test_confirming_a_report_raises_its_priority(client):
    report = create(client, *SEVERING_POINT, state="impassable")
    before = report["priority_score"]
    after = client.post(f"/api/reports/{report['id']}/status",
                        json={"status": "resolved"}).json()["priority_score"]
    assert after == pytest.approx(before * config.CONFIRMATION_BOOST, rel=1e-3)


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
    assert expected in pockets[0]["severing_edges"]

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
