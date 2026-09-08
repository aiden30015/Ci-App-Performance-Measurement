"""perfkit 자체 점검. `python tool/test_perfkit.py` 로 실행 (pytest 도 가능).

프레임워크 없이 assert 만 쓴다. 여기서 지키려는 건 판정 로직 하나다:
노이즈에 흔들리지 않으면서 진짜 회귀는 놓치지 않는가.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from perfkit import baseline as B  # noqa: E402
from perfkit import detect, metrics, report, store  # noqa: E402

CFG = {
    "sigma": 2.0,
    "scenarios": ["home_scroll"],
    "thresholds": {
        "default": {"change_percent": 15, "min_abs_delta": 0},
        "fps": {"change_percent": 10, "min_abs_delta": 1.0},
        "memory_mb": {"change_percent": 20, "min_abs_delta": 5.0},
        "dropped_frames": {"change_percent": 25, "min_abs_delta": 2},
    },
}


def _pair(metric, base_v, base_n, cur_v, cur_n):
    base = {"scenarios": {"home_scroll": {"metrics": {metric: {"value": base_v, "noise": base_n}}}}}
    cur = {"scenarios": {"home_scroll": {"metrics": {metric: {"value": cur_v, "noise": cur_n, "samples": []}}}}}
    rows = detect.judge(B.compare(base, cur, ["home_scroll"]), CFG)
    return rows[0]


def test_percentiles():
    v = [float(i) for i in range(1, 101)]
    assert metrics.percentile(v, 50) == 50.5
    assert round(metrics.percentile(v, 95), 2) == 95.05
    assert metrics.percentile([], 90) == 0.0
    assert metrics.percentile([7.0], 99) == 7.0


def test_normalize():
    raw = {
        "scenario": "s",
        # build/raster 길이가 다른 실제 케이스. 짧은 쪽에 맞춘다.
        "timeline": {"frame_build_times": [5000, 20000, 6000],
                     "frame_rasterizer_times": [2000, 3000]},
        "memory": {"rss_mb": 180.0},
        "startup_ms": 800,
        "duration_ms": 1000,
        "frame_budget_ms": 16.0,
    }
    m = metrics.normalize(raw)
    assert m["dropped_frames"] == 1.0          # 20+3=23ms 만 예산 초과
    assert m["jank_rate"] == 50.0              # 2 프레임 중 1개
    # 프레임 시간 7ms / 23ms → p50 15ms → 1000/15 = 66.7 이지만 주사율(1000/16)로 잘린다.
    assert round(m["p50_frame_time_ms"], 1) == 15.0
    assert round(m["fps"], 1) == 62.5
    assert m["memory_mb"] == 180.0 and m["startup_ms"] == 800.0


def test_empty_timeline_reports_nothing_rather_than_zero():
    """프레임이 없으면 0 FPS 를 만들어내지 않고 지표 자체를 뺀다."""
    m = metrics.normalize({"scenario": "app_startup", "timeline": None,
                           "memory": {"rss_mb": 118.0}, "startup_ms": 820,
                           "duration_ms": 300})
    assert "fps" not in m and "jank_rate" not in m and "p95_frame_time_ms" not in m
    assert m["startup_ms"] == 820.0 and m["memory_mb"] == 118.0


def test_outlier_and_noise():
    # 스파이크 1개는 median 을 흔들지 못한다.
    value, noise, dropped = metrics.summarize([60.0, 59.8, 60.1, 59.9, 12.0])
    assert 59.5 < value < 60.5 and dropped == [4]
    # 표본이 3개뿐이면 아무것도 버리지 않는다 (버리면 남는 게 2개 미만이 될 위험).
    _, _, dropped = metrics.summarize([60.0, 59.0, 12.0])
    assert dropped == []


def test_real_regression_fails():
    r = _pair("fps", 59.8, 0.3, 51.2, 0.3)
    assert r["verdict"] == "fail", r
    assert round(r["change_pct"], 1) == -14.4


def test_noise_does_not_fail():
    # 변화율은 임계값을 넘지만 러너 노이즈 범위 안 → 실패시키지 않는다.
    r = _pair("fps", 59.8, 4.0, 52.0, 4.0)
    assert r["verdict"] == "noisy", r


def test_small_absolute_change_does_not_fail():
    # 120MB → 145MB 는 +20.8% 지만 노이즈가 크면 통과. 반대로 노이즈가 작으면 실패.
    assert _pair("memory_mb", 120.0, 12.0, 145.0, 12.0)["verdict"] == "noisy"
    assert _pair("memory_mb", 120.0, 0.5, 145.0, 0.5)["verdict"] == "fail"
    # 4MB 변화(+3.3%)는 어느 쪽으로도 실패가 아니다.
    assert _pair("memory_mb", 120.0, 0.1, 124.0, 0.1)["verdict"] == "pass"


def test_improvement_is_not_regression():
    assert _pair("fps", 51.2, 0.3, 59.8, 0.3)["verdict"] == "improved"
    assert _pair("memory_mb", 200.0, 1.0, 150.0, 1.0)["verdict"] == "improved"


def test_zero_baseline():
    # 0 → 5 는 % 가 정의되지 않는다. 절대 하한(min_abs_delta=2)으로 판단.
    assert _pair("dropped_frames", 0.0, 0.0, 5.0, 0.0)["verdict"] == "fail"
    # 0 → 1 은 최소유의차(2)를 못 넘으므로 실패가 아니다 (관찰만).
    assert _pair("dropped_frames", 0.0, 0.0, 1.0, 0.0)["verdict"] != "fail"
    assert _pair("dropped_frames", 0.0, 0.0, 0.0, 0.0)["verdict"] == "pass"


def test_new_and_missing_scenarios():
    base = {"scenarios": {"home_scroll": {"metrics": {"fps": {"value": 60.0, "noise": 0.1}}}}}
    cur = {"scenarios": {"login": {"metrics": {"fps": {"value": 60.0, "noise": 0.1, "samples": []}}}}}
    rows = detect.judge(B.compare(base, cur, ["home_scroll", "login"]), CFG)
    assert [r["status"] for r in rows] == ["missing", "new"]
    assert detect.failed(rows) == []            # 없는 결과로 CI 를 떨어뜨리지 않는다


def test_metric_that_disappears_is_flagged():
    """baseline 에 있던 지표가 이번에 안 잡히면 조용히 사라지지 않고 표시된다."""
    base = {"scenarios": {"home_scroll": {"metrics": {
        "fps": {"value": 60.0, "noise": .1}, "memory_mb": {"value": 160.0, "noise": 1.0}}}}}
    cur = {"scenarios": {"home_scroll": {"metrics": {
        "memory_mb": {"value": 161.0, "noise": 1.0, "samples": []}}}}}
    rows = detect.judge(B.compare(base, cur, ["home_scroll"]), CFG)
    gone = [r for r in rows if r["status"] == "gone"]
    assert len(gone) == 1 and gone[0]["metric"] == "fps"
    assert detect.failed(rows) == []
    assert "⚠️" in report.render(rows, headline=["fps", "memory_mb"])


def test_report_and_storage_roundtrip():
    rows = [_pair("fps", 59.8, 0.3, 51.2, 0.3)]
    md = report.render(rows, headline=["fps"], commit="a81f23cdeadbeef",
                       dashboard_url="https://example.test/perf")
    assert "❌" in md and "-14.4%" in md and "View Performance Dashboard" in md

    cur = {"repeats": 3, "scenarios": {"home_scroll": {"metrics": {
        "fps": {"value": 51.2, "noise": 0.3, "samples": [51.2]}}}}}
    with tempfile.TemporaryDirectory() as d:
        store.append_run(Path(d), cur, rows, {"run_id": "1", "commit": "abc", "branch": "main"})
        store.append_run(Path(d), cur, rows, {"run_id": "2", "commit": "def", "branch": "main"})
        hist = store.read(Path(d) / store.HISTORY)
        regs = store.read(Path(d) / store.REGRESSIONS)
    assert len(hist) == 2 and hist[0]["status"] == "fail"
    assert hist[0]["metrics"]["fps"] == 51.2 and hist[0]["failed_metrics"] == ["fps"]
    assert len(regs) == 2 and regs[0]["metric"] == "fps"


def test_missing_scenario_fails_check():
    """시나리오가 통째로 빠지면 통과시키지 않는다 (측정 실패를 조용히 넘기지 않기)."""
    base = {"scenarios": {"home_scroll": {"metrics": {"fps": {"value": 60.0, "noise": .1}}}}}
    rows = detect.judge(B.compare(base, {"scenarios": {}}, ["home_scroll"]), CFG)
    assert [r["status"] for r in rows] == ["missing"]


def test_cli_end_to_end():
    """run 을 뺀 전 구간: aggregate → check → rebaseline → store → dashboard."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for i in (1, 2, 3):
            run = d / "runs" / f"run-{i}"
            run.mkdir(parents=True)
            (run / "home_scroll.json").write_text(json.dumps({
                "scenario": "home_scroll",
                "timeline": {"frame_build_times": [5000 + i * 100] * 50,
                             "frame_rasterizer_times": [3000] * 50},
                "memory": {"rss_mb": 160 + i}, "duration_ms": 1000,
                "frame_budget_ms": 16.67,
            }))

        def perfkit(*args, expect=0):
            rc = subprocess.call([sys.executable, "-m", "perfkit", *args],
                                 cwd=d, env={"PYTHONPATH": str(root / "tool"),
                                             "SYSTEMROOT": "C:/Windows", "PATH": ""})
            assert rc == expect, f"{args} → {rc}"

        # 이 테스트는 home_scroll 하나만 만든다. 나머지 시나리오가 missing 으로
        # 잡히지 않도록 축소된 설정을 쓴다.
        cfg_path = d / "perf.yaml"
        cfg_path.write_text(
            "sigma: 2.0\n"
            "device_profile: ci-emulator-api34\n"
            "scenarios: [home_scroll]\n"
            "thresholds:\n"
            "  default: { change_percent: 15, min_abs_delta: 0 }\n"
            "headline_metrics: [fps, jank_rate, p95_frame_time_ms, memory_mb]\n",
            encoding="utf-8")
        cfg = ["--config", str(cfg_path)]
        perfkit(*cfg, "aggregate")
        assert json.loads((d / "current.json").read_text())["scenarios"]["home_scroll"]
        # baseline 이 없으면 비교 없이 통과 (첫 실행에서 CI 를 떨어뜨리지 않는다).
        perfkit(*cfg, "check", "--baseline", str(d / "b.json"))
        assert "baseline" in (d / "perf_report.md").read_text(encoding="utf-8")
        perfkit(*cfg, "rebaseline", "--out", str(d / "b.json"), "--commit", "abc123",
                "--device-profile", "ci-emulator-api34")
        perfkit(*cfg, "check", "--baseline", str(d / "b.json"))  # 자기 자신과 비교 → pass
        perfkit(*cfg, "store", "--commit", "abc123", "--branch", "main")
        assert len(store.read(d / "perf-history" / store.HISTORY)) == 1
        perfkit(*cfg, "dashboard")
        assert (d / "site" / "index.html").exists()
        assert (d / "site" / store.HISTORY).exists()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
