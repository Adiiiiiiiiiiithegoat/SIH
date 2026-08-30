# NETRA

AI-assisted disaster-infrastructure assessment, built for SIH 2026. A citizen
photographs a damaged road or building; the backend classifies the damage,
binds it onto the real road network, computes which areas lose hospital
access as a result, and hands an operator a prioritised inspection queue.

**Detection is not prioritisation.** The point isn't "here's a damage
photo" — it's ranking by consequence (how many people, how much delay) so an
operator with ten crews and two hundred reports knows which ten to send first.

## Live demo

**https://james-innovations-bridge-printer.trycloudflare.com**

Tunnelled from a local `uvicorn` instance for the hackathon judging window —
not a persistent deployment. If the link is down, the host machine is
offline; run `uvicorn app.main:app --port 8001` plus `cloudflared tunnel
--url http://127.0.0.1:8001` to bring up a fresh one.

## Scope of this build

This build covers one coastal taluk — Surathkal–Mulki, Dakshina Kannada,
Karnataka — on purpose: a hackathon needs a bounding box small enough that
every number in the demo can be hand-checked against the source data (see
`REPORT.md`). It runs fully offline — the road graph, population raster,
settlements and OSM facility data for that bbox are frozen under
`app/data/`. A live demo can optionally call a cloud vision API for real
photo classification; without a key it falls back to a deterministic
offline stub, so a fresh clone and the test suite need nothing but Python.

## Scaling to a state, or the country

Nothing in the architecture is specific to this one taluk — it's the
*data*, not the code, that's scoped small for the demo:

- **The road graph, population raster, and facility list are all generated
  by bounding box**, not hand-built. `config.REPORT_BBOX` is the only place
  a region is named; a new district means re-running the same OSM +
  WorldPop pipeline against a new bbox, not writing new code.
- **OSM's road graph and WorldPop's 100 m population raster already cover
  all of India.** What's frozen under `app/data/` is a clip of two national,
  public datasets — not a one-off local collection that would need to be
  redone for every new region.
- **The priority formula, the report schema, and the detection contract are
  all geography-independent.** `priority = belief × population^k × delay`
  (validated against expert ordering in `REPORT.md` §5) never references
  where the road is. Neither does `CONTRACT.md`.
- **Detection is one stateless API call per photo.** Cost and latency scale
  with report volume, not with how much of the country is covered.

What would genuinely need engineering work to go national — flagged
honestly, not solved here:
- **Partition the graph.** One in-memory graph for all of India is the
  wrong shape. Route each report to a per-state or per-district graph,
  loaded on demand the way `network.net()` loads this one today.
- **Swap SQLite for a real database.** `store.py` is the only file that
  touches SQL — the seam a Postgres/multi-instance store would go through
  without `CONTRACT.md`'s report shape changing underneath it.
- **Automate the per-region data pipeline.** Downloading OSM, clipping
  WorldPop, and building `reference.json` for a new bbox is currently a
  manual run of the same scripts used for this demo; turning that into a
  batch job is ops work, not a redesign.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8001
```

That's the whole backend — it serves the API and, if `frontend/` is
present, the dashboard and upload pages too, all from one origin
(`http://127.0.0.1:8001/dashboard.html`).

For local frontend development instead, serve it separately so you can edit
static files without restarting anything:

```bash
cd frontend && python -m http.server 8000
```

`frontend/config.js` detects port 8000 and points API calls at
`127.0.0.1:8001` automatically. Open `http://127.0.0.1:8000/upload.html`
(citizen report form) or `.../dashboard.html` (operator console).

### Load the demo data

```bash
python -m app.demo_seed
```

Wipes `app/data/reports.db` and loads 11 real (freely-licensed, non-local —
see `app/data/demo_images/CREDITS.md`) damage photos pinned to real
Surathkal–Mulki coordinates, two of them on spans the severance engine
confirms cut a community off from care. Detection verdicts are pinned into
the disk cache alongside each photo, so a live re-upload during a demo
returns the same answer instantly with no network call.

### Real photo classification (optional)

Without any setup, photo uploads classify as `unknown` — deterministic,
offline, no missing dependency. To get a real verdict, drop a DeepSeek API
key in a `.env` file at the repo root (gitignored, never committed):

```
DEEPSEEK_API_KEY=sk-...
```

## Tests

```bash
pytest app/tests -q                 # backend — 99 tests, fully offline
node frontend/tests/test.js         # frontend logic, no browser needed
```

Two extra scripts at the repo root are worth re-running after touching
`binding.py`, `severance.py`, or a `config.py` threshold — they check the
live per-report engine agrees with an independent offline sweep:

```bash
python verify_binding.py
python verify_engine.py
```

## Project structure

```
app/            FastAPI backend: detection, road binding, severance engine,
                priority scoring, SQLite report store. app/data/ holds the
                frozen graph, raster, settlements and OSM reference layer.
frontend/       Static pages: upload.html (citizen), dashboard.html
                (operator console). No build step, no framework.
population/     WorldPop raster processing used to build app/data's raster.
medic/          Abandoned: a labelled-photo dataset download that never
                completed. Not used by anything that runs.
```

## Docs

- **[`CONTRACT.md`](CONTRACT.md)** — the report/road/pocket schema and every
  API endpoint. The single source of truth both frontend and backend code
  against.
- **[`PROJECT_STATE.md`](PROJECT_STATE.md)** — working notes for whoever
  picks this up next: what's locked, what's done, what's still open.
- **[`REPORT.md`](REPORT.md)** — methodology: the priority-formula parameter
  sweep, the population-sourcing bug it caught, and what was measured versus
  assumed.
