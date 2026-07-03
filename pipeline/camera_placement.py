"""산마다 노드 전량 Dijkstra + greedy k-center로 최소 카메라 배치를 구한다.

k-center(최소 최대 거리 시설 배치) 문제의 표준 2-근사 해법인 greedy
farthest-point 알고리즘을 쓴다: 매번 "현재 카메라 배치 기준 가장 늦게
탐지되는 최악의 노드"에 새 카메라를 놓고, 목표 시간 이하로 떨어지거나
산당 카메라 수 상한에 닿으면 멈춘다.
"""

from __future__ import annotations

import math

from .fire_graph import FireGraph, dijkstra
from .projection import haversine_m

SENSOR_RANGE_M = 1000.0  # 새집형 센서 실측 탐지 거리 상한(500m~1km)
TARGET_WORST_CASE_MIN = 20.0
MAX_CAMERAS_PER_MOUNTAIN = 6


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. 지도에 산 경계를 그리기 위한 근사 다각형."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _coverage_map(graph: FireGraph, node_indices: list[int]) -> dict[int, list[int]]:
    """각 노드가 카메라로 놓였을 때 실거리 SENSOR_RANGE_M 이내로 커버하는 노드 목록."""
    coverage: dict[int, list[int]] = {}
    for c in node_indices:
        lon_c, lat_c = graph.coords[c]
        covered = [
            u for u in node_indices
            if haversine_m(lon_c, lat_c, graph.coords[u][0], graph.coords[u][1]) <= SENSOR_RANGE_M
        ]
        coverage[c] = covered
    return coverage


def _all_pairs_time(graph: FireGraph, node_indices: list[int]) -> dict[int, dict[int, float]]:
    allowed = set(node_indices)
    return {v: dijkstra(graph, v, allowed) for v in node_indices}


def place_cameras_for_mountain(graph: FireGraph, mountain: dict) -> dict:
    """한 산에 대해 greedy k-center를 돌려 순서 있는 카메라 추천 목록을 만든다."""
    node_indices = mountain["nodeIndices"]
    dist_matrix = _all_pairs_time(graph, node_indices)
    coverage = _coverage_map(graph, node_indices)

    remaining_time = {v: math.inf for v in node_indices}
    chosen: list[int] = []
    history = []

    # 첫 카메라는 이 산의 최고점(seed)에서 가장 가까운 그래프 노드로 시작한다
    # (모든 노드가 동률로 무한대일 때 임의 선택 대신 직관적인 지점을 쓴다).
    seed_lon, seed_lat = mountain["seed"]["lon"], mountain["seed"]["lat"]
    first_pick = min(node_indices, key=lambda v: haversine_m(seed_lon, seed_lat, *graph.coords[v]))

    next_pick = first_pick
    for _ in range(MAX_CAMERAS_PER_MOUNTAIN):
        chosen.append(next_pick)
        for v in node_indices:
            best = dist_matrix[v]
            for u in coverage[next_pick]:
                t = best.get(u)
                if t is not None and t < remaining_time[v]:
                    remaining_time[v] = t

        worst_case = max(remaining_time.values())
        lon, lat = graph.coords[next_pick]
        history.append({
            "order": len(chosen),
            "lon": lon, "lat": lat,
            "worstCaseMin": round(worst_case, 1) if math.isfinite(worst_case) else None,
        })

        if worst_case <= TARGET_WORST_CASE_MIN:
            break

        # 다음 카메라: 현재 배치 기준 가장 늦게 탐지되는(최악의) 노드
        next_pick = max(remaining_time, key=remaining_time.get)

    node_coords = [graph.coords[v] for v in node_indices]
    lons = [c[0] for c in node_coords]
    lats = [c[1] for c in node_coords]

    return {
        "mountainId": mountain["mountainId"],
        "seed": mountain["seed"],
        "nodeCount": len(node_indices),
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "hull": [[lon, lat] for lon, lat in _convex_hull(node_coords)],
        "recommendedCameras": history,
    }


def place_cameras_all_mountains(graph: FireGraph, mountains: list[dict]) -> list[dict]:
    return [place_cameras_for_mountain(graph, m) for m in mountains]
