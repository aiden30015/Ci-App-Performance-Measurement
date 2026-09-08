import 'dart:convert';
import 'dart:io';

import 'package:flutter_driver/flutter_driver.dart';
import 'package:integration_test/common.dart';

/// 시나리오별로 `build/perf/<scenario>.json` 을 하나씩 떨군다.
/// 이 파일 포맷이 measurement 엔진과 분석 파이프라인 사이의 유일한 계약이다
/// (docs/DESIGN.md §4.1). raw timeline(수 MB)은 저장하지 않는다.
///
/// `integrationDriver()` 를 쓰지 않는 이유: 그 헬퍼는 모든 테스트가 통과했을 때만
/// responseDataCallback 을 호출한다. 시나리오 하나가 깨지면 나머지 5개의 측정치까지
/// 통째로 잃는다. 여기서는 실패해도 받은 데이터는 먼저 저장하고, 종료 코드로만 알린다.
Future<void> main() async {
  final driver = await FlutterDriver.connect();
  final raw = await driver.requestData(
    null,
    timeout: const Duration(minutes: 30),
  );
  await driver.close();

  final response = Response.fromJson(raw);
  final data = response.data;
  if (data != null) {
    final outDir = Directory(
      Platform.environment['PERF_OUT_DIR'] ?? 'build/perf',
    );
    await outDir.create(recursive: true);

    for (final entry in data.entries) {
      if (!entry.key.startsWith('timeline:')) continue;
      final name = entry.key.substring('timeline:'.length);
      final meta = (data['meta:$name'] ?? const {}) as Map<String, dynamic>;

      // 프레임이 거의 없는 구간(예: app_startup)은 summaryJson 이 던진다.
      // 그 시나리오의 startup/memory 는 여전히 유효하므로 timeline 만 비운다.
      // 0 으로 채우면 없는 성능을 있다고 보고하게 된다.
      Map<String, dynamic>? summary;
      try {
        summary = TimelineSummary.summarize(
          Timeline.fromJson(entry.value as Map<String, dynamic>),
        ).summaryJson;
      } catch (e) {
        stderr.writeln('[perf] $name: 타임라인 요약 실패 ($e) — 프레임 지표 없이 저장');
      }

      await File('${outDir.path}/$name.json').writeAsString(
        const JsonEncoder.withIndent('  ').convert({
          'scenario': name,
          'timeline': summary,
          'memory': meta['memory'],
          'startup_ms': meta['startup_ms'],
          'duration_ms': meta['duration_ms'],
          'frame_budget_ms': meta['frame_budget_ms'],
        }),
      );
      stdout.writeln('[perf] wrote ${outDir.path}/$name.json');
    }
  }

  if (!response.allTestsPassed) {
    stderr.writeln('Failure Details:\n${response.formattedFailureDetails}');
  }
  exit(response.allTestsPassed ? 0 : 1);
}
