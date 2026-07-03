"""연기 이동 후보 경로 생성 (기획서 6.3).

풍향(관측된 "바람이 불어오는" 방향)의 반대쪽으로 연기가 이동한다고 보고,
풍속 구간별 고정 길이 규칙(약풍 500m/중풍 1km/강풍 2~3km)으로 직선 경로를
만든다. 복잡한 유체 시뮬레이션은 하지 않는다(기획서 원칙 3).
"""

from __future__ import annotations

import math

DIRECTION_BEARING = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
    "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


def path_length_for_speed(avg_wind_speed_ms: float | None) -> float:
    if avg_wind_speed_ms is None:
        return 1000.0
    if avg_wind_speed_ms < 2.0:
        return 500.0
    if avg_wind_speed_ms <= 6.0:
        return 1000.0
    return 2500.0


def _project(lon: float, lat: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    bearing = math.radians(bearing_deg)
    dlat = (distance_m * math.cos(bearing)) / 110540.0
    dlon = (distance_m * math.sin(bearing)) / (111320.0 * math.cos(math.radians(lat)))
    return round(lon + dlon, 6), round(lat + dlat, 6)


def generate_smoke_paths(
    ignition_candidates: list[dict],
    wind_rose: dict,
    avg_wind_speed_ms: float | None,
    min_prob_pct: float = 3.0,
    sample_step_m: float = 150.0,
) -> list[dict]:
    length_m = path_length_for_speed(avg_wind_speed_ms)
    n_samples = max(2, int(length_m / sample_step_m))
    paths = []
    path_seq = 0
    for ig in ignition_candidates:
        for direction, prob in wind_rose.items():
            if prob < min_prob_pct:
                continue
            travel_bearing = (DIRECTION_BEARING[direction] + 180) % 360
            samples = [
                _project(ig["lon"], ig["lat"], travel_bearing, length_m * i / n_samples)
                for i in range(1, n_samples + 1)
            ]
            path_seq += 1
            paths.append({
                "id": f"path-{path_seq:04d}",
                "ignitionId": ig["id"],
                "fromDirection": direction,
                "probabilityPct": prob,
                "lengthM": length_m,
                "points": [[ig["lon"], ig["lat"]]] + [[lo, la] for lo, la in samples],
            })
    return paths
