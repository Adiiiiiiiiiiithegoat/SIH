# Surathkal–Mulki road network: disaster access analysis

Consolidated report of the graph-analysis work. Area: coastal Dakshina Kannada,
Karnataka. Reporting bbox **west 74.78, south 13.00, east 74.86, north 13.10**.

Everything below traces to OpenStreetMap or WorldPop. No settlement point,
population figure, or severance was invented or inferred.

---

## 1. What the analysis is

The original demo premise was: *"a bridge fails, a village is cut off from its
hospital."* The work below tested whether that premise is true in this area.
Short answer: **the severance findings are real, but the bridge framing is
weak** — the population is in ordinary road severances and highway detours, not
in bridge failures.

The analysis went through four generations, each fixing a real defect in the
previous one. That history matters because early numbers are still quoted in
older report files and are wrong.

| Gen | Unit of analysis | Key defect it fixed | Result |
|---|---|---|---|
| 1 | OSM `place` node, single snapped graph node | — | 10 "single-road" settlements, 0 bridge cut-offs |
| 2 | Settlement node *cluster*, 3 km buffered graph | bbox clipping made false dead ends | 0 single-road settlements — all 10 were artifacts |
| 3 | Connected **component** | severance tied to arbitrary OSM tagging | 1712 severing edges, mostly cul-de-sacs |
| 4 | Component + meaningfulness filter + WorldPop | driveways counted as severed pockets | **103 real pockets, 52 with population** |

---

## 2. Data

**Road network** — OSMnx 2.1.1 `graph_from_bbox`, `network_type="drive"`.
Downloaded with a **3 km buffer** on all sides (`74.75226, 12.97297, 74.88774,
13.12703`) because clipping at the reporting bbox truncates roads and creates
false dead ends. All connectivity runs on the buffered graph; only results whose
*affected area* centroid falls inside the reporting bbox are reported.

```
buffered graph : 4974 nodes, 12019 edges, 1 connected component
unbuffered     : 2392 nodes,  5774 edges   (kept for reference only)
```

**Health facilities** — OSM `amenity` in `hospital|clinic|doctors`, queried over
the buffered bbox: **15 facilities** (7 inside the reporting bbox, 8 in the
buffer ring). Excluding the buffer-ring 8 would have inflated every
distance-to-care figure.

**Settlements** — OSM `place` in `village|town|hamlet|suburb`: 48 nodes, 47
named. **Used only as human-readable labels.** No severance or population figure
depends on them.

**Population** — WorldPop 2020 constrained, 100 m gridded population count,
cropped to the AOI (`population/data/population_100m.tif`).

```
144 x 168 cells, EPSG:4326
bounds       : 74.75958, 12.98042, 74.87958, 13.12042
valid cells  : 6949 (29%)      nodata cells: 17243 (71%)
min valid    : 6.08            zero-valued valid cells: 0
total in crop: 203,302 people
```

A WorldPop count is a **modelled gridded estimate**, not an enumerated census
count, and is not tied to any administrative boundary. It must be labelled as an
estimate wherever it appears.

---

## 3. Method

**Severance.** Remove one undirected span (all parallel and both directed
edges). Any resulting connected component containing **no health facility** is a
severed pocket. Only graph-theoretic cut edges can split the graph, so those are
the only candidates — 1874 of 6095 spans.

**Detour.** For every span, recompute distance-to-care for all nodes via one
multi-source Dijkstra on the reversed graph from all 15 facility nodes. Nodes
losing more than 3 km are the affected set.

**Meaningfulness filter.** Raw severance counts every cul-de-sac losing its own
driveway. A pocket must clear all four:

| | Criterion | Passes alone | Removed cumulatively |
|---|---|---|---|
| c1 | pocket has ≥ 3 nodes | 311 | −1401 |
| c2 | convex hull area ≥ 0.01 km² | 108 | −203 |
| c3 | prior access to care < 10 km | 1708 | −0 |
| c4 | both edge endpoints degree ≥ 3 | 309 | −5 |

