"""환경변수 로딩 및 지역(의성/봉화) 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEM_ROOTS = {
    "uiseong": Path("D:/NewTech/DEM 의성"),
    "bonghwa": Path("D:/NewTech/DEM 봉화"),
}
TRAIL_ROOT = Path("D:/NewTech/등산로")
CACHE_DIR = ROOT / ".cache"
REFERENCE_DIR = ROOT / "data" / "reference"
WEB_DATA_DIR = ROOT / "web" / "data"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")

DATA_GO_KR_API_KEY = os.environ.get("DATA_GO_KR_API_KEY", "")
VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY", "")

# NGII 공개DEM 좌표계: 중부원점 2010 (EPSG:5186, GRS80)
DEM_EPSG = "5186"
DEM_TM_PARAMS = {
    "lon0_deg": 127.0,
    "lat0_deg": 38.0,
    "fe": 200000.0,
    "fn": 600000.0,
    "k0": 1.0,
    "ellipsoid": "GRS80",
}

# 산림청 등산로 shapefile(EPSG:5179, Korea 2000 / Unified CS)
TRAIL_EPSG = "5179"
TRAIL_TM_PARAMS = {
    "lon0_deg": 127.5,
    "lat0_deg": 38.0,
    "fe": 1000000.0,
    "fn": 2000000.0,
    "k0": 0.9996,
    "ellipsoid": "GRS80",
}


@dataclass
class RegionConfig:
    key: str
    name_ko: str
    sido_ko: str          # forestStusService locsi 매칭용
    gungu_ko: str         # forestStusService locgungu 매칭용
    asos_station_id: str  # 기상청 ASOS 관측소 코드
    dem_dir: Path
    dem_tiles: list[str]  # NGII 도엽번호
    seat_lon: float        # 군청 위치(지도 기본 중심, 근접 대기측정소 매칭용) - 정밀 스코어링에는 미사용
    seat_lat: float
    note: str = ""


REGIONS: dict[str, RegionConfig] = {
    "uiseong": RegionConfig(
        key="uiseong",
        name_ko="의성군",
        sido_ko="경북",
        gungu_ko="의성",
        asos_station_id="278",
        dem_dir=DEM_ROOTS["uiseong"],
        dem_tiles=["36806", "36807", "36808", "36810", "36811", "36812", "36815"],
        seat_lon=128.6970,
        seat_lat=36.3528,
    ),
    "bonghwa": RegionConfig(
        key="bonghwa",
        name_ko="봉화군",
        sido_ko="경북",
        gungu_ko="봉화",
        asos_station_id="271",
        dem_dir=DEM_ROOTS["bonghwa"],
        dem_tiles=["36803"],
        seat_lon=128.7326,
        seat_lat=36.8932,
        note="다운로드된 DEM이 타일 36803 1개뿐이라 해당 타일 범위만 분석됩니다.",
    ),
}

# 에어코리아는 측정소 좌표를 API로 주지 않는다(별도 상품 승인 필요, 현재 키는 403).
# 실시간 조회 결과에 실제로 등장하는 측정소 중 두 지역과 가까운 것만 수작업으로
# 좌표를 매핑해 최근접 매칭에 사용한다. 정밀 좌표가 아니라 "가장 가까운 후보"를
# 고르기 위한 근사치이며, 출처는 공개된 시군 중심 좌표다.
AIR_QUALITY_STATION_HINTS: dict[str, tuple[float, float]] = {
    "의성읍": (36.3528, 128.6970),
    "안동": (36.5684, 128.7294),
    "옥계": (36.5684, 128.7294),
    "용상동": (36.5684, 128.7294),
    "청송읍": (36.4358, 129.0572),
    "군위": (36.2427, 128.5726),
    "구미": (36.1196, 128.3444),
    "영주": (36.8056, 128.6239),
    "봉화읍": (36.8932, 128.7326),
    "예천": (36.6577, 128.4526),
}
