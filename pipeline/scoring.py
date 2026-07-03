"""카메라 후보지 스코어링 (기획서 6.5~6.13, 7절 최종 가중합, 8.2 설명 문장).

일부 서브스코어(보호대상 중요도/통신 가능성/전력 안정성/오탐 위험/생태 교란
위험)는 VWorld·생태·통신 원자료가 없어 DEM/등산로/지역 통계 기반의 근사치를
쓴다. 이 경우 각 서브스코어에 `approx: true`를 표시하고 근거 문장에도
명시해, 실제로 확보된 데이터와 근사치를 섞어 보여주지 않는다.
"""

from __future__ import annotations

import datetime as dt
import math

from . import config
from .dem import RegionDEM, line_of_sight
from .projection import haversine_m
from .sun import backlight_risk, bearing_deg, solar_position

REPRESENTATIVE_HOURS = [9, 12, 14, 15, 18]
DEFAULT_HOUR = 14
CAMERA_MAST_HEIGHT_M = 8.0
SMOKE_PLUME_HEIGHT_M = 80.0
MAX_SENSOR_RANGE_M = 1000.0  # 새집형 센서 실측 탐지 거리(500m~1km) 상한
PLUME_GROWTH_M_PER_MIN = 25.0  # simulate.py와 동일한 가정치(연기 확산에 따른 유효 반경 증가)
PREFILTER_BUFFER_M = 5000.0  # 경로 길이(최대 2.5km) + 확산 여유를 감안한 사전 필터 반경
DIVERSITY_CELL_SIZE_M = 3000.0


