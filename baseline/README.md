# baseline

`performance_baseline.json` 은 시나리오별 성능 기준값입니다. **자동으로 갱신되지 않습니다.**

- 갱신: Actions → `perf` workflow → Run workflow → `rebaseline: true`
  → `perf/rebaseline-<run>` 브랜치와 PR 이 만들어집니다. 사람이 보고 머지하세요.
- 변경 이력: `git log -p baseline/performance_baseline.json`
- 로컬에서: `PYTHONPATH=tool python -m perfkit rebaseline --commit "$(git rev-parse HEAD)"`

`device_profile` 이 다른 baseline 과는 비교하지 마세요. 러너/에뮬레이터가 바뀌면
절대값이 통째로 달라지므로, 리포트가 경고를 띄우고 그 비교는 신뢰할 수 없습니다.
현재 커밋된 baseline 은 `local-windows` (개발 머신)에서 뜬 것이라 형식 예시로만 쓰이고,
CI(`ci-emulator-api34`)에서는 첫 실행 후 rebaseline 이 필요합니다.
