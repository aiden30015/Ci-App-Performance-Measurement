"""변경된 파일 → 돌려야 할 시나리오.

앱 구조를 도구가 알 필요는 없다. 각 repo 의 perf.yaml 이 시나리오별 관련 경로를 적는다.

    selection:
      ignore: ["**/*.md", "docs/**"]        # 성능과 무관한 변경 — 이것만 바뀌면 측정 생략
      scenarios:
        member_list_scroll: ["lib/features/member/**"]

규칙(놓치는 쪽보다 더 도는 쪽이 안전하다):
  - selection 이 없으면 전체
  - ignore 에 걸리면 무시
  - 어떤 시나리오에도 안 걸리는 파일이 하나라도 있으면(공통 코드·빌드 설정 등) 전체
  - 그 외엔 걸린 시나리오의 합집합. 전부 ignore 면 생략(skip)
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache


@lru_cache(maxsize=None)
def _glob_re(pattern: str) -> re.Pattern:
    i, out = 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def matches(path: str, patterns: list[str]) -> bool:
    path = path.replace("\\", "/")
    return any(_glob_re(p).match(path) for p in patterns or [])


def select(changed: list[str], cfg: dict) -> dict:
    """{"scenarios": [...], "skip": bool, "reason": str}. scenarios 는 cfg 순서."""
    all_scenarios = list(cfg.get("scenarios") or [])
    sel = cfg.get("selection") or {}
    mapping = sel.get("scenarios") or {}
    if not mapping:
        return {"scenarios": all_scenarios, "skip": False,
                "reason": "selection 설정이 없어 전체 시나리오를 측정합니다."}

    ignore = sel.get("ignore") or []
    hit: dict[str, list[str]] = {}
    for f in changed:
        if matches(f, ignore):
            continue
        owners = [s for s in all_scenarios if matches(f, mapping.get(s) or [])]
        if not owners:
            return {"scenarios": all_scenarios, "skip": False,
                    "reason": f"`{f}` 는 특정 시나리오에 매핑되지 않은 변경이라 전체를 측정합니다."}
        for s in owners:
            hit.setdefault(s, []).append(f)

    if not hit:
        return {"scenarios": [], "skip": True,
                "reason": "성능과 무관한 변경만 있어 측정을 건너뜁니다."}
    chosen = [s for s in all_scenarios if s in hit]
    why = "; ".join(
        f"`{s}` ← `{hit[s][0]}`" + (f" 외 {len(hit[s]) - 1}건" if len(hit[s]) > 1 else "")
        for s in chosen)
    return {"scenarios": chosen, "skip": False,
            "reason": f"변경된 코드와 관련된 시나리오만 측정: {why}"}


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base}...{head}"], text=True, encoding="utf-8")
    return [line for line in out.splitlines() if line.strip()]
