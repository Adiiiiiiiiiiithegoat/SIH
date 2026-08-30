"""Edge binding: with a stated accuracy, without one, and with a bearing."""
from app import binding, config
from app.network import edge_id


# The 507 m span the all-edge sweep validated. Both endpoints are real junctions.
SPAN_A = (13.01087, 74.81183)
SPAN_B = (13.01229, 74.80747)


def test_binds_a_point_on_the_span_to_that_span(network):
    u = network.nearest_node(*SPAN_A)
    v = network.nearest_node(*SPAN_B)
    chosen, cands = binding.bind(SPAN_A[0], SPAN_A[1], 25.0)
    assert chosen == edge_id(u, v)
    assert cands[0]["distance_m"] < 1.0


def test_missing_accuracy_falls_back_to_the_configured_default(network):
    """A report with no gps_accuracy_m must still bind, using the 25 m default
    -- not the nearest edge at any distance."""
    with_default = binding.candidates(SPAN_A[0], SPAN_A[1], None)
    explicit = binding.candidates(SPAN_A[0], SPAN_A[1], config.DEFAULT_GPS_ACCURACY_M)
    assert [c["edge_id"] for c in with_default] == [c["edge_id"] for c in explicit]
    assert with_default[0]["edge_id"] == explicit[0]["edge_id"]


def test_a_wider_accuracy_considers_more_candidates(network):
    """The whole point of using accuracy: a poor fix must widen the shortlist,
    not silently pick the nearest edge as if the fix were perfect."""
    lat, lon = SPAN_A
    tight = binding.candidates(lat, lon, 10.0)
    loose = binding.candidates(lat, lon, 90.0)
    assert len(loose) > len(tight)
    assert all(c["distance_m"] <= 10.0 for c in tight)


def test_candidates_are_recorded_for_operator_override(network):
    _, cands = binding.bind(13.0170, 74.7955, 60.0)
    assert len(cands) > 1, "an operator needs alternatives to override with"
    assert len(cands) <= config.MAX_CANDIDATES
    scores = [c["score"] for c in cands]
    assert scores == sorted(scores, reverse=True), "best candidate must be first"
    for c in cands:
        assert set(c) == {"edge_id", "distance_m", "bearing_delta", "score"}


def test_bearing_prefers_the_perpendicular_edge(network):
    """A photographer faces across the road they are photographing, so the edge
    perpendicular to the camera should gain on one running parallel to it."""
    lat, lon = SPAN_A
    plain = {c["edge_id"]: c["score"] for c in binding.candidates(lat, lon, 25.0)}
    aimed = {c["edge_id"]: c for c in binding.candidates(lat, lon, 25.0, bearing=10.0)}

    perpendicular = max(aimed.values(), key=lambda c: c["bearing_delta"])
    parallel = min(aimed.values(), key=lambda c: c["bearing_delta"])
    assert perpendicular["bearing_delta"] > 60
    assert parallel["bearing_delta"] < 30

    gain = lambda c: c["score"] / plain[c["edge_id"]]
    assert gain(perpendicular) > gain(parallel)


def test_bearing_delta_is_none_without_a_bearing(network):
    for c in binding.candidates(*SPAN_A, 25.0):
        assert c["bearing_delta"] is None


def test_a_point_far_off_the_network_binds_to_nothing(network):
    """Out at sea. Better an unbound report an operator can place than a
    confident binding to a road 3 km away."""
    chosen, cands = binding.bind(13.05, 74.60, 25.0)
    assert chosen is None
    assert cands == []


def test_no_edge_within_accuracy_widens_once_rather_than_giving_up(network):
    """A tight accuracy that happens to exclude every edge should widen to the
    hard cap, not leave the report unbound."""
    lat, lon = 13.0135, 74.8065          # near, but not on, the pocket roads
    chosen, cands = binding.bind(lat, lon, 1.0)
    assert chosen is not None
    assert cands[0]["distance_m"] <= config.MAX_BIND_RADIUS_M
