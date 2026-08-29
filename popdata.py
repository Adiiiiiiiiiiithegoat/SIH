"""Population lookup for the coastal Karnataka AOI.

Two independent sources, census first:

1. ``data/settlements.csv`` -- manually entered Census 2011 figures. When a
   settlement has a ``census_population`` value it is used directly and the
   raster is never opened. The module loads and works with no raster present,
   so the whole system can run on manually entered figures alone.
2. ``data/population_100m.tif`` -- WorldPop 2020 constrained 100m population
   count, cropped to the AOI. Used only when a settlement has no census figure,
   and for arbitrary geometry / radius lookups.

Standalone: depends only on rasterio, geopandas, shapely, numpy and the two
files in data/. No imports from the rest of the project.

    from population import settlement_population, population_within_radius, population_in

Coordinate convention: coordinate pairs are (lon, lat) -- x first, GIS order.
Helpers taking scalars (population_within_radius, settlements) take lat, lon.
"""

import csv
import logging
import os

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.geometry import Point, Polygon, shape
from shapely.geometry.base import BaseGeometry

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RASTER = os.path.join(DATA, "population_100m.tif")
SETTLEMENTS = os.path.join(DATA, "settlements.csv")


def raster_available():
    """True if the cropped WorldPop raster is on disk. Import never requires it."""
    return os.path.exists(RASTER)


def _open():
    if not raster_available():
        raise FileNotFoundError(
            f"population raster not found at {RASTER}. Census figures in "
            f"{SETTLEMENTS} still work without it, but raster sums do not. "
            f"Run crop.py to regenerate it from the WorldPop national file."
        )
    # ponytail: reopened per call; cache the handle if this ever gets hot.
    return rasterio.open(RASTER)


def _as_geom(region):
    """Accept a shapely geometry, a GeoJSON-ish mapping, or a list of (lon, lat)."""
    if isinstance(region, BaseGeometry):
        return region
    if isinstance(region, dict):
        return shape(region)
    coords = list(region)
    if len(coords) < 3:
        raise ValueError("need a shapely geometry or >=3 (lon, lat) coordinates")
    return Polygon(coords)


def bbox_polygon(west, south, east, north):
    return Polygon([(west, south), (east, south), (east, north), (west, north)])


def population_in(region):
    """Total population inside `region`, by summing raster cells. Returns a float.

    Requires the raster -- raises FileNotFoundError if it is absent rather than
    returning a silent zero. Empty/tiny/off-raster regions return 0.0 or a
    prorated share of the single cell they sit in, without crashing.
    """
    geom = _as_geom(region)
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty or geom.area == 0:
        return 0.0

    with _open() as src:
        if src.crs and src.crs.to_epsg() != 4326:
            geom = gpd.GeoSeries([geom], crs=4326).to_crs(src.crs).iloc[0]
        cell_area = abs(src.transform.a * src.transform.e)
        try:
            arr, _ = rio_mask(src, [geom], crop=True, filled=False, all_touched=False)
        except ValueError:
            return 0.0  # geometry does not overlap the raster at all

        if arr.count() > 0:
            return float(arr.sum())  # guarded: sum of a fully-masked array is nan

        # No cells covered -- region is smaller than a cell, fell between cell
        # centres, or sits entirely on nodata. Prorate the containing cell by
        # area so tiny polygons don't silently read zero.
        c = geom.centroid
        if not (src.bounds.left <= c.x <= src.bounds.right
                and src.bounds.bottom <= c.y <= src.bounds.top):
            return 0.0
        value = next(src.sample([(c.x, c.y)], masked=True))[0]
        if value is np.ma.masked:
            return 0.0
        return float(value) * min(1.0, geom.area / cell_area)


