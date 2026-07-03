#!/usr/bin/env python3
"""정적 파일 서버 + 발화 지점 시뮬레이션 API.

대부분의 분석 결과는 run_pipeline.py가 미리 만든 web/data/*.json을 그대로
서빙하지만, 사용자가 지도에서 직접 지정한 지점의 "지금 시뮬레이션"은
그 자리에서 계산해야 하므로 /api/simulate 엔드포인트만 추가한다.
RegionDEM과 카메라 후보지는 지역별로 메모리에 캐시해 반복 요청을 빠르게 한다.
"""

import json
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import config
from pipeline.dem import load_region_dem
from pipeline.simulate import simulate_ignition

ROOT = Path(__file__).resolve().parent / "web"

_dem_cache: dict[str, object] = {}
_region_data_cache: dict[str, dict] = {}


def _get_dem(region_key: str):
    if region_key not in _dem_cache:
        _dem_cache[region_key] = load_region_dem(region_key)
    return _dem_cache[region_key]


def _get_region_data(region_key: str) -> dict:
    if region_key not in _region_data_cache:
        path = config.WEB_DATA_DIR / f"{region_key}.json"
        _region_data_cache[region_key] = json.loads(path.read_text(encoding="utf-8"))
    return _region_data_cache[region_key]


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _flatten_recommended_cameras(region_data: dict) -> list[dict]:
    """산별 그래프 최적화 결과(mountainCoverage)를 즉석 시뮬레이션이 쓰는
    {id, lon, lat} 형태의 평평한 카메라 목록으로 펼친다."""
    cameras = []
    for mountain in region_data.get("mountainCoverage", []):
        for cam in mountain.get("recommendedCameras", []):
            cameras.append({
                "id": f"{mountain['mountainId']}-cam{cam['order']}",
                "lon": cam["lon"], "lat": cam["lat"],
            })
    return cameras


def handle_simulate(query: dict) -> tuple[int, bytes]:
    region_key = query.get("region", [""])[0]
    if region_key not in config.REGIONS:
        return 400, _json_bytes({"error": "invalid_region"})
    try:
        lon = float(query.get("lon", [""])[0])
        lat = float(query.get("lat", [""])[0])
    except (ValueError, IndexError):
        return 400, _json_bytes({"error": "invalid_point"})
    season = query.get("season", ["봄"])[0]
    if season not in ("봄", "여름", "가을", "겨울"):
        return 400, _json_bytes({"error": "invalid_season"})

    dem = _get_dem(region_key)
    if not dem.in_bounds(lon, lat):
        return 422, _json_bytes({"error": "out_of_bounds", "message": "선택한 지점이 DEM 분석 범위 밖입니다."})

    region_data = _get_region_data(region_key)
    wind_bundle = region_data.get("windBySeason", {}).get(season) or region_data.get("wind", {})
    camera_candidates = _flatten_recommended_cameras(region_data)

    try:
        result = simulate_ignition(
            dem, camera_candidates, lon, lat,
            wind_bundle.get("windRose", {}), wind_bundle.get("avgWindSpeedMs"),
        )
    except Exception as exc:  # noqa: BLE001
        return 500, _json_bytes({"error": "simulate_failed", "message": str(exc)})

    result["season"] = season
    result["windSource"] = wind_bundle.get("source", "unavailable")
    return 200, _json_bytes(result)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/simulate":
            query = urllib.parse.parse_qs(parsed.query)
            status, body = handle_simulate(query)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8010), Handler)
    print("Serving http://127.0.0.1:8010")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