def _diversify_by_geography(cameras: list[dict], bbox: tuple[float, float, float, float]) -> list[dict]:
    """점수만으로 순위를 매기면 발화 후보지가 몰린 한 구역의 카메라들이 상위권을
    싹쓸이해 그 지역에만 카메라가 몰리는 것처럼 보인다. 지역을 격자로 나눠
    구역별로 돌아가며 최고점 후보를 뽑아, 상위 N개를 골라도 여러 산에 걸쳐
    고르게 분포하도록 순서를 재배열한다(개별 최종 점수는 그대로 유지).
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_span_m = haversine_m(lon_min, lat_min, lon_min, lat_max) or 1.0
    lon_span_m = haversine_m(lon_min, lat_min, lon_max, lat_min) or 1.0
    n_rows = max(3, min(12, round(lat_span_m / DIVERSITY_CELL_SIZE_M)))
    n_cols = max(3, min(12, round(lon_span_m / DIVERSITY_CELL_SIZE_M)))

    buckets: dict[tuple[int, int], list[dict]] = {}
    for cam in cameras:
        col = min(n_cols - 1, int((cam["lon"] - lon_min) / (lon_max - lon_min + 1e-9) * n_cols))
        row = min(n_rows - 1, int((cam["lat"] - lat_min) / (lat_max - lat_min + 1e-9) * n_rows))
        buckets.setdefault((row, col), []).append(cam)
    for bucket in buckets.values():
        bucket.sort(key=lambda c: c["scores"]["final"], reverse=True)

    ordered: list[dict] = []
    while True:
        active = [b for b in buckets.values() if b]
        if not active:
            break
        active.sort(key=lambda b: b[0]["scores"]["final"], reverse=True)
        for bucket in active:
            ordered.append(bucket.pop(0))
    return ordered


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _southness(aspect_deg: float) -> float:
    return (math.cos(math.radians(aspect_deg - 180)) + 1) / 2


def _optical_reliability_by_hour(optical_profile: dict, air_quality: dict) -> dict[int, float]:
    pm10 = air_quality.get("pm10")
    pm25 = air_quality.get("pm25")
    pm_penalty = 0.0
    if pm10 is not None:
        pm_penalty += 15 if pm10 > 150 else 8 if pm10 > 80 else 0
    if pm25 is not None:
        pm_penalty += 8 if pm25 > 75 else 4 if pm25 > 35 else 0

    result = {}
    for hour_str, stats in optical_profile.items():
        hour = int(hour_str)
        score = 100.0
        hum = stats.get("avgHumidityPct")
        if hum is not None:
            score -= 15 if hum > 90 else 7 if hum > 75 else 0
        vis = stats.get("avgVisibilityM")
        if vis is not None:
            score -= 20 if vis < 2000 else 8 if vis < 5000 else 0
        precip = stats.get("precipProbPct") or 0
        score -= min(20, precip * 0.3)
        score -= pm_penalty
        result[hour] = round(_clamp(score), 1)
    return result


def _maintenance_score(trail_distance_m: float | None, slope_deg: float | None) -> float:
    if trail_distance_m is None:
        dist_penalty = 45.0
    elif trail_distance_m <= 300:
        dist_penalty = 0.0
    elif trail_distance_m <= 1200:
        dist_penalty = 40.0 * (trail_distance_m - 300) / 900
    elif trail_distance_m <= 2500:
        dist_penalty = 40 + 30.0 * (trail_distance_m - 1200) / 1300
    else:
        dist_penalty = 80.0
    slope_penalty = 0.0 if not slope_deg or slope_deg <= 15 else min(30.0, (slope_deg - 15) * 1.5)
    return round(_clamp(100 - dist_penalty - slope_penalty), 1)


def _protected_target_score(seat_distance_m: float, trail_distance_m: float | None) -> float:
    seat_proximity = _clamp(100 * (1 - seat_distance_m / 15000))
    if trail_distance_m is not None and trail_distance_m <= 400:
        density = 100.0
    elif trail_distance_m is not None and trail_distance_m <= 1000:
        density = 40.0
    else:
        density = 15.0
    return round(_clamp(0.6 * seat_proximity + 0.4 * density), 1)


def _communication_score(prominence: float, trail_distance_m: float | None) -> float:
    base = 55 + 40 * ((prominence - 0.75) / 0.25)
    penalty = 10 if (trail_distance_m or 9999) > 3000 else 0
    return round(_clamp(base - penalty), 1)


def _power_stability_score(aspect_deg: float | None, prominence: float) -> float:
    south = _southness(aspect_deg) if aspect_deg is not None else 0.5
    return round(_clamp(south * 70 + prominence * 30), 1)


def _false_alarm_risk(trail_distance_m: float | None) -> float:
    if trail_distance_m is None:
        return 15.0
    return round(_clamp(40 - trail_distance_m / 40, 0, 40), 1)


ECOLOGICAL_RISK_NEUTRAL = 30.0


def _reasons(
    camera: dict,
    covered_ignitions: list[dict],
    smoke_visibility: float,
    optical_at_hour: float,
    hour: float,
    backlight: float,
    maintenance: float,
    protected: float,
    vworld_available: bool,
) -> list[str]:
    reasons = []
    high_risk_covered = [c for c in covered_ignitions if c["visibilityScore"] >= 55 and c["riskScore"] >= 55]
    if high_risk_covered:
        dirs = sorted({p["fromDirection"] for c in high_risk_covered for p in c.get("topPaths", [])})
        dir_txt = f"({'·'.join(dirs[:3])} 계열 풍향 기준)" if dirs else ""
        reasons.append(
            f"이 후보지는 고위험 발화 후보지 {len(high_risk_covered)}개의 연기 경로를 시야에 포함합니다 {dir_txt}."
        )
    else:
        reasons.append("이 후보지는 주변 발화 후보지의 연기 경로 관측 효율이 상대적으로 낮습니다.")

    if maintenance >= 70:
        reasons.append(f"임도/등산로와 {camera.get('trailDistanceM') or '?'}m 거리로 유지보수 접근이 수월합니다.")
    elif maintenance >= 40:
        reasons.append(f"임도/등산로와 {camera.get('trailDistanceM') or '?'}m 거리로 유지보수는 보통 수준입니다.")
    else:
        reasons.append("임도/등산로에서 멀어 유지보수 접근성이 낮은 편입니다.")

    if protected >= 60:
        reasons.append("군 중심지·생활권과 가까워 조기 감지 시 보호 가치가 높습니다.")

    if backlight >= 50:
        reasons.append(f"{int(hour)}시경 역광 위험이 높아 카메라 방향 조정이 필요합니다.")
    elif backlight <= 15:
        reasons.append(f"{int(hour)}시경 역광 위험은 낮은 편입니다.")

    if optical_at_hour < 55:
        reasons.append("습도·시정·대기질 조건상 해당 시간대 광학 신뢰도가 낮아질 수 있습니다.")

    if not vworld_available:
        reasons.append("VWorld 도로/건물/보호대상 데이터는 현재 미확보 상태라 근사치로 대체했습니다.")

    return reasons


def score_cameras(
    dem: RegionDEM,
    cfg,
    camera_candidates: list[dict],
    ignition_candidates: list[dict],
    smoke_paths: list[dict],
    wind_data: dict,
    fire_summary: dict,
    air_quality: dict,
    vworld_layers: dict,
) -> list[dict]:
    ignitions_by_id = {ig["id"]: ig for ig in ignition_candidates}
    paths_by_ignition: dict[str, list[dict]] = {}
    for p in smoke_paths:
        paths_by_ignition.setdefault(p["ignitionId"], []).append(p)

    optical_by_hour = _optical_reliability_by_hour(wind_data.get("opticalHourlyProfile", {}), air_quality)
    default_hour = DEFAULT_HOUR if DEFAULT_HOUR in optical_by_hour else (next(iter(optical_by_hour), DEFAULT_HOUR))

    sample_day = wind_data.get("sampleDay")
    rep_date = dt.date.fromisoformat(sample_day["date"]) if sample_day else dt.date.today()

    vworld_available = any(layer.get("available") for layer in vworld_layers.values())
    wind_speed = wind_data.get("avgWindSpeedMs") or 2.0
    prefilter_radius_m = MAX_SENSOR_RANGE_M + PREFILTER_BUFFER_M

    scored = []
    for cam in camera_candidates:
        covered = []
        for ig in ignition_candidates:
            dist = haversine_m(cam["lon"], cam["lat"], ig["lon"], ig["lat"])
            if dist > prefilter_radius_m:
                continue
            paths = paths_by_ignition.get(ig["id"], [])
            if not paths:
                direct = line_of_sight(
                    dem, (cam["lon"], cam["lat"]), CAMERA_MAST_HEIGHT_M,
                    (ig["lon"], ig["lat"]), SMOKE_PLUME_HEIGHT_M,
                ) if dist <= MAX_SENSOR_RANGE_M else {"score": 0.0}
                covered.append({"ignitionId": ig["id"], "riskScore": ig["riskScore"],
                                 "visibilityScore": direct["score"], "topPaths": []})
                continue

            prob_total = sum(p["probabilityPct"] for p in paths) or 1.0
            weighted_sum = 0.0
            top_paths = []
            for path in paths:
                # 발화지점 자체가 아니라, 시간이 지나며 번진 연기가 센서 사정거리
                # (500m~1km, 확산에 따라 점점 넓어짐) 안에 들어오는지를 본다.
                sample_points = path["points"][::2]
                n_total = len(path["points"])
                los_scores = []
                for idx, (lon, lat) in enumerate(sample_points):
                    point_dist_m = path["lengthM"] * (2 * idx + 1) / n_total
                    elapsed_min = point_dist_m / max(wind_speed, 0.3) / 60
                    effective_range_m = MAX_SENSOR_RANGE_M + PLUME_GROWTH_M_PER_MIN * elapsed_min
                    if haversine_m(cam["lon"], cam["lat"], lon, lat) > effective_range_m:
                        los_scores.append(0.0)
                        continue
                    result = line_of_sight(
                        dem, (cam["lon"], cam["lat"]), CAMERA_MAST_HEIGHT_M,
                        (lon, lat), SMOKE_PLUME_HEIGHT_M,
                    )
                    los_scores.append(result["score"])
                path_score = sum(los_scores) / len(los_scores) if los_scores else 0.0
                weight = path["probabilityPct"] / prob_total
                weighted_sum += weight * path_score
                if path_score >= 55:
                    top_paths.append({"fromDirection": path["fromDirection"], "score": round(path_score, 1)})

            covered.append({
                "ignitionId": ig["id"], "riskScore": ig["riskScore"],
                "visibilityScore": round(weighted_sum, 1),
                "topPaths": sorted(top_paths, key=lambda p: p["score"], reverse=True)[:3],
            })

        if covered:
            risk_weight_total = sum(c["riskScore"] for c in covered) or 1.0
            smoke_visibility = sum(c["visibilityScore"] * c["riskScore"] for c in covered) / risk_weight_total
            fire_risk_coverage = sum(c["riskScore"] * (c["visibilityScore"] / 100) for c in covered) / (
                sum(c["visibilityScore"] / 100 for c in covered) or 1.0
            )
        else:
            smoke_visibility = 0.0
            fire_risk_coverage = 0.0

        visible_covered = [c for c in covered if c["visibilityScore"] >= 40]
        if visible_covered:
            bearings = [
                bearing_deg(cam["lon"], cam["lat"], ignitions_by_id[c["ignitionId"]]["lon"], ignitions_by_id[c["ignitionId"]]["lat"])
                for c in visible_covered
            ]
            weights = [c["visibilityScore"] for c in visible_covered]
            sin_sum = sum(math.sin(math.radians(b)) * w for b, w in zip(bearings, weights))
            cos_sum = sum(math.cos(math.radians(b)) * w for b, w in zip(bearings, weights))
            primary_bearing = math.degrees(math.atan2(sin_sum, cos_sum)) % 360
        else:
            primary_bearing = 0.0

        # 6.8 역광 위험은 4.3절 정의대로 Optical Reliability Score의 감점 요소로
        # 합산한다(카메라별 조망 방향이 필요해 지역 단위 optical_by_hour와 별도 계산).
        backlight_by_hour = {}
        optical_with_backlight_by_hour = {}
        for hour in REPRESENTATIVE_HOURS:
            elev, az = solar_position(rep_date, hour, cam["lat"], cam["lon"])
            risk = backlight_risk(primary_bearing, az, elev)
            backlight_by_hour[hour] = risk
            base_optical = optical_by_hour.get(hour, 60.0)
            optical_with_backlight_by_hour[hour] = round(_clamp(base_optical - 0.4 * risk), 1)

        slope, aspect = dem.slope_aspect(cam["lon"], cam["lat"])
        maintenance = _maintenance_score(cam.get("trailDistanceM"), slope)
        seat_dist = haversine_m(cam["lon"], cam["lat"], cfg.seat_lon, cfg.seat_lat)
        protected = _protected_target_score(seat_dist, cam.get("trailDistanceM"))
        communication = _communication_score(cam.get("prominence", 0.75), cam.get("trailDistanceM"))
        power = _power_stability_score(aspect, cam.get("prominence", 0.75))
        false_alarm = _false_alarm_risk(cam.get("trailDistanceM"))
        ecological = ECOLOGICAL_RISK_NEUTRAL

        optical_default = optical_with_backlight_by_hour.get(default_hour, optical_by_hour.get(default_hour, 60.0))
        backlight_default = backlight_by_hour.get(default_hour, 0.0)

        final_score = _clamp(
            0.25 * fire_risk_coverage
            + 0.25 * smoke_visibility
            + 0.15 * optical_default
            + 0.10 * maintenance
            + 0.10 * protected
            + 0.05 * communication
            + 0.05 * power
            - 0.03 * false_alarm
            - 0.02 * ecological
        )

        reasons = _reasons(
            cam, covered, smoke_visibility, optical_default, default_hour,
            backlight_default, maintenance, protected, vworld_available,
        )

        scored.append({
            **cam,
            "slopeDeg": round(slope, 1) if slope is not None else None,
            "aspectDeg": round(aspect, 1) if aspect is not None else None,
            "primaryBearingDeg": round(primary_bearing, 1),
            "scores": {
                "final": round(final_score, 1),
                "fireRiskCoverage": round(fire_risk_coverage, 1),
                "smokeVisibility": round(smoke_visibility, 1),
                "opticalReliabilityBaseByHour": optical_by_hour,
                "opticalReliabilityByHour": {str(h): v for h, v in optical_with_backlight_by_hour.items()},
                "backlightRiskByHour": {str(h): v for h, v in backlight_by_hour.items()},
                "maintenanceAccess": maintenance,
                "protectedTarget": {"value": protected, "approx": True},
                "communication": {"value": communication, "approx": True},
                "powerStability": power,
                "falseAlarmRisk": {"value": false_alarm, "approx": True},
                "ecologicalRisk": {"value": ecological, "approx": True, "note": "생태 데이터 미포함, 중립값"},
            },
            "coveredIgnitions": covered,
            "reasons": reasons,
        })

    scored = _diversify_by_geography(scored, dem.lonlat_bbox())
    for rank, cam in enumerate(scored, start=1):
        cam["rank"] = rank
    return scored
