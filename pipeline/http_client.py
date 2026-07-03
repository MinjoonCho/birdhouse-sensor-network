"""공용 HTTP GET 헬퍼 (urllib, 추가 의존성 없음)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    pass


def get_json(base_url: str, params: dict, timeout: int = 20, retries: int = 2) -> dict:
    query = urllib.parse.urlencode(params, safe=",")
    url = f"{base_url}?{query}"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise ApiError(f"GET failed for {base_url}: {last_err}")


def get_text(base_url: str, params: dict, timeout: int = 20) -> str:
    query = urllib.parse.urlencode(params, safe=",")
    url = f"{base_url}?{query}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")
