"""산림청 등산로/임도 shapefile에서 의성/봉화 구간만 추출.

MNTN_CODE 앞 5자리가 법정동 시군구 코드(의성군=47730, 봉화군=47920)와
일치하는 라인만 골라 재사용한다. 유지보수 접근성(도로/임도 근접도) 계산의
1차 자료로 쓰고, 못 찾으면 VWorld 도로 레이어로 보완한다.
"""

from __future__ import annotations

import math
from pathlib import Path

from .. import config
from ..projection import TMParams, tm_inverse
from .. import shapefile as shp

GUNGU_CODE = {"uiseong": "47730", "bonghwa": "47920"}
CANDIDATE_ZIPS = ["27.zip", "47.zip", "51.zip"]
TRAIL_PARAMS = TMParams(**config.TRAIL_TM_PARAMS)


def _load_region_lines(region_key: str) -> list[list[tuple[float, float]]]:
    code = GUNGU_CODE[region_key]
    lines: list[list[tuple[float, float]]] = []
    for name in CANDIDATE_ZIPS:
        zip_path = config.TRAIL_ROOT / name
        if not zip_path.exists():
            continue
        geoms, records = shp.load_shapefile_from_zip(zip_path)
        for parts, record in zip(geoms, records):
            if record.get("MNTN_CODE", "")[:5] != code:
                continue
            for ring in parts:
                wgs_line = [tm_inverse(x, y, TRAIL_PARAMS) for x, y in ring]
                if len(wgs_line) >= 2:
                    lines.append(wgs_line)
    return lines


_CACHE: dict[str, list[list[tuple[float, float]]]] = {}


def region_trail_lines(region_key: str) -> list[list[tuple[float, float]]]:
    if region_key not in _CACHE:
        _CACHE[region_key] = _load_region_lines(region_key)
    return _CACHE[region_key]


def _point_to_segment_m(lon, lat, lon1, lat1, lon2, lat2) -> float:
    ref_lat = math.radians((lat + lat1 + lat2) / 3)
    scale_x = 111320 * math.cos(ref_lat)
    scale_y = 110540
    px, py = lon * scale_x, lat * scale_y
    ax, ay = lon1 * scale_x, lat1 * scale_y
    bx, by = lon2 * scale_x, lat2 * scale_y
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_trail_distance_m(region_key: str, lon: float, lat: float) -> float | None:
    lines = region_trail_lines(region_key)
    best = math.inf
    for line in lines:
        for i in range(len(line) - 1):
            (lon1, lat1), (lon2, lat2) = line[i], line[i + 1]
            d = _point_to_segment_m(lon, lat, lon1, lat1, lon2, lat2)
            if d < best:
                best = d
    return round(best, 1) if math.isfinite(best) else None