def population_within_radius(lat, lon, radius_m):
    """Population within `radius_m` metres of a centre point.

    For disconnected regions that don't correspond to a named settlement.
    Requires the raster: raises FileNotFoundError if it is absent, rather than
    returning a silent zero.
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if not raster_available():
        raise FileNotFoundError(
            f"radius lookup needs the population raster, but {RASTER} is missing. "
            f"Run crop.py to regenerate it."
        )
    gs = gpd.GeoSeries([Point(lon, lat)], crs=4326)
    circle = gs.to_crs(gs.estimate_utm_crs()).buffer(radius_m).to_crs(4326).iloc[0]
    return population_in(circle)


population_near = population_within_radius  # earlier name, kept as an alias


def load_settlements(path=SETTLEMENTS):
    """Rows from settlements.csv as dicts: name, lat, lon, census_population.

    Pure CSV read -- never touches the raster.
    """
    if not os.path.exists(path):
        log.warning("settlements file not found at %s", path)
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("name") or "").strip():
                continue
            census = (row.get("census_population") or "").strip()
            out.append({
                "name": row["name"].strip(),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "census_population": int(float(census)) if census else None,
            })
    return out


def settlement_population(name, radius_m=1000, settlements=None):
    """Population for a named settlement. Returns ``(value, source)``.

    Census figure wins outright -- when present the raster is never consulted,
    so this works with no raster file on disk. Falls back to summing the raster
    around the settlement only when there is no census figure and the raster is
    present. When neither is available the value is ``None`` (source
    ``"missing"``) and the settlement is logged, rather than raising.

    ``source`` says where the number came from, and a UI **must** show it --
    the two are not interchangeable and should never be presented as one figure:

    * ``"census"``  -- Census 2011 village-level data, hand-entered in
      settlements.csv from the Mangalore taluk village directory. An official
      enumerated count for a village unit. Label it as Census 2011.
    * ``"raster"``  -- a WorldPop gridded estimate: modelled 100m population
      counts summed over a circle of `radius_m` around the settlement's point.
      Not an enumerated count and not tied to any administrative boundary, so
      it will not match a census figure. Label it as an estimate.
    * ``"missing"`` -- no census figure and no raster; ``value`` is None.
      Show "unknown", not zero.

    Raises KeyError only if `name` is not in settlements.csv at all.
    """
    for s in (settlements if settlements is not None else load_settlements()):
        if s["name"].lower() == name.lower():
            if s["census_population"] is not None:
                return s["census_population"], "census"
            if not raster_available():
                log.warning(
                    "no data for settlement %r: census_population is blank in %s "
                    "and the raster %s is missing", s["name"], SETTLEMENTS, RASTER)
                return None, "missing"
            return population_within_radius(s["lat"], s["lon"], radius_m), "raster"
    raise KeyError(f"no settlement named {name!r} in {SETTLEMENTS}")


def all_settlement_populations(radius_m=1000):
    """``{name: (value, source)}`` for every row in settlements.csv."""
    rows = load_settlements()
    return {s["name"]: settlement_population(s["name"], radius_m, rows) for s in rows}


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    BBOX = (74.78, 13.00, 74.86, 13.10)  # west, south, east, north

    rows = load_settlements()
    assert rows, "settlements.csv should not be empty"
    seeded = [dict(rows[0], census_population=12345)]
    assert settlement_population(rows[0]["name"], settlements=seeded) == (12345, "census"), \
        "census figure must win over the raster"
    try:
        settlement_population("Nowhere At All", settlements=rows)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown settlement should raise KeyError")

    if not raster_available():
        print(f"raster absent ({RASTER}) -- census-only mode")
        print()
        for name, (value, source) in all_settlement_populations().items():
            shown = "unknown" if value is None else f"{value:,.0f}"
            print(f"  {name:<16} {shown:>10}  [{source}]")
        print()
        print("self-check ok (census-only path)")
        raise SystemExit(0)

    assert population_in(bbox_polygon(*BBOX)) > 0, "bbox should hold people"
    assert population_in(bbox_polygon(0.0, 0.0, 0.1, 0.1)) == 0.0, "off-raster -> 0"
    assert population_in(bbox_polygon(74.80, 13.05, 74.80001, 13.05001)) >= 0.0, "tiny -> no crash"
    assert population_within_radius(13.05, 74.82, 1000) >= 0.0, "radius -> no crash"
    try:
        population_in([(74.8, 13.0)])
    except ValueError:
        pass
    else:
        raise AssertionError("too few coords should raise")
    print("self-check ok")
    print()

    print(f"AOI bbox {BBOX}")
    print(f"  total population: {population_in(bbox_polygon(*BBOX)):,.0f}")
    print()

    print("Population per settlement (census if present, else raster within 1000 m):")
    for s in rows:
        value, source = settlement_population(s["name"], radius_m=1000, settlements=rows)
        shown = "unknown" if value is None else f"{value:,.0f}"
        print(f"  {s['name']:<16} ({s['lat']:.5f}, {s['lon']:.5f})  {shown:>10}  [{source}]")
