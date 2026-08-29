"""Component-based severance analysis on the buffered Surathkal->Mulki graph.

Unit of analysis is the CONNECTED COMPONENT, not the OSM place node. Remove an
edge; any resulting component holding no health facility is a severed pocket,
whether or not OSM happens to tag a settlement inside it. Place nodes are used
only as human-readable labels, never to decide whether severance occurred.
"""
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import networkx as nx
import numpy as np
import osmnx as ox
from shapely.geometry import MultiPoint

# popdata.py is a verbatim copy of population/population.py (no runtime import
# across into population/). Its two DATA FILES still live in population/data --
# pointing at them here rather than duplicating a 100 MB raster.
import popdata

popdata.DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "population", "data")
popdata.RASTER = os.path.join(popdata.DATA, "population_100m.tif")
popdata.SETTLEMENTS = os.path.join(popdata.DATA, "settlements.csv")

# --- tunables -------------------------------------------------------------
W, S, E, N = 74.78, 13.00, 74.86, 13.10
BUFFER_KM = 3.0
DETOUR_THRESHOLD_M = 3000.0
POCKET_BUFFER_M = 100.0   # widen a degenerate hull (point/line) so it has area
EDGE_TOL_M = 250.0        # a pocket this close to the download edge is a clip artifact
TOP_DETAIL = 20           # full detail for this many pockets; rest counted only

# meaningfulness filter -- a pocket must clear ALL of these to count as a real
# severance rather than a cul-de-sac losing its own driveway
MIN_POCKET_NODES = 3
MIN_HULL_KM2 = 0.01
MAX_PRIOR_ACCESS_M = 10_000.0
MIN_ENDPOINT_DEGREE = 3
GRAPHML_BUF = "surathkal_buffered.graphml"

DEG_LAT = BUFFER_KM / 111.0
DEG_LON = BUFFER_KM / (111.0 * np.cos(np.radians((S + N) / 2)))
BUF = (W - DEG_LON, S - DEG_LAT, E + DEG_LON, N + DEG_LAT)

# the two spans the earlier runs flagged as genuinely severing
KNOWN = [("unnamed 507 m", (13.01087, 74.81183), (13.01229, 74.80747)),
         ("unnamed 1041 m", (13.04465, 74.79198), (13.04676, 74.78375))]


def hr(title=""):
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


hr("SETUP")
print(f"graph:  {GRAPHML_BUF}")
G = ox.load_graphml(GRAPHML_BUF)
Gu = ox.convert.to_undirected(G) if hasattr(ox, "convert") else ox.utils_graph.get_undirected(G)
Gs = nx.Graph(Gu)
print(f"nodes {G.number_of_nodes()}  edges {G.number_of_edges()}  "
      f"components {nx.number_connected_components(Gs)}")

_ids = np.array(list(G.nodes))
_xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in _ids])
_coslat = float(np.cos(np.radians(_xy[:, 1].mean())))
_M_PER_DEG = 111_320.0
_pos = {int(n): (float(G.nodes[n]["x"]), float(G.nodes[n]["y"])) for n in G.nodes}


def nearest_node(lon, lat):
    dx = (_xy[:, 0] - lon) * _coslat
    dy = _xy[:, 1] - lat
    return int(_ids[int((dx * dx + dy * dy).argmin())])


def metres(lat1, lon1, lat2, lon2):
    return np.hypot((lat1 - lat2), (lon1 - lon2) * _coslat) * _M_PER_DEG


# --- population -----------------------------------------------------------
RASTER_OK = popdata.raster_available()
print(f"population raster: {popdata.RASTER}")
print(f"  available: {RASTER_OK}" + ("" if RASTER_OK else
      "   <-- WorldPop crop absent (national file still downloading);"
      " pocket populations report as None"))


