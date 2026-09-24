// FrameGuard 판정기를 그대로 호출하는 배치 래퍼 (비교 실험용).
// stdin: [{"id": "...", "baseline": [report.json...], "current": [report.json...]}]
// stdout: {"<id>": {"regression": bool, "rel_p95": double, "jank_delta": double}}
// 판정은 FrameGuard 의 StatisticalRegression(기본 임계값: median p95 +15% 또는
// jank +1%p) 이다. 우리 쪽 해석이 끼지 않도록 FrameGuard 코드만 쓴다.
import 'dart:convert';
import 'dart:io';

import 'package:frameguard/src/reporting/report.dart';
import 'package:frameguard/src/reporting/statistical.dart';

Future<void> main() async {
  final jobs = jsonDecode(await stdin.transform(utf8.decoder).join()) as List;
  final cache = <String, FrameGuardReport>{};
  FrameGuardReport load(String p) => cache.putIfAbsent(
      p,
      () => FrameGuardReport.fromJson(Map<String, Object?>.from(
          jsonDecode(File(p).readAsStringSync()) as Map)));

  const judge = StatisticalRegression();
  final out = <String, Object?>{};
  for (final j in jobs.cast<Map>()) {
    final base = StatisticalSummary.fromReports(
        (j['baseline'] as List).cast<String>().map(load).toList());
    final cur = StatisticalSummary.fromReports(
        (j['current'] as List).cast<String>().map(load).toList());
    final r = judge.compare(baseline: base, current: cur, scenario: '${j['id']}');
    out['${j['id']}'] = {
      'regression': r.isRegression,
      'rel_p95': r.relativeP95Change,
      'jank_delta': cur.medianJankRate - base.medianJankRate,
      'base_p95': base.medianP95Ms,
      'cur_p95': cur.medianP95Ms,
    };
  }
  stdout.write(jsonEncode(out));
}
