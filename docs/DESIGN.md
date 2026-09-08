# Flutter CI Performance Measurement — 설계 문서

> 순서: 기존 프로젝트 조사 → 요구사항 → Architecture → 데이터 모델 → 기술 선택 → CI → 구현 → Dashboard

---

## 1. 기존 프로젝트 조사

### 1.1 FrameGuard (`frameguard` 0.6.0, github.com/theworker02/frameguard)

조사일 2026-09-07 기준 pub.dev 0.6.0 / 저장소 star 0 / 최초 커밋 2026-08.

| 항목 | 내용 |
|---|---|
| 무엇을 측정하는가 | `SchedulerBinding.addTimingsCallback` + `FrameTiming` 기반 프레임 타이밍. jank severity(healthy/minor/major/severe), p50/p90/p95/p99, histogram, streak, build vs raster 분류, `FrameGuardRegion` rebuild 카운트. **메모리·startup·앱 외부 지표는 없음** (README "Deeper GC / memory correlation" 은 roadmap) |
| Baseline 관리 | `baselines/*.json` 파일. `frameguard baseline update` 로 **명시적** 갱신, 자동 덮어쓰기 안 함 |
| Regression 판단 | `FrameBudget`(절대 예산: maxJankFrames, maxJankRate, maxP95/P99) + baseline 비교. multi-run 통계(median, MAD, confidence interval, outlier 는 삭제 대신 flag) |
| CI 구성 | 재사용 가능한 GitHub Action `frameguard-check`, 또는 CLI 직접 호출. exit code 0/1/2 |
| 결과 표시 | JSON / text / HTML 리포트 + CSV·JUnit·SARIF·Markdown exporter (PR comment 용) |
| Historical data | **로컬 JSONL** (`frameguard history append`) — "gradual drift without a backend". CI run/commit 축의 장기 시계열은 아님 |
| Dashboard | **없음.** README Privacy 항목에 "No required dashboard" 를 명시적 non-goal 로 선언. roadmap 에 "Historical trend dashboards (still local-first)" 만 있음 |
| 실행 위치 | 앱에 `frameguard` 의존성을 추가하고 앱 코드/테스트를 계측 (in-app library) |

### 1.2 그 외

| 프로젝트 | 성격 | 겹치는 부분 / 안 겹치는 부분 |
|---|---|---|
| **Flutter DevTools Performance view** | 사람이 보는 인터랙티브 프로파일러. 세션 1회 탐색용 | 자동화·게이트·baseline 개념 없음. CI 불가 |
| **`integration_test` + `TimelineSummary`** (Flutter 공식) | `binding.traceAction` → `summaryJson` (avg/p90/p99/worst build·raster, missed budget count, frame_count, 프레임별 raw 배열) | 우리가 **채택**. 앱에 의존성 0. baseline/판정/저장/시각화는 전혀 없음 |
| **`flutter run --trace-startup`** (Flutter 공식) | `start_up_info.json` (engine init → first frame → first useful frame) | **채택.** `app_startup` 시나리오는 이걸로만 잰다 |
| **Perflutter** (`perflutter`, star 3) | 앱 런타임 navigation 성능 추적 패키지 | 프로덕션 런타임 관측용. CI regression gate·baseline 없음 |
| **`flutter_performance_testing` 0.0.1** | 벤치마크 유틸 모음 | 초기 단계. baseline/CI/dashboard 없음 |
| **Android Performance Tester** (동명 저장소 다수) | 디바이스 하드웨어 벤치마크(CPU/RAM/IO) | Flutter 앱 성능과 무관. 조사 대상에서 제외 |
| **JankStats (AndroidX)** | 프로덕션 jank 텔레메트리 | 플랫폼 레벨. Flutter 프레임 파이프라인 구분 불가, CI 게이트 아님 |

### 1.3 결론 — 중복 회피와 차별화

**FrameGuard 가 이미 잘 하는 것 → 다시 만들지 않는다**

- 프레임 타이밍 캡처 자체, jank severity 분류, 절대 budget 개념, exit code 규약, exporter 포맷.
- 우리는 캡처를 **Flutter 공식 `integration_test` + `TimelineSummary`** 로 하고(앱 의존성 0),
  분석 계층은 **파일 포맷 계약**(`runs/<run>/<scenario>.json`)으로만 결합한다.
  즉 나중에 FrameGuard 를 collector 로 갈아끼워도 analyzer 이하 파이프라인은 그대로다. (§4 참고)

