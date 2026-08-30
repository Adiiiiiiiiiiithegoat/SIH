# PROJECT_STATE.md

Brain-dump for a fresh session. Read this before touching anything. It replaces a
long planning conversation — verify claims against the code if something looks off
rather than trusting this file blindly. Written 2026-08-30, updated twice same
day: once after integration work landed, again after upload validation,
duplicate-photo suppression, and UI for the three previously backend-only
endpoints (re-bind, tuning, detours) — see "Current state" for what changed.

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

**Backend**: complete, `99 passed` via `pytest app/tests -q` (was 88 earlier
the same day; 94 after the operator-workflow endpoints; the last 5 are photo
upload validation and duplicate-photo suppression, both below). Runs offline
by default: `uvicorn app.main:app --host 127.0.0.1 --port 8001`. Endpoints
match `CONTRACT.md` exactly, plus `GET /api/roads`, `GET /api/pockets`,
`GET /api/detours`. `main.py` also optionally mounts `frontend/` at `/`, so a
real deployment can run one process for both the API and the static pages —
same-origin, no CORS — while local dev keeps the two-port split below.

**`POST /api/reports` now guards the upload itself, not just what happens
after.** A signature check (jpeg/png/gif/webp/heic magic bytes) rejects a
non-image with 422; anything over 25 MB (`config.MAX_UPLOAD_MB`) is rejected
with 413 — both checked against the bytes already read for the next thing:
**the same photo submitted twice — a double-tapped submit, a forwarded
file — no longer becomes two queue rows.** The upload is sha1-hashed and
matched against every live (`pending`/`in_progress`) report's stored image;
a match returns the existing report instead of inserting a new one. A
*different* photo of the same span still files its own report and
corroborates normally via `n_reports` — this only catches byte-identical
resubmits, not "two people photographed the same washout."

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
work. **Run uvicorn with `--reload`** — it is how the API is now started, and
it is not optional. Without it a change to `app/*.py` needs a manual process
restart to take effect, and a stale process silently serves the old logic: it
cost us a bogus "2 areas cut off" on an already-resolved case, because the
running server predated the commit that made resolving a report reopen its
road. Twice bitten.

The frontend added a real operator console since the original version of this
doc: priority-tiered queue (CRITICAL/HIGH/LOW/No impact), a Leaflet map with
severed-pocket popups ("est. NNN cut off · Why?"), and status tabs
(Pending/In progress/Closed). It matches the CONTRACT.md workflow additions
above. It briefly had a detection-mode selector (api/model toggle) too, but
that was removed once DeepSeek became the only real detector — see below.
This was the user's own work, done
concurrently with the integration work below — if a session mid-edit
disappears files from `frontend/`, that's very possibly just someone actively
saving, not corruption; ask before restoring anything from git.

**Every CONTRACT.md endpoint now has a UI path, not just an API.** Three
things that existed only as backend endpoints — reachable by curl, not by an
operator — got a dashboard control each:
- **Re-bind a mis-bound photo.** The report panel's "Road binding" row has a
  "Change binding" button that fetches `GET /api/reports/{id}` (the only
  request that carries `bind_candidates`), offers them in a picker labelled
  by road name and distance, and calls `POST /api/reports/{id}/edge` on
  apply. Verified against a live report with a genuinely ambiguous GPS fix:
  a 7-candidate picker, re-bind applied, queue and map re-rendered correctly.
- **Tuning panel.** A "Tuning" button in the top bar renders the 12
  `config.MUTABLE` constants (`GET /api/settings`) as plain number inputs;
  saving `POST`s them and re-ranks the queue live.
- **Detour zones on the map.** `GET /api/detours` was computed and already
  drove a report's `priority_reason` text, but the regions themselves were
  never drawn. Now an opt-in "Detour zones" layer-control overlay, off by
  default (amber, dashed) so it never competes with the severed-pocket
  polygons, which are the headline.

Also since the original doc: the product renamed **Responda → NETRA** on
both pages; the queue gained a Ref column and bridges/buildings-damaged
figures (hidden when zero); a full-screen photo lightbox; and explicit
"Loading map…" / "Loading reports…" states instead of a blank flash on
first paint. `frontend/mocks/data.js` grew matching fixtures (`detours`, a
non-empty `settings` object) so `dashboard.html` still runs standalone with
`USE_MOCKS: true` and none of this breaks the offline demo fallback.

