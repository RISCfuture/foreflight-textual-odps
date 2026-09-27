"""Tests for odp_kml.geo geodesy and local tangent plane utilities."""

import math

import pytest

from odp_kml.geo import (
    LatLon,
    LocalPlane,
    destination,
    distance_nm,
    heading_to_unit,
    initial_bearing,
    magnetic_to_true,
    normalize_bearing,
    true_to_magnetic,
    unit_to_heading,
)


def test_normalize_bearing_wraps_into_0_360():
    assert normalize_bearing(0) == pytest.approx(0)
    assert normalize_bearing(360) == pytest.approx(0)
    assert normalize_bearing(370) == pytest.approx(10)
    assert normalize_bearing(-10) == pytest.approx(350)


def test_magnetic_to_true_adds_easterly_variation():
    assert magnetic_to_true(123, 15) == pytest.approx(138)


def test_true_to_magnetic_subtracts_easterly_variation():
    assert true_to_magnetic(138, 15) == pytest.approx(123)


def test_magnetic_to_true_wraps_at_360():
    assert magnetic_to_true(350, 15) == pytest.approx(5)


def test_destination_60nm_due_north():
    start = LatLon(37.0, -122.0)
    end = destination(start, bearing_true=0.0, distance_nm=60.0)
    assert end.lat == pytest.approx(38.0, abs=0.01)
    assert end.lon == pytest.approx(-122.0, abs=0.01)


def test_initial_bearing_and_distance_ksfo_to_klax():
    ksfo = LatLon(37.6188, -122.3750)
    klax = LatLon(33.9425, -118.4081)
    assert distance_nm(ksfo, klax) == pytest.approx(293, abs=1)
    assert initial_bearing(ksfo, klax) == pytest.approx(137, abs=1)


def test_local_plane_round_trip_within_tolerance():
    origin = LatLon(37.0, -122.0)
    plane = LocalPlane(origin)
    far_point = destination(origin, bearing_true=45.0, distance_nm=30.0)

    x, y = plane.to_xy(far_point)
    round_tripped = plane.to_latlon(x, y)

    assert round_tripped.lat == pytest.approx(far_point.lat, abs=1e-6)
    assert round_tripped.lon == pytest.approx(far_point.lon, abs=1e-6)


def test_local_plane_xy_signs_for_point_one_nm_east():
    origin = LatLon(37.0, -122.0)
    plane = LocalPlane(origin)
    one_nm_of_lon = 1.0 / (60 * math.cos(math.radians(origin.lat)))
    east_point = LatLon(origin.lat, origin.lon + one_nm_of_lon)

    x, y = plane.to_xy(east_point)

    assert x == pytest.approx(1.0, abs=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)


def test_local_plane_wraps_longitude_delta():
    origin = LatLon(0.0, -179.0)
    plane = LocalPlane(origin)
    point = LatLon(0.0, 180.0)  # raw delta is 359 degrees, wraps to -1

    x, y = plane.to_xy(point)

    assert x == pytest.approx(-60.0, abs=1e-9)
    assert y == pytest.approx(0.0, abs=1e-9)


def test_heading_to_unit_east_is_positive_x():
    x, y = heading_to_unit(90)
    assert (x, y) == pytest.approx((1.0, 0.0), abs=1e-9)


def test_unit_to_heading_south_is_180():
    assert unit_to_heading(0.0, -1.0) == pytest.approx(180.0)
