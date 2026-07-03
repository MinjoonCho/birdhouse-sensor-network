"""단순화된 태양 위치(고도/방위각) 계산 - NOAA 근사식, 외부 의존성 없음.

역광 위험(6.8) 계산에 사용. 정밀 천문 계산이 아니라 시간대별 상대 비교용.
"""

from __future__ import annotations

import datetime as dt
import math


def solar_position(date: dt.date, hour: float, lat: float, lon: float, tz_offset_hours: float = 9.0) -> tuple[float, float]:
    """주어진 로컬 시각(시, 0~24)에 대한 (태양고도deg, 태양방위각deg 0=N,90=E)."""
    day_of_year = date.timetuple().tm_yday
    gamma = 2 * math.pi / 365.0 * (day_of_year - 1 + (hour - 12) / 24.0)

    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )

    time_offset = eqtime + 4 * lon - 60 * tz_offset_hours
    tst = hour * 60 + time_offset
    hour_angle = math.radians(tst / 4 - 180)

    lat_r = math.radians(lat)
    cos_zenith = (
        math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle)
    )
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)
    elevation = 90 - math.degrees(zenith)

    cos_az = (math.sin(decl) - math.sin(lat_r) * cos_zenith) / (math.cos(lat_r) * math.sin(zenith) + 1e-12)
    cos_az = max(-1.0, min(1.0, cos_az))
    azimuth = math.degrees(math.acos(cos_az))
    if hour_angle > 0:
        azimuth = 360 - azimuth

    return elevation, azimuth


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """lon1,lat1 -> lon2,lat2 방위각(0=N,90=E)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return math.degrees(math.atan2(x, y)) % 360


def backlight_risk(camera_bearing_deg: float, sun_azimuth_deg: float, sun_elevation_deg: float) -> float:
    """카메라가 바라보는 방향과 태양 방향이 가깝고 태양 고도가 낮을수록 역광 위험 증가. 0~100."""
    if sun_elevation_deg <= 0:
        return 0.0  # 야간에는 역광 개념 없음(적외선/야간 감지는 범위 밖)
    diff = abs((camera_bearing_deg - sun_azimuth_deg + 180) % 360 - 180)
    direction_factor = max(0.0, 1 - diff / 60.0)  # 60도 이상 벌어지면 위험 없음
    elevation_factor = max(0.0, 1 - sun_elevation_deg / 40.0)  # 낮은 태양일수록 위험 ↑
    return round(100 * direction_factor * elevation_factor, 1)
