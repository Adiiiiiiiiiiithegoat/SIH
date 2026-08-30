"""Seed 25 varied reports across the report bbox so the dashboard has content.

Deliberately mixed: real severing spans, ordinary roads, buildings, all three
states, both detection modes, corroborated edges, and one report with no
GPS accuracy at all. That spread is what makes the queue ordering visible during
development.

    python -m app.seed          # wipes reports and reseeds
"""
import datetime
import random

from app import binding, config, pipeline, store

# Spans the all-edge sweep flagged as genuinely severing. Seeding these means
# the dashboard shows real pockets rather than a queue of harmless roads.
SEVERING = [
    ((13.01087, 74.81183), (13.01229, 74.80747)),   # 5-node pocket, Madhya Padavu
    ((13.016616, 74.794199), (13.017250, 74.795746)),  # 24-node pocket near Mukka
]

# Ordinary points spread across the report bbox: 74.78-74.86 E, 13.00-13.10 N.
SPREAD = [
    (13.0224, 74.7900), (13.0350, 74.7941), (13.0446, 74.7957), (13.0515, 74.7821),
    (13.0557, 74.7962), (13.0637, 74.8480), (13.0690, 74.7853), (13.0845, 74.8068),
    (13.0898, 74.8130), (13.0904, 74.7871), (13.0459, 74.8220), (13.0227, 74.8393),
    (13.0194, 74.8442), (13.0019, 74.8560), (13.0001, 74.8207), (13.0110, 74.8161),
    (13.0480, 74.8710), (13.0658, 74.8524), (13.0300, 74.8100), (13.0750, 74.8000),
    (13.0550, 74.8300), (13.0400, 74.8600), (13.0150, 74.8250),
]

ROAD_STATES = ["impassable", "unknown", "passable"]
BUILDING_STATES = ["damaged", "unknown", "not_damaged"]
MODES = ["api", "manual"]


def _midpoint(a, b, jitter, rng):
    """A point roughly on the span, nudged by a plausible GPS error."""
    return (round((a[0] + b[0]) / 2 + rng.uniform(-jitter, jitter), 6),
            round((a[1] + b[1]) / 2 + rng.uniform(-jitter, jitter), 6))


def build():
    rng = random.Random(7)          # fixed: the dev dashboard should be stable
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []

    # 1-6: the two known severing spans, each reported more than once so
    # corroboration and n_reports are visible in the queue.
    for i, (a, b) in enumerate(SEVERING):
        for j in range(3):
            lat, lon = _midpoint(a, b, 0.00012, rng)
            rows.append({"lat": lat, "lon": lon,
                         "gps_accuracy_m": [12.0, 25.0, None][j],
                         "asset_type": "road",
                         "state": "impassable" if j < 2 else "unknown",
                         "confidence": [0.91, 0.84, 0.55][j],
                         "detection_mode": MODES[j % len(MODES)],
                         "bearing": [None, 95.0, None][j]})

    # 7-25: a spread of ordinary reports across the bbox.
    for i, (lat, lon) in enumerate(SPREAD[:19]):
        building = i % 4 == 3
        rows.append({
            "lat": round(lat + rng.uniform(-0.0004, 0.0004), 6),
            "lon": round(lon + rng.uniform(-0.0004, 0.0004), 6),
            "gps_accuracy_m": None if i % 5 == 0 else round(6 + (i % 7) * 3.5, 1),
            "asset_type": "building" if building else "road",
            "state": (BUILDING_STATES if building else ROAD_STATES)[i % 3],
            "confidence": round(0.58 + (i % 9) * 0.042, 2),
            "detection_mode": MODES[i % len(MODES)],
            "bearing": float((i * 47) % 360) if i % 3 == 0 else None,
        })

    for k, row in enumerate(rows):
        edge, candidates = (None, [])
        if row["asset_type"] == "road":
            edge, candidates = binding.bind(row["lat"], row["lon"],
                                            row["gps_accuracy_m"], row["bearing"])
        status = "resolved" if k % 9 == 4 else "rejected" if k % 11 == 7 else "pending"
        store.insert(
            # The frontend's own placeholder art, exactly as the mock used. A
            # seeded report has no real photo, and inventing /uploads/seed-NN.jpg
            # just gave the dashboard 25 broken thumbnails.
            image_path=f"mocks/images/report-{k % 6 + 1}.svg",
            created_at=(now - datetime.timedelta(minutes=17 * (len(rows) - k))).isoformat(),
            edge_id=edge, n_reports=1, status=status, priority_score=None,
            priority_reason=None, bind_candidates=candidates, **row)
    return len(rows)


def main():
    store.reset()
    count = build()
    result = pipeline.recompute()
    reports = store.all_reports()
    print(f"seeded {count} reports into {config.DB}")
    print(f"blocked edges: {len(result['blocked'])}   "
          f"severed pockets: {len(result['pockets'])}   "
          f"detour regions: {len(result['detours'])}   "
          f"severance query: {result['elapsed_ms']} ms")
    print("\ntop of the queue:")
    for r in reports[:8]:
        s = "  --  " if r["priority_score"] is None else f"{r['priority_score']:>7.1f}"
        print(f"  {s}  [{r['status']:<8}] {r['priority_reason']}")


if __name__ == "__main__":
    main()
