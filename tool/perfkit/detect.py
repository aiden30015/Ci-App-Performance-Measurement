"""regression-detector.

3중 게이트 (docs/DESIGN.md §6.3):
  1) |상대 변화| > threshold_percent
  2) |Δ|        > sigma * sqrt(noise_base^2 + noise_current^2)   ← CI 노이즈
  3) |Δ|        > min_abs_delta                                  ← 체감 하한
셋을 모두 넘고, 방향이 나쁜 쪽일 때만 regression.
"""
from __future__ import annotations

import math


def threshold_for(config: dict, metric: str) -> dict:
    t = config.get("thresholds", {})
    d = dict(t.get("default", {"change_percent": 15, "min_abs_delta": 0}))
    d.update(t.get(metric, {}))
    return d


def judge(rows: list[dict], config: dict) -> list[dict]:
    """compare() 결과에 verdict 를 붙여 돌려준다. rows 는 수정되지 않는다."""
    sigma = float(config.get("sigma", 2.0))
    out = []
    for row in rows:
        if row.get("status") != "ok":
            out.append(dict(row))
            continue

        th = threshold_for(config, row["metric"])
        delta = row["delta"]
        worse = delta < 0 if row["higher_is_better"] else delta > 0
        noise = math.hypot(row.get("baseline_noise", 0.0), row.get("current_noise", 0.0))

        pct = row.get("change_pct")
        over_threshold = (
            abs(pct) > float(th["change_percent"]) if pct is not None
            # baseline 이 0 인 지표(예: dropped_frames 0 → 3)는 %가 없으므로
            # 절대 하한만으로 본다.
            else abs(delta) > 0
        )
        significant = abs(delta) > sigma * noise
        material = abs(delta) > float(th.get("min_abs_delta", 0))

        verdict = "pass"
        if worse and over_threshold and significant and material:
            verdict = "fail"
        elif worse and over_threshold:
            # 임계값은 넘었지만 노이즈/체감 하한을 못 넘음 → 실패는 아니고 관찰 대상.
            verdict = "noisy"
        elif not worse and over_threshold and significant and material:
            verdict = "improved"

        out.append({
            **row,
            "verdict": verdict,
            "threshold_pct": th["change_percent"],
            "min_abs_delta": th.get("min_abs_delta", 0),
            "noise": noise,
            "significant": significant,
        })
    return out


def failed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("verdict") == "fail"]
