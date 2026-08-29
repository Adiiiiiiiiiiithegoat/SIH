"""Surathkal -> Mulki road-network structure check for the disaster-assessment demo.

v2: settlements are node CLUSTERS (not single snapped nodes), and the graph is
downloaded with a buffer so bbox clipping stops manufacturing false dead ends.
"""
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import networkx as nx
import numpy as np
import osmnx as ox

# --- tunables -------------------------------------------------------------
W, S, E, N = 74.78, 13.00, 74.86, 13.10   # reporting bbox (unbuffered)
BUFFER_KM = 3.0                            # download margin on all sides
CLUSTER_RADIUS_M = 300.0                   # settlement = every node within this
DETOUR_THRESHOLD_M = 3000.0                # "materially worse access to care"
TOP_N_EDGES = 15

GRAPHML = "surathkal.graphml"              # original, kept for reference
GRAPHML_BUF = "surathkal_buffered.graphml"

# single-approach names from the pre-fix run, for the artifact/survivor note
PREV_SINGLE = {"Chitrapu", "Kayar Katte", "Kilpady", "Krishnapura", "Kukkatte",
               "Mulki", "Pakshikere", "Sasihitlu", "Shimanthuru", "Surinje"}

DEG_LAT = BUFFER_KM / 111.0
DEG_LON = BUFFER_KM / (111.0 * np.cos(np.radians((S + N) / 2)))
BW, BS, BE, BN = W - DEG_LON, S - DEG_LAT, E + DEG_LON, N + DEG_LAT

VER = tuple(int(p) for p in ox.__version__.split(".")[:2] if p.isdigit())
NEW_BBOX = VER >= (2, 0)           # v2.x : bbox=(west, south, east, north)
MID_BBOX = (1, 9) <= VER < (2, 0)  # v1.9 : bbox=(north, south, east, west)


def hr(title=""):
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def bbox_variants(fn, box, **kw):
    w, s, e, n = box
    v2 = lambda: fn(bbox=(w, s, e, n), **kw)
    v19 = lambda: fn(bbox=(n, s, e, w), **kw)
    v1 = lambda: fn(north=n, south=s, east=e, west=w, **kw)
    if NEW_BBOX:
        return [v2, v19, v1]
    if MID_BBOX:
        return [v19, v1, v2]
    return [v1, v19, v2]


def inside(lon, lat, box):
    w, s, e, n = box
    return w <= lon <= e and s <= lat <= n


def call_bbox(fn, accept, box, **kw):
    """Try each API form; take the first whose result actually lands in the bbox."""
    last = None
    for form in bbox_variants(fn, box, **kw):
        try:
            result = form()
        except Exception as exc:
            last = exc
            continue
        if accept(result):
            return result
        last = ValueError("result did not land inside the requested bbox")
    if last is not None:
        print(f"  [all bbox call forms failed; last: {type(last).__name__}: {last}]")
    return None


# --------------------------------------------------------------------------
hr("ENVIRONMENT")
style = "v2 (west,south,east,north)" if NEW_BBOX else (
    "v1.9 (north,south,east,west)" if MID_BBOX else "v1.x separate kwargs")
print(f"osmnx    {ox.__version__}   -> bbox style: {style}")
print(f"networkx {nx.__version__}")
print(f"report bbox    west={W} south={S} east={E} north={N}")
print(f"download bbox  west={BW:.5f} south={BS:.5f} east={BE:.5f} north={BN:.5f}"
      f"   (+{BUFFER_KM} km all sides)")
print(f"cluster radius {CLUSTER_RADIUS_M:.0f} m    detour threshold {DETOUR_THRESHOLD_M/1000:.0f} km")

BUF = (BW, BS, BE, BN)
REPORT = (W, S, E, N)

# --- 1. road network (buffered) -------------------------------------------
hr("1. DRIVABLE ROAD NETWORK  (buffered download)")


def graph_ok(g):
    if g is None or g.number_of_nodes() == 0:
        return False
    sample = list(g.nodes(data=True))[:50]
    hits = sum(inside(d["x"], d["y"], BUF) for _, d in sample)
    return hits > len(sample) * 0.5


if os.path.exists(GRAPHML_BUF):
    print(f"loading cached {GRAPHML_BUF} (delete it to re-download)")
    G = ox.load_graphml(GRAPHML_BUF)
