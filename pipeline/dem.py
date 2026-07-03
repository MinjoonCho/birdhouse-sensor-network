"""NGII 공개DEM(ascii xyz, EPSG:5186) 로더 + 표고/경사/사면방향 + line-of-sight.

GDAL/numpy 없이 순수 파이썬으로 처리한다. 격자 간격은 90m.
"""

from __future__ import annotations

import math
import pickle
import zipfile
from array import array
from dataclasses import dataclass
from pathlib import Path

from . import config
from .projection import TMParams, haversine_m, tm_forward, tm_inverse

DEM_PARAMS = TMParams(**config.DEM_TM_PARAMS)


@dataclass
class Tile:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    cellsize: float
    ncols: int
    nrows: int
    z: array  # flat, row-major from ymin row upward: idx = row*ncols + col

    def contains(self, x: float, y: float) -> bool:
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax

    def _z_at(self, col: int, row: int) -> float | None:
        if col < 0 or row < 0 or col >= self.ncols or row >= self.nrows:
            return None
        v = self.z[row * self.ncols + col]
        return None if v == NODATA else v

    def elevation(self, x: float, y: float) -> float | None:
        fx = (x - self.xmin) / self.cellsize
        fy = (y - self.ymin) / self.cellsize
        col, row = int(math.floor(fx)), int(math.floor(fy))
        tx, ty = fx - col, fy - row

        corners = [
            self._z_at(col, row),
            self._z_at(col + 1, row),
            self._z_at(col, row + 1),
            self._z_at(col + 1, row + 1),
        ]
        known = [c for c in corners if c is not None]
        if not known:
            return None
        z00, z10, z01, z11 = (c if c is not None else sum(known) / len(known) for c in corners)
        z0 = z00 * (1 - tx) + z10 * tx
        z1 = z01 * (1 - tx) + z11 * tx
        return z0 * (1 - ty) + z1 * ty


NODATA = -9999.0


def _read_xyz_text(text: str) -> Tile:
    xs: set[float] = set()
    ys: set[float] = set()
    points: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
        xs.add(round(x, 2))
        ys.add(round(y, 2))
        points.append((x, y, z))

    xs_sorted = sorted(xs)
    ys_sorted = sorted(ys)
    ncols, nrows = len(xs_sorted), len(ys_sorted)
    cellsize = round(xs_sorted[1] - xs_sorted[0], 3) if ncols > 1 else 90.0
    xmin, xmax = xs_sorted[0], xs_sorted[-1]
    ymin, ymax = ys_sorted[0], ys_sorted[-1]

    grid = array("f", [NODATA] * (ncols * nrows))
    for x, y, z in points:
        col = round((x - xmin) / cellsize)
        row = round((y - ymin) / cellsize)
        if 0 <= col < ncols and 0 <= row < nrows:
            grid[row * ncols + col] = z

    return Tile(xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax, cellsize=cellsize,
                ncols=ncols, nrows=nrows, z=grid)


def load_tile(dem_dir: Path, tile_id: str) -> Tile:
    cache_path = config.CACHE_DIR / "dem" / f"{tile_id}.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        with cache_path.open("rb") as f:
            return pickle.load(f)

    zip_candidates = list(dem_dir.glob(f"*{tile_id}_ascii_*.zip"))
    if not zip_candidates:
        raise FileNotFoundError(f"DEM ascii zip for tile {tile_id} not found in {dem_dir}")
    zip_path = zip_candidates[0]

    with zipfile.ZipFile(zip_path) as zf:
        xyz_name = next(n for n in zf.namelist() if n.lower().endswith(".xyz"))
        text = zf.read(xyz_name).decode("utf-8", errors="ignore")

    tile = _read_xyz_text(text)
    with cache_path.open("wb") as f:
        pickle.dump(tile, f)
    return tile


class RegionDEM:
    def __init__(self, tiles: list[Tile], region_key: str = ""):
        self.tiles = tiles
        self.region_key = region_key
        self.xmin = min(t.xmin for t in tiles)
        self.xmax = max(t.xmax for t in tiles)
        self.ymin = min(t.ymin for t in tiles)
        self.ymax = max(t.ymax for t in tiles)
        self.cellsize = tiles[0].cellsize

    def lonlat_bbox(self) -> tuple[float, float, float, float]:
        lon1, lat1 = tm_inverse(self.xmin, self.ymin, DEM_PARAMS)
        lon2, lat2 = tm_inverse(self.xmax, self.ymax, DEM_PARAMS)
        return min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2)

    def _tile_for(self, x: float, y: float) -> Tile | None:
        for t in self.tiles:
            if t.contains(x, y):
                return t
        # nearest tile fallback (small edge gaps between adjacent sheets)
        best, best_d = None, float("inf")
        for t in self.tiles:
            cx = min(max(x, t.xmin), t.xmax)
            cy = min(max(y, t.ymin), t.ymax)
            d = (cx - x) ** 2 + (cy - y) ** 2
            if d < best_d:
                best, best_d = t, d
        return best if best_d < (500 ** 2) else None

    def elevation_xy(self, x: float, y: float) -> float | None:
        tile = self._tile_for(x, y)
        if tile is None:
            return None
        return tile.elevation(x, y)

    def elevation(self, lon: float, lat: float) -> float | None:
        x, y = tm_forward(lon, lat, DEM_PARAMS)
        return self.elevation_xy(x, y)

    def slope_aspect(self, lon: float, lat: float) -> tuple[float | None, float | None]:
        """경사(度)와 사면방향(度, 0=N,90=E)을 중앙차분으로 근사."""
        x, y = tm_forward(lon, lat, DEM_PARAMS)
        d = self.cellsize
        z_e = self.elevation_xy(x + d, y)
        z_w = self.elevation_xy(x - d, y)
        z_n = self.elevation_xy(x, y + d)
        z_s = self.elevation_xy(x, y - d)
        if None in (z_e, z_w, z_n, z_s):
            return None, None
        dzdx = (z_e - z_w) / (2 * d)
        dzdy = (z_n - z_s) / (2 * d)
        slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
        aspect = (math.degrees(math.atan2(dzdx, dzdy))) % 360
        return slope, aspect

    def in_bounds(self, lon: float, lat: float) -> bool:
        x, y = tm_forward(lon, lat, DEM_PARAMS)
        return self._tile_for(x, y) is not None