RB = None
if RASTER_OK:
    import rasterio
    with rasterio.open(popdata.RASTER) as _r:
        RB = _r.bounds
    print(f"  raster bounds: {tuple(round(b, 5) for b in RB)}")
    print(f"  graph bbox:    {tuple(round(b, 5) for b in BUF)}")
    if (RB.left > BUF[0] or RB.bottom > BUF[1]
            or RB.right < BUF[2] or RB.top < BUF[3]):
        print("  WARNING: raster is smaller than the graph. Pockets outside its")
        print("           footprint report coverage=outside, never a silent 0.")


def coverage(poly):
    """full / partial / outside -- the raster is narrower than the graph."""
    if RB is None:
        return "no-raster"
    x0, y0, x1, y1 = poly.bounds
    if x1 < RB.left or x0 > RB.right or y1 < RB.bottom or y0 > RB.top:
        return "outside"
    if x0 >= RB.left and x1 <= RB.right and y0 >= RB.bottom and y1 <= RB.top:
        return "full"
    return "partial"


def pop_of(poly):
    """Population inside a polygon, or None when unavailable/off-raster.

    Never returns 0.0 for a polygon the raster does not cover -- that would be
    indistinguishable from a genuinely empty pocket.
    """
    if not RASTER_OK or coverage(poly) == "outside":
        return None
    try:
        return float(popdata.population_in(poly))
    except FileNotFoundError:
        return None
    except Exception as exc:
        print(f"    [population lookup failed: {type(exc).__name__}: {exc}]")
        return None


def pop_str(poly):
    p, cov = pop_of(poly), coverage(poly)
    if p is None:
        return f"None ({'off raster' if cov == 'outside' else 'raster unavailable'})"
    return f"{p:,.1f}" + ("  [PARTIAL raster coverage -- undercount]"
                          if cov == "partial" else "")


def hull_of(nodes):
    """Convex hull of a node set, plus the polygon actually used for population.

    A 1- or 2-node pocket hulls to a point/line with zero area, which would read
    as zero people. Those get buffered by POCKET_BUFFER_M for the population
    lookup only; the reported area stays that of the raw hull.
    """
    pts = MultiPoint([_pos[n] for n in nodes])
    hull = pts.convex_hull
    if hull.area > 0:
        return hull, hull, False
    deg = POCKET_BUFFER_M / _M_PER_DEG
    return hull, hull.buffer(deg), True


def hull_area_km2(hull):
    if hull.area == 0:
        return 0.0
    return hull.area * (_M_PER_DEG / 1000.0) ** 2 * _coslat


# --- facilities and place labels ------------------------------------------
feat = ox.features_from_bbox


def points(tags, box):
    w, s, e, n = box
    gdf = feat(bbox=(w, s, e, n), tags=tags)
    key = list(tags)[0]
    out = []
    for idx, row in gdf.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        c = g if g.geom_type == "Point" else g.centroid
        name = row.get("name")
        osmid = idx[1] if isinstance(idx, tuple) else idx
        out.append({"name": name if isinstance(name, str) and name.strip()
                    else f"<unnamed {osmid}>",
                    "named": isinstance(name, str) and bool(name.strip()),
                    "type": str(row.get(key)), "lat": c.y, "lon": c.x})
    return out


hr("REFERENCE DATA")
facilities = points({"amenity": ["hospital", "clinic", "doctors"]}, BUF)
places = points({"place": ["village", "town", "hamlet", "suburb"]}, BUF)
named_places = [p for p in places if p["named"]]
for f in facilities:
    f["node"] = nearest_node(f["lon"], f["lat"])
FAC_NODES = {f["node"] for f in facilities}
print(f"health facilities: {len(facilities)}  (snapped to {len(FAC_NODES)} distinct nodes)")
print(f"OSM place nodes: {len(places)}  ({len(named_places)} named, used only as labels)")

# per-facility distance maps on the reversed graph -> lets us name the facility
R = G.reverse(copy=True)
per_fac = {}
for f in facilities:
    if f["node"] in per_fac:
        continue
    per_fac[f["node"]] = nx.single_source_dijkstra_path_length(R, f["node"], weight="length")
