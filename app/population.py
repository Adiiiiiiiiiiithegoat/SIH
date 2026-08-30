"""Population for an arbitrary pocket polygon, with its provenance attached.

Census and raster figures are NOT the same measurement and must never be shown
as one number. Every result carries a source:

    "census"  Census 2011 village count, hand-entered in settlements.csv. Used
              when a settlement's own point falls inside the polygon.
    "raster"  WorldPop 2020 constrained 100 m gridded estimate summed over the
              polygon. A model, not an enumeration; it will not match a census
              figure and is not tied to any administrative boundary.
    "missing" No census point inside and no usable raster cells. The value is
              None -- never 0.0.

That last case matters: this raster is ~71% nodata and its smallest valid value
is 6.08, so it contains no valid zero cell at all. Any 0.0 it appeared to
produce was a missing measurement, not an empty area.
"""
import csv
import functools
import threading

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.geometry import Point

from app import config

_RLOCK = threading.Lock()   # rasterio datasets are not reentrant across threads


@functools.lru_cache(maxsize=1)
def settlements():
    """Rows from settlements.csv: name, lat, lon, census_population (or None)."""
    out = []
    with open(config.SETTLEMENTS, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("name") or "").strip():
                continue
            census = (row.get("census_population") or "").strip()
            out.append({"name": row["name"].strip(),
                        "lat": float(row["lat"]), "lon": float(row["lon"]),
                        "census_population": int(float(census)) if census else None})
    return out


@functools.lru_cache(maxsize=1)
def _raster():
    """The open raster dataset, or None if it is absent.

    Opened once and kept -- a severance query hits this for every pocket, and
    reopening a file per pocket was the slowest thing in the loop.
    """
    try:
        return rasterio.open(config.RASTER)
    except (OSError, rasterio.errors.RasterioIOError):
        return None


def _coverage(poly):
    """full / partial / outside -- the raster crop is narrower than the graph."""
    src = _raster()
    if src is None:
        return "no-raster"
    b = src.bounds
    x0, y0, x1, y1 = poly.bounds
    if x1 < b.left or x0 > b.right or y1 < b.bottom or y0 > b.top:
        return "outside"
    if x0 >= b.left and x1 <= b.right and y0 >= b.bottom and y1 <= b.top:
        return "full"
    return "partial"


def _raster_population(poly):
    """(value or None, method, cells, coverage) summed over the polygon."""
    cov = _coverage(poly)
    src = _raster()
    if src is None:
        return None, "no-raster", 0, cov
    if cov == "outside":
        return None, "off-raster", 0, cov
    geom = poly if poly.is_valid else poly.buffer(0)
    with _RLOCK:
        try:
            cut, _ = rio_mask(src, [geom], crop=True, filled=False, all_touched=False)
        except ValueError:
            return None, "no-overlap", 0, cov
        if cut.count() > 0:
            return float(cut.sum()), "cells", int(cut.count()), cov

        # Smaller than a cell, or falling between cell centres. Prorate the cell
        # the centroid sits in -- but only if that cell actually holds data.
        c = geom.centroid
        b = src.bounds
        if not (b.left <= c.x <= b.right and b.bottom <= c.y <= b.top):
            return None, "off-raster", 0, cov
        v = next(src.sample([(c.x, c.y)], masked=True))[0]
        if v is np.ma.masked or np.ma.is_masked(v):
            return None, "nodata-only", 0, cov     # NOT a measured zero
        cell_area = abs(src.transform.a * src.transform.e)
    return float(v) * min(1.0, geom.area / cell_area), "prorated", 1, cov


def population_of(poly):
    """Population inside a pocket polygon.

    Returns ``{"population", "population_source", "method", "cells",
    "coverage", "settlements"}``. ``population`` is None when nothing could be
    measured -- callers must show "unknown", never zero.
    """
    inside = [s for s in settlements() if poly.covers(Point(s["lon"], s["lat"]))]
    census = [s for s in inside if s["census_population"] is not None]
    if census:
        return {"population": float(sum(s["census_population"] for s in census)),
                "population_source": "census", "method": "census-2011",
                "cells": 0, "coverage": _coverage(poly),
                "settlements": [s["name"] for s in census]}

    value, method, cells, cov = _raster_population(poly)
    return {"population": None if value is None else round(value, 1),
            "population_source": "raster" if value is not None else "missing",
            "method": method, "cells": cells, "coverage": cov,
            "settlements": [s["name"] for s in inside]}