def load_region_dem(region_key: str) -> RegionDEM:
    cfg = config.REGIONS[region_key]
    tiles = [load_tile(cfg.dem_dir, tile_id) for tile_id in cfg.dem_tiles]
    return RegionDEM(tiles, region_key=region_key)


EARTH_RADIUS_CORRECTION = 1.0 / (2 * 6371000.0) * 0.87  # 지구 곡률 - 대기굴절 보정(표준값 k=0.13)

_DECAY_NEAR_M = 400.0
_DECAY_FAR_M = 1000.0
_DECAY_FAR_FACTOR = 0.35


def _distance_decay(dist_m: float) -> float:
    """새집형 센서의 실측 탐지 거리는 500m~1km 수준이다. 400m 이내는 감쇠
    없음, 1km에 가까워질수록 35%까지 선형 감쇠(그 이상은 상위 range 상수에서
    아예 차단)."""
    if dist_m <= _DECAY_NEAR_M:
        return 1.0
    if dist_m >= _DECAY_FAR_M:
        return _DECAY_FAR_FACTOR
    t = (dist_m - _DECAY_NEAR_M) / (_DECAY_FAR_M - _DECAY_NEAR_M)
    return 1.0 - t * (1.0 - _DECAY_FAR_FACTOR)


def line_of_sight(
    dem: RegionDEM,
    observer_lonlat: tuple[float, float],
    observer_height_m: float,
    target_lonlat: tuple[float, float],
    target_height_m: float,
    sample_step_m: float = 90.0,
) -> dict:
    """단순 line-of-sight: 두 지점을 잇는 직선 위 DEM 표고 프로파일을 샘플링해
    중간 지형이 시야선을 가리는지 판정한다(지구 곡률/대기굴절 보정 포함).
    """
    ox, oy = tm_forward(observer_lonlat[0], observer_lonlat[1], DEM_PARAMS)
    tx, ty = tm_forward(target_lonlat[0], target_lonlat[1], DEM_PARAMS)
    dist = math.hypot(tx - ox, ty - oy)
    if dist < 1e-6:
        return {"visible": True, "distance_m": 0.0, "clearance_min_m": 0.0, "score": 100.0}

    n_samples = max(2, int(dist / sample_step_m))
    z_obs_ground = dem.elevation_xy(ox, oy)
    z_tgt_ground = dem.elevation_xy(tx, ty)
    if z_obs_ground is None or z_tgt_ground is None:
        return {"visible": False, "distance_m": dist, "clearance_min_m": None, "score": 0.0,
                "reason": "dem_no_data"}

    eye_h = z_obs_ground + observer_height_m
    tgt_h = z_tgt_ground + target_height_m

    min_clearance = float("inf")
    blocked_at_m = None
    for i in range(1, n_samples):
        frac = i / n_samples
        sx = ox + (tx - ox) * frac
        sy = oy + (ty - oy) * frac
        ground = dem.elevation_xy(sx, sy)
        if ground is None:
            continue
        d_here = dist * frac
        curvature_drop = EARTH_RADIUS_CORRECTION * d_here * (dist - d_here)
        line_h = eye_h * (1 - frac) + tgt_h * frac - curvature_drop
        clearance = line_h - ground
        if clearance < min_clearance:
            min_clearance = clearance
            if clearance < 0:
                blocked_at_m = d_here

    visible = min_clearance >= 0
    if visible:
        score = max(0.0, min(100.0, 50.0 + min_clearance / max(1.0, dist * 0.02) * 10.0))
    else:
        score = max(0.0, 40.0 + min_clearance)  # 음수 clearance일수록 감점
    score *= _distance_decay(dist)

    return {
        "visible": visible,
        "distance_m": round(dist, 1),
        "clearance_min_m": round(min_clearance, 1),
        "blocked_at_m": round(blocked_at_m, 1) if blocked_at_m else None,
        "score": round(score, 1),
    }
