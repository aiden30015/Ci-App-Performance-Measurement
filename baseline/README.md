# baseline

`performance_baseline.json` 은 시나리오별 성능 기준값입니다. **자동으로 갱신되지 않습니다.**

- 갱신: Actions → `perf` workflow → Run workflow → `rebaseline: true`
  → 실행한 브랜치에 baseline 커밋이 바로 올라가고, 그 브랜치의 PR 에 갱신 코멘트가 달립니다.
  기본 브랜치에서 실행하면 `perf/rebaseline-<run>` 브랜치로 따로 올라갑니다.
- 변경 이력: `git log -p baseline/performance_baseline.json`
- 로컬에서: `PYTHONPATH=tool python -m perfkit rebaseline --commit "$(git rev-parse HEAD)"`

`device_profile` 이 다른 baseline 과는 비교하지 마세요. 러너/에뮬레이터가 바뀌면
절대값이 통째로 달라지므로, 리포트가 경고를 띄우고 그 비교는 신뢰할 수 없습니다.
현재 커밋된 baseline 은 `local-windows` (개발 머신)에서 뜬 것이라 형식 예시로만 쓰이고,
CI(`ci-emulator-api34`)에서는 첫 실행 후 rebaseline 이 필요합니다.