else:
    G = call_bbox(ox.graph_from_bbox, graph_ok, BUF, network_type="drive")
    if G is None:
        sys.exit("FATAL: could not download the buffered road network.")
    ox.save_graphml(G, GRAPHML_BUF)
    print(f"downloaded and saved -> {GRAPHML_BUF}")
if os.path.exists(GRAPHML):
    print(f"(original unbuffered {GRAPHML} left in place, untouched)")

print(f"nodes: {G.number_of_nodes()}")
print(f"edges: {G.number_of_edges()}")

Gu = ox.convert.to_undirected(G) if hasattr(ox, "convert") else ox.utils_graph.get_undirected(G)
Gs = nx.Graph(Gu)  # simple undirected; parallel carriageways collapse -> conservative
comps = list(nx.connected_components(Gs))
lcc = max(comps, key=len)
print(f"connected components: {len(comps)} "
      f"(largest holds {len(lcc)}/{Gs.number_of_nodes()} nodes)")

# ponytail: osmnx's nearest_nodes needs scikit-learn on an unprojected graph.
# Equirectangular distance over a few thousand nodes is numpy; skip the dep.
_ids = np.array(list(G.nodes))
_xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in _ids])
_coslat = float(np.cos(np.radians(_xy[:, 1].mean())))
_M_PER_DEG = 111_320.0


def _dist_m(lon, lat):
    dx = (_xy[:, 0] - lon) * _coslat * _M_PER_DEG
    dy = (_xy[:, 1] - lat) * _M_PER_DEG
    return np.sqrt(dx * dx + dy * dy)


def nearest_node(lon, lat):
    return int(_ids[int(_dist_m(lon, lat).argmin())])


def node_cluster(lon, lat, radius=CLUSTER_RADIUS_M):
    """Every graph node within `radius` of the point; never empty."""
    d = _dist_m(lon, lat)
    sel = {int(i) for i in _ids[d <= radius]}
    sel.add(int(_ids[int(d.argmin())]))
    return sel


# --- feature query helper -------------------------------------------------
feat_fn = getattr(ox, "features_from_bbox", None) or getattr(ox, "geometries_from_bbox", None)


