"""report-generator. rows → Markdown. 파일 저장은 호출자가 한다."""
from __future__ import annotations

_ICON = {"fail": "❌", "improved": "🚀", "noisy": "🟡", "pass": "✅",
         "new": "🆕", "gone": "⚠️"}
_LABEL = {"ko": "성능", "en": "Performance"}


def _num(v, unit: str) -> str:
    if v is None:
        return "—"
    return (f"{v:.0f}{unit}" if abs(v) >= 100 else f"{v:.1f}{unit}")


def _change(row) -> str:
    pct, delta = row.get("change_pct"), row.get("delta")
    if pct is None:
        return "n/a" if delta is None else f"{delta:+.1f} (baseline 0)"
    return f"{pct:+.1f}%"


def render(
    rows: list[dict],
    *,
    headline: list[str],
    commit: str = "",
    run_url: str = "",
    dashboard_url: str = "",
    baseline_info: dict | None = None,
    warnings: list[str] | None = None,
) -> str:
    ok_rows = [r for r in rows if r.get("metric")]
    fails = [r for r in ok_rows if r.get("verdict") == "fail"]
    missing = [r["scenario"] for r in rows if r.get("status") == "missing"]

    md = ["## 📊 Flutter Performance Report", ""]
    if fails:
        md += [f"### ❌ Performance Regression — {len(fails)}건", ""]
        for r in fails:
            md.append(
                f"- **{r['scenario']} / {r['metric']}**: "
                f"`{_num(r['baseline'], r['unit'])}` → `{_num(r['current'], r['unit'])}` "
                f"(**{_change(r)}**, 임계 {r['threshold_pct']}%)"
            )
        md.append("")
    else:
        md += ["### ✅ Passed — 회귀 없음", ""]

    # 시나리오별 표. 실패한 시나리오를 위로 올린다.
    failed_scenarios = {r["scenario"] for r in fails}
    scenarios = sorted(
        {r["scenario"] for r in ok_rows},
        key=lambda s: (s not in failed_scenarios, s),
    )
    for name in scenarios:
        srows = [r for r in ok_rows if r["scenario"] == name]
        head = [r for r in srows if r["metric"] in headline]
        rest = [r for r in srows if r["metric"] not in headline]
        mark = "❌" if name in failed_scenarios else "✅"
        md += [f"#### {mark} `{name}`", "",
               "| | Metric | Baseline | Current | Change | Threshold |",
               "|---|---|---|---|---|---|"]
        md += [_row(r) for r in head]
        md.append("")
        if rest:
            md += ["<details><summary>기타 지표</summary>", "",
                   "| | Metric | Baseline | Current | Change | Threshold |",
                   "|---|---|---|---|---|---|"]
            md += [_row(r) for r in rest]
            md += ["", "</details>", ""]

    if missing:
        md += [f"> ⚠️ 결과가 없는 시나리오: {', '.join(f'`{s}`' for s in missing)}", ""]
    for w in warnings or []:
        md += [f"> ⚠️ {w}", ""]

    foot = []
    if commit:
        foot.append(f"commit `{commit[:7]}`")
    if baseline_info:
        foot.append(
            f"baseline `{(baseline_info.get('commit') or '?')[:7]}` "
            f"({baseline_info.get('updated_at', '?')})"
        )
        foot.append(f"device `{baseline_info.get('device_profile', '?')}`")
    if run_url:
        foot.append(f"[CI run]({run_url})")
    if dashboard_url:
        foot.append(f"**[📈 View Performance Dashboard]({dashboard_url})**")
    md.append("<sub>" + " · ".join(foot) + "</sub>")
    md += ["", "<sub>판정: |변화율| > 임계값 **AND** |Δ| > "
           "2σ·측정노이즈 **AND** |Δ| > 최소유의차. "
           "🟡 = 임계값은 넘었지만 노이즈 범위 안(실패 아님).</sub>"]
    return "\n".join(md)


def _row(r: dict) -> str:
    return (
        f"| {_ICON.get(r.get('verdict', r.get('status')), '')} | {r['metric']} "
        f"| {_num(r.get('baseline'), r['unit'])} | {_num(r.get('current'), r['unit'])} "
        f"| {_change(r)} | {r.get('threshold_pct', '—')}% |"
    )
