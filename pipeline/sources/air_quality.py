"""에어코리아 대기오염정보(ArpltnInforInqireSvc) 실시간 조회.

측정소 좌표를 주는 API는 현재 키로 접근 불가(403, 별도 상품 승인 필요)라서,
경북 실시간 목록에서 실제로 응답에 나오는 측정소만 골라 config의 수작업
좌표 힌트(AIR_QUALITY_STATION_HINTS)로 최근접 매칭한다. 매칭되는 측정소가
없으면 중립값으로 표시하고 sourceStatus에 fallback으로 남긴다.
"""

from __future__ import annotations

from .. import config
from ..http_client import ApiError, get_json
from ..projection import haversine_m

BASE_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def region_air_quality(region_key: str) -> dict:
    cfg = config.REGIONS[region_key]
    try:
        payload = get_json(BASE_URL, {
            "serviceKey": config.DATA_GO_KR_API_KEY,
            "returnType": "json",
            "numOfRows": 100,
            "pageNo": 1,
            "sidoName": "경북",
            "ver": "1.3",
        })
        items = payload.get("response", {}).get("body", {}).get("items", [])
    except ApiError:
        items = []

    best_station, best_dist = None, float("inf")
    for item in items:
        name = item.get("stationName", "")
        hint = None
        for key, coords in config.AIR_QUALITY_STATION_HINTS.items():
            if key in name or name in key:
                hint = coords
                break
        if hint is None:
            continue
        dist = haversine_m(cfg.seat_lon, cfg.seat_lat, hint[1], hint[0])
        if dist < best_dist:
            best_dist = dist
            best_station = item

    if best_station is None:
        return {
            "source": "fallback",
            "station": None,
            "pm10": None,
            "pm25": None,
            "khaiGrade": None,
            "note": "인근 실시간 측정소를 식별하지 못해 중립값으로 대체됨",
        }

    return {
        "source": "live",
        "station": best_station.get("stationName"),
        "distanceApproxM": round(best_dist),
        "pm10": _to_float(best_station.get("pm10Value")),
        "pm25": _to_float(best_station.get("pm25Value")),
        "khaiGrade": best_station.get("khaiGrade"),
        "dataTime": best_station.get("dataTime"),
    }
