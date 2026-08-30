"""Targeted severance query: given the blocked edges right now, what is cut off?

The sweep in ../severance.py removes every one of ~6000 spans in turn to find
which ones *could* sever. That is an offline study and takes minutes. This is
the online question -- one specific set of blockages, answered in one pass:

    1. hide the blocked edges (a view, not a copy -- no 12k-edge clone per call)
    2. connected components; any component holding no health facility is a
       severed pocket
    3. apply the four filters the sweep validated
    4. one Dijkstra from all facilities over the remaining graph gives every
       still-connected node's new distance to care -> the detours

The baseline distance-to-care map is computed once at startup (see
network.Network) and never recomputed, which is what keeps this under 500 ms.
"""
import functools
import time

import networkx as nx

from app import config
from app.network import edge_id, net
from app.population import population_of


def _blocked_pairs(n, blocked):
    """Blocked edge ids -> (u, v) tuples that actually exist in the graph."""
    pairs = []
    for eid in blocked:
        try:
            u, v = (int(x) for x in str(eid).split("-"))
        except ValueError:
            continue
        if n.graph.has_edge(u, v):
            pairs.append((u, v))
    return pairs


def _directed_triples(n, pairs):
    """Every (u, v, key) in the reversed multigraph backing these undirected
    spans. restricted_view on a MultiDiGraph needs all three components."""
    out = []
    for u, v in pairs:
        for a, b in ((u, v), (v, u)):
            data = n.R.get_edge_data(a, b)
            if data:
                out.extend((a, b, k) for k in data)
    return out


def _severing_edges(n, pocket, pairs):
    """The blocked edges straddling this pocket -- the ones that caused it."""
    return [(u, v) for u, v in pairs if (u in pocket) != (v in pocket)]


def analyse(blocked_edge_ids, detours=True):
    """Everything severed or delayed by the current blockages.

    Returns ``{"pockets", "detours", "node_added_m", "severed_nodes",
    "blocked", "elapsed_ms"}``. The last three are the lookup tables the
    priority scorer needs to turn one blocked edge into a delay in minutes.

    Pass ``detours=False`` to skip the Dijkstra when only severance matters --
    that is most of the cost, and the hypothetical "what if this unassessed
    road were also blocked" pass runs thousands of times more often than it
    needs detour numbers.
    """
    t0 = time.perf_counter()
    n = net()
    pairs = _blocked_pairs(n, set(blocked_edge_ids or ()))

    def done(pockets, detours, added):
        return {"pockets": pockets, "detours": detours, "node_added_m": added,
                "severed_nodes": severed_nodes,
                "blocked": [edge_id(u, v) for u, v in pairs],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}

    severed_nodes = set()
    if not pairs:
        return done([], [], {})

    # restricted_view is a read-only overlay: no graph is copied and no edge is
    # mutated, so concurrent requests cannot corrupt each other's graph.
    cut = nx.restricted_view(n.graph, [], pairs)

    pockets = []
    for comp in nx.connected_components(cut):
        if comp & n.fac_nodes:
            continue
        if n.touches_download_edge(comp):
            continue        # truncated at the download boundary, not severed
        assessed = _assess(n, comp, pairs)
        if assessed is not None:
            pockets.append(assessed)
            severed_nodes |= comp

    found, added = _detours(n, pairs, severed_nodes) if detours else ([], {})
    pockets.sort(key=lambda p: (p["population"] is not None,
                                p["population"] or 0, p["n_nodes"]), reverse=True)
    return done(pockets, found, added)


