#!/usr/bin/env python3
"""새집형 산불 조기감지 노드 배치 시뮬레이션 - 분석 파이프라인 실행.

사용:
    python run_pipeline.py --region uiseong
    python run_pipeline.py --region bonghwa
    python run_pipeline.py --region all
"""

from __future__ import annotations

import argparse
import json
import time

from pipeline import config
from pipeline.pipeline import run_region


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=["uiseong", "bonghwa", "all"], default="all")
    args = parser.parse_args()

    regions = list(config.REGIONS.keys()) if args.region == "all" else [args.region]

    config.WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for region_key in regions:
        t0 = time.time()
        result = run_region(region_key)
        out_path = config.WEB_DATA_DIR / f"{region_key}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.time() - t0
        print(
            f"[{region_key}] 완료 ({elapsed:.1f}s) -> {out_path} "
            f"(발화후보 {len(result['ignitionCandidates'])}, 카메라후보 {len(result['cameraCandidates'])}, "
            f"경로 {len(result['smokePaths'])})"
        )
        print(f"  sourceStatus: {result['sourceStatus']}")


if __name__ == "__main__":
    main()
