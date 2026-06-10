import 'package:flutter/material.dart';
import 'api_client.dart';
import 'theme.dart';
import 'connect_page.dart';
import 'dashboard_page.dart';

void main() {
  runApp(const CryptoBotApp());
}

class CryptoBotApp extends StatelessWidget {
  const CryptoBotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Crypto Bot',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(),
      home: const _Boot(),
    );
  }
}

/// 저장된 연결 정보가 있으면 대시보드로, 없으면 연결 화면으로.
class _Boot extends StatefulWidget {
  const _Boot();
  @override
  State<_Boot> createState() => _BootState();
}

class _BootState extends State<_Boot> {
  @override
  void initState() {
    super.initState();
    _decide();
  }

  Future<void> _decide() async {
    final client = await ApiClient.load();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(
      builder: (_) =>
          client == null ? const ConnectPage() : DashboardPage(client: client),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator(color: C.blue)),
    );
  }
}
