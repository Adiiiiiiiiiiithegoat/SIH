# Contract

Shared reference for frontend and backend. Change only by agreement of both sides.

## Report record

```
id, image_path, lat, lon, gps_accuracy_m, asset_type, state,
confidence, edge_id, n_reports, status, detection_mode,
priority_score, priority_reason, created_at
```

Field values:

- `asset_type`: `road` | `building`
- `state` (road): `passable` | `impassable` | `unknown`
- `state` (building): `damaged` | `not_damaged` | `unknown`
- `detection_mode`: `api` | `manual`
- `status`: `pending` | `in_progress` | `resolved` | `rejected`
  - `pending` — awaiting an operator's assessment.
  - `in_progress` — accepted; crews are working it. Still blocks the network,
    and counts as operator confirmation, which raises the report's priority.
  - `resolved` — the work is done and the road is open again. Stops blocking:
    the pocket it severed reconnects, its span returns to `passable`, and it
    stops corroborating other reports on the same span.
  - `rejected` — the operator says this is not real. Also stops blocking.
  Only `pending` and `in_progress` hold a road closed. The difference between
  `resolved` and `rejected` is history, not effect: a resolved report really
  happened and was fixed, a rejected one never happened.
  Any status may move to any other; the operator decides. Closing is reversible
  — moving a report back to `pending` restores the blockage and the figures.

## Road record

Returned by `GET /api/roads`, one per undirected span in the network.

```
edge_id, coordinates, state, name, highway, bridge, length_m
```

- `edge_id`: `"<node_u>-<node_v>"`, stable for the life of the graph.
- `coordinates`: `[[lat, lon], ...]`, at least two points, following the OSM
  way geometry where one is stored.
- `state`: `passable` | `impassable` | `unknown`. What live reports say, not an
  inspection — an unreported road is `passable`, which means "unreported".
  Impassable beats unknown beats passable when a span has mixed reports.
  Rejected reports do not count.
- `name`: display string; `"<unnamed>"` when the way carries no name or ref,
  `"<unknown>"` when the span is not in the graph. Never null.
- `bridge`: true if **any** segment of the span is tagged as a bridge — a span
  that is part bridge is the part that washes out. Same rule as the pocket
  record's `severing_edges[].bridge` below.
- `highway`: raw OSM class, or null when the way is untagged. Observed values
  in this graph: `trunk`, `primary`, `secondary`, `tertiary`, `residential`,
  `unclassified`, `living_street`, and the `*_link` ramp variants. Consumers
  must tolerate any OSM highway value, not just these.
- `length_m`: float, one decimal.

## Pocket record

Returned by `GET /api/pockets`, one per area that the currently blocked roads
have cut off from every health facility. Ordered by population, largest first;
pockets with no population figure sort last.

A pocket is only reported if it clears all four of the filters the all-edge
sweep validated: at least 3 nodes, convex hull at least 0.01 km², prior access
to care under 10 km, and a severing edge whose endpoints are both real
junctions. Anything less is a cul-de-sac losing its own driveway.

```
id, nodes, n_nodes, polygon, hull_area_km2, hull_widened, centroid,
population, population_source, population_method, population_coverage,
population_settlements, lost_facility, prior_access_km,
nearest_place, nearest_place_km, severing_edges, checks
```

- `id`: `"pocket-<lowest node id>"`. Stable while the pocket has the same nodes;
  it is derived from the shape, not a database key, so do not persist it.
- `nodes` / `n_nodes`: graph node ids inside the pocket, and how many.
- `polygon`: convex hull as `[[lat, lon], ...]`, closed ring. A pocket of fewer
  than 3 non-collinear nodes has no area and comes back as its own points.
- `hull_area_km2`, `hull_widened`: hull area, and whether a degenerate hull was
  buffered for the population lookup. The area reported is always the raw hull.
- `centroid`: `[lat, lon]`.
- `population`: float, or **null** when it could not be measured. Never 0.0 for
  an unmeasured area — the raster is ~71% nodata and holds no valid zero cell,
  so a zero there always meant "not measured". Show "unknown", never "0".
- `population_source`: `census` | `raster` | `missing`. **Not interchangeable.**
  `census` is an enumerated Census 2011 village count; `raster` is a modelled
  WorldPop 100 m estimate that will not match a census figure; `missing` means
  `population` is null. A UI must distinguish the first two.
- `population_method`: how the figure was obtained — `census-2011`, `cells`,
  `prorated`, `nodata-only`, `off-raster`, `no-overlap`, `no-raster`.
- `population_coverage`: `full` | `partial` | `outside` | `no-raster`. A
  `partial` sum is an undercount.
- `population_settlements`: names of settlements whose point falls inside.
- `lost_facility`: name of the nearest health facility the pocket could reach
  before the cut, or null if it could reach none to begin with.
- `prior_access_km`: road distance to that facility before the cut, or null.
- `nearest_place`, `nearest_place_km`: nearest named settlement, **as a label
  only**. It is not necessarily inside the pocket and never decides severance.
- `checks`: the four filters, all true for any pocket that is returned —
  `min_nodes`, `min_hull_km2`, `prior_access`, `endpoint_degree`.

### `severing_edges`

The blocked spans straddling the pocket boundary — the roads responsible for
the cut. Each has one endpoint inside the pocket and one outside. Carries the
full span so the map can highlight it without a second lookup.

```
edge_id, name, length_m, highway, bridge, coordinates, report_ids
```

- `edge_id`, `name`, `length_m`, `highway`, `coordinates`: as in the road record.
- `bridge`: true if **any** segment of the span is tagged as a bridge — a span
  that is part bridge is the part that washes out.
- `report_ids`: ids of the live reports asserting this span is `impassable`,
  in ascending order. These are the reports the pocket exists because of, so a
  UI can go from a cut-off area to the photos behind it. Rejected reports are
  excluded; a report in any other state is not a cause and does not appear.

Usually one edge. Several blockages can cut one pocket together, in which case
every straddling blocked edge is listed and clearing any single one may not
reconnect the area.

## Endpoints

```
POST   /api/reports
GET    /api/reports?status=pending
GET    /api/reports/{id}
POST   /api/reports/{id}/status
POST   /api/reports/{id}/state
POST   /api/reports/{id}/edge
GET    /api/roads
GET    /api/pockets
GET    /api/detours
GET    /api/settings
POST   /api/settings
```

`POST /api/reports/{id}/state` takes `{"state": "<value>"}` and overrides what
the detector decided. The value must be legal for the report's `asset_type`.
An override is a human assertion, so the record comes back with `confidence`
1.0 and `detection_mode` `manual`.

`POST /api/reports/{id}/edge` takes `{"edge_id": "<id>"}` and overrides the
binding, chosen from the `bind_candidates` on `GET /api/reports/{id}`. Any edge
in the graph is accepted, not only the shortlist.
