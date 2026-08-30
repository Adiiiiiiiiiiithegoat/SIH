"""Does measuring to real geometry instead of the chord actually bind better?

"Before" is simulated by swapping binding._span_distance back to the old
endpoint-to-endpoint calculation, so both runs go through the identical
control flow in candidates() and only the distance metric differs.

    .venv/Scripts/python.exe verify_binding.py
"""
import contextlib

from app import binding, config
from app.network import edge_id, net

n = net()
ACCURACY_M = 8.0
RADIUS = min(config.MAX_BIND_RADIUS_M, max(config.MIN_BIND_RADIUS_M, ACCURACY_M))


def chord_distance(nw, eid, u, v, plat, plon):
    """The OLD behaviour: straight line between the span's two endpoints."""
    (ax, ay), (bx, by) = nw.pos[int(u)], nw.pos[int(v)]
    return binding._point_segment_m(plat, plon, ay, ax, by, bx, nw.coslat)


@contextlib.contextmanager
def old_binder():
    live = binding._span_distance
    binding._span_distance = chord_distance
    try:
        yield
    finally:
        binding._span_distance = live


def probe(lat, lon, want_eid):
    """(distance to the intended span, sole candidate?, fallback fired?, chosen)."""
    cands = binding.candidates(lat, lon, ACCURACY_M)
    chosen = cands[0]["edge_id"] if cands else None
    mine = next((c for c in cands if c["edge_id"] == want_eid), None)
    # candidates() recurses to MAX_BIND_RADIUS_M only when nothing sat inside
    # the accuracy radius, so a best distance beyond RADIUS means it widened.
    fallback = bool(cands) and cands[0]["distance_m"] > RADIUS
    return (mine["distance_m"] if mine else None, len(cands) == 1, fallback,
            chosen, len(cands))


print("=" * 78)
print("EDGE BINDER: chord distance vs real polyline geometry")
print("=" * 78)
print(f"GPS accuracy {ACCURACY_M:.0f} m -> search radius {RADIUS:.0f} m   "
      f"(MAX_BIND_RADIUS_M fallback {config.MAX_BIND_RADIUS_M:.0f} m)")

# --- the two demo photo points -------------------------------------------
DEMO = [
    ("rank #1 severance", 13.017102, 74.795375, "5269747322-9647824661"),
    ("Madhya Padavu bridge", 13.012206, 74.807704, "9620418684-9620418686"),
]
print("\n" + "-" * 78)
print("THE TWO DEMO PHOTO POINTS")
print("-" * 78)
for label, lat, lon, want in DEMO:
    pts = len(binding._polyline(want))
    print(f"\n{label}   {lat:.6f}, {lon:.6f}")
    print(f"  intended span {want}  ({pts}-point geometry)")
    with old_binder():
        od, osole, ofb, ochosen, on = probe(lat, lon, want)
    nd, nsole, nfb, nchosen, nn = probe(lat, lon, want)
    print(f"  {'':<8} {'dist_m':>8} {'binds':>6} {'sole':>6} {'fallback':>9} {'cands':>6}")
    print(f"  {'BEFORE':<8} {od if od is None else f'{od:8.2f}':>8} "
          f"{'YES' if ochosen == want else 'NO':>6} {'yes' if osole else 'no':>6} "
          f"{'YES' if ofb else 'no':>9} {on:>6}")
    print(f"  {'AFTER':<8} {nd if nd is None else f'{nd:8.2f}':>8} "
          f"{'YES' if nchosen == want else 'NO':>6} {'yes' if nsole else 'no':>6} "
          f"{'YES' if nfb else 'no':>9} {nn:>6}")
    if ochosen != want:
        print(f"  BEFORE bound to the WRONG span: {ochosen}")

# --- broader sample -------------------------------------------------------
print("\n" + "-" * 78)
print("SAMPLE OF 200 POINTS ON REAL SPAN GEOMETRY")
print("-" * 78)
print("each point is an interior vertex of a span's OSM polyline -- a place a")
print("citizen could physically stand. endpoints are skipped: at a junction the")
print("'correct' span is genuinely ambiguous and would muddy the measurement.\n")

eids = sorted(n.spans)
step = max(1, len(eids) // 600)
samples = []
for eid in eids[::step]:
    pts = binding._polyline(eid)
    if len(pts) < 3:            # need an interior vertex
        continue
    lat, lon = pts[len(pts) // 2]
    samples.append((eid, lat, lon, len(pts)))
    if len(samples) >= 200:
        break

print(f"sampled {len(samples)} spans "
      f"(of {sum(1 for e in eids if len(binding._polyline(e)) >= 3)} with curved geometry, "
      f"{len(eids)} total)")

rows = {}
for mode, ctx in (("BEFORE", old_binder), ("AFTER", contextlib.nullcontext)):
    correct = fallback = missed = 0
    dists = []
    with ctx():
        for eid, lat, lon, _ in samples:
            d, _sole, fb, chosen, _k = probe(lat, lon, eid)
            if chosen == eid:
                correct += 1
            if fb:
                fallback += 1
            if d is None:
                missed += 1
            else:
                dists.append(d)
    dists.sort()
    rows[mode] = {
        "correct": correct, "fallback": fallback, "missed": missed,
        "median": dists[len(dists) // 2] if dists else float("nan"),
        "p90": dists[int(len(dists) * 0.9)] if dists else float("nan"),
        "max": dists[-1] if dists else float("nan"),
    }

tot = len(samples)
print(f"\n{'':<8} {'correct span':>14} {'fallback fired':>16} "
      f"{'not a candidate':>17} {'median_m':>9} {'p90_m':>8} {'max_m':>9}")
for mode in ("BEFORE", "AFTER"):
    r = rows[mode]
    print(f"{mode:<8} {r['correct']:>6}/{tot} {r['correct']/tot*100:>5.1f}% "
          f"{r['fallback']:>8}/{tot} {r['fallback']/tot*100:>5.1f}% "
          f"{r['missed']:>9}/{tot} {r['median']:>9.2f} {r['p90']:>8.2f} {r['max']:>9.2f}")

b, a = rows["BEFORE"], rows["AFTER"]
print(f"\ncorrect-span binding : {b['correct']/tot*100:.1f}% -> {a['correct']/tot*100:.1f}%"
      f"   ({a['correct'] - b['correct']:+d} points)")
print(f"fallback to {config.MAX_BIND_RADIUS_M:.0f} m    : {b['fallback']/tot*100:.1f}% -> "
      f"{a['fallback']/tot*100:.1f}%   ({a['fallback'] - b['fallback']:+d} points)")
print(f"median measured dist : {b['median']:.2f} m -> {a['median']:.2f} m")
print("=" * 78)
