"""Everything needed to script the live demo, pulled from the runtime engine.

Uses app/severance.py and app/network.py -- the same code path the backend
serves -- so nothing here can drift from what the dashboard shows.

    .venv/Scripts/python.exe demo_data.py
"""
import networkx as nx

from app import binding, config, priority, severance
from app.network import edge_id, net

# The two demo items, located by the coordinates that identify them in the
# analysis. Resolved to node ids at runtime rather than hard-coded.
ITEMS = [
    ("DEMO ITEM 1 -- rank #1 severance",
     (13.016616, 74.794199), (13.017250, 74.795746)),
    ("DEMO ITEM 2 -- Madhya Padavu bridge, rank #19",
     (13.01087, 74.81183), (13.01229, 74.80747)),
]

n = net()


def rule(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def fac_by_name(name):
    for f in n.facilities:
        if f["name"] == name:
            return f
    return None


def route_before(pocket_nodes, fac_name):
    """The actual pre-cut path from the pocket to the facility it loses."""
    f = fac_by_name(fac_name)
    if f is None:
        return None
    best = None
    for node in pocket_nodes:
        d = n.per_fac[f["node"]].get(node)
        if d is not None and (best is None or d < best[1]):
            best = (node, d)
    if best is None:
        return None
    # per_fac lives on the reversed graph: a path facility->node there is the
    # real-world path node->facility.
    path = nx.shortest_path(n.R, f["node"], best[0], weight="length")
    return best[0], best[1], list(reversed(path))


def photo_point(eid):
    """A coordinate a citizen photo could plausibly carry: on the carriageway,
    and close enough to bind on a normal phone fix.

    binding.candidates measures distance to each span's straight endpoint
    CHORD, not its real polyline. On a curved span the carriageway can sit tens
    of metres off that chord, so the naive midpoint-of-geometry only binds via
    the fallback widening. Pick the geometry vertex nearest the chord instead:
    still a real point on the road, but one the binder resolves tightly.
    """
    u, v = (int(x) for x in eid.split("-"))
    (ax, ay), (bx, by) = n.pos[u], n.pos[v]
    coords = n.span_coords(eid)
    if len(coords) < 2:
        return (ay + by) / 2.0, (ax + bx) / 2.0
    interior = coords[1:-1] or coords

    def chord_m(pt):
        return binding._point_segment_m(pt[0], pt[1], ay, ax, by, bx, n.coslat)

    best = min(interior, key=chord_m)
    return float(best[0]), float(best[1]), chord_m(best)


for label, pa, pb in ITEMS:
    u, v = n.nearest_node(*pa), n.nearest_node(*pb)
    eid = edge_id(u, v)
    rule(label)

    res = severance.analyse([eid])
    if not res["pockets"]:
        print(f"  no pocket for {eid} -- check the coordinates")
        continue
    p = res["pockets"][0]
    segs = n.spans[eid]
    d = max((s[3] for s in segs), key=lambda dd: float(dd.get("length", 0) or 0))
    bridge = d.get("bridge")

    print("\nSEVERING EDGE")
    print(f"  edge_id      : {eid}")
    print(f"  node IDs     : {u} -> {v}")
    print(f"  endpoint A   : {n.pos[u][1]:.6f}, {n.pos[u][0]:.6f}")
    print(f"  endpoint B   : {n.pos[v][1]:.6f}, {n.pos[v][0]:.6f}")
    print(f"  name         : {n.span_name(eid)}")
    print(f"  length       : {n.span_length(eid):.1f} m")
    print(f"  highway      : {d.get('highway')}")
    print(f"  bridge tag   : {bridge if bridge else 'NONE (not a bridge)'}")
    print(f"  endpoint degrees: {n.graph.degree(u)} / {n.graph.degree(v)}")
    print(f"  directed segments: {len(segs)}")
    print("  GEOMETRY FOR DRAWING (lat, lon):")
    for la, lo in n.span_coords(eid):
        print(f"    {la:.6f}, {lo:.6f}")

    print(f"\nSEVERED POCKET  ({p['n_nodes']} nodes)")
    print(f"  node IDs: {p['nodes']}")
    print(f"  centroid: {p['centroid'][0]:.6f}, {p['centroid'][1]:.6f}")
    print(f"  hull area: {p['hull_area_km2']} km2"
          + ("   (degenerate hull, buffered for population)" if p["hull_widened"] else ""))
    print("  HULL POLYGON (lat, lon):")
    for la, lo in p["polygon"]:
        print(f"    {la:.6f}, {lo:.6f}")

    print("\nPOPULATION")
    print(f"  value    : {p['population']} people")
    print(f"  source   : {p['population_source']}")
    print(f"  method   : {p['population_method']}   coverage: {p['population_coverage']}")
    if p["population_source"] == "raster":
        print("  WorldPop 2020 constrained 100 m gridded estimate, summed over the")
        print("  hull polygon. A MODELLED ESTIMATE, not an enumerated census count,")
        print("  and not tied to any administrative boundary. Label it as an estimate.")
    if p.get("population_settlements"):
        print(f"  census settlements inside: {p['population_settlements']}")

    print("\nFACILITY LOST")
    f = fac_by_name(p["lost_facility"])
    print(f"  name        : {p['lost_facility']}")
    if f:
        print(f"  coordinates : {f['lat']:.6f}, {f['lon']:.6f}")
        print(f"  type        : {f.get('type')}")
        print(f"  graph node  : {f['node']}")
    print(f"  prior access: {p['prior_access_km']} km by road")
    print(f"  after the cut: NO ROUTE to any of the {len(n.facilities)} facilities")

    rb = route_before(p["nodes"], p["lost_facility"])
    if rb:
        from_node, dist_m, path = rb
        print(f"\n  ROUTE BEFORE THE CUT  (node {from_node} -> {p['lost_facility']}, "
              f"{dist_m/1000:.2f} km, {len(path)} nodes)")
        print(f"    crosses the severing edge: "
              f"{'YES' if any(edge_id(path[i], path[i+1]) == eid for i in range(len(path)-1)) else 'no'}")
        print("    path (lat, lon):")
        for node in path:
            x, y = n.pos[int(node)]
            print(f"      {node:>12}  {y:.6f}, {x:.6f}")

    print("\nNEAREST NAMED SETTLEMENTS -- LABELS ONLY")
    print("  none of these was used to detect the severance, and none need lie")
    print("  inside the pocket.")
    cy, cx = p["centroid"]
    for q in sorted(n.places,
                    key=lambda z: n.metres(cy, cx, z["lat"], z["lon"]))[:3]:
        print(f"    {q['name']} ({q.get('type')})   "
              f"{n.metres(cy, cx, q['lat'], q['lon'])/1000:.2f} km   "
              f"@ {q['lat']:.6f}, {q['lon']:.6f}")

    print("\nPRIORITY SCORE  (factor by factor)")
    pop_used = (config.ASSUMED_POCKET_POPULATION if not p["population"]
                else float(p["population"]))
    b = priority.belief("impassable", 1.0, n_reports=1, status="pending")
    dm = priority.delay_minutes(True)
    popf = pop_used ** config.POPULATION_EXPONENT
    print(f"  belief            = {b:.4f}")
    print(f"    state=impassable, confidence=1.0, n_reports=1, status=pending")
    print(f"    = 1.0 * (0.6 + {config.CORROBORATION_WEIGHT} * (1 - 0.5^1)) = {b:.4f}")
    print(f"  population        = {pop_used:,.1f}")
    print(f"  population^{config.POPULATION_EXPONENT}     = {popf:.4f}")
    print(f"  delay_minutes     = {dm:.1f}   (DISCONNECT_PENALTY, severed)")
    print(f"  priority          = {b:.4f} * {popf:.4f} * {dm:.1f} "
          f"= {b * popf * dm:.1f}")
    print(f"  engine score()    = "
          f"{priority.score('impassable', 1.0, p['population'], True)}")
    print(f"  with UNKNOWN_BELIEF={config.UNKNOWN_BELIEF} instead (unverified report): "
          f"{priority.score('unknown', None, p['population'], True)}")

    lat, lon, chord_m = photo_point(eid)
    chosen, cands = binding.bind(lat, lon, gps_accuracy_m=8.0)
    print("\nDEMO PHOTO COORDINATE  (for EXIF GPS)")
    print(f"  latitude  : {lat:.6f}")
    print(f"  longitude : {lon:.6f}")
    print(f"  a vertex of the span's real OSM geometry, {chord_m:.1f} m from the")
    print(f"  straight endpoint chord that binding.candidates measures against")
    print(f"  suggested EXIF GPSHorizontalPositioningError: 8 m (a good phone fix)")
    print(f"  binds to  : {chosen}   -> {'CORRECT' if chosen == eid else 'WRONG EDGE'}")
    for c in cands[:3]:
        print(f"    candidate {c['edge_id']:<26} {c['distance_m']:>7.2f} m  "
              f"score {c['score']}" + ("   <- chosen" if c["edge_id"] == chosen else ""))

rule("DONE")
