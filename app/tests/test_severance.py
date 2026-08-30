"""The severance engine, checked against the all-edge sweep's known result.

The sweep in ../severance.py removed all ~6000 spans and recorded what each one
severs. The 507 m span at (13.01087,74.81183) -> (13.01229,74.80747) is the
case it validated in full, so it is the fixed point this engine must reproduce:
a 5-node pocket, hull 0.0344 km2, prior access 4.65 km, endpoint degrees 3/3,
losing Padmavathi Hospital. All four filters pass.
"""

import pytest

from app import config, severance
from app.network import edge_id


KNOWN_A = (13.01087, 74.81183)
KNOWN_B = (13.01229, 74.80747)


@pytest.fixture(scope="module")
def known_edge():
    from app.network import net
    n = net()
    return edge_id(n.nearest_node(*KNOWN_A), n.nearest_node(*KNOWN_B))


@pytest.fixture(scope="module")
def known_pocket(known_edge):
    result = severance.analyse([known_edge])
    assert len(result["pockets"]) == 1
    return result["pockets"][0]


# --- the four validated filters ------------------------------------------
def test_known_span_passes_all_four_criteria(known_pocket):
    assert known_pocket["checks"] == {"min_nodes": True, "min_hull_km2": True,
                                      "prior_access": True, "endpoint_degree": True}


def test_known_span_reproduces_the_sweep_numbers(known_pocket):
    assert known_pocket["n_nodes"] == 5
    assert known_pocket["hull_area_km2"] == pytest.approx(0.0344, abs=1e-4)
    assert known_pocket["prior_access_km"] == pytest.approx(4.65, abs=0.01)
    assert known_pocket["lost_facility"] == "Padmavathi Hospital, Dakshina Kannada"
    assert known_pocket["nearest_place"] == "Madhya Padavu"


def test_known_span_endpoints_are_real_junctions(network, known_edge):
    u, v = (int(x) for x in known_edge.split("-"))
    assert network.graph.degree(u) >= config.MIN_ENDPOINT_DEGREE
    assert network.graph.degree(v) >= config.MIN_ENDPOINT_DEGREE


def test_pocket_holds_no_health_facility(network, known_pocket):
    assert not set(known_pocket["nodes"]) & network.fac_nodes


def test_the_1041m_span_is_excluded_by_the_filter(network):
    """The sweep's other known span strands a single node with degree 1 at one
    end. It must NOT be reported -- that is a driveway, not a severance."""
    e = edge_id(network.nearest_node(13.04465, 74.79198),
                network.nearest_node(13.04676, 74.78375))
    assert severance.analyse([e])["pockets"] == []


@pytest.mark.parametrize("attr,value", [("MIN_POCKET_NODES", 6),
                                        ("MIN_HULL_KM2", 1.0),
                                        ("MAX_PRIOR_ACCESS_M", 100.0)])
def test_each_filter_can_reject_the_known_pocket(known_edge, attr, value):
    """Each threshold must actually gate the result -- a filter that never
    changes the answer is not filtering."""
    original = getattr(config, attr)
    try:
        setattr(config, attr, value)
        assert severance.analyse([known_edge])["pockets"] == []
    finally:
        setattr(config, attr, original)


# --- engine behaviour -----------------------------------------------------
def test_no_blockages_means_nothing_severed():
    result = severance.analyse([])
    assert result["pockets"] == [] and result["detours"] == []


def test_unknown_edge_ids_are_ignored_not_fatal():
    assert severance.analyse(["not-an-edge", "1-2", None])["pockets"] == []


def test_the_graph_is_never_mutated(network, known_edge):
    """restricted_view, not remove_edge: a query must leave the shared graph
    exactly as it found it, or concurrent requests corrupt each other."""
    before = network.graph.number_of_edges()
    severance.analyse([known_edge])
    assert network.graph.number_of_edges() == before
    u, v = (int(x) for x in known_edge.split("-"))
    assert network.graph.has_edge(u, v)


