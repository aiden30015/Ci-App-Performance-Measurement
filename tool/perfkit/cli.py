"""performance-runner + 파이프라인 글루.

  perfkit select       변경 파일 → 돌릴 시나리오 (perf.yaml 의 selection)
  perfkit run          시나리오를 N회 측정 → runs/run-<i>/<scenario>.json
  perfkit aggregate    runs/ → current.json
  perfkit check        current.json vs baseline → perf_report.md (+ exit 1)
  perfkit rebaseline   current.json → baseline/performance_baseline.json
  perfkit store        history.jsonl / regressions.jsonl append
  perfkit dashboard    정적 대시보드 디렉터리 생성
  perfkit seed-demo    데모용 히스토리 생성 (대시보드 확인·개발용)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import baseline as B
from . import detect, metrics, report, store
from . import select as S

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _run_dirs(runs: Path) -> list[Path]:
    dirs = sorted(d for d in Path(runs).iterdir() if d.is_dir())
    if not dirs:
        raise SystemExit(f"no run directories under {runs}")
    return dirs


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _trace_startup(flutter: str, a, out: Path) -> None:
    """app_startup 시나리오: Flutter 공식 `--trace-startup` 으로 콜드 스타트를 잰다.

    integration_test 안에서 재는 `main() → 첫 프레임` 은 이미 떠 있는 프로세스의
    위젯 빌드 시간(수 ms)이라 startup 이 아니다. 이건 엔진 진입부터 첫 프레임까지의
    실제 값이고, 앱을 한 번 더 띄우는 비용을 그만큼의 가치로 판단해 감수한다.
    """
    cmd = [flutter, "run", "--profile", "--trace-startup"]
    if a.device:
        cmd += ["-d", a.device]
    print(f"[perfkit]   startup trace: {' '.join(cmd)}", flush=True)
    try:
        rc = subprocess.call(cmd, cwd=a.app, timeout=900)
    except subprocess.TimeoutExpired:
        rc = -1
    info = Path(a.app) / "build" / "start_up_info.json"
    if rc != 0 or not info.exists():
        print("[perfkit]   startup trace 실패 — app_startup 결과 없음", file=sys.stderr)
        return
    raw = json.loads(info.read_text(encoding="utf-8"))
    (out / "app_startup.json").write_text(json.dumps({
        "scenario": "app_startup",
        "timeline": None,          # 프레임 분포는 이 방식으로 얻지 못한다
        "memory": None,
        "startup_ms": raw["timeToFirstFrameMicros"] / 1000,
        "startup_rasterized_ms": (
            raw["timeToFirstFrameRasterizedMicros"] / 1000
            if raw.get("timeToFirstFrameRasterizedMicros") else None),
        # 이 시나리오엔 traced action 이 없다. duration_ms 를 0 으로 넣으면
        # 대시보드에 없는 측정치가 생긴다.
        "duration_ms": None,
    }, indent=2), encoding="utf-8")
    info.unlink()                  # 다음 반복이 옛 결과를 주워가지 않도록


# --------------------------------------------------------------------- run
def cmd_run(a) -> int:
    cfg = load_config(a.config)
    flutter = shutil.which("flutter") or "flutter"
    repeats = a.repeats or int(cfg.get("repeats", 3))
    only = _csv(a.scenarios)          # 비면 perf.yaml 의 전체
    drive_only = [x for x in only if x != "app_startup"]
    for i in range(1, repeats + 1):
        out = Path(a.out) / f"run-{i}"
        # 이전 실행 결과를 남겨두면, 이번에 실패한 시나리오가 옛 값으로 조용히
        # 채워져 baseline 에 들어간다. 쓰기 전에 항상 비운다.
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        wanted = only or (cfg.get("scenarios") or [])
        if "app_startup" in wanted:
            _trace_startup(flutter, a, out)
        if only and not drive_only:
            continue                  # app_startup 만 골랐으면 flutter drive 는 필요 없다
        # --no-dds: DDS(Dart Development Service)가 켜져 있으면
        # IntegrationTestWidgetsFlutterBinding.traceAction() 이 여는 VM Service
        # WebSocket 이 "Connection refused" 로 매번 죽는다(Flutter 알려진 동작 —
        # 에러 메시지 자체가 --no-dds 를 권장한다). app_startup 을 뺀 모든
        # 시나리오가 이 때문에 결과를 못 내고 있었다.
        # driver 는 ROOT(perfkit 자신의 위치) 기준 절대경로 — 다른 repo 가 이 도구를
        # 재사용 workflow 로 불러 perfkit 을 서브디렉터리에 체크아웃해도 항상 찾는다.
        # target 은 반대로 cwd(=호출한 앱 repo) 기준 상대경로여야 한다.
        driver = str(ROOT / "test_driver" / "perf_driver.dart")
        cmd = [flutter, "drive", "--profile", "--no-dds",
               f"--driver={driver}",
               "--target=integration_test/perf_test.dart"]
        if drive_only:
            cmd.append(f"--dart-define=PERF_SCENARIOS={','.join(drive_only)}")
        if a.device:
            cmd += ["-d", a.device]
        print(f"[perfkit] run {i}/{repeats}: {' '.join(cmd)}", flush=True)
        rc = subprocess.call(cmd, cwd=a.app, env={**os.environ,
                                                  "PERF_OUT_DIR": str(out.resolve())})
        if rc != 0:
            # 러너에서 한 번 죽는 건 흔하다. 남은 반복으로 계속 갈지 여부는 호출자가 정한다.
            print(f"[perfkit] run {i} failed (exit {rc})", file=sys.stderr)
            if a.strict:
                return rc
    return 0


# ---------------------------------------------------------------- aggregate
def cmd_aggregate(a) -> int:
    cfg = load_config(a.config)
    current = metrics.aggregate(_run_dirs(Path(a.runs)),
                                _csv(a.scenarios) or cfg.get("scenarios"))
    Path(a.out).write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"[perfkit] {a.out}: {len(current['scenarios'])} scenarios "
          f"× {current['repeats']} runs")
    return 0


# ------------------------------------------------------------------- select
def cmd_select(a) -> int:
    cfg = load_config(a.config)
    if a.files_from:
        changed = [l.strip() for l in Path(a.files_from).read_text(encoding="utf-8").splitlines()
                   if l.strip()]
    else:
        changed = S.changed_files(a.base, a.head)
    r = S.select(changed, cfg)
    print(json.dumps({**r, "changed": len(changed)}, ensure_ascii=False, indent=2))
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        # 전체를 돌릴 땐 빈 값(=perf.yaml 의 전체)으로 내보낸다.
        full = r["scenarios"] == list(cfg.get("scenarios") or [])
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"scenarios={'' if full else ','.join(r['scenarios'])}\n")
            f.write(f"skip={'true' if r['skip'] else 'false'}\n")
            f.write(f"note={r['reason']}\n")
    return 0


# -------------------------------------------------------------------- check
def cmd_check(a) -> int:
    cfg = load_config(a.config)
    current = json.loads(Path(a.current).read_text(encoding="utf-8"))
    base = B.load(Path(a.baseline))
    warnings: list[str] = []

    if base is None:
        warnings.append(
            f"baseline `{a.baseline}` 이 없습니다. 이번 결과는 비교 없이 기록만 합니다. "
            "`perfkit rebaseline` 로 기준을 만드세요."
        )
        base = {"scenarios": {}}
    elif base.get("device_profile") != cfg.get("device_profile"):
        # DESIGN §6.1: 다른 하드웨어의 절대값 비교는 무의미하다. 경고만 남기고
        # 조용히 비교를 강행하면(이전 동작) 에뮬레이터 vs 로컬 데스크톱처럼 하드웨어가
        # 다를 때 "회귀"가 실제로는 하드웨어 차이일 뿐인데도 CI 를 fail 시킨다.
        warnings.append(
            f"device_profile 불일치 (baseline `{base.get('device_profile')}` vs "
            f"현재 `{cfg.get('device_profile')}`) — 다른 하드웨어라 비교할 수 없습니다. "
            "이번 결과는 회귀 판정 없이 기록만 합니다. `perfkit rebaseline` 로 이 러너 기준을 새로 만드세요."
        )
        base = {**base, "scenarios": {}}
    else:
        if B.is_stale(base, int(cfg.get("baseline_max_age_days", 30))):
            warnings.append(f"baseline 이 {cfg.get('baseline_max_age_days', 30)}일보다 "
                            "오래됐습니다. rebaseline 을 검토하세요.")

    only = _csv(a.scenarios)
    notes = [a.note] if a.note else []
    if only:
        skipped = [x for x in cfg.get("scenarios", []) if x not in only]
        notes.append(f"측정한 시나리오: {', '.join(f'`{x}`' for x in only)}"
                     + (f" · 건너뜀: {', '.join(f'`{x}`' for x in skipped)}" if skipped else ""))
    rows = detect.judge(B.compare(base, current, only or cfg.get("scenarios", [])), cfg)
    md = report.render(
        rows,
        headline=cfg.get("headline_metrics", list(metrics.METRICS)),
        commit=a.commit,
        run_url=a.run_url,
        dashboard_url=a.dashboard_url or cfg.get("dashboard_url", ""),
        baseline_info=base if base.get("scenarios") else None,
        warnings=warnings,
        notes=notes,
    )
    Path(a.out).write_text(md, encoding="utf-8")
    Path(a.rows_out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(md)

    # 시나리오가 통째로 비어 있으면 "성능 회귀"가 아니라 "측정 실패"다. 조용히
    # 통과시키면 게이트가 무의미해지므로 같은 exit 1 로 알린다.
    missing = [r["scenario"] for r in rows if r.get("status") == "missing"]
    if missing:
        print(f"[perfkit] 결과가 없는 시나리오: {', '.join(missing)}", file=sys.stderr)
    return 1 if (detect.failed(rows) or missing) else 0


# --------------------------------------------------------------- rebaseline
def cmd_rebaseline(a) -> int:
    cfg = load_config(a.config)
    current = json.loads(Path(a.current).read_text(encoding="utf-8"))
    new = B.build(current, a.commit, a.device_profile or cfg.get("device_profile", ""))
    old = B.load(Path(a.out))
    B.save(new, Path(a.out))
    print(f"[perfkit] baseline written: {a.out}")
    if old:
        # 무엇이 바뀌는지 사람이 보고 머지할 수 있게 요약을 남긴다.
        for name, data in new["scenarios"].items():
            for k, v in data["metrics"].items():
                o = old.get("scenarios", {}).get(name, {}).get("metrics", {}).get(k)
                if o and o["value"]:
                    pct = (v["value"] - o["value"]) / o["value"] * 100
                    if abs(pct) >= 5:
                        print(f"  {name}/{k}: {o['value']:.2f} → {v['value']:.2f} ({pct:+.1f}%)")
    return 0


# -------------------------------------------------------------------- store
def cmd_store(a) -> int:
    cfg = load_config(a.config)
    current = json.loads(Path(a.current).read_text(encoding="utf-8"))
    rows = json.loads(Path(a.rows).read_text(encoding="utf-8")) if Path(a.rows).exists() else []
    n = store.append_run(Path(a.out), current, rows, {
        "run_id": a.run_id, "commit": a.commit, "branch": a.branch,
        "pr": int(a.pr) if a.pr else None,
        "repo": a.repo,
        "device_profile": cfg.get("device_profile"),
    })
    print(f"[perfkit] history +{n['history']} rows, regressions +{n['regressions']}")
    return 0


# ---------------------------------------------------------------- dashboard
def cmd_dashboard(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # dashboard/index.html 은 perfkit 자체(ROOT)에서 온다. baseline 은 이 도구를
    # 호출한 프로젝트(cwd)의 것 — 여러 repo 에서 이 workflow 를 재사용할 때
    # perfkit 이 어디 체크아웃됐는지와 무관하게 baseline 은 항상 호출한 앱의 것이어야 한다.
    html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    base_src = B.DEFAULT_PATH

    data = {"seed-history": [], "seed-regressions": [], "seed-baseline": None}
    for key, f in (("seed-history", store.HISTORY), ("seed-regressions", store.REGRESSIONS)):
        src = Path(a.data) / f
        data[key] = store.read(src)
        (out / f).write_text(src.read_text(encoding="utf-8") if src.exists() else "",
                             encoding="utf-8")
    if base_src.exists():
        shutil.copy(base_src, out / "baseline.json")
        data["seed-baseline"] = json.loads(base_src.read_text(encoding="utf-8"))

    if a.inline:
        # fetch 가 막힌 곳(로컬 파일 열기, 샌드박스)에서도 보이도록 데이터를 페이지에
        # 심는다. 정상 호스팅에서는 fetch 가 이기므로 이 값은 쓰이지 않는다.
        for key, value in data.items():
            head = f'<script id="{key}" type="application/json">'
            start = html.index(head) + len(head)
            end = html.index("</script>", start)
            html = html[:start] + json.dumps(value, separators=(",", ":")) + html[end:]
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"[perfkit] dashboard → {out}" + (" (데이터 인라인)" if a.inline else ""))
    return 0


# ---------------------------------------------------------------- seed-demo
def cmd_seed_demo(a) -> int:
    """대시보드를 개발/확인하려고 몇 달치 CI 결과를 흉내낸다. 측정과는 무관."""
    import random
    from datetime import datetime, timedelta, timezone

    cfg = load_config(a.config)
    random.seed(7)
    base = {
        "app_startup": {"fps": 58.0, "jank_rate": 4.0, "p95_frame_time_ms": 19.0,
                        "memory_mb": 118, "startup_ms": 820},
        "login": {"fps": 60.0, "jank_rate": 0.4, "p95_frame_time_ms": 9.5,
                  "memory_mb": 120, "startup_ms": None},
        "home_scroll": {"fps": 59.9, "jank_rate": 0.8, "p95_frame_time_ms": 12.0,
                        "memory_mb": 160, "startup_ms": None},
        "community_scroll": {"fps": 59.6, "jank_rate": 1.5, "p95_frame_time_ms": 14.0,
                             "memory_mb": 172, "startup_ms": None},
        "community_detail": {"fps": 59.8, "jank_rate": 1.2, "p95_frame_time_ms": 17.1,
                             "memory_mb": 183, "startup_ms": None},
        "image_heavy_screen": {"fps": 57.5, "jank_rate": 6.0, "p95_frame_time_ms": 21.0,
                               "memory_mb": 240, "startup_ms": None},
    }
    # community_detail 만 서서히 나빠지다가 #108 에서 급락하는 시나리오를 만든다.
    drift = {"community_detail": {"fps": -0.09, "jank_rate": +0.09,
                                  "p95_frame_time_ms": +0.06, "memory_mb": +0.9}}
    cliff_at = 34
    out = Path(a.out)
    hist, regs = [], []
    t0 = datetime.now(timezone.utc) - timedelta(days=40)
    for n in range(40):
        commit = f"{random.getrandbits(28):07x}"
        ts = (t0 + timedelta(days=n, hours=random.randint(0, 6))).isoformat(timespec="seconds")
        for name, m in base.items():
            vals, noise = {}, {}
            for k, v in m.items():
                if v is None:
                    continue
                v = v + drift.get(name, {}).get(k, 0) * n
                if name == "community_detail" and n >= cliff_at:
                    v *= {"fps": 0.86, "jank_rate": 7.2, "p95_frame_time_ms": 1.33,
                          "memory_mb": 1.31}.get(k, 1.0)
                sd = abs(v) * 0.012 + 0.05
                vals[k] = round(random.gauss(v, sd), 3)
                noise[k] = round(sd, 3)
            failed = ["fps", "jank_rate", "p95_frame_time_ms", "memory_mb"] \
                if (name == "community_detail" and n >= cliff_at) else []
            hist.append({"schema": 1, "timestamp": ts, "run_id": str(100 + n),
                         "commit": commit, "branch": "main", "pr": 60 + n,
                         "device_profile": cfg.get("device_profile"), "repeats": 3,
                         "scenario": name, "metrics": vals, "noise": noise,
                         "status": "fail" if failed else "pass",
                         "failed_metrics": failed})
            if failed and n == cliff_at:
                regs += [{"schema": 1, "timestamp": ts, "run_id": str(100 + n),
                          "commit": commit, "branch": "main", "pr": 60 + n,
                          "scenario": name, "metric": k, "baseline": base[name][k],
                          "current": vals[k],
                          "change_pct": round((vals[k] - base[name][k]) / base[name][k] * 100, 1),
                          "threshold_pct": 10, "significant": True} for k in failed]
    out.mkdir(parents=True, exist_ok=True)
    (out / store.HISTORY).write_text(
        "\n".join(json.dumps(h, sort_keys=True) for h in hist) + "\n", encoding="utf-8")
    (out / store.REGRESSIONS).write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in regs) + "\n", encoding="utf-8")
    print(f"[perfkit] demo history: {len(hist)} rows, {len(regs)} regressions → {out}")
    return 0


def main(argv=None) -> int:
    # Windows 콘솔은 기본적으로 시스템 코드페이지(cp949 등)를 쓴다 — 리포트의
    # 화살표/이모지가 출력마다 들쭉날쭉 깨지는 걸 막으려고 전체 stdout/stderr 를
    # UTF-8 로 통일한다.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="perfkit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # cwd 기준 — 다른 repo 가 이 workflow 를 재사용할 때 perfkit 이 어디 체크아웃됐든
    # perf.yaml 은 항상 그 repo(cwd) 자신의 것을 읽어야 한다 (ROOT 를 쓰면 안 됨).
    p.add_argument("--config", default="perf.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    se = sub.add_parser("select", help="변경 파일로 돌릴 시나리오 고르기")
    se.add_argument("--base", default="origin/main")
    se.add_argument("--head", default="HEAD")
    se.add_argument("--files-from", help="git diff 대신 파일 목록(줄 단위)을 읽는다")
    se.set_defaults(fn=cmd_select)

    r = sub.add_parser("run", help="flutter drive 를 N회 실행")
    r.add_argument("--out", default="runs")
    r.add_argument("--app", default=".")
    r.add_argument("--device", default=os.environ.get("PERF_DEVICE"))
    r.add_argument("--repeats", type=int)
    r.add_argument("--scenarios", default="", help="쉼표로 구분한 일부 시나리오만 (비면 전체)")
    r.add_argument("--strict", action="store_true", help="한 번이라도 실패하면 중단")
    r.set_defaults(fn=cmd_run)

    g = sub.add_parser("aggregate", help="반복 측정 집계")
    g.add_argument("--runs", default="runs")
    g.add_argument("--out", default="current.json")
    g.add_argument("--scenarios", default="", help="일부 시나리오만 집계 (비면 전체)")
    g.set_defaults(fn=cmd_aggregate)

    c = sub.add_parser("check", help="baseline 비교 + 리포트 (회귀 시 exit 1)")
    c.add_argument("--current", default="current.json")
    c.add_argument("--baseline", default=str(B.DEFAULT_PATH))
    c.add_argument("--out", default="perf_report.md")
    c.add_argument("--rows-out", default="perf_rows.json")
    c.add_argument("--commit", default="")
    c.add_argument("--run-url", default="")
    c.add_argument("--dashboard-url", default="")
    c.add_argument("--scenarios", default="", help="이 시나리오만 비교 (비면 전체)")
    c.add_argument("--note", default="", help="리포트 상단에 붙일 안내 (예: 선택 사유)")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("rebaseline", help="현재 결과를 새 baseline 으로")
    b.add_argument("--current", default="current.json")
    b.add_argument("--out", default=str(B.DEFAULT_PATH))
    b.add_argument("--commit", default="")
    b.add_argument("--device-profile", default="")
    b.set_defaults(fn=cmd_rebaseline)

    s = sub.add_parser("store", help="history/regressions JSONL append")
    s.add_argument("--current", default="current.json")
    s.add_argument("--rows", default="perf_rows.json")
    s.add_argument("--out", default="perf-history")
    s.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    s.add_argument("--commit", default="")
    s.add_argument("--branch", default="")
    s.add_argument("--pr", default="")
    s.add_argument("--repo", default=(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com") + "/" +
        os.environ["GITHUB_REPOSITORY"]) if os.environ.get("GITHUB_REPOSITORY") else "")
    s.set_defaults(fn=cmd_store)

    d = sub.add_parser("dashboard", help="정적 대시보드 디렉터리 생성")
    d.add_argument("--data", default="perf-history")
    d.add_argument("--out", default="site")
    d.add_argument("--inline", action="store_true",
                   help="데이터를 HTML 에 심어 단일 파일로 만든다 (fetch 불가 환경용)")
    d.set_defaults(fn=cmd_dashboard)

    sd = sub.add_parser("seed-demo", help="데모용 히스토리 생성")
    sd.add_argument("--out", default="perf-history")
    sd.set_defaults(fn=cmd_seed_demo)

    a = p.parse_args(argv)
    return a.fn(a)
