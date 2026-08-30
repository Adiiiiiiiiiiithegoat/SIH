# PROJECT_STATE.md

Brain-dump for a fresh session. Read this before touching anything. It replaces a
long planning conversation — verify claims against the code if something looks off
rather than trusting this file blindly. Written 2026-08-30, updated same day after
integration work landed — see "Current state" for what changed.

## What this is

SIH 2026 project, team ₹35. AI-powered disaster-resilient infrastructure
assessment. A citizen uploads a photo of a damaged road or building; the backend
classifies the damage, binds it onto the real road network, computes which areas
lose hospital access as a result, and presents a prioritised inspection queue to
an operator. The core claim: **detection is not prioritisation** — ranking by
consequence (how many people, how much delay) is what makes this different from
"here's a damage photo."

Area: coastal Dakshina Kannada, Karnataka (Surathkal–Mulki). Fully offline —
graph, raster, settlements and OSM facility/place data are frozen under
`app/data/`.

## Decisions that are locked — do not re-derive

The priority formula, in `app/priority.py`, using constants from `app/config.py`:

```
priority = belief × (population ** POPULATION_EXPONENT) × delay_minutes
```

- `belief`: `impassable` → model confidence; `unknown` → `UNKNOWN_BELIEF` (0.5);
  `passable` → 0. Then `× (0.6 + CORROBORATION_WEIGHT × (1 − 0.5**n_reports))`,
  then `× CONFIRMATION_BOOST` if status is `resolved`, capped at 1.0.
- `delay_minutes`: `DISCONNECT_PENALTY` (300 min) if the report severs all
  access; otherwise `(added_km / ASSUMED_SPEED_KMH) × 60`.
- Current values (`app/config.py`): `POPULATION_EXPONENT=0.4`,
  `DISCONNECT_PENALTY=300`, `UNKNOWN_BELIEF=0.5`, `CORROBORATION_WEIGHT=0.4`,
  `CONFIRMATION_BOOST=1.25`, `ASSUMED_SPEED_KMH=35`.

These came from a parameter sweep (`severance.py` at repo root, §5 of
`REPORT.md`) testing seven formulas against expert ordering: this one scored
0.988 rank correlation, the naive multiplicative version scored 0.576. **Do not
change these six constants without being explicitly asked.** They are exposed
live via `GET/POST /api/settings` (`config.MUTABLE`) so an operator can see and
adjust them at runtime — that is a deliberate escape hatch, not permission to
silently retune the defaults.

**No categorical rule that severances outrank detours.** That was tried and
removed — `DISCONNECT_PENALTY` already encodes the difference continuously
(300 min vs. a few minutes for a typical detour is a ~45× multiplier on its
own), and a categorical gate double-counted it. Verified empirically in
`REPORT.md` §5: removing the gate did not reorder the top 20.

**Output vocabularies are fixed** (`CONTRACT.md`, `app/detect.py`):
- Roads: `passable | impassable | unknown`.
- Buildings: `damaged | not_damaged | unknown`.
- `unknown` is a deliberate first-class answer, ranked high via
  `UNKNOWN_BELIEF` — an unassessed road is an uninspected one, not a safe
  default.
- Bridges are never visually detected. A road segment is a bridge because its
  OSM `bridge` tag says so (`severing_edges[].bridge` in the pocket record).
- Water is an internal reason a road might be impassable; it is never its own
  output category.
- **Mild damage severity is not predicted.** Published benchmarks put mild-class
  F1 at 29 even for research systems, and human annotators only agree with each
  other 79% of the time on it. This is a deliberate scope cut, not an oversight.

**Report status is now a 4-state workflow, not 3** (added since the original
version of this doc — check `CONTRACT.md` if this drifts further):
`pending | in_progress | resolved | rejected`. `in_progress` and `resolved` both
still block the network (accepting a report isn't the same as the road being
cleared); only `rejected` clears it. Both `in_progress` and `resolved` apply the
same `CONFIRMATION_BOOST` in `app/priority.py::belief` — an operator accepting a
report on the ground is worth more than any number of uncorroborated citizen
reports, whether or not the fix is finished. Any status can move to any other;
the operator decides (`app/main.py::set_status`).

