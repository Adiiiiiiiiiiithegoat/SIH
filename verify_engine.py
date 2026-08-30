"""Does the runtime engine (app/severance.py) agree with the offline sweep?

The sweep in severance.py removes all ~6095 spans and is the reference. The
engine answers one blockage set at a time and is what the backend serves. If
they disagree on a known case that is an extraction bug, not a threshold to
retune -- so this script reports the mismatch and exits non-zero.

Run:  .venv/Scripts/python.exe verify_engine.py
"""
import time

from app import config, priority, severance
from app.network import edge_id, net, in_report_bbox

# Reference values, taken from the sweep. Do not edit these to make a test pass.
CASES = [
    {"label": "507 m span (Madhya Padavu)",
     "a": (13.01087, 74.81183), "b": (13.01229, 74.80747),
     "kind": "SEVERANCE", "nodes": 5, "filters": 4},
    {"label": "NH66 bridge (Hejamady)",
     "a": (13.09610, 74.78753), "b": (13.10042, 74.78831),
     "kind": "DETOUR", "nodes": 441, "added_km": 3.91},
]

# The sweep classified a detour at >3 km. app/config ships 500 m for the live
# dashboard, which is a different question -- compare like for like.
SWEEP_DETOUR_THRESHOLD_M = 3000.0

n = net()
failures = []


def resolve(case):
    u = n.nearest_node(*case["a"])
    v = n.nearest_node(*case["b"])
    return edge_id(u, v), u, v


print("=" * 78)
print("ENGINE vs SWEEP")
print("=" * 78)
print(f"engine   : app/severance.py  (backend runtime path)")
print(f"reference: severance.py      (offline all-edge sweep)")
print(f"facilities loaded: {len(n.facilities)}   graph nodes: {n.graph.number_of_nodes()}")
print(f"filters  : >={config.MIN_POCKET_NODES} nodes, >={config.MIN_HULL_KM2} km2, "
      f"prior <{config.MAX_PRIOR_ACCESS_M/1000:.0f} km, endpoint degree "
      f">={config.MIN_ENDPOINT_DEGREE}")

# --- case 1: severance ----------------------------------------------------
c = CASES[0]
eid, u, v = resolve(c)
print(f"\n--- {c['label']}")
print(f"    {c['a']} -> {c['b']}  resolves to nodes {u} -> {v}  edge_id {eid}")
res = severance.analyse([eid])
pockets = res["pockets"]
print(f"    pockets returned: {len(pockets)}   query {res['elapsed_ms']} ms")
if not pockets:
    failures.append(f"{c['label']}: engine found NO pocket, sweep found one")
else:
    p = pockets[0]
    checks = p["checks"]
    print(f"    nodes        : {p['n_nodes']}   (sweep: {c['nodes']})")
    print(f"    node ids     : {p['nodes']}")
    print(f"    hull area    : {p['hull_area_km2']} km2")
    print(f"    centroid     : {p['centroid']}")
    print(f"    population   : {p['population']}  [{p['population_source']}, "
          f"{p['population_method']}, coverage={p['population_coverage']}]")
    print(f"    lost facility: {p['lost_facility']}")
    print(f"    prior access : {p['prior_access_km']} km")
    print(f"    nearest place: {p['nearest_place']} at {p['nearest_place_km']} km"
          f"   (LABEL ONLY)")
    print(f"    filters      : " + "  ".join(
        f"{k}={'PASS' if val else 'FAIL'}" for k, val in checks.items()))
    if p["n_nodes"] != c["nodes"]:
        failures.append(f"{c['label']}: engine {p['n_nodes']} nodes, sweep {c['nodes']}")
    if not all(checks.values()):
        failures.append(f"{c['label']}: not all four filters passed: {checks}")
    elif len(checks) != c["filters"]:
        failures.append(f"{c['label']}: expected {c['filters']} filters, got {len(checks)}")

# --- case 2: detour -------------------------------------------------------
c = CASES[1]
eid, u, v = resolve(c)
print(f"\n--- {c['label']}")
print(f"    {c['a']} -> {c['b']}  resolves to nodes {u} -> {v}  edge_id {eid}")
print(f"    name={n.span_name(eid)}  length={n.span_length(eid):.1f} m")

live = config.DETOUR_THRESHOLD_M
config.DETOUR_THRESHOLD_M = SWEEP_DETOUR_THRESHOLD_M
try:
    res = severance.analyse([eid])
