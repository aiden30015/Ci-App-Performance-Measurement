"""metrics-collector + metrics-analyzer.

collector 출력(docs/DESIGN.md §4.1)을 지표로 정규화하고, 여러 번의 반복 측정을
outlier 제거 + median 으로 집계한다. baseline 이 뭔지 모른다.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

# 지표 정의 한 곳. (이름, 단위, 값이 클수록 좋은가)
METRICS: dict[str, tuple[str, bool]] = {
    "fps": ("", True),
    "jank_rate": ("%", False),
    "dropped_frames": ("", False),
    "p50_frame_time_ms": ("ms", False),
    "p90_frame_time_ms": ("ms", False),
    "p95_frame_time_ms": ("ms", False),
    "p99_frame_time_ms": ("ms", False),
    "memory_mb": ("MB", False),
    "startup_ms": ("ms", False),            # 엔진 진입 → 첫 프레임
    "startup_rasterized_ms": ("ms", False),  # 엔진 진입 → 첫 프레임 래스터 완료
    "duration_ms": ("ms", False),
}

# MAD(median absolute deviation) → 정규분포 표준편차 환산 상수.
_MAD_TO_SIGMA = 1.4826


def percentile(sorted_values: list[float], p: float) -> float:
    """선형 보간 백분위수. numpy 없이 쓰려고 직접 계산한다."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p / 100
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def normalize(raw: dict) -> dict[str, float]:
    """collector JSON 한 개 → 지표 dict.

    프레임 총 시간 = build + raster (같은 인덱스끼리). 두 배열 길이가 다를 수 있어
    짧은 쪽에 맞춘다. TimelineSummary 는 마이크로초 단위 정수를 준다.
    """
    tl = raw.get("timeline") or {}   # 요약 실패 시 None 이 들어온다
    build = [v / 1000 for v in tl.get("frame_build_times", [])]
    raster = [v / 1000 for v in tl.get("frame_rasterizer_times", [])]
    n = min(len(build), len(raster))
    total = [build[i] + raster[i] for i in range(n)] or build or raster
    total_sorted = sorted(total)

    budget = float(raw.get("frame_budget_ms") or 16.67)
    janky = sum(1 for t in total if t > budget)
    duration_ms = float(raw.get("duration_ms") or 0)

    out: dict[str, float] = {}
    if raw.get("duration_ms") is not None:
        out["duration_ms"] = duration_ms
    # 프레임이 하나도 없으면 프레임 지표를 0 으로 채우지 않고 아예 뺀다.
    # 0 FPS / 0% jank 는 "측정 안 됨"이지 "완벽함"이 아니다.
    if total:
        p50 = percentile(total_sorted, 50)
        out.update({
            # "이 프레임들이 유지할 수 있었던 프레임률" = min(주사율, 1000/p50).
            # frame_count / wall_clock 은 쓰지 않는다 — 테스트 하네스가 제스처
            # 사이에 노는 시간까지 분모에 들어가서 2 fps 같은 값이 나온다.
            "fps": min(1000 / budget, 1000 / p50) if p50 else 1000 / budget,
            "jank_rate": janky / len(total) * 100,
            "dropped_frames": float(janky),
            "p50_frame_time_ms": p50,
            "p90_frame_time_ms": percentile(total_sorted, 90),
            "p95_frame_time_ms": percentile(total_sorted, 95),
            "p99_frame_time_ms": percentile(total_sorted, 99),
        })
    mem = raw.get("memory") or {}
    if mem.get("rss_mb") is not None:
        out["memory_mb"] = float(mem["rss_mb"])
    for key in ("startup_ms", "startup_rasterized_ms"):
        if raw.get(key) is not None:
            out[key] = float(raw[key])
    return out


def summarize(samples: list[float]) -> tuple[float, float, list[int]]:
    """반복 측정값 → (median, noise, 버린 인덱스).

    outlier 는 median±3·MAD 밖. 단 표본이 2개 미만으로 줄어들면 버리지 않는다
    (전부 노이즈인 경우 지우는 쪽이 더 위험하다).
    """
    if not samples:
        return 0.0, 0.0, []
    med = statistics.median(samples)
    mad = statistics.median([abs(s - med) for s in samples]) * _MAD_TO_SIGMA
    dropped: list[int] = []
    if mad > 0 and len(samples) >= 4:
        dropped = [i for i, s in enumerate(samples) if abs(s - med) > 3 * mad]
        if len(samples) - len(dropped) < 2:
            dropped = []
    kept = [s for i, s in enumerate(samples) if i not in dropped]
    med = statistics.median(kept)
    noise = statistics.median([abs(s - med) for s in kept]) * _MAD_TO_SIGMA
    if noise == 0 and len(kept) > 1:
        # 표본이 적으면 MAD 가 0으로 붕괴한다. 그때는 범위를 노이즈로 쓴다.
        noise = (max(kept) - min(kept)) / 2
    return med, noise, dropped


def aggregate(run_dirs: list[Path], scenarios: list[str] | None = None) -> dict:
    """runs/run-*/ 여러 개 → current.json (DESIGN §4.2)."""
    per_scenario: dict[str, list[dict[str, float]]] = {}
    for d in run_dirs:
        for f in sorted(Path(d).glob("*.json")):
            raw = json.loads(f.read_text(encoding="utf-8"))
            name = raw.get("scenario") or f.stem
            if scenarios and name not in scenarios:
                continue
            per_scenario.setdefault(name, []).append(normalize(raw))

    result: dict[str, dict] = {}
    for name, runs in per_scenario.items():
        metrics: dict[str, dict] = {}
        dropped_runs: set[int] = set()
        for key in METRICS:
            samples = [r[key] for r in runs if key in r]
            if not samples:
                continue
            value, noise, dropped = summarize(samples)
            dropped_runs.update(dropped)
            metrics[key] = {
                "value": round(value, 4),
                "noise": round(noise, 4),
                "samples": [round(s, 4) for s in samples],
            }
        result[name] = {
            # outlier 는 지표별로 판단하므로 "이 run 전체를 버렸다"가 아니라
            # "몇 개의 run 이 어떤 지표에서 튀었다" 는 표시다.
            "runs": len(runs),
            "runs_flagged": len(dropped_runs),
            "metrics": metrics,
        }
    return {"schema": 1, "repeats": len(run_dirs), "scenarios": result}
