"""전체 파이프라인 오케스트레이션: 지역 하나를 분석해 결과 dict를 만든다."""

from __future__ import annotations

import datetime as dt

from . import candidates, config, scoring, smoke_paths
from .dem import load_region_dem
from .sources import air_quality, asos, fire_history, vworld_wfs


def run_region(region_key: str, log=print) -> dict:
    cfg = config.REGIONS[region_key]
    log(f"[{region_key}] DEM 로딩...")
    dem = load_region_dem(region_key)
    bbox = dem.lonlat_bbox()

    log(f"[{region_key}] 산림청 산불통계 수집...")
    fire_summary = fire_history.region_summary(region_key)

    log(f"[{region_key}] ASOS 바람/광학 데이터 수집(4계절)...")
    wind_by_season = asos.region_wind_by_season(region_key)
    wind_data = wind_by_season["봄"]

    log(f"[{region_key}] 에어코리아 대기질 조회...")
    air_quality_data = air_quality.region_air_quality(region_key)

    log(f"[{region_key}] VWorld WFS 레이어 시도...")
    vworld_layers = vworld_wfs.region_layers(region_key, bbox)

    log(f"[{region_key}] 발화 후보지 생성...")
    ignition_candidates = candidates.generate_ignition_candidates(dem, fire_summary)

    log(f"[{region_key}] 카메라 후보지 생성...")
    camera_candidates = candidates.generate_camera_candidates(dem, ignition_candidates)

    log(f"[{region_key}] 연기 이동 경로 생성...")
    paths = smoke_paths.generate_smoke_paths(
        ignition_candidates, wind_data.get("windRose", {}), wind_data.get("avgWindSpeedMs")
    )

    log(f"[{region_key}] line-of-sight 및 스코어링...")
    scored_cameras = scoring.score_cameras(
        dem, cfg, camera_candidates, ignition_candidates, paths,
        wind_data, fire_summary, air_quality_data, vworld_layers,
    )

    source_status = {
        "fireHistory": fire_summary["source"],
        "asosWind": wind_data["source"],
        "airQuality": air_quality_data["source"],
        "vworldWfs": "live" if any(l.get("available") for l in vworld_layers.values()) else "fallback",
        "dem": "live",
    }

    return {
        "region": region_key,
        "regionNameKo": cfg.name_ko,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": cfg.note,
        "bbox": bbox,
        "seat": {"lon": cfg.seat_lon, "lat": cfg.seat_lat},
        "sourceStatus": source_status,
        "fireHistorySummary": fire_summary,
        "wind": wind_data,
        "windBySeason": wind_by_season,
        "airQuality": air_quality_data,
        "ignitionCandidates": ignition_candidates,
        "cameraCandidates": scored_cameras,
        "smokePaths": paths,
    }