base_map = nx.multi_source_dijkstra_path_length(R, FAC_NODES, weight="length")
fac_name_of = {}
for f in facilities:
    fac_name_of.setdefault(f["node"], f["name"])


def best_facility(nodes):
    """(facility name, distance_m, from_node) reachable pre-cut from a node set."""
    best = None
    for fn, dmap in per_fac.items():
        for n in nodes:
            d = dmap.get(n)
            if d is not None and (best is None or d < best[1]):
                best = (fac_name_of[fn], d, n)
    return best


_TOL_LAT = EDGE_TOL_M / _M_PER_DEG
_TOL_LON = _TOL_LAT / _coslat


def clipped_at_download_edge(nodes):
    """True if the pocket reaches the download boundary.

    osmnx truncates ways at the bbox, so a component hanging off the boundary is
    a road that continues outside the download, not a genuinely severed pocket.
    """
    for n in nodes:
        x, y = _pos[n]
        if (abs(x - BUF[0]) < _TOL_LON or abs(x - BUF[2]) < _TOL_LON
                or abs(y - BUF[1]) < _TOL_LAT or abs(y - BUF[3]) < _TOL_LAT):
            return True
    return False


def in_report_bbox(lat, lon):
    return W <= lon <= E and S <= lat <= N


def nearest_named_place(lat, lon):
    if not named_places:
        return None
    p = min(named_places, key=lambda q: metres(lat, lon, q["lat"], q["lon"]))
    return p, metres(lat, lon, p["lat"], p["lon"])


# --- spans ----------------------------------------------------------------
spans = {}
for u, v, k, d in G.edges(keys=True, data=True):
    spans.setdefault(tuple(sorted((u, v))), []).append((u, v, k, d))


def is_bridge(d):
    b = d.get("bridge")
    if b is None:
        return False
    vals = b if isinstance(b, list) else [b]
    return any(str(x).strip().lower() not in ("", "no", "false") for x in vals)


def span_meta(pair):
    es = spans[pair]
    d = max((e[3] for e in es), key=lambda dd: float(dd.get("length", 0) or 0))
    nm = d.get("name") or d.get("ref") or "<unnamed>"
    nm = ", ".join(str(x) for x in nm) if isinstance(nm, list) else str(nm)
    return {"name": nm, "length": float(d.get("length", 0) or 0),
            "highway": d.get("highway"),
            "bridge": any(is_bridge(e[3]) for e in es),
            "bridge_tag": d.get("bridge"), "n_seg": len(es)}


def describe_pocket(nodes, indent="      "):
    hull, poly, widened = hull_of(nodes)
    c = poly.centroid
    lat, lon = c.y, c.x
    population = pop_of(poly)
    np_ = nearest_named_place(lat, lon)
    pre = best_facility(nodes)
    print(f"{indent}nodes ({len(nodes)}): "
          + ", ".join(str(n) for n in sorted(nodes)[:12])
          + (" ..." if len(nodes) > 12 else ""))
    print(f"{indent}centroid: {lat:.5f}, {lon:.5f}")
    print(f"{indent}hull: {hull.geom_type}, area {hull_area_km2(hull):.4f} km2"
          + (f"  (population taken over hull buffered {POCKET_BUFFER_M:.0f} m"
             f" -- raw hull has no area)" if widened else ""))
    print(f"{indent}population: {pop_str(poly)}"
          + (f"  [WorldPop raster, coverage={coverage(poly)}]"
             if population is not None else ""))
    if np_:
        p, dm = np_
        print(f"{indent}nearest named place: {p['name']} ({p['type']}) {dm/1000:.2f} km away"
              f"  -- LABEL ONLY, this place node is NOT inside the pocket")
    else:
        print(f"{indent}nearest named place: none")
    if pre:
        fname, fd, fnode = pre
        print(f"{indent}before the cut reached: {fname} at {fd/1000:.2f} km (via node {fnode})")
    else:
        print(f"{indent}before the cut reached: nothing (already unreachable)")
    return {"n": len(nodes), "pop": population, "hull": hull, "poly": poly,
            "centroid": (lat, lon), "pre": pre, "label": np_}