**우리가 차별화하는 것 (FrameGuard 의 명시적 non-goal 또는 미구현 영역)**

1. **CI-native, commit 축 시계열.** FrameGuard 의 history 는 "로컬 드리프트 확인용 JSONL". 우리는 `run_id / commit / branch / PR` 을 1급 키로 갖는 저장 구조를 처음부터 설계하고, main 브랜치 CI 가 영구 append 한다.
2. **Historical Performance Trend Dashboard.** FrameGuard 가 "No required dashboard" 로 선언한 영역. 우리 프로젝트의 핵심 기능.
3. **Frame 밖의 지표.** memory(RSS), startup, 시나리오 실행시간까지 동일 파이프라인에서 회귀 판정.
4. **Scenario 를 CI 매트릭스의 1급 단위로.** 시나리오별 baseline / 시나리오별 임계값 / 시나리오별 추세.
5. **CI 노이즈 대응 판정.** "baseline 보다 크다" 가 아니라 `상대변화 > 임계값` **AND** `변화량 > σ × 측정노이즈` **AND** `변화량 > 최소유의차`. 3중 게이트로 false positive 를 막는다 (§6.3).
6. **Regression 이력 자체를 데이터로 저장.** 언제 처음 나빠졌는지 / 어느 커밋인지 추적.

**한 줄 요약:** FrameGuard 는 *"이 빌드를 떨어뜨려야 하나?"* 를 로컬에서 답한다. 이 프로젝트는 *"지난 3개월 동안 이 화면이 어떻게 나빠져 왔고, 그게 어느 PR 이었나?"* 를 CI 에서 답한다.

---

## 2. 요구사항

### 기능 요구사항
| ID | 내용 |
|---|---|
| F1 | 시나리오 단위로 frame time / FPS / jank rate / p50·p90·p95·p99 / dropped frames / startup / memory / 실행시간 수집 |
| F2 | 시나리오별 N회 반복 측정 + outlier 제거 + median 집계 |
| F3 | Baseline 을 Git 저장소에 JSON 으로 보관, 명시적 rebaseline 으로만 갱신 |
| F4 | metric 별 임계값 + 통계적 유의성으로 regression 판정, 시나리오·지표별 개별 결과 유지 |
| F5 | PR 에 Markdown 리포트(Step Summary + PR comment), regression 시 CI fail |
| F6 | 모든 CI 실행 결과를 commit/PR 키와 함께 영구 저장 |
| F7 | 장기 추세 Dashboard (지표별·시나리오별 추세, regression 시점 표시, 기간 필터, 커밋 링크) |
| F8 | Regression 발생 이력 저장 및 조회 |

### 비기능 요구사항
| ID | 내용 |
|---|---|
| N1 | 앱 코드에 성능 측정용 런타임 의존성을 추가하지 않는다 (integration_test 만 사용) |
| N2 | 외부 인프라(DB 서버, SaaS) 없이 GitHub 만으로 동작 |
| N3 | collector 교체 가능 (파일 포맷 계약으로만 결합) |
| N4 | CI 러너 변동성으로 인한 false positive 최소화 |
| N5 | 리포트/대시보드는 CI 로그를 뒤지지 않고 읽을 수 있어야 함 |

### 비목표 (Non-goals)
- 프로덕션 런타임 성능 텔레메트리 (Perflutter/Firebase Performance 영역)
- 프레임 캡처 엔진 자체 재구현 (Flutter 공식 API 사용)
- 실기기 팜 / 디바이스 매트릭스 (러너 1종 고정, §6.1)

---

## 3. Architecture

```
                       perf.yaml (시나리오·임계값·반복수)
                              │
 Flutter app                  ▼
 + integration_test ──▶ performance-runner   (perfkit run)        tool/perfkit/cli.py
   (traceAction)             │  flutter run --trace-startup + flutter drive, × N repeats
                             ▼
                       metrics-collector      runs/run-<i>/<scenario>.json
                             │  TimelineSummary + memory + startup
                             ▼
                       metrics-analyzer       (perfkit aggregate) metrics.py
                             │  정규화 · outlier(MAD) · median · noise
                             ▼  current.json
                       baseline-manager       baseline.py
                             │  baseline/performance_baseline.json (git)
                             ▼
                       regression-detector    detect.py
                             │  임계값 ∧ 통계적 유의성 ∧ 최소유의차
                             ▼
                       report-generator       report.py  → perf_report.md
                             │
                             ▼
                       result-storage         store.py   → history.jsonl / regressions.jsonl
                             │                             (perf-history 브랜치)
                             ▼
                       dashboard              dashboard/index.html (정적, JSONL fetch)
```

