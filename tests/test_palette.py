"""Tests for odp_kml.palette: neighbouring airports get different colors."""

from odp_kml.geo import LatLon
from odp_kml.palette import PALETTE_SIZE, assign_palettes
from odp_kml.shapes import AirportDrawing


def drawing(lid: str, lat: float, lon: float) -> AirportDrawing:
    return AirportDrawing(lid, lid, (), position=LatLon(lat, lon))


def test_airports_within_the_radius_get_distinct_palettes():
    close = [drawing(f"A{i}", 38.0, -117.0 + i * 0.2) for i in range(4)]
    coloured = assign_palettes(close, radius_nm=40)
    assert len({d.palette for d in coloured}) == 4


def test_distant_airports_may_reuse_a_palette():
    far = [drawing("AAA", 38.0, -117.0), drawing("BBB", 45.0, -90.0)]
    coloured = assign_palettes(far, radius_nm=40)
    assert [d.palette for d in coloured] == [0, 0]


def test_palettes_stay_within_the_palette_size():
    crowd = [drawing(f"C{i:02d}", 38.0 + i * 0.05, -117.0) for i in range(12)]
    assert all(
        0 <= d.palette < PALETTE_SIZE for d in assign_palettes(crowd, radius_nm=40)
    )
