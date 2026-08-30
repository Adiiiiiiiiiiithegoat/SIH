"""All configuration for the backend. Nothing tunable lives outside this file.

Two kinds of value here:

* LOCKED constants -- the priority weights below came out of a parameter sweep.
  They are exposed via GET/POST /api/settings so an operator can see and adjust
  them live, but they must not be re-derived or quietly tuned in code.
* Paths and analysis thresholds validated by the all-edge sweep in
  ../severance.py. Changing those changes what counts as a severance.
"""
import os

# --- paths ----------------------------------------------------------------
APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
DATA = os.path.join(APP, "data")
UPLOADS = os.path.join(DATA, "uploads")

GRAPHML = os.path.join(DATA, "surathkal_buffered.graphml")
RASTER = os.path.join(DATA, "population_100m.tif")
SETTLEMENTS = os.path.join(DATA, "settlements.csv")
REFERENCE_JSON = os.path.join(DATA, "reference.json")   # frozen OSM facilities/places
DB = os.path.join(DATA, "reports.db")

# --- area of interest -----------------------------------------------------
# Report bbox is what the dashboard shows. The graph is buffered 3 km beyond it
# so routes leaving the bbox still resolve; components touching the buffered
# boundary are download artifacts, not real pockets.
REPORT_BBOX = (74.78, 13.00, 74.86, 13.10)          # west, south, east, north
BUFFER_KM = 3.0
_DEG_LAT = BUFFER_KM / 111.0
_DEG_LON = BUFFER_KM / (111.0 * 0.9744)             # cos(13.05 deg)
BUFFERED_BBOX = (REPORT_BBOX[0] - _DEG_LON, REPORT_BBOX[1] - _DEG_LAT,
                 REPORT_BBOX[2] + _DEG_LON, REPORT_BBOX[3] + _DEG_LAT)

# --- severance filter (validated by the all-edge sweep) -------------------
# A pocket must clear ALL FOUR to count as a real severance rather than a
# cul-de-sac losing its own driveway.
MIN_POCKET_NODES = 3
MIN_HULL_KM2 = 0.01
MAX_PRIOR_ACCESS_M = 10_000.0
MIN_ENDPOINT_DEGREE = 3

POCKET_BUFFER_M = 100.0     # widen a degenerate (point/line) hull so it has area
EDGE_TOL_M = 250.0          # this close to the download boundary => clip artifact

# DELIBERATELY 500 m, and deliberately NOT the sweep's 3 km. Do not "reconcile"
# these -- they answer different questions.
#
#   ../severance.py uses 3 km to pick out demo-grade catastrophic detours from
#   ~6000 hypothetical single-edge failures. It carries its own module-level
#   constant and imports the ROOT config.py, which has no detour threshold, so
#   editing this line cannot move the sweep.
#
#   Here the question is what an operator should see. 500 m surfaces meaningful
#   degradation rather than only catastrophe, and the priority score already
#   sorts trivial detours to the bottom by itself: a 500 m detour is 0.86 delay
#   minutes against 300 for a severance, so it cannot crowd out anything real.
#
# Raising this to 3 km would silently hide every moderate detour from the
# dashboard. Same NH66 blockage: 526 nodes affected at 500 m, 441 at 3 km.
DETOUR_THRESHOLD_M = 500.0  # added distance worth reporting as a detour

# --- edge binding ---------------------------------------------------------
DEFAULT_GPS_ACCURACY_M = 25.0   # used when a report carries no accuracy
MIN_BIND_RADIUS_M = 10.0        # never search a radius tighter than this
MAX_BIND_RADIUS_M = 100.0       # ... nor wider than this, however bad the fix
BEARING_WEIGHT = 0.35           # how much a perpendicular camera bearing helps
MAX_CANDIDATES = 8              # candidates recorded for operator override

# --- priority scoring (LOCKED -- derived from a parameter sweep) ----------
# priority = belief * (population ** POPULATION_EXPONENT) * delay_minutes
POPULATION_EXPONENT = 0.4
DISCONNECT_PENALTY = 300      # minutes
UNKNOWN_BELIEF = 0.5
CORROBORATION_WEIGHT = 0.4
CONFIRMATION_BOOST = 1.25
ASSUMED_SPEED_KMH = 35

# belief:
#   impassable -> model confidence
#   unknown    -> UNKNOWN_BELIEF
#   passable   -> 0
#   then * (0.6 + CORROBORATION_WEIGHT * (1 - 0.5 ** n_reports))
#   then * CONFIRMATION_BOOST if status is resolved, capped at 1.0
#
# delay_minutes:
#   severed from all care -> DISCONNECT_PENALTY
#   otherwise             -> (added_km / ASSUMED_SPEED_KMH) * 60

ASSUMED_POCKET_POPULATION = 50.0   # stand-in when the raster has no data there

# --- detection ------------------------------------------------------------
DETECTION_MODE = "model"      # api | model | manual -- live-adjustable
STUB_CONFIDENCE = 0.72        # placeholder until a real detector plugs in

# Keys a client may change through POST /api/settings.
MUTABLE = ("DETECTION_MODE", "POPULATION_EXPONENT", "DISCONNECT_PENALTY",
           "UNKNOWN_BELIEF", "CORROBORATION_WEIGHT", "CONFIRMATION_BOOST",
           "ASSUMED_SPEED_KMH", "DEFAULT_GPS_ACCURACY_M", "DETOUR_THRESHOLD_M",
           "MIN_POCKET_NODES", "MIN_HULL_KM2", "MAX_PRIOR_ACCESS_M",
           "MIN_ENDPOINT_DEGREE")
