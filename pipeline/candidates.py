"""발화 후보지(6.1) / 카메라 후보지(6.4) 생성.

DEM(경사·사면방향·능선 근접성)과 임도/등산로 근접도를 기반으로 한 규칙 기반
생성이다. 개별 과거 산불의 정확한 좌표는 API가 제공하지 않으므로, 산림청
통계는 지역·계절 위험 가중치로만 반영한다(fire_history.region_summary).
"""

from __future__ import annotations

import math

from .dem import RegionDEM
from .sources import trails

GRID_STEP_M = 450.0
CAMERA_GRID_STEP_M = 220.0
NEIGHBOR_RADIUS_M = 400.0
CAMERA_PROMINENCE_MIN = 0.55  # 짧은 탐지 거리에서는 압도적 봉우리가 아니어도 발화지 근처면 유효하다
# 센서 실측 탐지 거리가 500m~1km라서, 그 범위가 서로 이어지도록 후보 간격을
# 좁히고 개수를 크게 늘린다(기존 150개는 봉우리 전망 위주 카메라 가정 때 값).
CAMERA_SHORTLIST_CAP = 1400
CAMERA_DEDUPE_MIN_DIST_M = 280.0


def _deg_steps(dem: RegionDEM, step_m: float) -> tuple[float, float, float]:
    lon_min, lat_min, lon_max, lat_max = dem.lonlat_bbox()
    mid_lat = (lat_min + lat_max) / 2
    lat_step = step_m / 110540.0
    lon_step = step_m / (111320.0 * math.cos(math.radians(mid_lat)))
    return lon_step, lat_step, mid_lat


def _grid_points(dem: RegionDEM, step_m: float) -> list[tuple[float, float]]:
    lon_min, lat_min, lon_max, lat_max = dem.lonlat_bbox()
    lon_step, lat_step, _ = _deg_steps(dem, step_m)
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


def _southness(aspect_deg: float) -> float:
    """사면방향이 남향(180도)에 가까울수록 1, 북향이면 0."""
    return (math.cos(math.radians(aspect_deg - 180)) + 1) / 2


def _slope_suitability(slope_deg: float) -> float:
    """산불 확산에 유리한 경사(15~35도)에서 최대, 너무 평탄/급경사면 감소."""
    if slope_deg <= 0:
        return 0.2
    if slope_deg < 15:
        return 0.2 + 0.8 * (slope_deg / 15)
    if slope_deg <= 35:
        return 1.0
    return max(0.2, 1.0 - (slope_deg - 35) / 40)


def _local_prominence(dem: RegionDEM, lon: float, lat: float, elevation: float, radius_m: float) -> float:
    """8방향 이웃 대비 상대적으로 높은 지대인지(0~1)."""
    lon_step, lat_step, _ = _deg_steps(dem, radius_m)
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


def _dedupe_by_distance(items: list[dict], min_dist_m: float) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        too_close = False
        for k in kept:
            d = math.hypot((item["lon"] - k["lon"]) * 88000, (item["lat"] - k["lat"]) * 110540)
            if d < min_dist_m:
                too_close = True
                break
        if not too_close:
            kept.append(item)
    return kept


def generate_ignition_candidates(dem: RegionDEM, fire_summary: dict, top_n: int = 40) -> list[dict]:
    points = _grid_points(dem, GRID_STEP_M)
    season_counts = fire_summary.get("bySeason", {})
    total_season = sum(season_counts.values()) or 1
    spring_weight = 0.7 + 0.6 * (season_counts.get("봄", 0) / total_season)

    prelim = []
    for lon, lat in points:
        elevation = dem.elevation(lon, lat)
        slope, aspect = dem.slope_aspect(lon, lat)
        if elevation is None or slope is None:
            continue
        base = 0.55 * _southness(aspect) + 0.45 * _slope_suitability(slope)
        prelim.append({"lon": lon, "lat": lat, "elevation": elevation, "slope": slope,
                        "aspect": aspect, "base": base})

    prelim.sort(key=lambda p: p["base"], reverse=True)
    shortlist = prelim[: top_n * 5]

    for p in shortlist:
        dist = trails.nearest_trail_distance_m(dem.region_key, p["lon"], p["lat"])
        p["trailDistanceM"] = dist
        proximity = 0.0 if dist is None else max(0.0, 1 - dist / 1500.0)
        risk = (0.45 * p["base"] + 0.30 * proximity + 0.25 * 0.5) * spring_weight
        p["riskScore"] = round(min(100.0, risk * 100), 1)

    shortlist.sort(key=lambda p: p["riskScore"], reverse=True)
    deduped = _dedupe_by_distance(shortlist, min_dist_m=700.0)[:top_n]

    candidates = []
    for i, p in enumerate(deduped):
        south_facing = p["aspect"] is not None and 120 <= p["aspect"] <= 240
        near_trail = p["trailDistanceM"] is not None and p["trailDistanceM"] <= 500
        risk_type = []
        if south_facing:
            risk_type.append("남사면 건조지")
        if near_trail:
            risk_type.append("임도·생활권 인접 산림")
        if p["slope"] and 15 <= p["slope"] <= 35:
            risk_type.append("확산 유리 경사")
        candidates.append({
            "id": f"ig-{i+1:03d}",
            "lon": p["lon"], "lat": p["lat"],
            "elevation": round(p["elevation"], 1),
            "slopeDeg": round(p["slope"], 1),
            "aspectDeg": round(p["aspect"], 1),
            "trailDistanceM": p["trailDistanceM"],
            "riskScore": p["riskScore"],
            "riskType": risk_type or ["일반 산림"],
        })
    return candidates