def pockets_after(pair):
    """Components left with no health facility after removing this span."""
    u, v = pair
    Gs.remove_edge(u, v)
    out = [c for c in nx.connected_components(Gs) if not (c & FAC_NODES)]
    Gs.add_edge(u, v)
    return out


# --- 1. severing edges ----------------------------------------------------
hr("1. SEVERING EDGES  (component-based, no reliance on OSM place tags)")
print("only graph-theoretic cut edges can split the graph, so those are the")
print("candidates; each is then checked for a component holding no facility.\n")

cut_edges = [tuple(sorted(e)) for e in nx.bridges(Gs)]
print(f"graph-theoretic cut edges: {len(cut_edges)} of {len(spans)} spans")

raw = []
for pair in cut_edges:
    pk = pockets_after(pair)
    if pk:
        raw.append((pair, pk))
print(f"cut edges stranding a facility-free component (raw): {len(raw)}")

# A component hanging off the download boundary is a truncated road, not a
# severed pocket. Separate those out rather than letting them dominate.
candidates, artifacts = [], 0
for pair, pk in raw:
    real = [nodes for nodes in pk if not clipped_at_download_edge(nodes)]
    if not real:
        artifacts += 1
        continue
    for nodes in real:
        candidates.append((pair, frozenset(nodes)))
print(f"  of which pure download-boundary artifacts (discarded): {artifacts}")
print(f"  candidate pockets surviving the boundary check: {len(candidates)}")


def assess(pair, nodes):
    """Per-criterion verdict for one candidate pocket."""
    u, v = pair
    hull, poly, widened = hull_of(nodes)
    area = hull_area_km2(hull)
    dists = [base_map[n] for n in nodes if n in base_map]
    prior = min(dists) if dists else None
    du, dv = Gs.degree(u), Gs.degree(v)
    return {
        "pair": pair, "nodes": nodes, "n": len(nodes), "area": area,
        "prior": prior, "deg": (du, dv), "hull": hull, "poly": poly,
        "widened": widened,
        "c1": len(nodes) >= MIN_POCKET_NODES,
        "c2": area >= MIN_HULL_KM2,
        "c3": prior is not None and prior < MAX_PRIOR_ACCESS_M,
        "c4": du >= MIN_ENDPOINT_DEGREE and dv >= MIN_ENDPOINT_DEGREE,
    }


CRITERIA = [
    ("c1", f"pocket has >= {MIN_POCKET_NODES} nodes"),
    ("c2", f"convex hull area >= {MIN_HULL_KM2} km2"),
    ("c3", f"prior access to care < {MAX_PRIOR_ACCESS_M/1000:.0f} km"),
    ("c4", f"both removed-edge endpoints have degree >= {MIN_ENDPOINT_DEGREE}"),
]

assessed = [assess(p, n) for p, n in candidates]

hr("1b. MEANINGFULNESS FILTER")
print(f"candidate pockets entering the filter: {len(assessed)}\n")
print("pass rate for each criterion ON ITS OWN (shows which filter does the work):")
for key, label in CRITERIA:
    k = sum(1 for a in assessed if a[key])
    print(f"  {key}  {label:<52} {k:>5} pass  {len(assessed)-k:>5} fail")

print("\ncumulative funnel, applied in order:")
run = assessed
print(f"  {'start':<58} {len(run):>5}")
for key, label in CRITERIA:
    before = len(run)
    run = [a for a in run if a[key]]
    print(f"  + {key}  {label:<54} {len(run):>5}   (-{before - len(run)})")

survivors = run
survivors.sort(key=lambda a: a["n"], reverse=True)
hr(f"1c. SURVIVING POCKETS  ({len(survivors)})")
if not RASTER_OK:
    print("ranked by node count; population is None until the WorldPop crop exists.\n")
