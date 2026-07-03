"""순수 파이썬 Shapefile(.shp/.dbf) 리더.

k-fireguard-wildfire-atlas/shp_to_geojson.py 의 PolyLine 리더를 그대로 가져오고,
어떤 zip이 의성/봉화 등산로인지 식별하기 위한 최소 DBF 속성 리더를 추가한다.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path


def read_polylines(shp_bytes: bytes) -> list[list[list[tuple[float, float]]]]:
    """PolyLine/Polygon(+Z/M) shp 바이트 → feature별 part 좌표 리스트(원본 CRS)."""
    pos = 100
    features: list[list[list[tuple[float, float]]]] = []
    length = len(shp_bytes)
    while pos < length:
        content_len = struct.unpack(">i", shp_bytes[pos + 4:pos + 8])[0]
        rec_start = pos + 8
        shape_type = struct.unpack("<i", shp_bytes[rec_start:rec_start + 4])[0]
        if shape_type in (3, 5, 13, 15, 23, 25):
            off = rec_start + 4 + 32
            num_parts, num_points = struct.unpack("<2i", shp_bytes[off:off + 8])
            off += 8
            parts = list(struct.unpack(f"<{num_parts}i", shp_bytes[off:off + 4 * num_parts]))
            off += 4 * num_parts
            xy = struct.unpack(f"<{2 * num_points}d", shp_bytes[off:off + 16 * num_points])
            rings: list[list[tuple[float, float]]] = []
            for p in range(num_parts):
                start = parts[p]
                end = parts[p + 1] if p + 1 < num_parts else num_points
                ring = [(xy[2 * i], xy[2 * i + 1]) for i in range(start, end)]
                if len(ring) >= 2:
                    rings.append(ring)
            if rings:
                features.append(rings)
        pos = rec_start + content_len * 2
    return features


def read_dbf_records(dbf_bytes: bytes) -> list[dict]:
    """dBase III/IV 속성 레코드를 dict 리스트로 반환 (한글 CP949 가정)."""
    n_records, header_len, record_len = struct.unpack("<I H H", dbf_bytes[4:12])
    fields = []
    pos = 32
    while dbf_bytes[pos] != 0x0D:
        name = dbf_bytes[pos:pos + 11].split(b"\x00")[0].decode("cp949", errors="ignore")
        field_type = chr(dbf_bytes[pos + 11])
        length = dbf_bytes[pos + 16]
        fields.append((name, field_type, length))
        pos += 32

    records = []
    rec_start = header_len
    for i in range(n_records):
        raw = dbf_bytes[rec_start:rec_start + record_len]
        rec_start += record_len
        if not raw or raw[0:1] == b"*":
            continue
        offset = 1
        row = {}
        for name, ftype, length in fields:
            value = raw[offset:offset + length].decode("cp949", errors="ignore").strip()
            row[name] = value
            offset += length
        records.append(row)
    return records


def load_shapefile_from_zip(zip_path: Path) -> tuple[list[list[list[tuple[float, float]]]], list[dict]]:
    """zip 안의 .shp/.dbf 를 읽어 (geometry parts, attribute records) 반환."""
    with zipfile.ZipFile(zip_path) as zf:
        shp_name = next(n for n in zf.namelist() if n.lower().endswith(".shp"))
        dbf_name = next((n for n in zf.namelist() if n.lower().endswith(".dbf")), None)
        shp_bytes = zf.read(shp_name)
        dbf_records = read_dbf_records(zf.read(dbf_name)) if dbf_name else []
    return read_polylines(shp_bytes), dbf_records
