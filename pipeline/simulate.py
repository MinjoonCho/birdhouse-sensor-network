"""사용자가 지도에서 직접 지정한 지점에 대한 즉석 발화 시뮬레이션.

산림청 발화 "기록"이 있는 곳만 대비하는 게 아니라, 임의의 발화 가능 지점에
대해서도 "연기가 어느 방향으로 퍼지고, 지금 배치된 카메라망으로 몇 분 만에
탐지될 것으로 예상되는지"를 즉시 계산한다. 기존 150개 카메라 후보지 배치를
그대로 사용해 일관성을 유지한다.
"""

from __future__ import annotations

from .dem import RegionDEM, line_of_sight
from .projection import haversine_m
from .smoke_paths import generate_smoke_paths

CAMERA_MAST_HEIGHT_M = 8.0
SMOKE_PLUME_HEIGHT_M = 80.0
CAMERA_RANGE_FOR_SIM_M = 7000.0
VISIBLE_THRESHOLD = 50.0
DETECTION_LATENCY_MIN = 3.0  # 카메라 스캔 주기 + 연기 판별 처리 지연 가정치


def simulate_ignition(
    dem: RegionDEM,
    camera_candidates: list[dict],
    lon: float,
    lat: float,
    wind_rose: dict,
    avg_wind_speed_ms: float | None,
    min_prob_pct: float = 3.0,
) -> dict:
    elevation = dem.elevation(lon, lat)
    wind_speed = avg_wind_speed_ms or 2.0
    synthetic = {"id": "sim", "lon": lon, "lat": lat}
    paths = generate_smoke_paths([synthetic], wind_rose, wind_speed, min_prob_pct=min_prob_pct)

    # 지점 인근 카메라만 검사해 계산량을 줄인다.
    nearby_cameras = [
        cam for cam in camera_candidates
        if haversine_m(lon, lat, cam["lon"], cam["lat"]) <= CAMERA_RANGE_FOR_SIM_M * 1.6
    ]

    directions = []
    for path in paths:
        cumulative_dist = 0.0
        prev_lon, prev_lat = lon, lat
        detected = False
        detect_time_min = None
        detect_camera = None
        detect_score = None

        for plon, plat in path["points"]:
            cumulative_dist += haversine_m(prev_lon, prev_lat, plon, plat)
            prev_lon, prev_lat = plon, plat

            best_score, best_cam = 0.0, None
            for cam in nearby_cameras:
                if haversine_m(cam["lon"], cam["lat"], plon, plat) > CAMERA_RANGE_FOR_SIM_M:
                    continue
                result = line_of_sight(
                    dem, (cam["lon"], cam["lat"]), CAMERA_MAST_HEIGHT_M,
                    (plon, plat), SMOKE_PLUME_HEIGHT_M,
                )
                if result["score"] > best_score:
                    best_score, best_cam = result["score"], cam["id"]

            if best_score >= VISIBLE_THRESHOLD:
                detected = True
                detect_time_min = round(cumulative_dist / max(wind_speed, 0.3) / 60 + DETECTION_LATENCY_MIN, 1)
                detect_camera = best_cam
                detect_score = round(best_score, 1)
                break

        directions.append({
            "direction": path["fromDirection"],
            "probabilityPct": path["probabilityPct"],
            "lengthM": path["lengthM"],
            "points": path["points"],
            "detected": detected,
            "detectionTimeMin": detect_time_min,
            "detectingCameraId": detect_camera,
            "visibilityScoreAtDetect": detect_score,
        })

    directions.sort(key=lambda d: d["probabilityPct"], reverse=True)
    detected_dirs = [d for d in directions if d["detected"]]
    undetected_prob = round(sum(d["probabilityPct"] for d in directions if not d["detected"]), 1)

    expected_time = None
    if detected_dirs:
        weight_total = sum(d["probabilityPct"] for d in detected_dirs) or 1.0
        expected_time = round(
            sum(d["detectionTimeMin"] * d["probabilityPct"] for d in detected_dirs) / weight_total, 1
        )

    return {
        "point": {"lon": lon, "lat": lat, "elevation": round(elevation, 1) if elevation is not None else None},
        "windSpeedMsUsed": wind_speed,
        "directions": directions,
        "headlineDirection": directions[0] if directions else None,
        "expectedDetectionTimeMin": expected_time,
        "undetectedProbabilityPct": undetected_prob,
        "assumptions": {
            "visibleThreshold": VISIBLE_THRESHOLD,
            "detectionLatencyMin": DETECTION_LATENCY_MIN,
            "note": "탐지 지연은 카메라 스캔 주기+연기 판별 처리 시간에 대한 가정치(고정 3분)이며, "
                    "실제 장비 사양이 정해지면 조정이 필요합니다.",
        },
    }
