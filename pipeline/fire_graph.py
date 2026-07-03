"""격자 그래프 생성 + 경사 가중 산불 확산 시간(Dijkstra).

기획서의 "복잡한 유체 시뮬레이션은 하지 않는다" 원칙을 지키면서도, 실제
산불이 오르막에서 훨씬 빨리 번진다는 특성(화염이 위쪽 연료를 미리 가열)은
단순한 경사 가중치로 반영한다. 풍향은 이번 그래프에는 반영하지 않는다
(카메라 배치 최적화는 등방향 최악의 경우를 가정 - 바람이 어느 쪽으로 불든
가장 늦게 탐지되는 지점을 기준으로 삼는 것이 안전 마진 측면에서 타당하다).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .dem import RegionDEM
from .grid_utils import deg_steps
from .projection import haversine_m

FIRE_GRID_STEP_M = 300.0
BASE_SPREAD_M_PER_MIN = 4.0  # 봄철 중간 조건 지표화 가정치(문서화된 단순화)
UPHILL_COEF = 3.0
DOWNHILL_COEF = 1.5
MIN_MULTIPLIER = 0.3
MAX_MULTIPLIER = 4.0


@dataclass
class FireGraph:
    coords: list[tuple[float, float]]  # index -> (lon, lat)
    elevations: list[float]
    adjacency: list[list[tuple[int, float]]] = field(default_factory=list)  # index -> [(neighbor_idx, time_min)]

    def __len__(self) -> int:
        return len(self.coords)


def _spread_multiplier(slope: float) -> float:
    """slope = 고도차/거리(오르막이면 양수). 오르막일수록 빠르고, 내리막은 완만히 느려진다."""
    if slope >= 0:
        multiplier = math.exp(UPHILL_COEF * slope)
    else:
        multiplier = math.exp(DOWNHILL_COEF * slope)
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))


_NEIGHBOR_OFFSETS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def build_fire_graph(dem: RegionDEM) -> FireGraph:
    lon_min, lat_min, lon_max, lat_max = dem.lonlat_bbox()
    lon_step, lat_step, _ = deg_steps(dem, FIRE_GRID_STEP_M)

    index_by_rc: dict[tuple[int, int], int] = {}
    coords: list[tuple[float, float]] = []
    elevations: list[float] = []

    row = 0
    lat = lat_min
    while lat <= lat_max:
        col = 0
        lon = lon_min
        while lon <= lon_max:
            if dem.in_bounds(lon, lat):
                elevation = dem.elevation(lon, lat)
                if elevation is not None:
                    index_by_rc[(row, col)] = len(coords)
                    coords.append((round(lon, 6), round(lat, 6)))
                    elevations.append(elevation)
            lon += lon_step
            col += 1
        lat += lat_step
        row += 1

    adjacency: list[list[tuple[int, float]]] = [[] for _ in coords]
    for (r, c), idx in index_by_rc.items():
        lon_a, lat_a = coords[idx]
        elev_a = elevations[idx]
        for dr, dc in _NEIGHBOR_OFFSETS:
            n_idx = index_by_rc.get((r + dr, c + dc))
            if n_idx is None:
                continue
            lon_b, lat_b = coords[n_idx]
            elev_b = elevations[n_idx]
            dist_m = haversine_m(lon_a, lat_a, lon_b, lat_b)
            if dist_m <= 0:
                continue
            slope = (elev_b - elev_a) / dist_m
            multiplier = _spread_multiplier(slope)
            time_min = dist_m / (BASE_SPREAD_M_PER_MIN * multiplier)
            adjacency[idx].append((n_idx, time_min))

    return FireGraph(coords=coords, elevations=elevations, adjacency=adjacency)


def dijkstra(graph: FireGraph, source: int, allowed_nodes: set[int]) -> dict[int, float]:
    """`allowed_nodes` 안에서만 확산한다고 보고(다른 산으로 안 번짐), source에서
    각 노드까지의 최단 도달 시간(분)을 반환한다."""
    dist: dict[int, float] = {source: 0.0}
    visited: set[int] = set()
    heap: list[tuple[float, int]] = [(0.0, source)]
    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        for neighbor, weight in graph.adjacency[node]:
            if neighbor not in allowed_nodes:
                continue
            nd = d + weight
            if nd < dist.get(neighbor, math.inf):
                dist[neighbor] = nd
                heapq.heappush(heap, (nd, neighbor))
    return dist
