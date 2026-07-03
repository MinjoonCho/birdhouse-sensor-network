"""기상청 ASOS 시간자료(AsosHourlyInfoService) 수집.

계절별(봄/여름/가을/겨울) 오후(12~18시) 바람 방향 분포(16방위)를 확률로 만들고,
광학 신뢰도 계산에 쓸 시간대별 습도/시정/강수 프로필, 그리고 UI의
"시간 선택"·"발화 지점 시뮬레이션" 데모용 대표 하루 시계열을 만든다.
"""

from __future__ import annotations

import datetime as dt

from .. import config
from ..http_client import ApiError, get_json

BASE_URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"

DIRECTIONS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

SEASON_MONTHS = {"봄": (3, 5), "여름": (6, 8), "가을": (9, 11), "겨울": (12, 2)}


def _direction_bin(wd_deg: float) -> str:
    idx = int(round(wd_deg / 22.5)) % 16
    return DIRECTIONS[idx]


def _fetch_range(station_id: str, start_dt: str, end_dt: str, page_size: int = 999) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        payload = get_json(BASE_URL, {
            "serviceKey": config.DATA_GO_KR_API_KEY,
            "pageNo": page,
            "numOfRows": page_size,
            "dataType": "JSON",
            "dataCd": "ASOS",
            "dateCd": "HR",
            "startDt": start_dt,
            "startHh": "00",
            "endDt": end_dt,
            "endHh": "23",
            "stnIds": station_id,
        })
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


def _season_ranges(season: str, n_years: int) -> list[tuple[str, str]]:
    """계절별 (startDt, endDt) 목록. 겨울은 12월~다음해 2월로 연도를 넘긴다."""
    start_month, end_month = SEASON_MONTHS[season]
    today = dt.date.today()
    current_year = today.year if today.month >= end_month or start_month > end_month else today.year - 1
    ranges = []
    for y in range(current_year - n_years + 1, current_year + 1):
        if start_month > end_month:  # 겨울: 12월(y) ~ 2월(y+1)
            start = f"{y}{start_month:02d}01"
            end_year = y + 1
            end_day = 28
            end = f"{end_year}{end_month:02d}{end_day:02d}"
        else:
            start = f"{y}{start_month:02d}01"
            end_day = 30 if end_month in (4, 6, 9, 11) else 31
            end = f"{y}{end_month:02d}{end_day:02d}"
        ranges.append((start, end))
    return ranges


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _aggregate_wind(rows: list[dict]) -> dict:
    afternoon = [r for r in rows if 12 <= int(r.get("tm", "0000-00-00 00")[-5:-3] or 0) <= 18]
    dir_counts = {d: 0 for d in DIRECTIONS}
    calm = 0
    speed_sum, speed_n = 0.0, 0
    for r in afternoon:
        ws = _to_float(r.get("ws"), 0.0)
        wd = _to_float(r.get("wd"))
        if ws is not None:
            speed_sum += ws
            speed_n += 1
        if ws is not None and ws < 0.3:
            calm += 1
        elif wd is not None:
            dir_counts[_direction_bin(wd)] += 1

    total_dir = sum(dir_counts.values()) or 1
    wind_rose = {d: round(c / total_dir * 100, 1) for d, c in dir_counts.items()}
    avg_speed = round(speed_sum / speed_n, 2) if speed_n else None
    calm_pct = round(calm / max(1, len(afternoon)) * 100, 1)
    return {"windRose": wind_rose, "avgWindSpeedMs": avg_speed, "calmPct": calm_pct}


def _hourly_profile(rows: list[dict]) -> dict:
    hourly_profile: dict[str, dict] = {}
    for hour in range(24):
        hour_rows = [r for r in rows if r.get("tm", "").endswith(f"{hour:02d}:00")]
        if not hour_rows:
            continue
        hums = [v for v in (_to_float(r.get("hm")) for r in hour_rows) if v is not None]
        viss = [v for v in (_to_float(r.get("vs")) for r in hour_rows) if v is not None]
        rains = [1 for r in hour_rows if _to_float(r.get("rn")) and _to_float(r.get("rn")) > 0]
        hourly_profile[str(hour)] = {
            "avgHumidityPct": round(sum(hums) / len(hums), 1) if hums else None,
            "avgVisibilityM": round(sum(viss) / len(viss), 0) if viss else None,
            "precipProbPct": round(len(rains) / len(hour_rows) * 100, 1),
        }
    return hourly_profile


def _sample_day(rows: list[dict]) -> dict | None:
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        date_part = r.get("tm", "")[:10]
        by_date.setdefault(date_part, []).append(r)
    for date_part in sorted(by_date.keys(), reverse=True):
        day_rows = sorted(by_date[date_part], key=lambda r: r.get("tm", ""))
        if len(day_rows) >= 20:
            return {
                "date": date_part,
                "hours": [
                    {
                        "hour": int(r.get("tm", "0000-00-00 00")[-5:-3]),
                        "wd": _to_float(r.get("wd")),
                        "ws": _to_float(r.get("ws")),
                        "ta": _to_float(r.get("ta")),
                        "hm": _to_float(r.get("hm")),
                        "rn": _to_float(r.get("rn"), 0.0),
                        "vs": _to_float(r.get("vs")),
                    }
                    for r in day_rows
                ],
            }
    return None


def _season_bundle(station_id: str, season: str, n_years: int) -> dict:
    ranges = _season_ranges(season, n_years)
    rows: list[dict] = []
    try:
        for start_dt, end_dt in ranges:
            rows.extend(_fetch_range(station_id, start_dt, end_dt))
        source = "live" if rows else "unavailable"
    except ApiError:
        source = "unavailable"

    if not rows:
        return {"source": "unavailable", "windRose": {}, "avgWindSpeedMs": None,
                "calmPct": None, "opticalHourlyProfile": {}, "sampleDay": None,
                "yearsUsed": []}

    result = {"source": source, **_aggregate_wind(rows),
              "opticalHourlyProfile": _hourly_profile(rows), "sampleDay": _sample_day(rows),
              "yearsUsed": sorted({int(s[:4]) for s, _ in ranges})}
    return result


def region_wind_and_optical(region_key: str) -> dict:
    """기존 호환용: 봄철(3개 시즌) 결과만 반환."""
    cfg = config.REGIONS[region_key]
    return _season_bundle(cfg.asos_station_id, "봄", n_years=3)


def region_wind_by_season(region_key: str) -> dict:
    """4계절 각각의 windRose/광학 프로필. 발화 지점 시뮬레이션(계절 선택)에 사용.
    봄은 이미 main 파이프라인에서 3개 시즌으로 계산하므로 재사용하고, 나머지
    계절은 API 호출량을 아끼기 위해 최근 2개 시즌만 사용한다.
    """
    cfg = config.REGIONS[region_key]
    bundle = {"봄": _season_bundle(cfg.asos_station_id, "봄", n_years=3)}
    for season in ("여름", "가을", "겨울"):
        bundle[season] = _season_bundle(cfg.asos_station_id, season, n_years=2)
    return bundle