There's also `POST /api/reports/{id}/state`, an operator override of the
*detected* state (separate from the status workflow above) — for when the
detector says `unknown` and a human looking at the photo can do better. It
always sets `confidence=1.0` and `detection_mode="manual"`, because an override
is an assertion, not a detection.

**Population sourcing is strict** (`CONTRACT.md` pocket record,
`population_source`): `census` (enumerated Census 2011 village counts,
`population/data/settlements.csv`, 8 villages) and `raster` (WorldPop 2020
modelled 100 m gridded estimate) are never displayed identically — a UI must
distinguish them. Missing population is `null` and renders as "unknown," never
`0` — the raster is ~71% nodata and has no valid zero cell anywhere, so a
measured `0.0` would always have meant "not measured," never "nobody lives
here." (This was a real bug, found and fixed: see `REPORT.md` §4 — it changed
48 of 103 pocket results in the offline sweep. `app/severance.py` is verified
clean; `population/population.py` / `popdata.py` are noted in `REPORT.md` as
still carrying the old silent-zero behavior — check before touching either.)

## Current state (verified 2026-08-30, don't trust an older claim)

**Backend**: complete, `94 passed` via `pytest app/tests -q` (was 88 earlier
the same day; the operator-workflow endpoints below added 6 tests). Runs
offline by default: `uvicorn app.main:app --host 127.0.0.1 --port 8001`.
Endpoints match `CONTRACT.md` exactly, plus `GET /api/roads`,
`GET /api/pockets`, `GET /api/detours`.

Two standalone verification scripts exist outside `app/tests` and are worth
re-running after any change to `binding.py`, `severance.py`, or `config.py`
thresholds:
- `verify_binding.py` — checks the two demo photo points bind to the correct
  span, plus a 200-point sample comparing real-polyline distance vs.
  straight-chord distance.
- `verify_engine.py` — checks the live per-edge engine (`app/severance.py`)
  agrees with the offline all-edge sweep (root `severance.py`) on the two demo
  cases, and that a handful of blocked edges resolves in under 500 ms.

**Frontend and backend are now connected and verified live, not just by
API call — by driving the actual `upload.html` and `dashboard.html` in a real
(headless Chromium) browser.** `frontend/config.js` is `USE_MOCKS: false`,
`API_BASE_URL: "http://127.0.0.1:8001"`. Confirmed in that run: file-input →
EXIF GPS auto-read → marker placement → `POST /api/reports` → correct edge
bind → dashboard re-render, zero console/page errors. Dev setup in practice:
a plain `python -m http.server 8000` in `frontend/` serves the static pages;
`uvicorn` serves the API on `8001`; both need to be running for anything to
work, and uvicorn is **not** run with `--reload` here, so a code change to
`app/*.py` needs a manual process restart to take effect (kill the PID
listening on 8001, relaunch the same uvicorn command) — this bit us once
mid-session.

The frontend added a real operator console since the original version of this
doc: priority-tiered queue (CRITICAL/HIGH/LOW/No impact), a Leaflet map with
severed-pocket popups ("est. NNN cut off · Why?"), status tabs
(Pending/In progress/Closed), and a detection-mode selector. It matches the
CONTRACT.md workflow additions above. This was the user's own work, done
concurrently with the integration work below — if a session mid-edit
disappears files from `frontend/`, that's very possibly just someone actively
saving, not corruption; ask before restoring anything from git.

