"""result-storage. append-only JSONL (docs/DESIGN.md §4.4, §4.5).

파일 하나가 곧 스키마다. 나중에 SQLite/Parquet 로 옮겨도 행 구조는 그대로다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY = "history.jsonl"
REGRESSIONS = "regressions.jsonl"


def _append(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def history_records(current: dict, rows: list[dict], meta: dict) -> list[dict]:
    """CI run 1회 × 시나리오 N개 → history 행 N개."""
    failed = {(r["scenario"], r["metric"]) for r in rows if r.get("verdict") == "fail"}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = []
    for name, data in current["scenarios"].items():
        out.append({
            "schema": 1,
            "timestamp": meta.get("timestamp") or now,
            "run_id": meta.get("run_id"),
            "commit": meta.get("commit"),
            "branch": meta.get("branch"),
            "pr": meta.get("pr"),
            # 대시보드에서 commit/PR/run 으로 바로 넘어가기 위한 저장소 주소.
            "repo": meta.get("repo") or None,
            "device_profile": meta.get("device_profile"),
            "repeats": current.get("repeats"),
            "scenario": name,
            "metrics": {k: v["value"] for k, v in data["metrics"].items()},
            "noise": {k: v["noise"] for k, v in data["metrics"].items()},
            "status": "fail" if any(s == name for s, _ in failed) else "pass",
            "failed_metrics": sorted(m for s, m in failed if s == name),
        })
    return out


def regression_records(rows: list[dict], meta: dict) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [{
        "schema": 1,
        "timestamp": meta.get("timestamp") or now,
        "run_id": meta.get("run_id"),
        "commit": meta.get("commit"),
        "branch": meta.get("branch"),
        "pr": meta.get("pr"),
        "repo": meta.get("repo") or None,
        "scenario": r["scenario"],
        "metric": r["metric"],
        "baseline": r["baseline"],
        "current": r["current"],
        "change_pct": r.get("change_pct"),
        "threshold_pct": r.get("threshold_pct"),
        "significant": r.get("significant"),
    } for r in rows if r.get("verdict") == "fail"]


def append_run(out_dir: Path, current: dict, rows: list[dict], meta: dict) -> dict:
    out_dir = Path(out_dir)
    hist = history_records(current, rows, meta)
    regs = regression_records(rows, meta)
    _append(out_dir / HISTORY, hist)
    if regs:
        _append(out_dir / REGRESSIONS, regs)
    return {"history": len(hist), "regressions": len(regs)}


def read(path: Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
