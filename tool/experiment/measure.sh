#!/usr/bin/env bash
# 비교 실험 측정 (exp/frameguard-compare). 에뮬레이터 안에서 한 번 호출된다.
#   MODE=stored : INJECT 버전만 REPEATS 회  → out/run-*
#   MODE=paired : 같은 에뮬레이터에서 base(0) 와 INJECT 버전을 번갈아 REPEATS 회
#                 → out/base/run-*, out/var/run-*
set -uo pipefail
SCEN=home_scroll,community_scroll
run() {  # $1=inject $2=dest
  rm -rf _tmp
  PYTHONPATH=tool python -m perfkit run --out _tmp --repeats 1 --scenarios "$SCEN" \
    --dart-define "PERF_INJECT_US=$1"
  mkdir -p "$(dirname "$2")" && mv _tmp/run-1 "$2"
}
if [ "$MODE" = stored ]; then
  for i in $(seq 1 "$REPEATS"); do run "$INJECT" "out/run-$i"; done
else
  for i in $(seq 1 "$REPEATS"); do
    # 순서 편향을 없애려고 홀수 회차는 base 먼저, 짝수 회차는 variant 먼저.
    if [ $((i % 2)) = 1 ]; then
      run 0 "out/base/run-$i"; run "$INJECT" "out/var/run-$i"
    else
      run "$INJECT" "out/var/run-$i"; run 0 "out/base/run-$i"
    fi
  done
fi
find out -name '*.json' | sort