else:
    print("ranked by node count, population shown where the raster resolves it.\n")

if not survivors:
    print("NONE survived the filter.")
for i, a in enumerate(survivors, 1):
    m = span_meta(a["pair"])
    u, v = a["pair"]
    na, nb = G.nodes[u], G.nodes[v]
    pop = pop_of(a["poly"])
    cen = a["poly"].centroid
    print(f"\n#{i}  {'BRIDGE' if m['bridge'] else 'road  '}  {m['name']}"
          f"{'' if in_report_bbox(cen.y, cen.x) else '   [outside report bbox]'}")
    print(f"    bridge tag: {m['bridge_tag'] if m['bridge'] else 'none'}   "
          f"highway={m['highway']}   length {m['length']:.1f} m")
    print(f"    ({na['y']:.5f},{na['x']:.5f}) -> ({nb['y']:.5f},{nb['x']:.5f})"
          f"   endpoint degrees {a['deg'][0]}/{a['deg'][1]}")
    print(f"    pocket: {a['n']} nodes, hull {a['area']:.4f} km2, "
          f"prior access {a['prior']/1000:.2f} km, population {pop_str(a['poly'])}")
    describe_pocket(set(a["nodes"]))

hr("1d. THE TWO KNOWN SPANS AGAINST THE FILTER")
for label, pa, pb in KNOWN:
    u, v = nearest_node(pa[1], pa[0]), nearest_node(pb[1], pb[0])
    pair = tuple(sorted((u, v)))
    print(f"\n{label}: ({pa[0]:.5f},{pa[1]:.5f}) -> ({pb[0]:.5f},{pb[1]:.5f})  "
          f"nodes {u} -> {v}")
    mine = [a for a in assessed if a["pair"] == pair]
    if not mine:
        print("  no candidate pocket for this span "
              "(either not a cut edge, or its pocket was a boundary artifact)")
        continue
    for a in mine:
        verdict = all(a[k] for k, _ in CRITERIA)
        print(f"  pocket of {a['n']} nodes, hull {a['area']:.4f} km2, "
              f"prior access {a['prior']/1000:.2f} km, endpoint degrees "
              f"{a['deg'][0]}/{a['deg'][1]}")
        for key, lab in CRITERIA:
            print(f"    {'PASS' if a[key] else 'FAIL'}  {key}  {lab}")
        print(f"  VERDICT: {'PASSES the filter' if verdict else 'EXCLUDED'}")

if "--no-detour" in sys.argv:
    hr("DONE (detour sweep skipped via --no-detour)")
    raise SystemExit(0)

# --- 2. detour sweep ------------------------------------------------------
hr(f"2. DETOUR SWEEP  (all {len(spans)} spans; nodes losing >"
   f"{DETOUR_THRESHOLD_M/1000:.0f} km of access, still reaching care)")


def care_map_without(pair):
    es = spans[pair]
    rev = [(v, u, k, d) for u, v, k, d in es]
    R.remove_edges_from([(a, b, k) for a, b, k, _ in rev])
    dm = nx.multi_source_dijkstra_path_length(R, FAC_NODES, weight="length")
    R.add_edges_from(rev)
    return dm


t0 = time.time()
detours = []
for i, pair in enumerate(spans, 1):
    if i % 1500 == 0:
        print(f"  ... {i}/{len(spans)}  ({time.time()-t0:.0f}s)", flush=True)
    dm = care_map_without(pair)
    hit = [n for n, b in base_map.items()
           if n in dm and dm[n] - b > DETOUR_THRESHOLD_M]
    if hit:
        worst = max(dm[n] - base_map[n] for n in hit)
        detours.append((pair, set(hit), worst))
print(f"done in {time.time()-t0:.0f}s")

det_rows = []
for pair, nodes, worst in detours:
    _, poly, _ = hull_of(nodes)
    det_rows.append({"pair": pair, "nodes": nodes, "worst": worst,
                     "pop": pop_of(poly), "meta": span_meta(pair)})
