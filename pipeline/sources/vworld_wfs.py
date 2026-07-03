"""VWorld WFS 레이어(도로/건물/수계/행정경계) 조회.

기획서 5.3의 격자 반복 호출 원칙을 구현하되, 서비스 승인/도메인 문제로
호출이 실패하면(현재 키는 INCORRECT_KEY) 예외를 던지지 않고 layer별로
available=False 를 반환한다. 다운스트림 scoring은 이 경우 해당 서브스코어를
중립값으로 대체하고 근거 문장에 "데이터 미확보"를 명시한다 — 없는 데이터를
임의로 지어내지 않는다.
"""

from __future__ import annotations

from .. import config
from ..http_client import ApiError, get_json

BASE_URL = "https://api.vworld.kr/req/wfs"

LAYERS = {
    "roads": "lt_l_moctlink",
    "buildings": "lt_c_spbd",
    "hydro": "lt_c_wkmstrm",
    "admin_boundary": "lt_c_adsigg",
}


def _grid_bboxes(bbox: tuple[float, float, float, float], step_deg: float = 0.1):
    west, south, east, north = bbox
    lat = south
    while lat < north:
        lon = west
        top = min(lat + step_deg, north)
        while lon < east:
            right = min(lon + step_deg, east)
            yield (lon, lat, right, top)
            lon = right
        lat = top


def fetch_layer(typename: str, bbox: tuple[float, float, float, float]) -> dict | None:
    """단일 bbox에 대해 WFS GetFeature 시도. 실패하면 None."""
    try:
        payload = get_json(BASE_URL, {
            "SERVICE": "WFS",
            "VERSION": "1.1.0",
            "REQUEST": "GetFeature",
            "KEY": config.VWORLD_API_KEY,
            "TYPENAME": typename,
            "BBOX": ",".join(str(round(v, 5)) for v in bbox),
            "MAXFEATURES": 1000,
            "OUTPUT": "application/json",
        }, timeout=15, retries=0)
    except ApiError:
        return None
    if "features" not in payload:
        return None
    return payload


def region_layers(region_key: str, bbox: tuple[float, float, float, float]) -> dict:
    """기획서 5.3 원칙대로 bbox를 격자로 나눠 반복 호출, 실패 시 available=False."""
    result: dict[str, dict] = {}
    for layer_name, typename in LAYERS.items():
        features: list[dict] = []
        ok = False
        for sub_bbox in _grid_bboxes(bbox):
            payload = fetch_layer(typename, sub_bbox)
            if payload is None:
                ok = False
                break
            ok = True
            features.extend(payload.get("features", []))
        result[layer_name] = {
            "available": ok,
            "featureCount": len(features),
            "features": features if ok else [],
        }
    return result
