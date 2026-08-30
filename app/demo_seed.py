"""Wipe the DB and load the 11-photo live-demo set.

Real photos (Wikimedia Commons, freely licensed -- see
app/data/demo_images/CREDITS.md) pinned to real coordinates in the
Surathkal-Mulki area: 6 impassable roads/bridges, 3 clear roads, 2 damaged
buildings. Two of the impassable spans are cut-offs the severance engine
confirms, so the dashboard shows a blocked area straight away; the 3 clear
roads keep the map from being a wall of red.

    .venv/Scripts/python.exe -m app.demo_seed

The detected `state` for each photo is pinned here for demo determinism, and
the same verdict is written into the detect cache (keyed by image bytes) so a
live re-upload of the same photo during the demo returns the same answer with
no network call. Drop the `state`/`confidence` fields and let
detect.detect() classify live if you want the model in the loop.
"""
import datetime
import hashlib
import json
import os
import shutil

from app import binding, config, detect, pipeline, store

SRC = os.path.join(config.DATA, "demo_images")

# image, asset, detected state, confidence, (lat, lon), gps_accuracy_m, note
DEMO = [
    ("road-1-collapse.jpg",     "road",     "impassable", 0.95,
     (13.017102, 74.795375),  8.0, "rank-1 severance -- residential road, ~682 people cut off from care"),
    ("road-2-washout.jpg",      "road",     "impassable", 0.88,
     (13.051512, 74.782116), 12.0, "washout near Sasihitlu -- road has alternatives, low priority"),
    ("road-3-flood.jpg",        "road",     "impassable", 0.83,
     (13.044559, 74.795745), 15.0, "flood damage near Haleyangadi"),
    ("bridge-1-washout.jpg",    "road",     "impassable", 0.93,
     (13.012206, 74.807704),  8.0, "Madhya Padavu bridge severance -- the one bridge-tagged cut-off in the bbox"),
    ("bridge-2-arch.jpg",       "road",     "impassable", 0.90,
     (13.056269, 74.797046), 10.0, "NH66 bridge -- trunk road, big detour if closed"),
    ("bridge-3-footbridge.jpg", "road",     "impassable", 0.86,
     (13.087183, 74.813095), 10.0, "Shimanthoor Temple Road bridge"),
    ("building-1-collapse.jpg", "building", "damaged",    0.91,
     (13.063710, 74.847974), 12.0, "collapsed building in Kinnigoli town"),
    ("building-2-collapse.jpg", "building", "damaged",    0.88,
     (13.090409, 74.787047), 12.0, "collapsed building in Mulki town"),

    # "Citizen checked, road is clear" -- photo of an intact road, model agrees.
    ("road-clear-1.jpg", "road", "passable", 0.92, (13.001500, 74.810000), 10.0, "clear near Krishnapura"),
    ("road-clear-2.jpg", "road", "passable", 0.90, (13.035025, 74.794077), 10.0, "clear near Pavanje"),
    ("road-clear-3.jpg", "road", "passable", 0.89, (13.067686, 74.818799), 10.0, "clear near Punaroor"),
]


def _install_image(name):
    """Copy a bundled demo photo into uploads/ under a fresh name; also pin its
    detection verdict in the cache so a live re-upload matches."""
    src = os.path.join(SRC, name)
    with open(src, "rb") as fh:
        raw = fh.read()
    os.makedirs(config.UPLOADS, exist_ok=True)
    dst_name = f"demo-{name}"
    shutil.copyfile(src, os.path.join(config.UPLOADS, dst_name))
    return f"/uploads/{dst_name}", hashlib.sha1(raw).hexdigest()


def _pin_detection(digest, asset_type, state, confidence):
    os.makedirs(config.DETECT_CACHE, exist_ok=True)
    path = os.path.join(config.DETECT_CACHE, f"{digest}.json")
    with open(path, "w") as fh:
        json.dump({"state": state, "confidence": confidence,
                   "reason": "pinned for the live demo"}, fh)


def build():
    now = datetime.datetime.now(datetime.timezone.utc)
    for i, (img, asset, state, conf, (lat, lon), acc, _note) in enumerate(DEMO):
        image_path, digest = _install_image(img)
        _pin_detection(digest, asset, state, conf)

        edge, candidates = (None, [])
        if asset == "road":
            edge, candidates = binding.bind(lat, lon, acc, None)

        store.insert(
            image_path=image_path,
            created_at=(now - datetime.timedelta(minutes=13 * (len(DEMO) - i))).isoformat(),
            lat=lat, lon=lon, gps_accuracy_m=acc, asset_type=asset,
            state=state, confidence=conf, detection_mode="api",
            edge_id=edge, n_reports=1, status="pending",
            priority_score=None, priority_reason=None,
            bearing=None, bind_candidates=candidates)
    return len(DEMO)


def main():
    store.reset()
    count = build()
    result = pipeline.recompute()
    print(f"seeded {count} demo reports into {config.DB}")
    print(f"blocked edges: {len(result['blocked'])}   "
          f"severed pockets: {len(result['pockets'])}   "
          f"detour regions: {len(result['detours'])}")
    for p in result["pockets"]:
        print(f"  blocked area: ~{p['population']} people, lost {p['lost_facility']}, "
              f"via {[e['edge_id'] for e in p['severing_edges']]}")
    print("\nqueue:")
    for r in store.all_reports():
        s = "   --  " if r["priority_score"] is None else f"{r['priority_score']:>8.1f}"
        print(f"  {s}  [{r['asset_type']:<8} {r['state']:<10}] edge={r['edge_id']}  {r['priority_reason']}")


if __name__ == "__main__":
    main()
