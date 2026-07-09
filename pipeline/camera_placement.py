"""노드 전량 Dijkstra + greedy 알고리즘으로 카메라 배치를 구한다.

두 가지 목표(objective)를 지원한다:

- worst (k-center, 표준 2-근사 greedy farthest-point): 매번 "현재 배치 기준
  가장 늦게 탐지되는 최악의 노드"에 카메라를 추가한다. "카메라 N개로 최장
  탐지 시간을 얼마까지 줄일 수 있나"에 대한 답.
- average (k-median류 greedy): 매번 "추가했을 때 전체 평균 탐지 시간을 가장
  많이 낮추는" 후보에 카메라를 놓는다. "평균 탐지 시간을 N분 이하로 만들려면
  카메라가 몇 개 필요한가"에 대한 답. k-center만큼 이론적 근사 보장이 강하진
  않지만, 이 규모(산 하나당 수십~수백 노드)에서는 충분히 빠르고 실용적이다.

두 목표 모두 정지 조건을 "목표 시간(target_minutes)" 또는 "카메라 개수
(camera_count)" 중 하나로 줄 수 있어, 배치 파이프라인(산마다 고정 기준)과
실시간 질의(사용자가 고른 목표/개수) 양쪽에 같은 코드를 쓴다.
"""

from __future__ import annotations

import math

from .fire_graph import FireGraph, dijkstra
from .projection import haversine_m

