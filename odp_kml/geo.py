"""Geodesy on a spherical earth plus a local tangent plane for constructions."""

from __future__ import annotations

import dataclasses
import math

EARTH_RADIUS_NM = 3440.065


@dataclasses.dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float


def normalize_bearing(deg: float) -> float:
    """Wrap a bearing in degrees into the range [0, 360)."""
    return deg % 360.0


def magnetic_to_true(magnetic: float, variation_east: float) -> float:
    """Convert a magnetic bearing to true, given easterly variation (positive east)."""
    return normalize_bearing(magnetic + variation_east)


def true_to_magnetic(true: float, variation_east: float) -> float:
    """Convert a true bearing to magnetic, given easterly variation (positive east)."""
    return normalize_bearing(true - variation_east)


def _normalize_longitude(lon: float) -> float:
    """Wrap a longitude in degrees into the range (-180, 180]."""
    return 180.0 - (180.0 - lon) % 360.0


def destination(start: LatLon, bearing_true: float, distance_nm: float) -> LatLon:
    """Solve the spherical direct problem: the point reached from `start`.

    Travels along the given true bearing for `distance_nm` nautical miles.
    """
    angular_distance = distance_nm / EARTH_RADIUS_NM
    lat1 = math.radians(start.lat)
    lon1 = math.radians(start.lon)
    bearing = math.radians(bearing_true)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )

    return LatLon(math.degrees(lat2), _normalize_longitude(math.degrees(lon2)))


def initial_bearing(a: LatLon, b: LatLon) -> float:
    """Compute the true initial bearing along the great circle from `a` to `b`."""
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    delta_lon = math.radians(b.lon - a.lon)

    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        delta_lon
    )
    return normalize_bearing(math.degrees(math.atan2(y, x)))


def distance_nm(a: LatLon, b: LatLon) -> float:
    """Compute the great-circle distance between two points in nautical miles."""
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(b.lon - a.lon)

    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return (
        EARTH_RADIUS_NM * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    )


def heading_to_unit(bearing: float) -> tuple[float, float]:
    """Convert a true bearing to a unit vector (x east, y north) in NM."""
    radians = math.radians(bearing)
    return math.sin(radians), math.cos(radians)


def unit_to_heading(x: float, y: float) -> float:
    """Convert a plane vector (x east, y north) to a normalized true bearing."""
    return normalize_bearing(math.degrees(math.atan2(x, y)))


class LocalPlane:
    """A local tangent plane centered on `origin`, with x east and y north in NM."""

    def __init__(self, origin: LatLon) -> None:
        self.origin = origin
        self._cos_origin_lat = math.cos(math.radians(origin.lat))

    def to_xy(self, p: LatLon) -> tuple[float, float]:
        """Project a lat/lon point onto the plane as (x, y) in nautical miles."""
        delta_lat = p.lat - self.origin.lat
        delta_lon = _wrap_longitude_delta(p.lon - self.origin.lon)

        x = delta_lon * self._cos_origin_lat * 60
        y = delta_lat * 60
        return x, y

    def to_latlon(self, x: float, y: float) -> LatLon:
        """Convert a plane point (x, y) in nautical miles back to lat/lon."""
        delta_lat = y / 60
        delta_lon = x / (60 * self._cos_origin_lat)
        return LatLon(
            self.origin.lat + delta_lat,
            _normalize_longitude(self.origin.lon + delta_lon),
        )


def _wrap_longitude_delta(delta_lon: float) -> float:
    """Wrap a longitude difference in degrees into the range (-180, 180]."""
    return 180.0 - (180.0 - delta_lon) % 360.0
