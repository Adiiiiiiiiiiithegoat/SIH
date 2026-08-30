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
- `detection_mode`: `api` | `model` | `manual`
- `status`: `pending` | `resolved` | `rejected`

## Road record

Returned by `GET /api/roads`, one per undirected span in the network.

```
edge_id, coordinates, state, name, highway, length_m
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
- `highway`: raw OSM class, or null when the way is untagged. Observed values
  in this graph: `trunk`, `primary`, `secondary`, `tertiary`, `residential`,
  `unclassified`, `living_street`, and the `*_link` ramp variants. Consumers
  must tolerate any OSM highway value, not just these.
- `length_m`: float, one decimal.

## Endpoints

```
POST   /api/reports
GET    /api/reports?status=pending
GET    /api/reports/{id}
POST   /api/reports/{id}/status
GET    /api/roads
GET    /api/pockets
GET    /api/settings
POST   /api/settings
```
