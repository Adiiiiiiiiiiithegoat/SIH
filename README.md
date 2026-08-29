# SIH

Disaster-response asset mapping: citizens report road/building damage with a
photo + GPS; the backend classifies each report, deduplicates against a road
network, scores priority, and exposes it for a map dashboard. Includes the
data pipeline for road graphs, WorldPop population, and MEDIC medical facilities.

## Folder split

- `frontend/` — owned by the frontend developer.
- `app/` and everything else (data pipeline scripts, rasters, graphs) — backend.

See `CONTRACT.md` for the shared report schema and API endpoints.