SENSOR_RANGE_M = 1000.0  # 새집형 센서 실측 탐지 거리 상한(500m~1km)
TARGET_WORST_CASE_MIN = 20.0
MAX_CAMERAS_PER_MOUNTAIN = 6
DEFAULT_MAX_CAMERAS = 20  # 실시간 질의 시 무한 루프 방지용 안전 상한


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain. 지도에 산/영역 경계를 그리기 위한 근사 다각형."""
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


def _first_pick(graph: FireGraph, node_indices: list[int], seed_lon: float, seed_lat: float) -> int:
    """모든 노드가 동률(무한대)일 때 임의 선택 대신, 기준점(산 최고점 등)에서
    가장 가까운 그래프 노드로 시작한다."""
    return min(node_indices, key=lambda v: haversine_m(seed_lon, seed_lat, *graph.coords[v]))


def _greedy_worst_case(
    graph: FireGraph, node_indices: list[int], seed_lon: float, seed_lat: float,
    target_minutes: float | None, camera_count: int | None,
) -> list[dict]:
    dist_matrix = _all_pairs_time(graph, node_indices)
    coverage = _coverage_map(graph, node_indices)
    remaining_time = {v: math.inf for v in node_indices}

    max_iterations = camera_count if camera_count is not None else DEFAULT_MAX_CAMERAS
    next_pick = _first_pick(graph, node_indices, seed_lon, seed_lat)
    history = []

    for _ in range(max_iterations):
        for v in node_indices:
            best = dist_matrix[v]
            for u in coverage[next_pick]:
                t = best.get(u)
                if t is not None and t < remaining_time[v]:
                    remaining_time[v] = t

        worst_case = max(remaining_time.values())
        avg_case = sum(remaining_time.values()) / len(remaining_time) if math.isfinite(worst_case) else math.inf
        lon, lat = graph.coords[next_pick]
        history.append({
            "order": len(history) + 1,
            "lon": lon, "lat": lat,
            "worstCaseMin": round(worst_case, 1) if math.isfinite(worst_case) else None,
            "avgCaseMin": round(avg_case, 1) if math.isfinite(avg_case) else None,
        })

        # 목표 시간에 먼저 도달하거나(둘 다 지정된 경우 목표가 우선), 지정된
        # 카메라 개수에 도달하면(range()가 이미 상한을 걸어주므로) 멈춘다.
        if target_minutes is not None and worst_case <= target_minutes:
            break

        next_pick = max(remaining_time, key=remaining_time.get)

    return history


def _greedy_average(
    graph: FireGraph, node_indices: list[int], seed_lon: float, seed_lat: float,
    target_avg_minutes: float | None, camera_count: int | None,
) -> list[dict]:
    dist_matrix = _all_pairs_time(graph, node_indices)
    coverage = _coverage_map(graph, node_indices)
    remaining_time = {v: math.inf for v in node_indices}
    remaining_candidates = set(node_indices)

    max_iterations = camera_count if camera_count is not None else DEFAULT_MAX_CAMERAS
    history = []
    n = len(node_indices)

    for step in range(max_iterations):
        if step == 0:
            next_pick = _first_pick(graph, node_indices, seed_lon, seed_lat)
        else:
            # 후보마다 "추가했을 때 예상 평균 탐지 시간"을 계산해 가장 낮추는 것을 고른다.
            best_candidate, best_sum = None, math.inf
            for candidate in remaining_candidates:
                projected_sum = 0.0
                for v in node_indices:
                    best_time = remaining_time[v]
                    for u in coverage[candidate]:
                        t = dist_matrix[v].get(u)
                        if t is not None and t < best_time:
                            best_time = t
                    projected_sum += best_time
                if projected_sum < best_sum:
                    best_sum, best_candidate = projected_sum, candidate
            next_pick = best_candidate

        remaining_candidates.discard(next_pick)
        for v in node_indices:
            best = dist_matrix[v]
            for u in coverage[next_pick]:
                t = best.get(u)
                if t is not None and t < remaining_time[v]:
                    remaining_time[v] = t

        worst_case = max(remaining_time.values())
        avg_case = sum(remaining_time.values()) / n if math.isfinite(worst_case) else math.inf
        lon, lat = graph.coords[next_pick]
        history.append({
            "order": len(history) + 1,
            "lon": lon, "lat": lat,
            "worstCaseMin": round(worst_case, 1) if math.isfinite(worst_case) else None,
            "avgCaseMin": round(avg_case, 1) if math.isfinite(avg_case) else None,
        })

        if target_avg_minutes is not None and avg_case <= target_avg_minutes:
            break
        if not remaining_candidates:
            break

    return history


def plan_cameras(
    graph: FireGraph,
    node_indices: list[int],
    seed: dict,
    objective: str = "worst",
    target_minutes: float | None = None,
    camera_count: int | None = None,
    area_id: str | None = None,
) -> dict:
    """실시간 질의용 공용 진입점. objective="worst"|"average", target_minutes와
    camera_count 중 정확히 하나를 지정한다(둘 다 없으면 worst는 20분, average는
    10분 기본값)."""
    if target_minutes is None and camera_count is None:
        target_minutes = TARGET_WORST_CASE_MIN if objective == "worst" else 10.0

    if objective == "average":
        history = _greedy_average(graph, node_indices, seed["lon"], seed["lat"], target_minutes, camera_count)
    else:
        history = _greedy_worst_case(graph, node_indices, seed["lon"], seed["lat"], target_minutes, camera_count)

    node_coords = [graph.coords[v] for v in node_indices]
    lons = [c[0] for c in node_coords]
    lats = [c[1] for c in node_coords]

    return {
        "areaId": area_id,
        "seed": seed,
        "nodeCount": len(node_indices),
        "objective": objective,
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "hull": [[lon, lat] for lon, lat in _convex_hull(node_coords)],
        "recommendedCameras": history,
    }


def place_cameras_for_mountain(graph: FireGraph, mountain: dict) -> dict:
    """배치 파이프라인용: 산마다 고정 기준(목표 20분, 최대 6개 중 먼저 도달하는 쪽)으로 계산한다."""
    result = plan_cameras(
        graph, mountain["nodeIndices"], mountain["seed"], objective="worst",
        target_minutes=TARGET_WORST_CASE_MIN, camera_count=MAX_CAMERAS_PER_MOUNTAIN,
        area_id=mountain["mountainId"],
    )
    result["mountainId"] = mountain["mountainId"]
    del result["areaId"]
    del result["objective"]
    return result


def place_cameras_all_mountains(graph: FireGraph, mountains: list[dict]) -> list[dict]:
    return [place_cameras_for_mountain(graph, m) for m in mountains]
