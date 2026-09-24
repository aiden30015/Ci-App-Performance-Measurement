"""perfkit vs FrameGuard 판정 정확도 비교 (exp/frameguard-compare).

artifacts/<mode>-<inject>-<shard>/ 를 읽어 세 가지 비교 방식 × 두 판정기로
오탐률(inject=0 인데 FAIL)과 탐지율(inject>0 인데 FAIL)을 낸다.

  single : 다른 러너 1대(3회)를 baseline 으로 → 지금 GOMS 가 쓰는 방식
  pooled : 러너 5대(15회) 중앙값을 baseline 으로 → #149 방식
  paired : 같은 러너에서 base/variant 번갈아 3회씩

usage: PYTHONPATH=tool python tool/experiment/analyze.py artifacts [perf.yaml ...]
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from perfkit import baseline as B, detect, metrics

SCEN = ["home_scroll", "community_scroll"]
ROOT = Path(__file__).resolve().parents[2]


def runs_of(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("run-*") if p.is_dir())


def fg_files(run_dirs: list[Path], scen: str) -> list[str]:
    return [str(p) for d in run_dirs if (p := d / "frameguard" / f"{scen}.json").exists()]


def load(art: Path):
    stored, paired = defaultdict(list), defaultdict(list)
    for d in sorted(x for x in art.iterdir() if x.is_dir()):
        mode, inject, shard = d.name.split("-")
        if mode == "stored":
            r = runs_of(d)
            if r:
                stored[int(inject)].append((d.name, r))
        else:
            b, v = runs_of(d / "base"), runs_of(d / "var")
            if b and v:
                paired[int(inject)].append((d.name, b, v))
    return stored, paired


def pk_fail(base_runs, cur_runs, cfg) -> dict[str, bool]:
    base = B.build(metrics.aggregate(base_runs, SCEN), "x", "x")
    cur = metrics.aggregate(cur_runs, SCEN)
    rows = detect.judge(B.compare(base, cur, SCEN), cfg)
    return {s: any(r["scenario"] == s and r.get("verdict") == "fail" for r in rows) for s in SCEN}


def fg_rule_on_timeline(base_runs, cur_runs) -> dict[str, bool]:
    """FrameGuard 의 판정 규칙(StatisticalRegression 기본값)을 perfkit 이 모은
    Timeline 데이터에 그대로 적용한다. 수집기 차이와 판정 규칙 차이를 떼어 보려는 것.
    규칙: median p95 상대변화 > 15%  또는  median jank_rate 증가 > 1%p."""
    import statistics

    def per_run(runs, s):
        out = []
        for d in runs:
            f = d / f"{s}.json"
            if f.exists():
                m = metrics.normalize(json.loads(f.read_text(encoding="utf-8")))
                if "p95_frame_time_ms" in m:
                    out.append((m["p95_frame_time_ms"], m["jank_rate"] / 100))
        return out

    res = {}
    for s in SCEN:
        b, c = per_run(base_runs, s), per_run(cur_runs, s)
        if len(b) < 3 or len(c) < 3:
            continue
        bp, cp = statistics.median(x[0] for x in b), statistics.median(x[0] for x in c)
        bj, cj = statistics.median(x[1] for x in b), statistics.median(x[1] for x in c)
        rel = (cp - bp) / bp if bp else 0.0
        res[s] = rel > 0.15 or (cj - bj) > 0.01
    return res


def main():
    art = Path(sys.argv[1])
    cfg_paths = sys.argv[2:] or [str(ROOT / "perf.yaml")]
    cfgs = {Path(p).stem: yaml.safe_load(Path(p).read_text(encoding="utf-8")) for p in cfg_paths}
    stored, paired = load(art)
    print("jobs:", {k: len(v) for k, v in stored.items()}, "paired:", {k: len(v) for k, v in paired.items()})

    # (protocol, inject, base_runs, cur_runs) 비교 목록
    cases = []
    A = stored.get(0, [])
    for (na, ra), inj in itertools.product(A, sorted(stored)):
        for nb, rb in stored[inj]:
            if nb != na:
                cases.append(("single", inj, ra, rb))
    # pooled: A 를 5/나머지로 나누는 모든 조합 중 최대 60개
    k = min(5, len(A) - 1)
    for pool in itertools.islice(itertools.combinations(range(len(A)), k), 60):
        base = [r for i in pool for r in A[i][1]]
        for inj in sorted(stored):
            for idx, (nb, rb) in enumerate(stored[inj]):
                if inj == 0 and idx in pool:
                    continue
                cases.append(("pooled", inj, base, rb))
    for inj, lst in paired.items():
        for _, b, v in lst:
            cases.append(("paired", inj, b, v))

    # FrameGuard 는 한 번에 배치로
    fg_jobs = []
    for i, (_, _, b, c) in enumerate(cases):
        for s in SCEN:
            fb, fc = fg_files(b, s), fg_files(c, s)
            if len(fb) >= 3 and len(fc) >= 3:
                fg_jobs.append({"id": f"{i}|{s}", "baseline": fb, "current": fc})
    fg = json.loads(subprocess.run(
        ["dart", "run", "tool/experiment/fg_judge.dart"], cwd=ROOT, check=True,
        input=json.dumps(fg_jobs), capture_output=True, text=True, shell=sys.platform == "win32",
    ).stdout)

    tally = defaultdict(lambda: [0, 0])  # (judge, proto, inj, scen) -> [fail, total]
    for i, (proto, inj, b, c) in enumerate(cases):
        for name, cfg in cfgs.items():
            for s, f in pk_fail(b, c, cfg).items():
                t = tally[(f"perfkit[{name}]", proto, inj, s)]
                t[0] += f; t[1] += 1
        for s, f in fg_rule_on_timeline(b, c).items():
            t = tally[("fg-rule@timeline", proto, inj, s)]
            t[0] += f; t[1] += 1
        for s in SCEN:
            r = fg.get(f"{i}|{s}")
            if r:
                t = tally[("frameguard", proto, inj, s)]
                t[0] += r["regression"]; t[1] += 1

    judges = sorted({k[0] for k in tally})
    injs = sorted({k[2] for k in tally})
    print("\nFAIL 비율 (inject=0 → 오탐률, 낮을수록 좋음 / inject>0 → 탐지율, 높을수록 좋음)\n")
    for s in SCEN:
        print(f"## {s}")
        print("| judge | protocol | " + " | ".join(f"inject={i}µs" for i in injs) + " |")
        print("|---|---|" + "---|" * len(injs))
        for j in judges:
            for p in ("single", "pooled", "paired"):
                cells = []
                for inj in injs:
                    f, n = tally.get((j, p, inj, s), [0, 0])
                    cells.append(f"{f / n:.0%} ({f}/{n})" if n else "-")
                if any(c != "-" for c in cells):
                    print(f"| {j} | {p} | " + " | ".join(cells) + " |")
        print()
    out = art / "result.json"
    out.write_text(json.dumps({"|".join(map(str, k)): v for k, v in tally.items()}, indent=1))


if __name__ == "__main__":
    main()
