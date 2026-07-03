"""Transverse Mercator 역변환 (GRS80), 파라미터화된 공용 버전.

k-fireguard-wildfire-atlas/shp_to_geojson.py 의 EPSG:5179 전용 tm_inverse를
lon0/lat0/FE/FN/k0 파라미터를 받도록 일반화해, DEM(EPSG:5186)과
등산로 shapefile(EPSG:5179)에 동일한 함수를 재사용한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

A = 6378137.0
INV_F = 298.257222101  # GRS80


@dataclass(frozen=True)
class TMParams:
    lon0_deg: float
    lat0_deg: float
    fe: float
    fn: float
    k0: float = 1.0
    ellipsoid: str = "GRS80"


def _meridian_arc(lat: float, a: float, e2: float) -> float:
    e4 = e2 * e2
    e6 = e4 * e2
    return a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
        - (35 * e6 / 3072) * math.sin(6 * lat)
    )


def tm_inverse(easting: float, northing: float, params: TMParams) -> tuple[float, float]:
    """TM(easting, northing) → (lon, lat) degrees, WGS84 근사(수 m 이내 오차)."""
    lat0 = math.radians(params.lat0_deg)
    lon0 = math.radians(params.lon0_deg)
    fe, fn, k0 = params.fe, params.fn, params.k0

    f = 1.0 / INV_F
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    m0 = _meridian_arc(lat0, A, e2)
    m = m0 + (northing - fn) / k0
    mu = m / (A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )

    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    tan_phi = math.tan(phi)
    c1 = ep2 * cos_phi ** 2
    t1 = tan_phi ** 2
    n1 = A / math.sqrt(1 - e2 * sin_phi ** 2)
    r1 = A * (1 - e2) / (1 - e2 * sin_phi ** 2) ** 1.5
    d = (easting - fe) / (n1 * k0)

    lat = phi - (n1 * tan_phi / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos_phi

    return math.degrees(lon), math.degrees(lat)


def tm_forward(lon_deg: float, lat_deg: float, params: TMParams) -> tuple[float, float]:
    """(lon, lat) degrees → TM(easting, northing). DEM 좌표계 검증용."""
    lat0 = math.radians(params.lat0_deg)
    lon0 = math.radians(params.lon0_deg)
    fe, fn, k0 = params.fe, params.fn, params.k0
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    f = 1.0 / INV_F
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    sin_phi = math.sin(lat)
    cos_phi = math.cos(lat)
    tan_phi = math.tan(lat)
    n1 = A / math.sqrt(1 - e2 * sin_phi ** 2)
    t1 = tan_phi ** 2
    c1 = ep2 * cos_phi ** 2
    a1 = (lon - lon0) * cos_phi
    m = _meridian_arc(lat, A, e2)
    m0 = _meridian_arc(lat0, A, e2)

    easting = fe + k0 * n1 * (
        a1
        + (1 - t1 + c1) * a1 ** 3 / 6
        + (5 - 18 * t1 + t1 ** 2 + 72 * c1 - 58 * ep2) * a1 ** 5 / 120
    )
    northing = fn + k0 * (
        (m - m0)
        + n1 * tan_phi * (
            a1 ** 2 / 2
            + (5 - t1 + 9 * c1 + 4 * c1 ** 2) * a1 ** 4 / 24
            + (61 - 58 * t1 + t1 ** 2 + 600 * c1 - 330 * ep2) * a1 ** 6 / 720
        )
    )
    return easting, northing


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