**결합 규칙**

- 각 단계는 **파일**로만 통신한다. 앞 단계를 바꿔도 뒤 단계는 모른다.
- 측정 엔진(Dart) ↔ 분석기(Python) 경계는 `runs/<run>/<scenario>.json`.
- 분석기 ↔ Dashboard 경계는 `history.jsonl`. Dashboard 는 Python 을 import 하지 않고, 정적 파일만 읽는다.
- 그래서 collector 를 FrameGuard 로 바꾸거나, dashboard 를 Next.js 로 바꿔도 나머지는 무변경.

**모듈 책임**

| 모듈 | 파일 | 책임 | 하지 않는 일 |
|---|---|---|---|
| runner | `cli.py run` | `--trace-startup` + `flutter drive` 를 N회 실행 | 지표 해석 |
| collector | `perf_driver.dart` | timeline → summary + memory/startup 직렬화 | 판정 |
| analyzer | `metrics.py` | 정규화, 백분위, outlier, median/noise | baseline 접근 |
| baseline | `baseline.py` | baseline 로드/생성/비교 | 합격 판정 |
| detector | `detect.py` | 임계값·유의성 판정 | 출력 포맷 |
| reporter | `report.py` | Markdown | 파일 저장 위치 결정 |
| storage | `store.py` | history/regression JSONL append | 판정 |
| dashboard | `dashboard/index.html` | 시각화 | 계산 |

---

## 4. 데이터 모델

### 4.1 계약 1 — collector 출력 `runs/run-<i>/<scenario>.json`
```json
{
  "scenario": "community_detail",
  "timeline": { "...TimelineSummary.summaryJson..." },
  "memory": { "rss_mb": 182.4, "max_rss_mb": 210.1 },
  "startup_ms": 820,
  "duration_ms": 5421,
  "frame_budget_ms": 16.67
}
```
이 4개 키만 맞추면 어떤 collector 든 붙는다 (N3).

`timeline` 은 **null 일 수 있다** (프레임이 거의 없는 구간은 `TimelineSummary` 가 요약을 거부한다).
이때 analyzer 는 프레임 지표를 0 으로 채우지 않고 **아예 내보내지 않는다** — 0 FPS 는
"측정 안 됨"이지 "완벽함"이 아니다. baseline 에 있던 지표가 이번에 사라지면
리포트에 ⚠️ 로 표시된다 (추세선이 조용히 끊기는 것을 막는다).

### 4.2 계약 2 — analyzer 출력 `current.json`
```json
{
  "schema": 1,
  "generated_at": "2026-09-07T10:30:00Z",
  "repeats": 3,
  "scenarios": {
    "community_detail": {
      "runs_used": 3, "runs_dropped": 0,
      "metrics": {
        "fps":               { "value": 59.4, "noise": 0.31, "samples": [59.4, 59.2, 59.6] },
        "jank_rate":         { "value": 1.2,  "noise": 0.15, "samples": [...] },
        "p95_frame_time_ms": { "value": 18.4, "noise": 0.9,  "samples": [...] },
        "memory_mb":         { "value": 182.4,"noise": 2.1,  "samples": [...] },
        "startup_ms":        { "value": 820,  "noise": 14.0, "samples": [...] }
      }
    }
  }
}
```
`value` = median, `noise` = MAD 를 정규분포 표준편차로 환산한 값(× 1.4826).

### 4.3 Baseline `baseline/performance_baseline.json` (Git 관리)
```json
{
  "schema": 1,
  "updated_at": "2026-09-01T08:00:00Z",
  "commit": "a81f23c",
  "device_profile": "ci-emulator-api34",
  "repeats": 3,
  "scenarios": { "community_detail": { "metrics": { "fps": {"value":59.8,"noise":0.4}, ... } } }
}
```
- Git 파일이므로 변경 이력 = `git log baseline/performance_baseline.json`.
- CI 는 절대 자동 커밋하지 않는다. `workflow_dispatch(rebaseline)` → PR 생성 → 사람이 머지.

