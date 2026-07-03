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
MAX_SENSOR_RANGE_M = 1000.0  # 새집형 센서 실측 탐지 거리(500m~1km) 상한
PLUME_GROWTH_M_PER_MIN = 25.0  # 연기가 시간이 지날수록 옆으로 번져 탐지 반경도 넓어진다는 가정치
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

    # 지점 인근 카메라만 검사해 계산량을 줄인다. 경로 끝점(최대 2.5km)과
    # 그 시점까지 넓어진 탐지 반경까지 여유를 두고 미리 추린다.
    prefilter_radius_m = MAX_SENSOR_RANGE_M + 5000.0
    nearby_cameras = [
        cam for cam in camera_candidates
        if haversine_m(lon, lat, cam["lon"], cam["lat"]) <= prefilter_radius_m
    ]

    directions = []
    for path in paths:
        cumulative_dist = 0.0
        prev_lon, prev_lat = lon, lat
        detected = False
        detect_time_min = None
        detect_camera = None
        detect_score = None
        detect_point = None
        timed_points = []

        for plon, plat in path["points"]:
            cumulative_dist += haversine_m(prev_lon, prev_lat, plon, plat)
            prev_lon, prev_lat = plon, plat
            elapsed_min = round(cumulative_dist / max(wind_speed, 0.3) / 60, 1)
            timed_points.append([plon, plat, elapsed_min])

            # 연기가 퍼진 시간만큼 유효 탐지 반경도 넓어진다고 본다 - 발화 지점
            # 자체가 아니라, 시간이 지나며 번진 연기가 언제 센서 사정거리 안에
            # 들어오는지를 찾는 것이 핵심이다.
            effective_range_m = MAX_SENSOR_RANGE_M + PLUME_GROWTH_M_PER_MIN * elapsed_min

            best_score, best_cam = 0.0, None
            for cam in nearby_cameras:
                if haversine_m(cam["lon"], cam["lat"], plon, plat) > effective_range_m:
                    continue
                result = line_of_sight(
                    dem, (cam["lon"], cam["lat"]), CAMERA_MAST_HEIGHT_M,
                    (plon, plat), SMOKE_PLUME_HEIGHT_M,
                )
                if result["score"] > best_score:
                    best_score, best_cam = result["score"], cam["id"]

            if best_score >= VISIBLE_THRESHOLD and not detected:
                detected = True
                detect_time_min = round(elapsed_min + DETECTION_LATENCY_MIN, 1)
                detect_camera = best_cam
                detect_score = round(best_score, 1)
                detect_point = [plon, plat]
                break

        directions.append({
            "direction": path["fromDirection"],
            "probabilityPct": path["probabilityPct"],
            "lengthM": path["lengthM"],
            "points": timed_points,
            "detected": detected,
            "detectionTimeMin": detect_time_min,
            "detectingCameraId": detect_camera,
            "visibilityScoreAtDetect": detect_score,
            "detectingPoint": detect_point,
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
            "sensorRangeM": MAX_SENSOR_RANGE_M,
            "plumeGrowthMPerMin": PLUME_GROWTH_M_PER_MIN,
            "note": "센서 탐지 거리는 500m~1km 가정. 연기가 퍼질수록(분당 "
                    f"{PLUME_GROWTH_M_PER_MIN}m) 유효 탐지 반경이 넓어진다고 보고, "
                    "발화 지점이 아니라 시간에 따라 번진 연기가 반경 안에 들어오는 "
                    "시점을 탐지 시각으로 계산합니다. 탐지 지연 3분은 카메라 스캔 "
                    "주기+연기 판별 처리 시간 가정치이며, 실제 장비 사양이 정해지면 "
                    "조정이 필요합니다.",
        },
    }
