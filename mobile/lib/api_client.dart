import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// 백엔드(PC 데스크톱 앱의 FastAPI 서버)와 통신.
/// 폰은 비-loopback 이므로 Bearer 토큰 필수.
class ApiClient {
  String baseUrl;
  String token;

  ApiClient({required this.baseUrl, required this.token});

  static const _kUrl = 'server_url';
  static const _kToken = 'server_token';

  static Future<ApiClient?> load() async {
    final sp = await SharedPreferences.getInstance();
    final url = sp.getString(_kUrl);
    final token = sp.getString(_kToken);
    if (url == null || url.isEmpty) return null;
    return ApiClient(baseUrl: url, token: token ?? '');
  }

  Future<void> save() async {
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kUrl, baseUrl);
    await sp.setString(_kToken, token);
  }

  static Future<void> clear() async {
    final sp = await SharedPreferences.getInstance();
    await sp.remove(_kUrl);
    await sp.remove(_kToken);
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token.isNotEmpty) 'Authorization': 'Bearer $token',
      };

  Uri _u(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> health() async {
    final r = await http.get(_u('/api/health'), headers: _headers)
        .timeout(const Duration(seconds: 6));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> status() async {
    final r = await http.get(_u('/api/status'), headers: _headers)
        .timeout(const Duration(seconds: 8));
    if (r.statusCode == 401) throw Exception('토큰이 올바르지 않습니다');
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> balance() async {
    final r = await http.get(_u('/api/balance'), headers: _headers)
        .timeout(const Duration(seconds: 10));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  Future<List<dynamic>> logs({int n = 120}) async {
    final r = await http.get(_u('/api/logs?n=$n'), headers: _headers)
        .timeout(const Duration(seconds: 8));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return (j['logs'] as List?) ?? [];
  }

  Future<List<dynamic>> presets() async {
    final r = await http.get(_u('/api/presets'), headers: _headers)
        .timeout(const Duration(seconds: 8));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return (j['presets'] as List?) ?? [];
  }

  Future<void> start(String preset) async {
    final r = await http.post(_u('/api/start'),
        headers: _headers, body: jsonEncode({'preset': preset}))
        .timeout(const Duration(seconds: 12));
    if (r.statusCode >= 400) {
      throw Exception(_err(r.body));
    }
  }

  Future<void> stop() async {
    final r = await http.post(_u('/api/stop'), headers: _headers)
        .timeout(const Duration(seconds: 12));
    if (r.statusCode >= 400) {
      throw Exception(_err(r.body));
    }
  }

  String _err(String body) {
    try {
      final j = jsonDecode(body);
      return (j['detail'] ?? body).toString();
    } catch (_) {
      return body;
    }
  }
}
