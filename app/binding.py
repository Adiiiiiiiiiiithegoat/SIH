"""Bind a report to the road segment it is actually about.

GPS in a phone photo is good to roughly 10 m and often worse, so the nearest
edge is frequently the wrong edge -- a parallel service road, or the cross
street the photographer was standing on. Instead: take every edge whose
geometry passes within the report's own stated accuracy, score them, and keep
the whole shortlist so an operator can override the choice.

Scoring, highest wins:

    score = (1 - d / radius) * (1 + BEARING_WEIGHT * perpendicularity)

`d` is the distance from the report to the span's real polyline
geometry, not to a straight line between its endpoints. The bearing
term applies only when EXIF gives a compass direction: a photographer facing a
blocked road stands across it, so the edge running roughly perpendicular to the
camera is the one being photographed. A parallel edge gets no bonus.
"""
import functools
import math

from app import config
from app.network import M_PER_DEG, edge_id, net


def _point_segment_m(plat, plon, alat, alon, blat, blon, coslat):
    """Distance in metres from P to segment AB, in local flat-earth metres."""
    px, py = (plon - alon) * coslat, plat - alat
    bx, by = (blon - alon) * coslat, blat - alat
    denom = bx * bx + by * by
    t = 0.0 if denom == 0 else max(0.0, min(1.0, (px * bx + py * by) / denom))
    dx, dy = px - t * bx, py - t * by
    return math.hypot(dx, dy) * M_PER_DEG


@functools.lru_cache(maxsize=None)
def _polyline(eid):
    """The span's real geometry as ((lat, lon), ...), cached.

    Cached because candidates() asks for dozens of spans per report and
    span_coords rebuilds the list each call.
    """
    return tuple((float(a), float(b)) for a, b in net().span_coords(eid))


def _span_distance(n, eid, u, v, plat, plon):
    """Distance from a report to a span's ACTUAL geometry, not its chord.

    Measuring to the straight endpoint chord is wrong wherever a road bends:
    a point standing on the carriageway of the 507 m Madhya Padavu span sits
    34.4 m from that span's chord, which is outside the radius a good GPS fix
    produces. Reports then mis-bind to a straighter neighbour, or bind only
    because candidates() widens to MAX_BIND_RADIUS_M -- which throws away the
    accuracy radius the whole design rests on.

    A two-point geometry is a straight line, so this reduces exactly to the
    previous endpoint-to-endpoint calculation.
    """
    pts = _polyline(eid)
    if len(pts) < 2:
        (ax, ay), (bx, by) = n.pos[int(u)], n.pos[int(v)]
        return _point_segment_m(plat, plon, ay, ax, by, bx, n.coslat)
    return min(_point_segment_m(plat, plon, pts[i][0], pts[i][1],
                                pts[i + 1][0], pts[i + 1][1], n.coslat)
               for i in range(len(pts) - 1))


def _span_bearing(n, eid):
    """Compass bearing of the span, 0-180 (undirected, so a span and its
    reverse are the same line)."""
    u, v = (int(x) for x in eid.split("-"))
    (ux, uy), (vx, vy) = n.pos[u], n.pos[v]
    return math.degrees(math.atan2((vx - ux) * n.coslat, vy - uy)) % 180.0


def candidates(lat, lon, gps_accuracy_m=None, bearing=None):
    """Ranked edge candidates for a report at (lat, lon).

    Returns a list of dicts (best first), each with edge_id, distance_m, score
    and bearing_delta. Empty only if there is no road at all within
    MAX_BIND_RADIUS_M -- e.g. a report dropped in the sea.
    """
    n = net()
    acc = config.DEFAULT_GPS_ACCURACY_M if gps_accuracy_m is None else float(gps_accuracy_m)
    radius = min(config.MAX_BIND_RADIUS_M, max(config.MIN_BIND_RADIUS_M, acc))

    # Node-level prefilter: any edge within `radius` has an endpoint within
    # radius + the longest span, but scanning a generous box is cheaper than
    # being clever, and the graph is only 5k nodes.
    box = (radius + 2000.0) / M_PER_DEG
    near = {int(i) for i, (x, y) in n.pos.items()
            if abs(y - lat) < box and abs(x - lon) < box / n.coslat}

    scored = []
    seen = set()
    for u in near:
        for v in n.graph.neighbors(u):
            eid = edge_id(u, v)
            if eid in seen:
                continue
            seen.add(eid)
            d = _span_distance(n, eid, u, v, lat, lon)
            if d > radius:
                continue
            proximity = 1.0 - d / radius            # 1 at the point, 0 at the rim
            perp = 0.0
            delta = None
            if bearing is not None:
                delta = abs(((bearing - _span_bearing(n, eid)) + 90) % 180 - 90)
                perp = abs(delta) / 90.0            # 1.0 when exactly perpendicular
            scored.append({"edge_id": eid, "distance_m": round(d, 2),
                           "bearing_delta": None if delta is None else round(delta, 1),
                           "score": round(proximity * (1 + config.BEARING_WEIGHT * perp), 4)})

    if not scored:
        # Nothing inside the accuracy circle. Widen once to the hard cap rather
        # than returning an unbound report -- an operator can still override.
        if radius < config.MAX_BIND_RADIUS_M:
            return candidates(lat, lon, config.MAX_BIND_RADIUS_M, bearing)
        return []

    scored.sort(key=lambda c: (-c["score"], c["distance_m"]))
    return scored[:config.MAX_CANDIDATES]


def bind(lat, lon, gps_accuracy_m=None, bearing=None):
    """(chosen_edge_id or None, candidate list). The list is stored on the
    report so an operator can pick a different edge later."""
    cands = candidates(lat, lon, gps_accuracy_m, bearing)
    return (cands[0]["edge_id"] if cands else None), cands