```
1874 cut edges
1866 strand a facility-free component
 154 are download-boundary artifacts (roads truncated by the buffer edge) — discarded
1712 candidate pockets
 103 survive the filter
```

**c3 is inert** — only 4 pockets in the entire graph were already beyond 10 km,
and none of those survived c1+c2. c4 barely matters (−5) because it correlates
almost perfectly with c1: a cul-de-sac stem has both a low-degree endpoint and a
tiny pocket. **c1 does essentially all the work.**

---

## 4. A data-integrity fix that changed 48 results

`population/population.py :: population_in` returns `0.0` when a polygon covers
only nodata cells. That makes *"nobody lives here"* and *"we never measured
here"* identical.

This raster is 71% nodata and its minimum valid value is 6.08 — **it contains no
zero-valued valid cell anywhere**. So every `0.0` it produced was necessarily a
missing measurement, not a real zero.

```
before:  52 with a value | 48 exactly 0.0 |  3 None
after :  52 with a value |  0 exactly 0.0 | 51 None  (48 nodata-only + 3 off-raster)
```

All 48 zeros were artifacts. Fixed in `severance.py`; the shared module was left
untouched.

**This bug is still live in the shared module** and reaches `settlement_population`
via `population_within_radius` → `population_in`. Any settlement with no census
figure sitting on nodata reads as 0 people.

---

## 5. Priority scoring

Per `config.py`, with no categorical tier:

```
priority = belief × (population ^ POPULATION_EXPONENT) × delay_minutes

belief              = 0.5   (UNKNOWN_BELIEF — hypothetical failures, no field
                             reports, so uniform across every row and cannot
                             affect ordering)
POPULATION_EXPONENT = 0.4
delay_minutes       = 300              for a severance (DISCONNECT_PENALTY)
                    = added_km/35×60   for a detour   (ASSUMED_SPEED_KMH)
```

### Top 20, in-bbox, single formula, no tiers

```
  #     score kind              pop delay_min  nodes   len_m brdg  highway      edge
  1    2039.3 SEVERANCE       681.6     300.0     24     182   no  residential  <unnamed>
  2    1378.3 SEVERANCE       255.9     300.0      9      33   no  unclassified <unnamed>
  3    1300.5 SEVERANCE       221.3     300.0      7      74   no  residential  <unnamed>
  4    1298.8 SEVERANCE       220.6     300.0      9      49   no  residential  <unnamed>
  5    1296.7 SEVERANCE       219.7     300.0     14      16   no  unclassified <unnamed>
  6    1284.8 SEVERANCE       214.7     300.0      7     975   no  unclassified <unnamed>
  7    1267.6 SEVERANCE       207.6     300.0      7      34   no  unclassified <unnamed>
  8    1248.1 SEVERANCE       199.7     300.0      5       6   no  residential  <unnamed>
  9    1090.4 SEVERANCE       142.5     300.0      5      45   no  unclassified <unnamed>
 10     784.8 SEVERANCE        62.6     300.0     14      56   no  residential  <unnamed>
 11     741.0 SEVERANCE        54.2     300.0      5      53   no  residential  <unnamed>
 12     676.9 SEVERANCE        43.3     300.0      9      87   no  residential  <unnamed>
 13     676.9 SEVERANCE        43.3     300.0      7      15   no  residential  <unnamed>
 14     609.9 SEVERANCE        33.3     300.0      7     123   no  unclassified <unnamed>
 15     609.9 SEVERANCE        33.3     300.0      7      55   no  residential  <unnamed>
 16     599.5 SEVERANCE        31.9     300.0      5     155   no  residential  <unnamed>
 17     584.9 SEVERANCE        30.0     300.0     11      46   no  residential  <unnamed>
 18     569.9 SEVERANCE        28.1     300.0      7     557   no  unclassified <unnamed>
 19     557.7 SEVERANCE        26.7     300.0      5     507  YES  unclassified <unnamed>  <- Madhya Padavu bridge
 20     494.0 SEVERANCE        19.7     300.0      5      17   no  residential  <unnamed>

in-bbox scored results: 69   (34 more unscorable — population is None)
```