**Detection is now real, not just a seam.** `app/detect.py` mode="api" calls
DeepSeek's vision model (`deepseek-v4-flash-vision-exp`, OpenAI-compatible
`chat/completions` at `api.deepseek.com`) with a JSON-only prompt per asset
type, parses `{state, confidence, reason}`, and caches the raw result to disk
at `cache/detect/<sha1-of-image-bytes>.json` (gitignored) so a repeated photo
never hits the network twice — this is the "cached vision-API detection" the
plan called for. The key lives in a gitignored `.env` at repo root
(`DEEPSEEK_API_KEY=...`), loaded by a ~5-line reader in `app/config.py`
(no `python-dotenv` dependency added). **Default `DETECTION_MODE` in
`app/config.py` is still `"model"`** (the stub) — deliberately left unchanged
so a fresh clone and `pytest` stay fully offline; flip it live with
`POST /api/settings {"detection_mode": "api"}` per session (this does not
persist across a backend restart — it's in-memory `config.DETECTION_MODE`,
reset to `"model"` every time the process restarts). Any API failure
(network, bad JSON, anything) falls back to the same constant stub silently —
by design, per the existing "never raises" contract.

Verified live against the two locked demo photos (see below): DeepSeek
correctly called both `impassable` (0.95 and 1.0 confidence), both bound to
the exact right edge, both pockets came back with the exact populations from
`demo_data.txt` (681.6 and 26.7).

`mode="model"` is still the placeholder stub it always was: `state="unknown"`,
`confidence=STUB_CONFIDENCE` (0.72), deliberately constant. `mode="manual"`
is real and unchanged: echoes the operator's asserted state at full
confidence.

The MEDIC dataset download was abandoned: `medic/raw/dl_progress.log` shows all
8 parts stuck at `0/1358896324, retrying`, and `medic/data/` is empty. It needed
roughly 7 of the remaining 8 hours at the time, so it was cut. Unaffected by the
DeepSeek work above — MEDIC was for a trained model, DeepSeek is a hosted API,
they were never the same plan.

**`app/data/reports.db` currently holds exactly two reports** — the two
canonical demo cases below, freshly uploaded through the real photo → API →
bind → recompute path, nothing else. It had accumulated ~25 mixed test
reports from both manual API testing and browser testing before being reset;
it's gitignored and trivial to wipe again (`rm app/data/reports.db`, restart
uvicorn) if it gets cluttered before rehearsal.

**Uncommitted, untracked at repo root**: `REPORT.md`, `PROJECT_STATE.md`,
`demo_data.py`, `demo_data.txt`, `verify_binding.py`, `verify_engine.py` — none
of this is committed yet. `.env` is untracked and gitignored (correctly —
never commit it).

## What is not done

- **Real demo photography.** Everything above was verified with two
  *synthetic* placeholder JPEGs (PIL-drawn, EXIF-tagged via `piexif` at the
  exact two demo coordinates) — they proved the pipeline works, they are not
  presentable demo assets. Actual phone photos taken at or near
  `13.017102, 74.795375` (item 1) and `13.012206, 74.807704` (item 2), or any
  photo whose visible content matches "impassable road," need to replace them
  before the real run. Same upload path, same caching — nothing else changes.
- **The demo has never been rehearsed**, live or dry-run, start to finish in
  front of anyone.
- Detection mode resets to `"model"` on every backend restart (in-memory
  setting) — whoever runs the rehearsal needs to flip it to `"api"` again each
  time the server restarts, or it'll silently demo the stub instead of the
  real model. Worth a one-line startup script if this trips someone up twice.

## The plan for remaining time

1. ~~Integration testing first~~ — **done.** Frontend and backend are
   connected and verified end-to-end in a real browser, not just by API call.
2. ~~Cached vision-API detection on demo photos~~ — **done for the two
   canonical demo cases**, DeepSeek wired and cached. Extending to a full
   ~10-photo demo set is still open, gated on having real photos to run
   through it (see above).
3. **Rehearsal, repeatedly** — next up, not started.
4. **Keep two hours of buffer.**

## The demo

Two cases, pulled straight from the live engine by `demo_data.py` (so nothing
here can drift from what the dashboard actually shows — see
`demo_data.txt` for full geometry, routes, and node IDs).