### 4.4 History `history.jsonl` (한 줄 = 1 CI run × 1 scenario)
```json
{"schema":1,"run_id":"123","commit":"a81f23c","branch":"main","pr":null,
 "timestamp":"2026-09-07T10:30:00Z","scenario":"community_detail",
 "metrics":{"fps":59.8,"jank_rate":1.2,"p95_frame_time_ms":17.1,"memory_mb":183,"startup_ms":820},
 "noise":{"fps":0.3,...},"repeats":3,"device_profile":"ci-emulator-api34","status":"pass"}
```
append-only. 시나리오를 행에 풀어놨기 때문에 나중에 SQLite/Parquet 로 그대로 적재된다.

### 4.5 Regression 이력 `regressions.jsonl`
```json
{"timestamp":"2026-09-05T...","commit":"a81f23c","pr":108,"run_id":"512",
 "scenario":"community_detail","metric":"fps",
 "baseline":59.8,"current":51.2,"change_pct":-14.4,"threshold_pct":10,"significant":true}
```
"언제 처음 나빠졌나" 는 이 파일을 scenario+metric 으로 정렬해 첫 항목을 보면 된다.

---

## 5. 기술 선택

| 영역 | 후보 | 선택 | 이유 |
|---|---|---|---|
| 측정 | FrameTiming API / integration_test / flutter_driver / DevTools API / FrameGuard | **integration_test + `binding.traceAction` + `TimelineSummary`** | 공식 API. 앱 런타임 의존성 0 (N1). raw 프레임 배열을 주기 때문에 p50~p99·jank rate 를 우리가 직접 계산 가능. FrameGuard 는 앱에 의존성을 요구하고 memory/startup 을 못 준다 |
| 언어(분석) | Dart / Python / Node | **Python 3 (표준 라이브러리 + PyYAML)** | GitHub 러너 기본 탑재. 통계·JSON 처리에 의존성 불필요. Dart 로 하면 분석기가 앱 pubspec 에 묶인다 |
| CI | GitHub Actions | **GitHub Actions** (`reactivecircus/android-emulator-runner`) | 요구사항 |
| 저장 | JSON / JSONL / SQLite / PostgreSQL / Artifacts | **JSONL on orphan `perf-history` branch** | ① append-only 라 JSON 전체 재작성/머지 충돌 없음 ② Artifacts 는 기본 90일 만료 → 장기 추세 불가 ③ Postgres 는 N2 위반 ④ SQLite 는 바이너리라 Git diff/충돌에 취약. **전환 기준: 10만 행(≈ 5년치) 또는 파일 20MB 초과 시 SQLite 로 적재 후 Pages 에 배포** |
| Dashboard | React / Next.js / Flutter Web | **정적 HTML 1파일 + Chart.js (GitHub Pages)** | 빌드 스텝·node_modules·배포 파이프라인 0. 데이터가 JSONL 정적 파일이라 SSR 이 줄 게 없다. Flutter Web 은 초기 로드 비용이 대시보드에 과함. **전환 기준: 서버측 집계·인증·다중 저장소가 필요해지면 Next.js** |
| 차트 | Chart.js / D3 / Recharts | **Chart.js 4 (CDN)** | 시계열 line + annotation + click 핸들러가 기본 제공. D3 는 직접 그려야 하고 Recharts 는 React 필요 |

---

## 6. CI 변동성 대응 (핵심 설계 원칙)

### 6.1 러너 성능 차이
- 러너 이미지·API level·에뮬레이터 옵션을 `perf.yaml` 의 `device_profile` 로 고정하고, baseline·history 에 함께 기록한다.
- **profile 이 다르면 비교하지 않는다** (detector 가 경고 후 skip). 다른 하드웨어의 절대값 비교는 무의미하다.
- 러너 변동을 아예 제거하고 싶으면 코드 변경 없이 가능:
  base 커밋을 같은 러너에서 측정 → `perfkit rebaseline --from runs-base --out /tmp/base.json` → 그 파일로 `check`.
  (baseline-manager 는 파일이 어디서 왔는지 신경 쓰지 않는다)

### 6.2 반복 측정과 outlier
- 기본 `repeats: 3` (프로세스를 매번 새로 띄우는 반복 → 워밍업·GC·JIT 편차 포함).
- 각 시나리오 안에서는 워밍업 스크롤 3회를 측정에서 제외.
- outlier 는 **median ± 3·MAD** 밖이면 제외하되, 남는 표본이 2개 미만이면 제외하지 않는다(전부 노이즈면 지우는 게 더 위험).
- 대표값은 mean 이 아니라 **median** (단일 스파이크에 둔감).

