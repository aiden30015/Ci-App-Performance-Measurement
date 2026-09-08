import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

void main() => runApp(const DemoApp());

/// 성능 시나리오를 태울 대상 앱. 실제 프로젝트에서는 이 파일만 여러분의 앱으로
/// 바꾸고 integration_test/perf_test.dart 의 시나리오 본문을 맞춰주면 됩니다.
class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'perf demo',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(useMaterial3: true),
        routes: {
          '/': (_) => const LoginScreen(),
          '/home': (_) => const HomeShell(),
        },
      );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _busy = false;

  Future<void> _login() async {
    setState(() => _busy = true);
    // 네트워크 왕복을 흉내내는 고정 지연. 측정이 흔들리지 않게 상수로 둔다.
    await Future<void>.delayed(const Duration(milliseconds: 120));
    if (mounted) Navigator.of(context).pushReplacementNamed('/home');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 360),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const FlutterLogo(size: 72),
                const SizedBox(height: 24),
                const TextField(
                  key: Key('login_id'),
                  decoration: InputDecoration(labelText: 'ID'),
                ),
                const TextField(
                  key: Key('login_pw'),
                  obscureText: true,
                  decoration: InputDecoration(labelText: 'Password'),
                ),
                const SizedBox(height: 24),
                FilledButton(
                  key: const Key('login_submit'),
                  onPressed: _busy ? null : _login,
                  child: const Text('Sign in'),
                ),
              ],
            ),
          ),
        ),
      );
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('perf demo')),
        body: IndexedStack(
          index: _tab,
          children: const [HomeFeed(), CommunityList(), ImageHeavyGrid()],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _tab,
          onDestinationSelected: (i) => setState(() => _tab = i),
          destinations: const [
            NavigationDestination(
              key: Key('tab_home'),
              icon: Icon(Icons.home),
              label: 'Home',
            ),
            NavigationDestination(
              key: Key('tab_community'),
              icon: Icon(Icons.forum),
              label: 'Community',
            ),
            NavigationDestination(
              key: Key('tab_gallery'),
              icon: Icon(Icons.photo_library),
              label: 'Gallery',
            ),
          ],
        ),
      );
}

class HomeFeed extends StatelessWidget {
  const HomeFeed({super.key});

  @override
  Widget build(BuildContext context) => ListView.builder(
        key: const Key('home_list'),
        itemCount: 2000,
        itemBuilder: (context, i) => ListTile(
          leading: CircleAvatar(child: Text('$i')),
          title: Text('item $i'),
          subtitle: Text('subtitle for item $i'),
        ),
      );
}

class CommunityList extends StatelessWidget {
  const CommunityList({super.key});

  @override
  Widget build(BuildContext context) => ListView.builder(
        key: const Key('community_list'),
        itemCount: 800,
        itemBuilder: (context, i) => Card(
          margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: ListTile(
            key: Key('post_$i'),
            leading: _Swatch(seed: i),
            title: Text('post #$i'),
            subtitle: Text('${i % 97} comments · ${i % 43} likes'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => CommunityDetail(id: i)),
            ),
          ),
        ),
      );
}

/// 상세 화면: 그림자 + 그라디언트 + 블러가 섞인, 래스터가 무거운 화면.
class CommunityDetail extends StatelessWidget {
  const CommunityDetail({super.key, required this.id});

  final int id;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text('post #$id')),
        body: ListView.builder(
          key: const Key('detail_list'),
          itemCount: 300,
          itemBuilder: (context, i) => Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Container(
              height: 96,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(16),
                gradient: LinearGradient(
                  colors: [_hue(id + i), _hue(id + i + 40)],
                ),
                boxShadow: const [
                  BoxShadow(blurRadius: 12, offset: Offset(0, 4)),
                ],
              ),
              child: Center(child: Text('comment $i')),
            ),
          ),
        ),
      );
}

/// 이미지가 많은 화면. 에셋/네트워크 없이 코드로 만든 그라디언트 + 블러로
/// 래스터 부하만 재현한다. CI 에서 외부 의존성이 생기면 측정이 흔들린다.
class ImageHeavyGrid extends StatelessWidget {
  const ImageHeavyGrid({super.key});

  @override
  Widget build(BuildContext context) => GridView.builder(
        key: const Key('gallery_grid'),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 3,
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
        ),
        padding: const EdgeInsets.all(8),
        itemCount: 600,
        itemBuilder: (context, i) => ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: ImageFiltered(
            imageFilter: ui.ImageFilter.blur(sigmaX: 1.5, sigmaY: 1.5),
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: SweepGradient(colors: [_hue(i), _hue(i + 60), _hue(i)]),
              ),
              child: Center(child: Text('$i')),
            ),
          ),
        ),
      );
}

class _Swatch extends StatelessWidget {
  const _Swatch({required this.seed});

  final int seed;

  @override
  Widget build(BuildContext context) => Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: LinearGradient(colors: [_hue(seed), _hue(seed + 30)]),
        ),
      );
}

Color _hue(int i) =>
    HSVColor.fromAHSV(1, (i * 37) % 360, .55, .85 - .2 * math.sin(i / 7)).toColor();
