"""The road network, loaded once and cached: geometry, facilities, baselines.

Everything here is derived from files under app/data/ -- no network access, no
imports from outside app/. Loading is deferred to first use so importing the
module (e.g. in tests) stays cheap.

Coordinate convention matches the frontend: (lat, lon) pairs, lat first.
Edge ids are the stringified sorted node pair, e.g. "245616376-245617692",
so they are stable across restarts and safe to put in a URL.
"""
import json
import math
import threading

import networkx as nx
import numpy as np
from shapely.geometry import MultiPoint

from app import config

M_PER_DEG = 111_320.0
_lock = threading.Lock()
_net = None


def edge_id(u, v):
    a, b = sorted((int(u), int(v)))
    return f"{a}-{b}"


def parse_edge_id(eid):
    a, b = eid.split("-")
    return int(a), int(b)


class Network:
    """Immutable view of the road graph plus the reference layers on top of it."""

    def __init__(self):
        import osmnx as ox
        G = ox.load_graphml(config.GRAPHML)
        self.G = G
        self.graph = nx.Graph(ox.convert.to_undirected(G))

        self.pos = {int(n): (float(G.nodes[n]["x"]), float(G.nodes[n]["y"]))
                    for n in G.nodes}                       # node -> (lon, lat)
        self._ids = np.fromiter(self.pos, dtype=np.int64, count=len(self.pos))
        self._xy = np.array([self.pos[int(n)] for n in self._ids])
        self.coslat = float(math.cos(math.radians(self._xy[:, 1].mean())))

        # One undirected span may back several directed OSM segments; keep the
        # longest-named one for display and remember them all for removal.
        self.spans = {}
        for u, v, k, d in G.edges(keys=True, data=True):
            self.spans.setdefault(edge_id(u, v), []).append((int(u), int(v), k, d))

        ref = json.load(open(config.REFERENCE_JSON, encoding="utf-8"))
        self.facilities = ref["facilities"]
        self.places = [p for p in ref["places"] if p["named"]]
        for f in self.facilities:
            f["node"] = self.nearest_node(f["lat"], f["lon"])
        self.fac_nodes = {f["node"] for f in self.facilities}
        self.fac_name = {}
        for f in self.facilities:
            self.fac_name.setdefault(f["node"], f["name"])

        # Baseline distance to the nearest facility, per node. Computed once on
        # the reversed graph (distance TO care, not FROM it) and reused by every
        # severance query -- this is the cache that keeps the query under 500 ms.
        self.R = G.reverse(copy=True)
        self.base_map = nx.multi_source_dijkstra_path_length(
            self.R, self.fac_nodes, weight="length")
        # Per-facility maps let us name the facility a pocket loses.
        self.per_fac = {n: nx.single_source_dijkstra_path_length(
            self.R, n, weight="length") for n in self.fac_nodes}

        self._tol_lat = config.EDGE_TOL_M / M_PER_DEG
        self._tol_lon = self._tol_lat / self.coslat

    # --- geometry ---------------------------------------------------------
    def metres(self, lat1, lon1, lat2, lon2):
        return math.hypot(lat1 - lat2, (lon1 - lon2) * self.coslat) * M_PER_DEG

    def nearest_node(self, lat, lon):
        dx = (self._xy[:, 0] - lon) * self.coslat
        dy = self._xy[:, 1] - lat
        return int(self._ids[int((dx * dx + dy * dy).argmin())])

    def hull(self, nodes):
        """(raw convex hull, polygon safe for population, was_widened).

        A 1- or 2-node pocket hulls to a point or line with zero area, which
        would read as zero people. Those are buffered for the population lookup
        only; the reported area stays that of the raw hull.
        """
        h = MultiPoint([self.pos[int(n)] for n in nodes]).convex_hull
        if h.area > 0:
            return h, h, False
        return h, h.buffer(config.POCKET_BUFFER_M / M_PER_DEG), True

    def hull_area_km2(self, h):
        return h.area * (M_PER_DEG / 1000.0) ** 2 * self.coslat if h.area else 0.0

    def polygon_latlon(self, h):
        """Hull as [[lat, lon], ...] for the map. Degenerate hulls come back as
        their own points -- the frontend draws whatever it is given."""
        if h.geom_type == "Polygon":
            return [[round(y, 6), round(x, 6)] for x, y in h.exterior.coords]
        if h.geom_type == "LineString":
            return [[round(y, 6), round(x, 6)] for x, y in h.coords]
        return [[round(h.y, 6), round(h.x, 6)]]

    # --- labels -----------------------------------------------------------
    def touches_download_edge(self, nodes):
        """True if the component reaches the buffered download boundary.

        osmnx truncates ways at the bbox, so such a component is a road that
        continues outside the download, not a genuinely severed pocket.
        """
        w, s, e, n = config.BUFFERED_BBOX
        for node in nodes:
            x, y = self.pos[int(node)]
            if (abs(x - w) < self._tol_lon or abs(x - e) < self._tol_lon
                    or abs(y - s) < self._tol_lat or abs(y - n) < self._tol_lat):
                return True
        return False

    def nearest_place(self, lat, lon):
        """Nearest named settlement -- a LABEL only. It is not necessarily
        inside the pocket and never decides whether severance occurred."""
        if not self.places:
            return None, None
        p = min(self.places, key=lambda q: self.metres(lat, lon, q["lat"], q["lon"]))
        return p["name"], self.metres(lat, lon, p["lat"], p["lon"])

    def best_facility(self, nodes):
        """(name, distance_m) of the closest facility reachable from a node set
        before any cut. None if nothing was reachable to begin with."""
        best = None
        for fnode, dmap in self.per_fac.items():
            for n in nodes:
                d = dmap.get(n)
                if d is not None and (best is None or d < best[1]):
                    best = (self.fac_name[fnode], d)
        return best

    def span_name(self, eid):
        segs = self.spans.get(eid)
        if not segs:
            return "<unknown>"
        d = max((s[3] for s in segs), key=lambda dd: float(dd.get("length", 0) or 0))
        nm = d.get("name") or d.get("ref") or "<unnamed>"
        return ", ".join(str(x) for x in nm) if isinstance(nm, list) else str(nm)

    def span_highway(self, eid):
        """The span's OSM highway class, raw ('trunk', 'residential', ...).

        Same longest-segment rule as span_name. A way tagged with several
        classes comes back as a list from osmnx; keep the first, which is the
        primary classification.
        """
        segs = self.spans.get(eid)
        if not segs:
            return None
        d = max((s[3] for s in segs), key=lambda dd: float(dd.get("length", 0) or 0))
        hw = d.get("highway")
        if isinstance(hw, list):
            hw = hw[0] if hw else None
        return str(hw) if hw else None

    def span_length(self, eid):
        segs = self.spans.get(eid, [])
        return max((float(s[3].get("length", 0) or 0) for s in segs), default=0.0)

    def span_coords(self, eid):
        """The span's real geometry as [[lat, lon], ...], following the OSM
        way where one is stored rather than drawing a straight line."""
        segs = self.spans.get(eid, [])
        if not segs:
            return []
        u, v, _, d = max(segs, key=lambda s: float(s[3].get("length", 0) or 0))
        geom = d.get("geometry")
        if geom is not None and hasattr(geom, "coords"):
            return [[round(y, 6), round(x, 6)] for x, y in geom.coords]
        return [[round(self.pos[u][1], 6), round(self.pos[u][0], 6)],
                [round(self.pos[v][1], 6), round(self.pos[v][0], 6)]]


def net():
    """The one Network instance. Built on first call (~4 s), reused after."""
    global _net
    if _net is None:
        with _lock:
            if _net is None:
                _net = Network()
    return _net


def in_report_bbox(lat, lon):
    w, s, e, n = config.REPORT_BBOX
    return w <= lon <= e and s <= lat <= n
