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
