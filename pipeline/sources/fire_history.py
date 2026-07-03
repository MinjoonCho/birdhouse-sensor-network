"""산림청 산불발생통계(forestStusService) 수집.

이 API는 시/군/구/동/번지 행정명만 주고 위도·경도를 주지 않는다. 따라서
개별 과거 산불에 좌표를 임의로 부여하지 않고, 지역·월·계절·원인별
집계 통계로만 사용해 발화 후보지 생성의 "위험 가중치"에 반영한다
(기획서 6.1의 위험 점수 산출 재료 중 하나).
"""

from __future__ import annotations

from .. import config
from ..http_client import ApiError, get_json

BASE_URL = "https://apis.data.go.kr/1400000/forestStusService/getfirestatsservice"

SEASON_MAP = {
    12: "겨울", 1: "겨울", 2: "겨울",
    3: "봄", 4: "봄", 5: "봄",
    6: "여름", 7: "여름", 8: "여름",
    9: "가을", 10: "가을", 11: "가을",
}


def fetch_all_records(page_size: int = 1000, max_pages: int = 20) -> list[dict]:
    records: list[dict] = []
    page = 1
    while page <= max_pages:
        try:
            payload = get_json(BASE_URL, {
                "serviceKey": config.DATA_GO_KR_API_KEY,
                "pageNo": page,
                "numOfRows": page_size,
                "_type": "json",
            })
        except ApiError:
            break
        body = payload.get("response", {}).get("body", {})
        items = body.get("items", {})
        item = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(item, dict):
            item = [item]
        if not item:
            break
        records.extend(item)
        total = int(body.get("totalCount", 0))
        if page * page_size >= total:
            break
        page += 1
    return records


def region_summary(region_key: str) -> dict:
    """지역 필터링 + 월별/계절별/원인별 집계."""
    cfg = config.REGIONS[region_key]
    try:
        all_records = fetch_all_records()
        source = "live"
    except Exception:
        all_records = []
        source = "unavailable"

    matched = [
        r for r in all_records
        if cfg.sido_ko in str(r.get("locsi", "")) and cfg.gungu_ko in str(r.get("locgungu", ""))
    ]

    by_month = {m: 0 for m in range(1, 13)}
    by_season = {"봄": 0, "여름": 0, "가을": 0, "겨울": 0}
    by_cause: dict[str, int] = {}
    total_damage_area = 0.0
    years_seen = set()

    for r in matched:
        try:
            month = int(r.get("startmonth", 0))
        except (TypeError, ValueError):
            month = 0
        if month in by_month:
            by_month[month] += 1
            by_season[SEASON_MAP[month]] += 1
        cause = r.get("firecause") or "미상"
        by_cause[cause] = by_cause.get(cause, 0) + 1
        try:
            total_damage_area += float(r.get("damagearea") or 0)
        except (TypeError, ValueError):
            pass
        if r.get("startyear"):
            years_seen.add(r.get("startyear"))

    return {
        "source": source,
        "totalMatched": len(matched),
        "byMonth": by_month,
        "bySeason": by_season,
        "byCause": by_cause,
        "totalDamageAreaHa": round(total_damage_area, 2),
        "yearsCovered": sorted(years_seen),
    }
