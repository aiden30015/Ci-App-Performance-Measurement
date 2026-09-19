# Flutter Performance CI

Flutter 앱의 성능을 **시나리오 단위로** 측정하고, baseline 과 비교해 회귀를 잡고,
결과를 커밋 축으로 계속 쌓아 **장기 추세 대시보드**로 보여주는 CI 파이프라인.

```
Flutter app → integration_test → 지표 수집 → 반복·통계 집계 → baseline 비교
→ 회귀 판정 → PR 리포트 / CI 실패 → JSONL 히스토리 → Trend Dashboard
```

설계 근거·조사 내용은 **[docs/DESIGN.md](docs/DESIGN.md)** 에 있습니다.

## FrameGuard 와 무엇이 다른가

[FrameGuard](https://pub.dev/packages/frameguard) 는 프레임 타이밍 캡처·budget·baseline·CI 게이트를
이미 잘 합니다. 그건 다시 만들지 않았습니다. 이 프로젝트는 FrameGuard 가 명시적으로 하지 않는 쪽을 맡습니다.

| | FrameGuard | 이 프로젝트 |
|---|---|---|
| 실행 위치 | 앱에 의존성 추가 (in-app) | `integration_test` 만 사용, **앱 런타임 의존성 0** |
| 지표 | 프레임 타이밍 전용 | 프레임 + **memory + startup + 실행시간** |
| 히스토리 | 로컬 JSONL (드리프트 확인용) | **commit / PR / run_id 축의 영구 시계열** |
| Dashboard | 없음 (명시적 non-goal) | **핵심 기능** — 시나리오별 장기 추세 + 회귀 시점 |
| 회귀 판정 | budget + baseline + multi-run 통계 | 임계값 **∧** 통계적 유의성 **∧** 최소유의차 (3중 게이트) |

한 줄로: FrameGuard 는 *"이 빌드를 떨어뜨려야 하나"*, 이 프로젝트는
*"지난 3개월 동안 이 화면이 어떻게 나빠져 왔고 그게 어느 PR 이었나"* 를 답합니다.

## 빠르게 해보기

```bash
flutter pub get
pip install pyyaml

# 1) 측정 (perf.yaml 의 repeats 만큼 앱을 새로 띄워 반복)
PYTHONPATH=tool python -m perfkit run --device windows

# 2) 반복 결과 집계 (outlier 제거 + median + 노이즈)
PYTHONPATH=tool python -m perfkit aggregate

# 3) baseline 이 없으면 먼저 만든다
PYTHONPATH=tool python -m perfkit rebaseline --commit "$(git rev-parse HEAD)"

# 4) 비교 → perf_report.md (회귀면 exit 1)
PYTHONPATH=tool python -m perfkit check

# 5) 히스토리에 쌓고 대시보드 생성
PYTHONPATH=tool python -m perfkit store --commit "$(git rev-parse HEAD)" --branch main
PYTHONPATH=tool python -m perfkit dashboard --out site
python -m http.server -d site 8000
```

대시보드를 데이터 없이 먼저 보고 싶으면:
`PYTHONPATH=tool python -m perfkit seed-demo` 로 40회 CI run 을 흉내낸 데모 히스토리를 만든 뒤 위 5)를 실행하세요.
대시보드를 파일 하나로 들고 다니려면(사내 공유, fetch 가 막힌 환경) `perfkit dashboard --inline`.

자체 점검: `python tool/test_perfkit.py`

## 구성

| 경로 | 역할 |
|---|---|
| `perf.yaml` | 시나리오, 반복 횟수, 지표별 임계값, 러너 프로파일 |
| `integration_test/perf_test.dart` | 시나리오 정의 (측정 대상) |
| `test_driver/perf_driver.dart` | 타임라인 → `runs/run-N/<scenario>.json` |
| `tool/perfkit/` | runner · analyzer · baseline · detector · reporter · storage |
| `baseline/performance_baseline.json` | Git 으로 관리하는 기준값 (자동 갱신 안 함) |
| `dashboard/index.html` | 정적 대시보드 (JSONL 만 읽음) |
| `.github/workflows/perf.yml` | PR 게이트 / main 히스토리 적재 / Pages 배포 |

## 내 앱에 붙이기

1. `lib/main.dart` 를 여러분의 앱으로 바꾸고,
   `integration_test/perf_test.dart` 의 `scenario(...)` 본문을 실제 화면 흐름으로 교체합니다.
2. `perf.yaml` 의 `scenarios` 목록을 맞춥니다.
3. 첫 실행에서 `rebaseline` 으로 기준을 만들고 커밋합니다.
4. GitHub Pages 를 켜고 `perf.yaml` 의 `dashboard_url` 에 주소를 넣으면 PR 코멘트에 링크가 붙습니다.

## PR 에서 관련 시나리오만 돌리기 (선택)

에뮬레이터 측정은 12~15분이라 모든 PR 마다 전부 돌리면 무겁습니다. `perf.yaml` 에 시나리오별
관련 경로를 적고 재사용 workflow 에 `select-changed: true` 를 주면, PR 에서 변경된 파일과
겹치는 시나리오만 측정합니다.

```yaml
selection:
  ignore: ["**/*.md", "docs/**"]            # 이것만 바뀌면 측정 자체를 건너뜀
  scenarios:
    member_list_scroll: ["lib/features/member/**"]
    login: ["lib/features/auth/**", "lib/features/splash/**"]
```

- 어떤 시나리오에도 안 걸리는 파일(공통 코드, 빌드 설정 등)이 하나라도 바뀌면 **전체**를 돕니다. 놓치는 쪽보다 더 도는 쪽이 안전하기 때문입니다.
- `selection` 이 없으면 전체를 돕니다. push·수동 실행도 항상 전체입니다 (히스토리/baseline 은 전체 결과여야 하므로).
- 수동으로 고르려면 `scenarios: 'home_scroll,login'` 입력을 씁니다. 부분 결과로는 `rebaseline` 할 수 없습니다.
- PR 코멘트 상단에 왜 그 시나리오를 골랐는지, 무엇을 건너뛰었는지 표시됩니다.
- 로컬 확인: `perfkit select --base origin/main`

## 알아둘 것

- `app_startup` 은 integration test 가 아니라 `flutter run --trace-startup` 으로 잽니다
  (반복마다 앱을 한 번 더 띄웁니다). 그래서 이 시나리오만 프레임 지표가 없습니다.
- `fps` 는 `min(주사율, 1000/p50 프레임 시간)` 입니다. 벽시계 기준 프레임 수가 아닙니다.
- `memory_mb` 는 앱 프로세스 RSS 입니다. GPU/네이티브 힙은 분해되지 않습니다.
- 다른 러너에서 뜬 baseline 과는 비교하지 마세요. `device_profile` 이 다르면 리포트가 경고합니다.

