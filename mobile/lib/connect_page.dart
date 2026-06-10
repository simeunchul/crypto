import 'package:flutter/material.dart';
import 'api_client.dart';
import 'theme.dart';
import 'dashboard_page.dart';

/// 서버 주소 + 토큰 입력 (PC 앱의 '폰 앱 연결 정보' 에서 확인한 값).
class ConnectPage extends StatefulWidget {
  final ApiClient? existing;
  const ConnectPage({super.key, this.existing});

  @override
  State<ConnectPage> createState() => _ConnectPageState();
}

class _ConnectPageState extends State<ConnectPage> {
  late final TextEditingController _url =
      TextEditingController(text: widget.existing?.baseUrl ?? 'http://192.168.0.10:8787');
  late final TextEditingController _token =
      TextEditingController(text: widget.existing?.token ?? '');
  bool _busy = false;
  String? _error;

  Future<void> _connect() async {
    setState(() { _busy = true; _error = null; });
    var url = _url.text.trim();
    if (url.endsWith('/')) url = url.substring(0, url.length - 1);
    final client = ApiClient(baseUrl: url, token: _token.text.trim());
    try {
      final h = await client.health();
      if (h['ok'] != true) throw Exception('서버 응답 오류');
      // 토큰 검증: status 호출 (401 이면 throw)
      await client.status();
      await client.save();
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => DashboardPage(client: client)),
      );
    } catch (e) {
      setState(() => _error = '연결 실패: ${e.toString().replaceFirst('Exception: ', '')}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('서버 연결')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 8),
            const Text('PC 앱에서 "📱 폰 앱 연결 정보" 를 열어\n주소와 토큰을 확인 후 입력하세요.',
                style: TextStyle(color: C.muted, height: 1.5)),
            const SizedBox(height: 24),
            _field('서버 주소', _url, hint: 'http://192.168.0.10:8787'),
            const SizedBox(height: 16),
            _field('토큰', _token, hint: '토큰 문자열 붙여넣기', obscure: false),
            const SizedBox(height: 24),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Text(_error!, style: const TextStyle(color: C.red)),
              ),
            FilledButton(
              onPressed: _busy ? null : _connect,
              style: FilledButton.styleFrom(
                backgroundColor: C.blue,
                padding: const EdgeInsets.symmetric(vertical: 16),
              ),
              child: _busy
                  ? const SizedBox(height: 20, width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('연결', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _field(String label, TextEditingController c,
      {String? hint, bool obscure = false}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(color: C.muted, fontSize: 13)),
        const SizedBox(height: 6),
        TextField(
          controller: c,
          obscureText: obscure,
          style: const TextStyle(color: C.text),
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: const TextStyle(color: C.muted),
            filled: true,
            fillColor: C.card2,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: C.line),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: C.line),
            ),
          ),
        ),
      ],
    );
  }
}