### The important negative result

**Removing the categorical gate did not change the ordering.** The top 20 is
still entirely severances, and no detour appears anywhere near the top.

The reason: `DISCONNECT_PENALTY = 300` versus ~6.7 minutes for a 4 km detour is
already a **45× multiplier**, while the 0.4 exponent compresses a 17× population
difference to only 3.2×. The formula in `config.py` behaves almost exactly like
the tier gate that was removed.

Worked example — the largest detour in the area against the top severance:

```
NH66 detour   0.5 × 12073.3^0.4 × 6.7  = 0.5 × 42.92 × 6.7  =  143.8
Mukka sever   0.5 ×   681.6^0.4 × 300  = 0.5 × 13.60 × 300  = 2039.3
```

The severance outranks the detour by 14× despite affecting 18× fewer people. If
that is not the intended behaviour, the lever is `DISCONNECT_PENALTY` or
`POPULATION_EXPONENT`, not the ranking code.

---

## 6. Demo candidate A — NH66 bridge (detour)

The strongest *bridge* result in the area. **Not in the scored top 20**, for two
separate reasons: its affected-area centroid (13.1043) falls north of the
reporting bbox, and its score (143.8) would place it well below #20 anyway.

```
EDGE
  node IDs    : 7485197680 -> 7485197716
  endpoint A  : 13.096099, 74.787527
  endpoint B  : 13.100421, 74.788311
  name        : NH66
  length      : 488.1 m
  highway     : trunk
  bridge tag  : yes
  endpoint degrees: 3 / 3
  geometry    : 6-point LINESTRING (full coords in report8.txt §5)

AFFECTED AREA  (441 nodes)
  bounding box: lat 13.078357..13.126348   lon 74.766619..74.788174
  hull area   : 7.2164 km2
  centroid    : 13.104277, 74.776700

POPULATION
  value : 12,073.3 people
  source: WorldPop 2020 constrained 100 m gridded estimate
  method: 560 raster cells, coverage=PARTIAL  <- straddles the raster edge, so
                                                 this is an UNDERCOUNT

ROUTING TO CARE   worst-affected node 7497904721 (13.106495, 74.786328)
  BEFORE: Mulki Government Hospital          2.67 km
  AFTER : St. Ann's Chethana Nursing Home    6.58 km
  change: +3.91 km  — re-routes to a DIFFERENT facility

NEAREST NAMED PLACES — LABELS ONLY, none used to detect this impact
  Hejamady (village)        0.57 km
  Hejamady Kodi (village)   1.23 km
  Mulki (town)              1.91 km
```

**Pitch line:** a 488 m national-highway bridge fails; ~12,000 people lose their
nearest hospital and re-route 3.91 km further to a different one.

---

## 7. Demo candidate B — Madhya Padavu bridge (severance)

The only bridge-tagged severance inside the reporting bbox. Ranks **#19 of 69**.

```
EDGE
  node IDs    : 9620418684 -> 9620418686
  endpoint A  : 13.010870, 74.811831
  endpoint B  : 13.012287, 74.807465
  name        : <unnamed>
  length      : 506.9 m
  highway     : unclassified
  bridge tag  : yes
  endpoint degrees: 3 / 3
  geometry    : 17-point LINESTRING, both directions

AFFECTED AREA  (5 nodes)
  node IDs    : 9620418686, 9620418702, 9620418703, 9620418712, 12500511916
  bounding box: lat 13.011746..13.015066   lon 74.806037..74.807610
  hull area   : 0.0344 km2
  centroid    : 13.013189, 74.806888

POPULATION
  value : 26.7 people
  source: WorldPop 2020 constrained 100 m gridded estimate
  method: 2 raster cells, coverage=full

ROUTING TO CARE   worst-affected node 9620418686 (13.012287, 74.807465)
  BEFORE: Padmavathi Hospital, Dakshina Kannada   4.65 km
  AFTER : NO ROUTE to any of the 15 facilities — cut off entirely

NEAREST NAMED PLACES — LABELS ONLY, none inside the pocket
  Madhya Padavu (village)   1.03 km
```