det_rows.sort(key=lambda r: (r["pop"] if r["pop"] is not None else -1,
                             len(r["nodes"]), r["worst"]), reverse=True)

print(f"\nspans adding >{DETOUR_THRESHOLD_M/1000:.0f} km for at least one node: {len(det_rows)}")
for i, r in enumerate(det_rows, 1):
    m, (u, v) = r["meta"], r["pair"]
    a, b = G.nodes[u], G.nodes[v]
    print(f"\n#{i}  {'BRIDGE' if m['bridge'] else 'road  '}  {m['name']}   "
          f"highway={m['highway']}   {m['length']:.1f} m")
    print(f"    ({a['y']:.5f},{a['x']:.5f}) -> ({b['y']:.5f},{b['x']:.5f})")
    print(f"    nodes affected: {len(r['nodes'])}   worst added: {r['worst']/1000:.2f} km   "
          f"population: " + ("None" if r["pop"] is None else f"{r['pop']:,.1f}"))
    lbl = nearest_named_place(*[c for c in hull_of(r["nodes"])[1].centroid.coords[0][::-1]])
    if lbl:
        print(f"    nearest named place to affected area: {lbl[0]['name']} "
              f"{lbl[1]/1000:.2f} km  -- LABEL ONLY")

# --- 3. combined ranking --------------------------------------------------
hr("3. COMBINED RANKING  (severance outranks any detour)")
print("population affected; severance first, then detour.\n")
print("severance rows are the FILTERED survivors, not the raw 1700+.\n")
rank = ([("SEVER", a["pair"], span_meta(a["pair"]), pop_of(a["poly"]), a["n"], None)
         for a in survivors]
        + [("DETOUR", r["pair"], r["meta"], r["pop"], len(r["nodes"]), r["worst"])
           for r in det_rows])
print(f"{'#':>3}  {'kind':<6} {'brdg':<5} {'pop':>10} {'nodes':>6} {'worst_km':>9}  edge")
print("-" * 96)
for i, (kind, pair, m, pop, nn, worst) in enumerate(rank, 1):
    print(f"{i:>3}  {kind:<6} {'YES' if m['bridge'] else 'no':<5} "
          f"{'None' if pop is None else f'{pop:,.1f}':>10} {nn:>6} "
          f"{'-' if worst is None else f'{worst/1000:.2f}':>9}  "
          f"{m['name'][:30]} ({m['length']:.0f} m, {m['highway']})")

# --- 4. the two known spans ----------------------------------------------
hr("4. THE TWO KNOWN SEVERING SPANS, CONFIRMED")
for label, a, b in KNOWN:
    u, v = nearest_node(a[1], a[0]), nearest_node(b[1], b[0])
    pair = tuple(sorted((u, v)))
    print(f"\n{label}: ({a[0]:.5f},{a[1]:.5f}) -> ({b[0]:.5f},{b[1]:.5f})")
    print(f"  resolved to nodes {u} -> {v}")
    if pair not in spans:
        print("  NO SUCH SPAN in the buffered graph (endpoints not directly connected)")
        continue
    m = span_meta(pair)
    print(f"  edge: {m['name']}   length {m['length']:.1f} m   highway={m['highway']}")
    print(f"  BRIDGE TAG: {m['bridge_tag'] if m['bridge'] else 'NONE -- not tagged as a bridge'}")
    pk = pockets_after(pair)
    if not pk:
        print("  removal severs nothing (no facility-free component)")
        continue
    print(f"  severed pockets: {len(pk)}")
    for nodes in pk:
        info = describe_pocket(nodes, indent="    ")
        print(f"    polygon area: {hull_area_km2(info['hull']):.4f} km2")
        print(f"    pocket population: {pop_str(info['poly'])}")
        if info["pre"]:
            print(f"    facility that becomes unreachable: {info['pre'][0]} "
                  f"(was {info['pre'][1]/1000:.2f} km)")

hr("DONE")