finally:
    config.DETOUR_THRESHOLD_M = live

det = res["detours"]
total_nodes = sum(d["n_nodes"] for d in det)
worst = max((d["added_km"] for d in det), default=0.0)
print(f"    at the sweep's {SWEEP_DETOUR_THRESHOLD_M/1000:.0f} km threshold:")
print(f"    detour clusters : {len(det)}")
print(f"    nodes affected  : {total_nodes}   (sweep: {c['nodes']})")
print(f"    worst added     : {worst:.2f} km   (sweep: {c['added_km']:.2f} km)")
print(f"    pockets severed : {len(res['pockets'])}   (sweep: 0)")
print(f"    query           : {res['elapsed_ms']} ms")
for d in det:
    print(f"      cluster {d['n_nodes']:>4} nodes  +{d['added_km']:.2f} km  "
          f"pop {d['population']}  near {d['nearest_place']} "
          f"{d['nearest_place_km']} km (LABEL ONLY)")
if total_nodes != c["nodes"]:
    failures.append(f"{c['label']}: engine {total_nodes} nodes, sweep {c['nodes']}")
if abs(worst - c["added_km"]) > 0.01:
    failures.append(f"{c['label']}: engine +{worst:.2f} km, sweep +{c['added_km']:.2f} km")
if res["pockets"]:
    failures.append(f"{c['label']}: engine severed a pocket, sweep severed none")

print(f"\n    at the shipped {live:.0f} m threshold (what the dashboard uses):")
res_live = severance.analyse([eid])
print(f"    clusters {len(res_live['detours'])}, nodes "
      f"{sum(d['n_nodes'] for d in res_live['detours'])}, "
      f"worst +{max((d['added_km'] for d in res_live['detours']), default=0):.2f} km")

# --- timing ---------------------------------------------------------------
print("\n" + "=" * 78)
print("TIMING  (target: under 500 ms for a handful of blocked edges)")
print("=" * 78)
blocked = [resolve(CASES[0])[0], resolve(CASES[1])[0]]
extra = [e for e in list(n.spans)[:3] if e not in blocked]
blocked += extra
severance.analyse(blocked)                       # warm
runs = []
for _ in range(5):
    t = time.perf_counter()
    r = severance.analyse(blocked)
    runs.append((time.perf_counter() - t) * 1000)
print(f"blocked edges: {len(blocked)}  ->  {blocked}")
print(f"pockets {len(r['pockets'])}, detour clusters {len(r['detours'])}")
print(f"runs (ms): " + ", ".join(f"{x:.1f}" for x in runs))
print(f"median {sorted(runs)[len(runs)//2]:.1f} ms   max {max(runs):.1f} ms   "
      f"-> {'PASS' if max(runs) < 500 else 'FAIL'} (<500 ms)")
if max(runs) >= 500:
    failures.append(f"timing: {max(runs):.1f} ms exceeds the 500 ms target")

sev_only = []
for _ in range(5):
    t = time.perf_counter()
    severance.analyse(blocked, detours=False)
    sev_only.append((time.perf_counter() - t) * 1000)
print(f"severance only (detours=False): median {sorted(sev_only)[2]:.1f} ms")

# --- scoring sanity -------------------------------------------------------
print("\n" + "=" * 78)
print("SCORING  (single formula, no category gate)")
print("=" * 78)
print("priority = belief * (population ** POPULATION_EXPONENT) * delay_minutes")
print(f"  POPULATION_EXPONENT={config.POPULATION_EXPONENT}  "
      f"DISCONNECT_PENALTY={config.DISCONNECT_PENALTY}  "
      f"ASSUMED_SPEED_KMH={config.ASSUMED_SPEED_KMH}  "
      f"UNKNOWN_BELIEF={config.UNKNOWN_BELIEF}")
print(f"  delay(severed)      = {priority.delay_minutes(True):.1f} min")
print(f"  delay(+3.91 km)     = {priority.delay_minutes(False, 3.91):.2f} min")
print("app/priority.py applies one formula to both kinds; there is no branch that")
print("ranks a severance above a detour beyond the delay_minutes difference.")

print("\n" + "=" * 78)
if failures:
    print(f"MISMATCHES: {len(failures)}  -- extraction bug, do NOT retune expectations")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("ENGINE AGREES WITH THE SWEEP ON BOTH KNOWN CASES")
print("=" * 78)
