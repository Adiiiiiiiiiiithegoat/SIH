"""One-time: freeze OSM health facilities and place labels into app/data/reference.json.

The API must run fully offline, so it never calls Overpass. This reads the
osmnx response cache already in ../cache/ and writes a plain JSON the runtime
loads. Re-run only if the AOI changes and a fresh Overpass response is cached.

    python -m app.extract_reference
"""
import glob
import json
import os

from app import config

W, S, E, N = config.BUFFERED_BBOX


def _centroid(el, nodes):
    if el["type"] == "node":
        return el["lat"], el["lon"]
    pts = [nodes[n] for n in el.get("nodes", []) if n in nodes]
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _harvest(key, wanted):
    """Every element in the cache tagged `key` in `wanted`, inside the buffered bbox."""
    found = {}
    for path in glob.glob(os.path.join(config.ROOT, "cache", "*.json")):
        if os.path.getsize(path) > 1_000_000:
            continue                       # road-network downloads, not POIs
        with open(path, encoding="utf-8") as fh:
            els = json.load(fh).get("elements", [])
        nodes = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}
        for el in els:
            tags = el.get("tags") or {}
            if tags.get(key) not in wanted:
                continue
            c = _centroid(el, nodes)
            if c is None or not (S <= c[0] <= N and W <= c[1] <= E):
                continue
            name = tags.get("name")
            named = isinstance(name, str) and bool(name.strip())
            found[(el["type"], el["id"])] = {
                "name": name.strip() if named else f"<unnamed {el['id']}>",
                "named": named, "type": tags[key],
                "lat": round(c[0], 7), "lon": round(c[1], 7)}
    return sorted(found.values(), key=lambda f: (f["name"], f["lat"]))


def main():
    data = {"facilities": _harvest("amenity", {"hospital", "clinic", "doctors"}),
            "places": _harvest("place", {"village", "town", "hamlet", "suburb"})}
    if not data["facilities"]:
        raise SystemExit("no facilities found in ../cache -- nothing to freeze")
    with open(config.REFERENCE_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    print(f"{config.REFERENCE_JSON}: {len(data['facilities'])} facilities, "
          f"{len(data['places'])} places "
          f"({sum(1 for p in data['places'] if p['named'])} named)")


if __name__ == "__main__":
    main()
