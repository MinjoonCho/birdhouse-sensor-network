"""산(mountain) 분할 — 완전한 워터셰드 대신 가벼운 근사.

두드러진 봉우리를 시드로 찾고, 서로 가까운 시드는 하나의 산 덩어리로 합친
뒤, 격자의 모든 노드를 가장 가까운 산 시드에 배정한다. 각 산은 이후
`camera_placement.py`에서 완전히 독립된 서브그래프로 다뤄진다.
"""

from __future__ import annotations

from .dem import RegionDEM
from .grid_utils import grid_points
from .projection import haversine_m

PEAK_SEED_SCAN_STEP_M = 450.0
PEAK_NMS_RADIUS_M = 1500.0  # 이 반경 안에 이미 더 높은 봉우리가 있으면 시드로 인정하지 않는다
MIN_MOUNTAIN_NODES = 15


def _find_peak_seeds(dem: RegionDEM) -> list[dict]:
    """비최대치 억제(NMS)로 서로 PEAK_NMS_RADIUS_M 이상 떨어진 지역 최고점만
    봉우리 시드로 뽑는다. 프로미넌스 기반 로컬 비교 + 단일연결 군집화는
    이웃한 봉우리들이 능선을 타고 연쇄적으로 하나로 뭉치는 문제가 있어서
    (실측: 두 지역 모두 산 전체가 한 덩어리로 합쳐짐) 이 방식으로 대체했다."""
    candidates = []
    for lon, lat in grid_points(dem, PEAK_SEED_SCAN_STEP_M):
        elevation = dem.elevation(lon, lat)
        if elevation is not None:
            candidates.append({"lon": lon, "lat": lat, "elevation": elevation})
    candidates.sort(key=lambda c: -c["elevation"])

    seeds: list[dict] = []
    for c in candidates:
        too_close = any(
            haversine_m(c["lon"], c["lat"], s["lon"], s["lat"]) <= PEAK_NMS_RADIUS_M
            for s in seeds
        )
        if not too_close:
            seeds.append(c)
    return seeds


def _nearest_seed_idx(lon: float, lat: float, seeds: list[dict]) -> int:
    best_idx, best_d = -1, float("inf")
    for idx, s in enumerate(seeds):
        d = haversine_m(lon, lat, s["lon"], s["lat"])
        if d < best_d:
            best_d, best_idx = d, idx
    return best_idx


def segment_mountains(dem: RegionDEM, fire_grid_points: list[tuple[float, float]]) -> list[dict]:
    """`fire_grid_points`(fire_graph.py의 격자 노드 좌표)를 산 단위로 묶는다.

    반환: [{"mountainId", "seed": {lon,lat,elevation}, "nodeIndices": [fire_grid_points 인덱스...]}]
    """
    seeds = _find_peak_seeds(dem)
    if not seeds:
        return []

    assignments = [_nearest_seed_idx(lon, lat, seeds) for lon, lat in fire_grid_points]

    node_lists: dict[int, list[int]] = {}
    for node_idx, seed_idx in enumerate(assignments):
        node_lists.setdefault(seed_idx, []).append(node_idx)

    # 자잘한 산(노드 15개 미만)은 제거하고, 그 노드들을 남은 산 중 가장 가까운
    # 곳으로 재배정한다(한 번만 수행하는 근사 - 연쇄 재귀는 하지 않음).
    small_seed_indices = {i for i, nodes in node_lists.items() if len(nodes) < MIN_MOUNTAIN_NODES}
    if small_seed_indices and len(small_seed_indices) < len(seeds):
        remaining_indices = [i for i in range(len(seeds)) if i not in small_seed_indices]
        remaining_seeds = [seeds[i] for i in remaining_indices]
        orphan_nodes = [idx for i in small_seed_indices for idx in node_lists[i]]
        for node_idx in orphan_nodes:
            lon, lat = fire_grid_points[node_idx]
            local_idx = _nearest_seed_idx(lon, lat, remaining_seeds)
            node_lists.setdefault(remaining_indices[local_idx], []).append(node_idx)
        for i in small_seed_indices:
            node_lists.pop(i, None)

    result = []
    for i, (seed_idx, node_indices) in enumerate(sorted(node_lists.items(), key=lambda kv: -len(kv[1]))):
        if len(node_indices) < MIN_MOUNTAIN_NODES:
            continue
        result.append({
            "mountainId": f"mtn-{i+1:02d}",
            "seed": seeds[seed_idx],
            "nodeIndices": node_indices,
        })
    return result