**Demo item 1 — rank #1 severance, the strongest result overall, and it is
NOT a bridge.**
- Edge `5269747322-9647824661`, unnamed residential road, 182.0 m, no bridge
  tag, both endpoints degree 3.
- Severs a 24-node pocket, hull 0.2783 km², centroid 13.017537, 74.798508 —
  labeled only as "near Mukka village" (1.07 km away, not inside the pocket).
- Population: **681.6 people** (WorldPop raster estimate, full coverage, 29
  cells).
- Before the cut: 1.58 km by road to **Health Subcenter** (13.009436,
  74.796131). After: no route to any of the 15 facilities in the graph —
  cut off entirely.
- Priority score: belief 0.8 (impassable, confidence 1.0, 1 report, pending)
  × 681.6^0.4 (13.596) × 300 min = **3263.0**. (With `UNKNOWN_BELIEF`=0.5
  instead of a confirmed report: 1631.5.)
- **Verified demo photo coordinate** (EXIF GPS): `13.017102, 74.795375` — a
  real vertex of the span's OSM polyline, 0.4 m off the straight
  endpoint-chord that `binding.candidates` measures against. Suggested
  `GPSHorizontalPositioningError`: 8 m. Binds correctly to
  `5269747322-9647824661` (chosen candidate, distance 0.40 m, score 0.9597).

**Demo item 2 — Madhya Padavu bridge, rank #19 of 69 in-bbox scored results.**
The only bridge-tagged severance inside the reporting bbox — the "a bridge
fails" story, kept because it's clean, even though it ranks far below item 1.
- Edge `9620418684-9620418686`, unnamed, 506.9 m, `highway=unclassified`,
  **bridge tag: yes**, both endpoints degree 3.
- Severs a 5-node pocket, hull 0.0344 km², centroid 13.013189, 74.806888 —
  labeled "near Madhya Padavu village" (1.03 km away).
- Population: **26.7 people** (WorldPop raster, full coverage, 2 cells).
- Before the cut: 4.65 km by road to **Padmavathi Hospital, Dakshina Kannada**.
  After: no route to any of the 15 facilities.
- Priority score: belief 0.8 × 26.7^0.4 (3.7205) × 300 min = **892.9**. (With
  `UNKNOWN_BELIEF`=0.5: 446.5.)
- **Verified demo photo coordinate**: `13.012206, 74.807704` — 0.3 m off the
  chord, suggested accuracy 8 m. Binds correctly to `9620418684-9620418686`
  (distance 0.32 m, score 0.9675).

Both coordinates were checked by `verify_binding.py` against real span
geometry, not just the straight endpoint chord — and, since this doc was first
written, both have now actually been run through the live path: real photo
upload (via a real browser for item 1), real DeepSeek classification, real
`binding.bind()`, both landing on the exact expected edge. Not just an offline
script's prediction anymore.

## Honest positions to hold — deliberate, do not soften or hide

- **No public dataset labels road passability from ground photos.** This is
  why detection is a stub with a real seam, not a trained model with a hedge.
- **Bridges are not the dominant failure mode here.** Only 3 of 103 surviving
  severance pockets and 5 of 100 detour spans sit behind a bridge-tagged edge
  in the offline sweep. Ordinary residential/unclassified roads carry far more
  of the affected population — the rank-1 result (681.6 people) is a plain
  residential street, not a bridge. The Madhya Padavu bridge is kept as a demo
  item because it's a clean, legible story, not because it's representative.
- **Accuracy expectations cite published benchmarks, not promised results.**
  E.g. the mild-damage F1-29 / 79%-agreement figures above are why that class
  isn't predicted at all.
- **Nothing dispatches to an authority without operator confirmation.** The
  report lifecycle (`pending → resolved/rejected`) and `POST
  /api/reports/{id}/status` exist precisely so a human sits in the loop before
  anything is acted on.

## Working style for whoever picks this up

Direct and brief. No preamble, no metaphors. Decisions get made in plain
language before implementation detail. Honest disclosure of limitations is
preferred over optimistic framing. Push back when a plan is wrong rather than
agreeing with it.
