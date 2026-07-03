"""발화 후보지(6.1) 생성.

DEM(경사·사면방향)과 임도 근접도를 기반으로 한 규칙 기반 생성이다. 개별 과거
산불의 정확한 좌표는 API가 제공하지 않으므로, 산림청 통계는 지역·계절 위험
가중치로만 반영한다(fire_history.region_summary).

카메라 후보지는 더 이상 이 모듈의 휴리스틱 점수로 만들지 않는다 -
`mountains.py`/`fire_graph.py`/`camera_placement.py`의 그래프 기반
k-center 최적화(`mountainCoverage`)가 이를 대체한다.
"""

from __future__ import annotations

import math

from .dem import RegionDEM
from .grid_utils import grid_points
from .sources import trails

GRID_STEP_M = 450.0


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
    points = grid_points(dem, GRID_STEP_M)
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
