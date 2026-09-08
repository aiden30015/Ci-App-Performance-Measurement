"""baseline-manager.

baseline 파일을 만들고 읽고, current 와 짝지어 변화량을 계산한다.
합격/불합격은 판단하지 않는다 (그건 detect.py).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import metrics as M

DEFAULT_PATH = Path("baseline/performance_baseline.json")


def load(path: Path = DEFAULT_PATH) -> dict | None:
    if not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build(current: dict, commit: str, device_profile: str) -> dict:
    """current.json → baseline. samples 는 버리고 value/noise 만 남긴다."""
    return {
        "schema": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "device_profile": device_profile,
        "repeats": current.get("repeats"),
        "scenarios": {
            name: {
                "metrics": {
                    k: {"value": v["value"], "noise": v["noise"]}
                    for k, v in data["metrics"].items()
                }
            }
            for name, data in current["scenarios"].items()
        },
    }


def save(baseline: dict, path: Path = DEFAULT_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")


def is_stale(baseline: dict, max_age_days: int) -> bool:
    ts = baseline.get("updated_at")
    if not ts:
        return True
    updated = datetime.fromisoformat(ts)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated > timedelta(days=max_age_days)


def compare(baseline: dict, current: dict, scenarios: list[str]) -> list[dict]:
    """시나리오 × 지표 행을 만든다. 판정 없이 숫자만 채운다.

    status 는 여기서 'ok'/'new'/'missing' 세 가지만 정한다. 회귀 여부는 detect 가 붙인다.
    """
    rows: list[dict] = []
    base_s = baseline.get("scenarios", {})
    cur_s = current.get("scenarios", {})

    for name in scenarios or sorted(set(base_s) | set(cur_s)):
        if name not in cur_s:
            rows.append({"scenario": name, "metric": None, "status": "missing"})
            continue
        for key, cur in cur_s[name]["metrics"].items():
            unit, higher_is_better = M.METRICS[key]
            b = base_s.get(name, {}).get("metrics", {}).get(key)
            row = {
                "scenario": name,
                "metric": key,
                "unit": unit,
                "higher_is_better": higher_is_better,
                "current": cur["value"],
                "current_noise": cur["noise"],
            }
            if b is None:
                rows.append({**row, "status": "new"})
                continue
            delta = cur["value"] - b["value"]
            rows.append({
                **row,
                "baseline": b["value"],
                "baseline_noise": b.get("noise", 0.0),
                "delta": delta,
                # baseline 이 0 이면 %가 정의되지 않는다. None 으로 두고
                # detect 에서 min_abs_delta 만으로 판단한다.
                "change_pct": (delta / b["value"] * 100) if b["value"] else None,
                "status": "ok",
            })

        # baseline 에는 있는데 이번엔 안 잡힌 지표. 추세선이 조용히 끊기는 걸 막는다.
        for key, b in base_s.get(name, {}).get("metrics", {}).items():
            if key in cur_s[name]["metrics"]:
                continue
            unit, higher_is_better = M.METRICS[key]
            rows.append({
                "scenario": name, "metric": key, "unit": unit,
                "higher_is_better": higher_is_better,
                "baseline": b["value"], "current": None, "status": "gone",
            })
    return rows