def _assess(n, comp, pairs):
    """One candidate pocket against the four validated filters.

    All four must pass, or this is a cul-de-sac losing its own driveway rather
    than a severance worth an operator's attention.
    """
    hull, poly, widened = n.hull(comp)
    area = n.hull_area_km2(hull)
    dists = [n.base_map[x] for x in comp if x in n.base_map]
    prior = min(dists) if dists else None
    sev = _severing_edges(n, comp, pairs)
    degrees = [(n.graph.degree(u), n.graph.degree(v)) for u, v in sev]

    checks = {
        "min_nodes": len(comp) >= config.MIN_POCKET_NODES,
        "min_hull_km2": area >= config.MIN_HULL_KM2,
        "prior_access": prior is not None and prior < config.MAX_PRIOR_ACCESS_M,
        # At least one blocked edge must be a real junction-to-junction cut
        # rather than a spur. With several blockages, any one qualifying is
        # enough -- the pocket is severed however many edges it took.
        "endpoint_degree": any(du >= config.MIN_ENDPOINT_DEGREE
                               and dv >= config.MIN_ENDPOINT_DEGREE
                               for du, dv in degrees),
    }
    if not all(checks.values()):
        return None

    pop = population_of(poly)
    centroid = poly.centroid
    lost = n.best_facility(comp)
    place, place_m = n.nearest_place(centroid.y, centroid.x)
    ordered = sorted(int(x) for x in comp)
    return {
        "id": f"pocket-{ordered[0]}",
        "nodes": ordered,
        "n_nodes": len(comp),
        "polygon": n.polygon_latlon(hull),
        "hull_area_km2": round(area, 4),
        "centroid": [round(centroid.y, 6), round(centroid.x, 6)],
        "population": pop["population"],
        "population_source": pop["population_source"],
        "population_method": pop["method"],
        "population_coverage": pop["coverage"],
        "population_settlements": pop["settlements"],
        "hull_widened": widened,
        "lost_facility": lost[0] if lost else None,
        "prior_access_km": round(lost[1] / 1000.0, 2) if lost else None,
        "nearest_place": place,
        "nearest_place_km": round(place_m / 1000.0, 2) if place_m is not None else None,
        "severing_edges": [edge_id(u, v) for u, v in sev],
        "checks": checks,
    }


def _detours(n, pairs, severed_nodes):
    """Nodes that still reach care, but further than before.

    One multi-source Dijkstra over the cut graph, differenced against the cached
    baseline. Affected nodes are then grouped into connected clusters so the
    dashboard shows a handful of regions rather than hundreds of loose nodes.
    """
    cut_r = nx.restricted_view(n.R, [], _directed_triples(n, pairs))
    after = nx.multi_source_dijkstra_path_length(cut_r, n.fac_nodes, weight="length")

    added = {}
    for node, base in n.base_map.items():
        if node in severed_nodes:
            continue
        now = after.get(node)
        if now is None or now - base <= config.DETOUR_THRESHOLD_M:
            continue
        added[int(node)] = round(now - base, 1)
    if not added:
        return [], {}

    sub = nx.restricted_view(n.graph.subgraph(added), [], pairs)
    detours = []
    for comp in nx.connected_components(sub):
        hull, poly, _ = n.hull(comp)
        centroid = poly.centroid
        pop = population_of(poly)
        place, place_m = n.nearest_place(centroid.y, centroid.x)
        deltas = sorted(added[int(x)] for x in comp)
        detours.append({
            "id": f"detour-{min(int(x) for x in comp)}",
            "nodes": sorted(int(x) for x in comp),
            "n_nodes": len(comp),
            "polygon": n.polygon_latlon(hull),
            "centroid": [round(centroid.y, 6), round(centroid.x, 6)],
            "added_km": round(deltas[-1] / 1000.0, 2),
            "median_added_km": round(deltas[len(deltas) // 2] / 1000.0, 2),
            "population": pop["population"],
            "population_source": pop["population_source"],
            "nearest_place": place,
            "nearest_place_km": round(place_m / 1000.0, 2) if place_m is not None else None,
        })
    detours.sort(key=lambda d: (d["added_km"], d["n_nodes"]), reverse=True)
    return detours, added


@functools.lru_cache(maxsize=512)
def hypothetical(edge_id_, blocked):
    """Would blocking `edge_id_` as well sever a pocket? Severance only.

    Memoised on (edge, current blockages) because the recompute pass asks this
    once per unassessed road report and the answer is identical for every
    report bound to the same edge.
    """
    result = analyse(tuple(blocked) + (edge_id_,), detours=False)
    known = {p["id"] for p in analyse(tuple(blocked), detours=False)["pockets"]}
    return [p for p in result["pockets"] if p["id"] not in known]
