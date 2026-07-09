#!/usr/bin/env python3
"""정적 파일 서버 + 발화 지점 시뮬레이션 API.

대부분의 분석 결과는 run_pipeline.py가 미리 만든 web/data/*.json을 그대로
서빙하지만, 사용자가 지도에서 직접 지정한 지점의 "지금 시뮬레이션"은
그 자리에서 계산해야 하므로 /api/simulate 엔드포인트만 추가한다.
RegionDEM과 카메라 후보지는 지역별로 메모리에 캐시해 반복 요청을 빠르게 한다.
"""

import json
import pickle
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import camera_placement, config, fire_graph, mountains
from pipeline.dem import load_region_dem
from pipeline.pipeline import GRAPH_CACHE_DIR, _cache_graph_and_mountains
from pipeline.projection import haversine_m
from pipeline.simulate import simulate_ignition

ROOT = Path(__file__).resolve().parent / "web"

_dem_cache: dict[str, object] = {}
_region_data_cache: dict[str, dict] = {}
_graph_cache: dict[str, tuple] = {}


def _get_dem(region_key: str):
    if region_key not in _dem_cache:
        _dem_cache[region_key] = load_region_dem(region_key)
    return _dem_cache[region_key]


def _get_graph_and_mountains(region_key: str) -> tuple:
    """산불 확산 그래프와 산 분할 결과를 반환한다. run_pipeline.py가 이미
    계산해 디스크에 캐시해뒀다면 즉시 로드하고(수 초), 없으면 그 자리에서
    다시 계산한다(의성 기준 산 분할만 약 90초 걸릴 수 있음 - 최초 1회뿐이며
    이후 메모리에 캐시된다)."""
    if region_key in _graph_cache:
        return _graph_cache[region_key]

    cache_path = GRAPH_CACHE_DIR / f"{region_key}.pkl"
    if cache_path.exists():
        with cache_path.open("rb") as f:
            cached = pickle.load(f)
        result = (cached["graph"], cached["mountains"])
    else:
        dem = _get_dem(region_key)
        graph = fire_graph.build_fire_graph(dem)
        mountain_list = mountains.segment_mountains(dem, graph.coords)
        _cache_graph_and_mountains(region_key, graph, mountain_list)
        result = (graph, mountain_list)

    _graph_cache[region_key] = result
    return result


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


MAX_CUSTOM_AREA_RADIUS_M = 5000.0


def handle_plan_cameras(query: dict) -> tuple[int, bytes]:
    """실시간 카메라 배치 질의. 두 입력 형태를 지원한다:
    - mountainId: 미리 분할해 둔 산 하나를 그대로 쓴다.
    - lon/lat(+radiusM): 그 지점 반경 안의 그래프 노드로 즉석 영역을 만든다.
    목표는 objective(worst|average) + targetMinutes 또는 cameraCount 중 하나로 준다."""
    region_key = query.get("region", [""])[0]
    if region_key not in config.REGIONS:
        return 400, _json_bytes({"error": "invalid_region"})

    objective = query.get("objective", ["worst"])[0]
    if objective not in ("worst", "average"):
        return 400, _json_bytes({"error": "invalid_objective"})

    target_raw = query.get("targetMinutes", [None])[0]
    count_raw = query.get("cameraCount", [None])[0]
    try:
        target_minutes = float(target_raw) if target_raw not in (None, "") else None
        camera_count = int(count_raw) if count_raw not in (None, "") else None
    except ValueError:
        return 400, _json_bytes({"error": "invalid_target"})
    if target_minutes is None and camera_count is None:
        return 400, _json_bytes({"error": "missing_target", "message": "targetMinutes 또는 cameraCount 중 하나가 필요합니다."})
    if camera_count is not None and camera_count > camera_placement.DEFAULT_MAX_CAMERAS:
        camera_count = camera_placement.DEFAULT_MAX_CAMERAS

    try:
        graph, mountain_list = _get_graph_and_mountains(region_key)
    except Exception as exc:  # noqa: BLE001
        return 500, _json_bytes({"error": "graph_unavailable", "message": str(exc)})

    mountain_id = query.get("mountainId", [None])[0]
    if mountain_id:
        mountain = next((m for m in mountain_list if m["mountainId"] == mountain_id), None)
        if mountain is None:
            return 404, _json_bytes({"error": "mountain_not_found"})
        node_indices, seed, area_id = mountain["nodeIndices"], mountain["seed"], mountain_id
    else:
        try:
            lon = float(query.get("lon", [""])[0])
            lat = float(query.get("lat", [""])[0])
        except (ValueError, IndexError):
            return 400, _json_bytes({"error": "invalid_point"})
        radius_raw = query.get("radiusM", ["1500"])[0]
        try:
            radius_m = min(MAX_CUSTOM_AREA_RADIUS_M, max(300.0, float(radius_raw)))
        except ValueError:
            return 400, _json_bytes({"error": "invalid_radius"})

        node_indices = [
            i for i, (lo, la) in enumerate(graph.coords)
            if haversine_m(lon, lat, lo, la) <= radius_m
        ]
        if not node_indices:
            return 422, _json_bytes({"error": "no_nodes_in_range", "message": "해당 반경 안에 분석 격자가 없습니다."})
        elevation = graph.elevations[min(node_indices, key=lambda i: haversine_m(lon, lat, *graph.coords[i]))]
        seed = {"lon": lon, "lat": lat, "elevation": elevation}
        area_id = "custom"

    try:
        result = camera_placement.plan_cameras(
            graph, node_indices, seed, objective=objective,
            target_minutes=target_minutes, camera_count=camera_count, area_id=area_id,
        )
    except Exception as exc:  # noqa: BLE001
        return 500, _json_bytes({"error": "plan_failed", "message": str(exc)})

    return 200, _json_bytes(result)


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

    def _write_json_response(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/simulate":
            self._write_json_response(*handle_simulate(query))
            return
        if parsed.path == "/api/plan_cameras":
            self._write_json_response(*handle_plan_cameras(query))
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