def test_pocket_carries_everything_the_dashboard_needs(known_pocket):
    for field in ("nodes", "polygon", "centroid", "population",
                  "population_source", "lost_facility", "prior_access_km"):
        assert field in known_pocket
    assert known_pocket["population_source"] in ("census", "raster", "missing")
    assert len(known_pocket["polygon"]) >= 4
    for lat, lon in known_pocket["polygon"]:
        assert 12.9 < lat < 13.2 and 74.7 < lon < 74.9, "polygon is (lat, lon)"


def test_blocking_a_through_road_reports_a_detour(network):
    """Severance is not the only harm: a road that still connects but adds
    kilometres must be reported with the added distance and nodes affected."""
    blocked, result = None, None
    for eid in list(network.spans)[:400]:
        result = severance.analyse([eid])
        if result["detours"] and not result["pockets"]:
            blocked = eid
            break
    assert blocked, "no detour-only span found in the sample"
    d = result["detours"][0]
    assert d["added_km"] > config.DETOUR_THRESHOLD_M / 1000.0
    assert d["n_nodes"] == len(d["nodes"]) >= 1


def test_severed_nodes_are_not_also_counted_as_detoured(known_edge):
    """A node with no route at all has not been given a longer route."""
    result = severance.analyse([known_edge])
    severed = {n for p in result["pockets"] for n in p["nodes"]}
    detoured = {n for d in result["detours"] for n in d["nodes"]}
    assert not severed & detoured


# --- performance ----------------------------------------------------------
def test_query_is_under_the_500ms_budget(network, known_edge):
    """The target for a typical set of blockages. The baseline distance-to-care
    map is cached at startup; if that regresses this is what catches it."""
    import random
    rng = random.Random(3)
    blocked = [known_edge] + [edge_id(u, v) for u, v in
                              rng.sample(list(network.graph.edges()), 9)]
    severance.analyse(blocked)                      # warm any lazy import
    timings = [severance.analyse(blocked)["elapsed_ms"] for _ in range(3)]
    assert min(timings) < 500, f"severance query too slow: {timings} ms"


# --- severing edges on the pocket record ---------------------------------
def test_known_span_is_exactly_the_pockets_severing_edge(network, known_pocket, known_edge):
    """The 507 m span at (13.01087,74.81183)->(13.01229,74.80747) is the only
    thing cutting this pocket off, so it must be the only edge named -- not a
    superset that would have an operator checking roads that are still open."""
    assert [e["edge_id"] for e in known_pocket["severing_edges"]] == [known_edge]

    u, v = (int(x) for x in known_edge.split("-"))
    assert (u in known_pocket["nodes"]) != (v in known_pocket["nodes"]), \
        "a severing edge straddles the pocket boundary: one endpoint in, one out"


def test_severing_edges_carry_enough_to_draw_without_a_second_lookup(known_pocket):
    edge = known_pocket["severing_edges"][0]
    assert set(edge) == {"edge_id", "name", "length_m", "highway",
                         "bridge", "coordinates", "report_ids"}
    assert edge["length_m"] == pytest.approx(506.9, abs=0.1), "the sweep's 507 m span"
    assert edge["highway"] == "unclassified"
    assert edge["bridge"] is True, "the sweep recorded this span as bridge-tagged"
    assert len(edge["coordinates"]) >= 2
    for lat, lon in edge["coordinates"]:
        assert 12.9 < lat < 13.2 and 74.7 < lon < 74.9, "coordinates are (lat, lon)"


def test_an_edge_that_does_not_touch_the_pocket_is_not_listed(network, known_edge):
    """Blocking an unrelated road elsewhere must not get credited with this
    pocket -- attribution is per-edge, not "everything blocked right now"."""
    unrelated = edge_id(network.nearest_node(13.0904, 74.7871),
                        next(iter(network.graph.neighbors(
                            network.nearest_node(13.0904, 74.7871)))))
    result = severance.analyse([known_edge, unrelated])
    pocket = next(p for p in result["pockets"] if p["n_nodes"] == 5)
    assert [e["edge_id"] for e in pocket["severing_edges"]] == [known_edge]