def parse_pop(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def query_points(tags, label, box):
    """[{name, type, lat, lon, pop}] for an OSM tag query; [] on empty/failure."""
    if feat_fn is None:
        print("  osmnx exposes neither features_from_bbox nor geometries_from_bbox")
        return []
    gdf = call_bbox(feat_fn, lambda g: g is not None, box, tags=tags)
    if gdf is None or len(gdf) == 0:
        return []
    key = list(tags.keys())[0]
    out = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        c = geom if geom.geom_type == "Point" else geom.centroid
        if not inside(c.x, c.y, box):
            continue
        osmid = idx[1] if isinstance(idx, tuple) else idx
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            name = f"<unnamed {label} {osmid}>"
        kind = row.get(key)
        out.append({"name": name, "type": str(kind) if kind else "?",
                    "lat": c.y, "lon": c.x, "pop": parse_pop(row.get("population"))})
    return out


# --- 2. health facilities -------------------------------------------------
hr("2. HEALTH FACILITIES  (amenity = hospital | clinic | doctors)")
print("queried over the BUFFERED bbox: a hospital just outside the report area is")
print("still a real destination, and excluding it would inflate distance-to-care.\n")
hospitals = query_points({"amenity": ["hospital", "clinic", "doctors"]}, "facility", BUF)
if not hospitals:
    print("NONE FOUND -- OSM carries no matching amenity tag in this area.")
    print("Step 6 needs facilities; define them manually if this gap is real.")
else:
    n_in = sum(inside(h["lon"], h["lat"], REPORT) for h in hospitals)
    print(f"found: {len(hospitals)}   ({n_in} inside the original bbox, "
          f"{len(hospitals) - n_in} in the buffer ring)\n")
    for i, h in enumerate(sorted(hospitals, key=lambda d: d["name"]), 1):
        tag = "" if inside(h["lon"], h["lat"], REPORT) else "  [buffer]"
        print(f"{i:3}. {h['name'][:42]:<42} {h['type']:<9} {h['lat']:.5f}, {h['lon']:.5f}{tag}")

# --- 3. settlements -------------------------------------------------------
hr("3. SETTLEMENTS  (place = village | town | hamlet | suburb)")
all_setts = query_points({"place": ["village", "town", "hamlet", "suburb"]}, "place", BUF)
settlements = [s for s in all_setts if inside(s["lon"], s["lat"], REPORT)]
print(f"found in buffered area: {len(all_setts)}")
print(f"REPORTED (place node inside the original bbox): {len(settlements)}")
print("buffer-ring settlements are dropped from reporting but their roads stay in the graph.\n")
if not settlements:
    print("NONE FOUND inside the original bbox -- steps 5 and 6 cannot run.")
else:
    for i, s in enumerate(sorted(settlements, key=lambda d: d["name"]), 1):
        pop = f"pop {s['pop']}" if s["pop"] else "pop unknown"
        print(f"{i:3}. {s['name'][:38]:<38} {s['type']:<9} "
              f"{s['lat']:.5f}, {s['lon']:.5f}  {pop}")

# attach clusters
for s in settlements:
    s["cluster"] = node_cluster(s["lon"], s["lat"])
    s["node"] = nearest_node(s["lon"], s["lat"])

# --- 4. bridges -----------------------------------------------------------
hr("4. BRIDGE EDGES  (sorted by length, longest first)")


def is_bridge(d):
    b = d.get("bridge")
    if b is None:
        return False
    vals = b if isinstance(b, list) else [b]
    return any(str(v).strip().lower() not in ("", "no", "false") for v in vals)


def ename(d):
    n = d.get("name") or d.get("ref") or "<unnamed>"
    return ", ".join(str(x) for x in n) if isinstance(n, list) else str(n)


# group every directed/parallel edge by its unordered endpoint pair = one span
spans_all = {}
for u, v, k, d in G.edges(keys=True, data=True):
    spans_all.setdefault(tuple(sorted((u, v))), []).append((u, v, k, d))

bridge_spans = {p: es for p, es in spans_all.items() if any(is_bridge(d) for *_, d in es)}
ordered_bridges = sorted(bridge_spans.items(),
                         key=lambda kv: -max(float(d.get("length", 0) or 0) for *_, d in kv[1]))
n_bridge_edges = sum(len(es) for es in bridge_spans.values())
print(f"bridge edge-segments: {n_bridge_edges}   unique undirected spans: {len(bridge_spans)}\n")
if not bridge_spans:
    print("NONE FOUND.")
for i, (pair, es) in enumerate(ordered_bridges, 1):
    d = max((e[3] for e in es), key=lambda dd: float(dd.get("length", 0) or 0))
    u, v = pair
    a, b = G.nodes[u], G.nodes[v]
    ring = "" if inside(a["x"], a["y"], REPORT) else "  [buffer]"
    print(f"{i:3}. {ename(d)[:34]:<34} {float(d.get('length', 0) or 0):8.1f} m   "
          f"({a['y']:.5f},{a['x']:.5f}) -> ({b['y']:.5f},{b['x']:.5f}){ring}")

# --- 5. road approaches per settlement ------------------------------------
hr("5. ROAD APPROACHES PER SETTLEMENT  (cluster-based, buffered graph)")
print("edges that must be cut to sever the settlement's whole node cluster from the")
print("network core. cluster size 1 = still a point estimate, reading is fragile.\n")

if not settlements:
    print("SKIPPED -- no settlements in the report bbox.")
    ranked = []
else:
    hub = max(lcc, key=lambda n: Gs.degree(n))
    ranked = []
    for s in settlements:
        cl = s["cluster"] & set(Gs.nodes)
        in_core = cl & lcc
        if not in_core:
            ranked.append((0, s, "cluster outside largest component"))
            continue
        if hub in cl:
            ranked.append((sum(Gs.degree(n) for n in cl), s, "cluster contains the hub"))
            continue
        mapping = {n: "_CLUSTER" for n in cl}
        H = nx.relabel_nodes(Gs, mapping, copy=True)
        H.remove_edges_from(list(nx.selfloop_edges(H)))
        try:
            ranked.append((nx.edge_connectivity(H, "_CLUSTER", hub), s, ""))
        except Exception:
            ranked.append((H.degree("_CLUSTER"), s, "connectivity failed; showing degree"))
    ranked.sort(key=lambda t: (t[0], t[1]["name"]))

    print(f"{'appr':>4} {'clust':>5} {'settlement':<38} {'pop':>7}  note")
    print("-" * 92)
    for c, s, note in ranked:
        flag = "  <== SINGLE ROAD APPROACH" if c == 1 else ("  <== ISOLATED" if c == 0 else "")
        frag = "  (cluster=1, fragile)" if len(s["cluster"]) == 1 else ""
        pop = str(s["pop"]) if s["pop"] else "-"
        print(f"{c:>4} {len(s['cluster']):>5} {s['name'][:38]:<38} {pop:>7}  {note}{frag}{flag}")

    now_single = {s["name"] for c, s, _ in ranked if c <= 1}
    print(f"\nsettlements with <= 1 road approach: {len(now_single)}")
    print(f"  {', '.join(sorted(now_single)) if now_single else '(none)'}")
    print(f"\nsurvived both fixes (single before AND after): "
          f"{', '.join(sorted(PREV_SINGLE & now_single)) or '(none)'}")
    print(f"clipping/snapping artifacts (single before, not after): "
          f"{', '.join(sorted(PREV_SINGLE - now_single)) or '(none)'}")
    print(f"newly revealed (single only after the fixes): "
          f"{', '.join(sorted(now_single - PREV_SINGLE)) or '(none)'}")

# --- 6 + 7. edge removal impact -------------------------------------------
hr("6. EDGE REMOVAL -> IMPACT ON ACCESS TO HEALTH CARE  (cluster-based)")

if not settlements or not hospitals:
    print(f"SKIPPED -- settlements={len(settlements)}, facilities={len(hospitals)}; both needed.")
else:
    hnodes = {nearest_node(h["lon"], h["lat"]) for h in hospitals}
    hnode_name = {}
    for h in hospitals:
        hnode_name.setdefault(nearest_node(h["lon"], h["lat"]), h["name"])
    R = G.reverse(copy=True)  # reversed: one Dijkstra = every node's distance TO care

    def care_map():
        try:
            return nx.multi_source_dijkstra_path_length(R, hnodes, weight="length")
        except Exception:
            return {}

    def cluster_dist(dm, s):
        vals = [dm[n] for n in s["cluster"] if n in dm]
        return min(vals) if vals else None

    base_map = care_map()
    for s in settlements:
        s["base"] = cluster_dist(base_map, s)

    print("BASELINE distance to nearest health facility (min over cluster):\n")
    for s in sorted(settlements, key=lambda d: d["name"]):
        pop = f"pop {s['pop']}" if s["pop"] else ""
        print(f"  {s['name'][:38]:<38} "
              + (f"{s['base']/1000:8.2f} km" if s["base"] is not None else "  UNREACHABLE")
              + f"   {pop}")

    def evaluate(pair):
        """Remove one undirected span, return [(settlement, before, after|None)]."""
        es = spans_all[pair]
        rev = [(v, u, k, d) for u, v, k, d in es]
        R.remove_edges_from([(a, b, k) for a, b, k, _ in rev])
        dm = care_map()
        R.add_edges_from(rev)
        hits = []
        for s in settlements:
            if s["base"] is None:
                continue
            after = cluster_dist(dm, s)
            if after is None or after - s["base"] > DETOUR_THRESHOLD_M:
                hits.append((s, s["base"], after))
        return hits

    def severity(hits):
        cut = [h for h in hits if h[2] is None]
        det = [h for h in hits if h[2] is not None]
        return (sum(h[0]["pop"] or 0 for h in cut), len(cut),
                sum(h[0]["pop"] or 0 for h in det), sum(h[2] - h[1] for h in det))

    def describe(pair, hits, rank=None):
        es = spans_all[pair]
        d = max((e[3] for e in es), key=lambda dd: float(dd.get("length", 0) or 0))
        u, v = pair
        a, b = G.nodes[u], G.nodes[v]
        kind = "BRIDGE" if pair in bridge_spans else "road  "
        head = f"{'#' + str(rank) + ' ' if rank else ''}{kind}  {ename(d)}"
        print(f"\n{head}")
        print(f"    length {float(d.get('length', 0) or 0):.1f} m   "
              f"({a['y']:.5f},{a['x']:.5f}) -> ({b['y']:.5f},{b['x']:.5f})")
        print(f"    highway={d.get('highway')}  segments removed={len(es)}")
        for s, b0, b1 in sorted(hits, key=lambda h: (h[2] is not None, -(h[0]["pop"] or 0))):
            pop = f"pop {s['pop']}" if s["pop"] else "pop unknown"
            if b1 is None:
                print(f"      CUT OFF   {s['name'][:30]:<30} {pop:<14} "
                      f"{b0/1000:.2f} km -> no route to any facility")
            else:
                print(f"      +{(b1-b0)/1000:6.2f} km {s['name'][:30]:<30} {pop:<14} "
                      f"{b0/1000:.2f} -> {b1/1000:.2f} km")

    # ---- bridges only
    print(f"\n\nBRIDGE SPANS: cut-off, or more than {DETOUR_THRESHOLD_M/1000:.0f} km added\n")
    bridge_hits = []
    for pair in bridge_spans:
        hits = evaluate(pair)
        if hits:
            bridge_hits.append((severity(hits), pair, hits))
    bridge_hits.sort(key=lambda t: t[0], reverse=True)
    print(f"critical bridges: {len(bridge_hits)} of {len(bridge_spans)}")
    if not bridge_hits:
        print("NONE.")
    for i, (_, pair, hits) in enumerate(bridge_hits, 1):
        describe(pair, hits, i)

    # ---- every span, bridge or not
    hr(f"7. ALL EDGES SWEEP  (is any ordinary road more critical than the bridges?)")
    total = len(spans_all)
    print(f"testing all {total} undirected spans, one shortest-path recompute each ...")
    t0 = time.time()
    all_hits = []
    for i, pair in enumerate(spans_all, 1):
        if i % 1000 == 0:
            print(f"  ... {i}/{total}  ({time.time() - t0:.0f}s elapsed)", flush=True)
        hits = evaluate(pair)
        if hits:
            all_hits.append((severity(hits), pair, hits))
    print(f"done in {time.time() - t0:.0f}s")
    all_hits.sort(key=lambda t: t[0], reverse=True)

    n_cut = sum(1 for sev, _, _ in all_hits if sev[1] > 0)
    n_br = sum(1 for _, pair, _ in all_hits if pair in bridge_spans)
    print(f"\nspans with any impact: {len(all_hits)}   "
          f"(of which bridges: {n_br}, ordinary road: {len(all_hits) - n_br})")
    print(f"spans that fully disconnect at least one settlement: {n_cut}")
    print(f"\nTOP {TOP_N_EDGES} MOST CRITICAL EDGES OVERALL")
    print("ranked by: people cut off > settlements cut off > people detoured > total detour")
    for i, (_, pair, hits) in enumerate(all_hits[:TOP_N_EDGES], 1):
        describe(pair, hits, i)

    # ---- the demo pick
    hr("DEMO PICK")
    cut_only = [(sev, p, h) for sev, p, h in all_hits if sev[1] > 0]
    if not cut_only:
        print("No single edge fully disconnects a settlement. Strongest available case is a")
        print("detour, not a cut-off -- see #1 above.")
    else:
        sev, pair, hits = cut_only[0]
        es = spans_all[pair]
        d = max((e[3] for e in es), key=lambda dd: float(dd.get("length", 0) or 0))
        u, v = pair
        a, b = G.nodes[u], G.nodes[v]
        cut = [h for h in hits if h[2] is None]
        s0 = max(cut, key=lambda h: h[0]["pop"] or 0)[0]
        near = min(hospitals, key=lambda h: (h["lat"] - s0["lat"]) ** 2
                   + ((h["lon"] - s0["lon"]) * _coslat) ** 2)
        print(f"settlement : {s0['name']}  ({s0['type']}, "
              f"pop {s0['pop'] if s0['pop'] else 'unknown'})  "
              f"{s0['lat']:.5f}, {s0['lon']:.5f}")
        print(f"cluster    : {len(s0['cluster'])} graph nodes within {CLUSTER_RADIUS_M:.0f} m")
        print(f"edge       : {'BRIDGE' if pair in bridge_spans else 'road'}  {ename(d)}  "
              f"{float(d.get('length', 0) or 0):.1f} m  highway={d.get('highway')}")
        print(f"             ({a['y']:.5f},{a['x']:.5f}) -> ({b['y']:.5f},{b['x']:.5f})")
        print(f"baseline   : {s0['base']/1000:.2f} km to care")
        print(f"after cut  : no route to ANY of the {len(hospitals)} facilities")
        print(f"nearest facility by straight line: {near['name']} "
              f"({near['lat']:.5f}, {near['lon']:.5f})")
        others = [h[0]["name"] for h in cut if h[0] is not s0]
        if others:
            print(f"also cut off: {', '.join(others)}")

hr("DONE")
