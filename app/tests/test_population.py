"""Population provenance. Census and raster are never the same measurement.

The raster is ~71% nodata and its smallest valid value is 6.08 -- it holds no
valid zero cell at all. So any 0.0 it appears to produce is a missing
measurement, and reporting it as zero people would silently bury a real pocket.
"""
import pytest
from shapely.geometry import Polygon

from app import population


def box(west, south, east, north):
    return Polygon([(west, south), (east, south), (east, north), (west, north)])


def test_settlements_load_with_and_without_census_figures():
    rows = population.settlements()
    assert len(rows) == 16
    with_census = [s for s in rows if s["census_population"] is not None]
    assert len(with_census) == 8, "8 of the 16 settlements have Census 2011 figures"


def test_a_polygon_over_a_census_settlement_is_sourced_as_census():
    mulki = next(s for s in population.settlements() if s["name"] == "Mulki")
    d = 0.002
    result = population.population_of(
        box(mulki["lon"] - d, mulki["lat"] - d, mulki["lon"] + d, mulki["lat"] + d))
    assert result["population_source"] == "census"
    assert result["population"] == 17274.0
    assert result["settlements"] == ["Mulki"]


def test_census_wins_outright_and_is_never_mixed_with_the_raster():
    """The two figures measure different things. A polygon containing a census
    settlement returns the enumerated count, not a blend."""
    mulki = next(s for s in population.settlements() if s["name"] == "Mulki")
    result = population.population_of(
        box(mulki["lon"] - 0.004, mulki["lat"] - 0.004,
            mulki["lon"] + 0.004, mulki["lat"] + 0.004))
    assert result["population_source"] == "census"
    assert result["method"] == "census-2011"
    assert result["cells"] == 0, "the raster must not be consulted at all"


def test_a_settlement_without_a_census_figure_falls_through_to_the_raster():
    """Chitrapu has a blank census_population -- it must not be reported as a
    census figure, and must not be reported as zero."""
    chitrapu = next(s for s in population.settlements() if s["name"] == "Chitrapu")
    assert chitrapu["census_population"] is None
    d = 0.004
    result = population.population_of(
        box(chitrapu["lon"] - d, chitrapu["lat"] - d,
            chitrapu["lon"] + d, chitrapu["lat"] + d))
    assert result["population_source"] == "raster"
    assert result["population"] > 0


def test_a_polygon_off_the_raster_returns_none_never_zero():
    """The critical case: outside the raster footprint is 'we never measured
    here', not 'nobody lives here'."""
    result = population.population_of(box(74.00, 13.00, 74.01, 13.01))
    assert result["population"] is None
    assert result["population"] != 0.0
    assert result["population_source"] == "missing"
    assert result["method"] == "off-raster"


def test_source_is_always_one_of_the_three_declared_values():
    for poly in (box(74.80, 13.02, 74.81, 13.03),
                 box(74.00, 13.00, 74.01, 13.01),
                 box(74.7871 - 1e-4, 13.0904 - 1e-4, 74.7871 + 1e-4, 13.0904 + 1e-4)):
        assert population.population_of(poly)["population_source"] in (
            "census", "raster", "missing")


def test_a_missing_value_is_never_reported_with_a_source_of_raster():
    """population_source must not claim a measurement that does not exist."""
    result = population.population_of(box(74.00, 13.00, 74.01, 13.01))
    assert not (result["population"] is None and result["population_source"] == "raster")


def test_coverage_is_reported_so_a_partial_sum_is_not_read_as_complete():
    inside = population.population_of(box(74.80, 13.02, 74.81, 13.03))
    outside = population.population_of(box(74.00, 13.00, 74.01, 13.01))
    assert inside["coverage"] == "full"
    assert outside["coverage"] == "outside"


def test_a_polygon_smaller_than_a_raster_cell_does_not_read_as_empty():
    """A degenerate pocket hull buffered to 100 m can still be sub-cell. It must
    prorate the containing cell or say 'missing' -- never invent a zero."""
    result = population.population_of(box(74.8000, 13.0200, 74.80005, 13.02005))
    assert result["population"] is None or result["population"] > 0
    assert result["method"] in ("cells", "prorated", "nodata-only")


def test_pockets_carry_their_source_through_to_the_severance_result(network):
    from app import severance
    from app.network import edge_id
    e = edge_id(network.nearest_node(13.01087, 74.81183),
                network.nearest_node(13.01229, 74.80747))
    pocket = severance.analyse([e])["pockets"][0]
    assert pocket["population_source"] == "raster"
    assert pocket["population"] == pytest.approx(26.7, abs=0.1)