### 6.3 회귀 판정식 (3중 게이트)

지표가 나쁜 방향으로 움직였고, 아래 셋을 **모두** 만족할 때만 FAIL:

```
1) 상대 변화   |Δ| / baseline × 100  >  threshold_percent
2) 통계적 유의  |Δ|  >  sigma × sqrt(noise_base² + noise_current²)     (sigma 기본 2.0)
3) 최소 유의차  |Δ|  >  min_abs_delta                                   (지표별 절대 하한)
```

- (1)만 쓰면 baseline 이 작은 값일 때(jank_rate 0.2% → 0.5%) +150% 로 폭발 → false positive.
- (2)는 러너 노이즈 안의 변화를 걸러낸다. noise 는 §4.2 의 MAD 기반 값.
- (3)은 "17.0ms → 17.9ms" 처럼 통계적으로는 유의하지만 사람에게 의미 없는 변화를 걸러낸다.
- 반대로 **너무 느슨해지는 것**(false negative)은 dashboard 의 장기 추세가 잡는다.
  한 PR 당 −2% 는 게이트를 통과하지만, 30 커밋 뒤 −30% 는 추세선에서 바로 보인다. 이게 §7 이 게이트와 별개로 필요한 이유다.

### 6.4 Baseline drift
- baseline 은 자동 갱신하지 않는다. 갱신은 `workflow_dispatch` → PR → 리뷰.
- baseline 이 오래되면(기본 30일) 리포트에 경고 배지를 표시한다.
- dashboard 는 baseline 변경 시점을 세로선으로 표시해 "성능이 좋아진 것"과 "기준을 낮춘 것"을 구분한다.

### 6.5 부분 실패를 정보 손실로 만들지 않는다

- Flutter 가 제공하는 `integrationDriver()` 헬퍼는 **모든 테스트가 통과했을 때만** 결과 콜백을
  호출한다. 시나리오 하나가 깨지면 나머지 5개의 측정치까지 통째로 사라진다.
  그래서 `test_driver/perf_driver.dart` 는 이 헬퍼를 쓰지 않고 `FlutterDriver` 를 직접 열어
  **받은 데이터를 먼저 저장한 뒤** 종료 코드로만 실패를 알린다.
- 결과가 아예 없는 시나리오는 "회귀 없음"이 아니라 **측정 실패**다. `check` 는 이 경우에도
  exit 1 을 낸다. 조용히 통과시키면 테스트가 깨진 채로 게이트가 무력화된다.

### 6.6 알려진 한계 (정직하게 기록)
- `startup_ms` 는 `flutter run --trace-startup` 의 `timeToFirstFrameMicros` 다.
  (처음에는 integration_test 안에서 `main()` → 첫 프레임을 쟀는데, 실측해보니 **5ms** 가 나왔다 —
  이미 떠 있는 프로세스의 위젯 빌드 시간이지 startup 이 아니다. 앱을 한 번 더 띄우는 비용을
  감수하고 공식 API 로 바꿨다.) 이 시나리오는 프레임 분포를 주지 않으므로 `timeline` 은 null 이다.
- `memory_mb` 는 Dart `ProcessInfo.currentRss` — 앱 프로세스 RSS 이며 GPU/네이티브 힙 분해는 못 한다.
- `fps` 는 `min(주사율, 1000 / p50_frame_time)` 이다. `frame_count / wall_clock` 은 쓰지 않는다 —
  실측해보니 테스트 하네스가 제스처 사이에 노는 시간이 분모에 들어가 36초에 65 프레임,
  즉 2 fps 라는 무의미한 값이 나왔다. 지금 값은 "이 프레임들이 유지할 수 있었던 프레임률"이다.

---

## 7. 개발자 경험 (최종 흐름)

```
PR push → GitHub Actions → 시나리오 × 3회 측정 → aggregate → baseline 비교
        → detect → perf_report.md → Step Summary + PR comment → pass/fail
main merge → 같은 측정 → history.jsonl append (perf-history 브랜치)
        → dashboard 재배포 (GitHub Pages)
```
PR 코멘트에는 실패한 시나리오/지표 표와 `[View Performance Dashboard]` 링크가 들어가고,
링크를 누르면 해당 시나리오의 3개월 추세와 regression 마커가 보인다.
