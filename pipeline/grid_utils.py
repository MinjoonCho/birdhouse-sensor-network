"""DEM 위에 균일 격자를 놓기 위한 공용 헬퍼.

candidates.py(카메라/발화 후보지 생성)와 mountains.py(산 분할) 양쪽에서
같은 격자 정의를 재사용한다.
"""

from __future__ import annotations

import math

from .dem import RegionDEM


def deg_steps(dem: RegionDEM, step_m: float) -> tuple[float, float, float]:
    lon_min, lat_min, lon_max, lat_max = dem.lonlat_bbox()
    mid_lat = (lat_min + lat_max) / 2
    lat_step = step_m / 110540.0
    lon_step = step_m / (111320.0 * math.cos(math.radians(mid_lat)))
    return lon_step, lat_step, mid_lat


def grid_points(dem: RegionDEM, step_m: float) -> list[tuple[float, float]]:
    lon_min, lat_min, lon_max, lat_max = dem.lonlat_bbox()
    lon_step, lat_step, _ = deg_steps(dem, step_m)
    points = []
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            if dem.in_bounds(lon, lat):
                points.append((round(lon, 6), round(lat, 6)))
            lon += lon_step
        lat += lat_step
    return points


def local_prominence(dem: RegionDEM, lon: float, lat: float, elevation: float, radius_m: float) -> float:
    """8방향 이웃 대비 상대적으로 높은 지대인지(0~1)."""
    lon_step, lat_step, _ = deg_steps(dem, radius_m)
    higher = 0
    total = 0
    for dlon, dlat in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
        nlon, nlat = lon + dlon * lon_step, lat + dlat * lat_step
        nz = dem.elevation(nlon, nlat)
        if nz is None:
            continue
        total += 1
        if elevation >= nz:
            higher += 1
    return higher / total if total else 0.0