**Detection is now real, not just a seam.** `app/detect.py` mode="api" calls
DeepSeek's vision model (`deepseek-v4-flash-vision-exp`, OpenAI-compatible
`chat/completions` at `api.deepseek.com`) with a JSON-only prompt per asset
type, parses `{state, confidence, reason}`, and caches the raw result to disk
at `cache/detect/<sha1-of-image-bytes>.json` (gitignored) so a repeated photo
never hits the network twice — this is the "cached vision-API detection" the
plan called for. The key lives in a gitignored `.env` at repo root
(`DEEPSEEK_API_KEY=...`), loaded by a ~5-line reader in `app/config.py`
(no `python-dotenv` dependency added).

**The `mode="model"` stub and its live toggle are gone.** There was never a
trained model behind it — it was a `state="unknown"` placeholder from before
DeepSeek was wired up — and keeping it as a selectable option was the exact
footgun that reset silently to the stub on every backend restart (see the
now-removed "What is not done" bullet about it). `config.DETECTION_MODE` is
now a fixed `"api"`, not in `MUTABLE`, and `POST /api/settings` no longer
accepts a `detection_mode` key at all. Offline stays safe with zero flags:
no `DEEPSEEK_API_KEY` (or a failed call) still falls through to the same
`"unknown"` stub inside `detect()`, so a fresh clone and `pytest` are fully
offline by default. `mode="manual"` is unchanged: echoes the operator's
asserted state at full confidence.

Verified live against the two locked demo photos (see below): DeepSeek
correctly called both `impassable` (0.95 and 1.0 confidence), both bound to
the exact right edge, both pockets came back with the exact populations from
`demo_data.txt` (681.6 and 26.7).

The MEDIC dataset download was abandoned: `medic/raw/dl_progress.log` shows all
8 parts stuck at `0/1358896324, retrying`, and `medic/data/` is empty. It needed
roughly 7 of the remaining 8 hours at the time, so it was cut. Unaffected by the
DeepSeek work above — MEDIC was for a trained model, DeepSeek is a hosted API,
they were never the same plan.

**`app/data/reports.db` is local, gitignored runtime state — not part of the
repo.** Running `python -m app.demo_seed` wipes it and loads the current
11-photo demo set (see "The demo" below for the two headline cases within
it); running it again is idempotent. Trivial to wipe by hand too
(`rm app/data/reports.db`, restart uvicorn) if it gets cluttered with ad-hoc
test reports before rehearsal.

**Repo is clean.** `REPORT.md`, `PROJECT_STATE.md`, `demo_data.py`,
`demo_data.txt`, `verify_binding.py`, `verify_engine.py`, `app/demo_seed.py`,
and `app/data/demo_images/` are all committed — a fresh clone can run the
tests, the verify scripts, and seed the live demo with no missing file. Only
`.env` sits outside version control, gitignored on purpose.

## What is not done

- **Real demo photography.** The two synthetic PIL-drawn placeholders are
  gone — `app/demo_seed.py` (committed) now loads 11 real disaster photos
  from `app/data/demo_images/` (8 damage, 3 clear-road; freely licensed,
  credited in that folder's `CREDITS.md`) pinned to real Surathkal-Mulki
  coordinates, with verdicts pinned into the detect cache for offline
  determinism. Still not photos of *this* area — Japan, Greece, Turkey,
  Bengaluru stand-ins wearing local coordinates. Fine for the pinned/cached
  playback path; only bites if someone uploads a fresh photo of their own
  during the demo and expects a locally-grounded answer. Actual phone photos
  of Surathkal-Mulki would close this properly; same upload path either way.
- **The demo has never been rehearsed**, live or dry-run, start to finish in
  front of anyone. This is the one item on this list that actually matters —
  everything else here is now either done or a known, low-risk stand-in.

## The plan for remaining time

1. ~~Integration testing first~~ — **done.** Frontend and backend are
   connected and verified end-to-end in a real browser, not just by API call.
2. ~~Cached vision-API detection on demo photos~~ — **done**, extended past
   the original two cases to an 11-photo set (`app/demo_seed.py`), all
   pinned and cached for offline determinism.
3. ~~Every CONTRACT.md endpoint reachable from the operator console~~ —
   **done.** Edge re-binding, the tuning panel, and the detour-zone overlay
   were backend-only; all three now have a UI, verified live.
4. **Rehearsal, repeatedly** — next up, not started. The one thing left that
   isn't already done.
5. **Keep two hours of buffer.**

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