**Pitch line:** a 507 m bridge fails; a pocket of homes 1 km from Madhya Padavu
loses all road access to health care. Clean cut-off story, but only 26.7 people.

---

## 8. The strongest in-bbox result overall

Rank #1, score 2039.3 — and it is **not** a bridge.

```
EDGE          unnamed, 182.0 m, highway=residential, NO bridge tag
              nodes 5269747322 -> 9647824661
              (13.016616, 74.794199) -> (13.017250, 74.795746)
POCKET        24 nodes, hull 0.2783 km2, centroid 13.017537, 74.798508
POPULATION    681.6 people  (29 raster cells, coverage=full)
BEFORE        Health Subcenter, 1.58 km
AFTER         no route to any facility — cut off entirely
LABEL         Mukka (village) 1.07 km away — label only, not inside the pocket
```

---

## 9. Open items and known limits

**Raster re-crop — done.** `crop.py` originally used a 2 km buffer while the
graph uses 3 km, leaving the raster narrower than the graph (three pockets fell
off its edge and the NH66 affected area read `coverage=partial`, an undercount).
`crop.py` now derives its buffer the same way `app/config.py` does and
`app/data/population_100m.tif` has been regenerated from it — its bounds
(`74.7521, 12.9729, 74.8879, 13.1271`) now match the graph's 3 km buffer.
Verified live: both demo pockets report `coverage=full`.

Note: the 3 km re-crop only ever fixed 3 of the 51 `None`s. The other 48 are
nodata *inside* the current footprint and stay `None` correctly — that part of
the finding still holds.

**Bridge tagging is sparse.** Only 3 of 103 surviving pockets sit behind a
bridge-tagged edge, and only 5 of 100 detour spans. Most severances are unnamed
residential streets.

**Simple-graph connectivity.** Parallel carriageways collapse to one edge in the
undirected simple graph used for connectivity, which understates redundancy.
Conservative, not wrong.

**Degenerate hulls.** A 1–2 node pocket hulls to a point or line with zero area;
those are buffered 100 m for the population lookup only. Reported area stays the
raw hull's, and the output flags every case.

**Population is modelled.** Every figure here is WorldPop gridded estimate, not
census. `population/data/settlements.csv` holds hand-entered Census 2011 figures
for 8 villages (Mulki 17274, Haleyangadi 4563, Kilpady 3389, Surinje 3098,
Sasihitlu 2228, Pavanje 1737, Shimanthuru 1607, Padu Panambur 1326) — those are
enumerated counts and should never be mixed with raster estimates in one column.

---

## 10. Files

| File | What it is |
|---|---|
| `severance.py` | The current analysis. Component-based severance, filter, detour sweep, scoring, demo detail. `--no-detour` skips the 140 s sweep. |
| `inspect_area.py` | Generation-2 analysis (settlement clusters, buffered graph). Superseded. |
| `popdata.py` | Verbatim copy of `population/population.py`, so nothing imports across into `population/` at runtime. |
| `surathkal_buffered.graphml` | The 3 km buffered drive network (4974 nodes). |
| `surathkal.graphml` | Original unbuffered network. Reference only — its dead ends are clipping artifacts. |
| `report8.txt` | **Current full output** (2544 lines) — includes full geometry and all 441 node IDs. |
| `report6.txt` | Filter + shortlist, no detour sweep. |
| `report7.txt` | Full run, correct populations, pre-scoring. |
| `report.txt` … `report5.txt` | Earlier generations. **Contain superseded numbers** — `report5.txt` and earlier have the silent-zero population bug. |

### Numbers that are now wrong and should not be quoted

- "10 settlements with a single road approach" (gen 1) — all 10 were bbox clipping artifacts.
- "0 critical bridges" (gen 2) — an artifact of tying severance to OSM place nodes.
- Any population of exactly `0.0` in `report5.txt` or earlier — those are nodata, not measured zeros.