CAMERA_IGNITION_PROXIMITY_CAP_M = 2500.0  # 이 거리를 넘으면 근접도 가점 0


def generate_camera_candidates(dem: RegionDEM, ignition_candidates: list[dict], top_n: int = 450) -> list[dict]:
    """센서 실측 탐지 거리가 500m~1km이므로, "얼마나 두드러진 봉우리인가"만으로
    고르면 정작 발화 후보지 근처에는 센서가 하나도 없는 경우가 생긴다. 그래서
    1차 선별 단계부터 두드러짐과 발화 후보지 근접도를 함께 반영해, 조금 낮은
    언덕이라도 실제 발화 위험 지역 근처에 있으면 살아남도록 한다. 지역 전체
    분포(어느 한쪽에 몰리지 않게)는 이후 scoring.py의 지리적 다양화 단계에서
    별도로 보장한다.
    """
    points = _grid_points(dem, CAMERA_GRID_STEP_M)
    prelim = []
    for lon, lat in points:
        elevation = dem.elevation(lon, lat)
        if elevation is None:
            continue
        prominence = _local_prominence(dem, lon, lat, elevation, NEIGHBOR_RADIUS_M)
        if prominence < CAMERA_PROMINENCE_MIN:
            continue
        nearest_ignition_m = min(
            (_haversine_m(lon, lat, ig["lon"], ig["lat"]) for ig in ignition_candidates),
            default=None,
        )
        proximity = max(0.0, 1 - (nearest_ignition_m or 1e9) / CAMERA_IGNITION_PROXIMITY_CAP_M)
        combined = 0.5 * prominence + 0.5 * proximity
        prelim.append({"lon": lon, "lat": lat, "elevation": elevation, "prominence": prominence,
                        "nearestIgnitionM": nearest_ignition_m, "combined": combined})

    # 두드러짐과 발화 후보지 근접도를 함께 봐서 1차 후보군을 추린다.
    prelim.sort(key=lambda p: p["combined"], reverse=True)
    shortlist = prelim[:CAMERA_SHORTLIST_CAP]

    for p in shortlist:
        dist = trails.nearest_trail_distance_m(dem.region_key, p["lon"], p["lat"])
        p["trailDistanceM"] = dist
        proximity_score = max(0.0, 1 - (p["nearestIgnitionM"] or 1e9) / 2000.0) * 50
        access_score = max(0.0, 1 - (dist or 1e9) / 1500.0) * 25
        p["prefilterScore"] = proximity_score + access_score + p["prominence"] * 25

    shortlist.sort(key=lambda p: p["prefilterScore"], reverse=True)
    deduped = _dedupe_by_distance(shortlist, min_dist_m=CAMERA_DEDUPE_MIN_DIST_M)[:top_n]

    candidates = []
    for i, p in enumerate(deduped):
        candidates.append({
            "id": f"cam-{i+1:03d}",
            "lon": p["lon"], "lat": p["lat"],
            "elevation": round(p["elevation"], 1),
            "prominence": round(p["prominence"], 2),
            "trailDistanceM": p["trailDistanceM"],
            "nearestIgnitionM": round(p["nearestIgnitionM"], 1) if p["nearestIgnitionM"] else None,
        })
    return candidates


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
