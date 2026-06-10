import 'dart:async';
import 'package:flutter/material.dart';
import 'api_client.dart';
import 'theme.dart';
import 'connect_page.dart';

class DashboardPage extends StatefulWidget {
  final ApiClient client;
  const DashboardPage({super.key, required this.client});

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  Map<String, dynamic> _status = {};
  Map<String, dynamic>? _balance;
  List<dynamic> _logs = [];
  List<dynamic> _presets = [];
  String? _selectedPreset;
  String _env = 'testnet';
  Timer? _timer;
  bool _online = false;
  String? _msg;

  @override
  void initState() {
    super.initState();
    _loadPresets();
    _tick();
    _timer = Timer.periodic(const Duration(milliseconds: 2500), (_) => _tick());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _loadPresets() async {
    try {
      final p = await widget.client.presets();
      setState(() {
        _presets = p;
        _selectedPreset ??= p.isNotEmpty ? p.first['key'] as String : null;
      });
    } catch (_) {}
  }

  Future<void> _tick() async {
    try {
      final s = await widget.client.status();
      List<dynamic> logs = [];
      Map<String, dynamic>? bal;
      try { logs = await widget.client.logs(); } catch (_) {}
      try {
        final b = await widget.client.balance();
        if (b['ok'] == true) bal = b['balance'] as Map<String, dynamic>;
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _status = s;
        _env = (s['env'] ?? 'testnet').toString();
        if (logs.isNotEmpty) _logs = logs;
        if (bal != null) _balance = bal;
        _online = true;
      });
    } catch (e) {
      if (mounted) setState(() => _online = false);
    }
  }

  bool get _running {
    final st = (_status['state'] ?? 'idle').toString();
    return st == 'running' || st == 'starting';
  }

  Future<void> _start() async {
    if (_selectedPreset == null) return;
    if (_env == 'mainnet') {
      final ok = await _confirmMainnet();
      if (ok != true) return;
    }
    setState(() => _msg = null);
    try {
      await widget.client.start(_selectedPreset!);
      setState(() => _msg = '봇 시작됨');
      _tick();
    } catch (e) {
      setState(() => _msg = '시작 실패: ${e.toString().replaceFirst('Exception: ', '')}');
    }
  }

  Future<void> _stop() async {
    setState(() => _msg = null);
    try {
      await widget.client.stop();
      setState(() => _msg = '정지 요청됨');
      _tick();
    } catch (e) {
      setState(() => _msg = '정지 실패: ${e.toString().replaceFirst('Exception: ', '')}');
    }
  }

  Future<bool?> _confirmMainnet() {
    final preset = _presets.firstWhere((p) => p['key'] == _selectedPreset,
        orElse: () => {'label': _selectedPreset});
    return showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: C.card,
        title: const Text('실거래 확인', style: TextStyle(color: C.red)),
        content: Text(
          '실거래(MAINNET) 계정에서 봇을 시작합니다.\n\n구성: ${preset['label']}\n진짜 돈으로 주문이 실행됩니다.\n\n계속하시겠습니까?',
          style: const TextStyle(color: C.text, height: 1.5),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: C.red),
            child: const Text('시작'),
          ),
        ],
      ),
    );
  }

  Future<void> _disconnect() async {
    await ApiClient.clear();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => const ConnectPage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final mainnet = _env == 'mainnet';
    return Scaffold(
      appBar: AppBar(
        title: Row(children: [
          const Text('Crypto Bot', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(width: 10),
          _envBadge(mainnet),
        ]),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Center(
              child: Text(_online ? '● 연결됨' : '● 끊김',
                  style: TextStyle(color: _online ? C.green : C.amber, fontSize: 12)),
            ),
          ),
          IconButton(onPressed: _disconnect, icon: const Icon(Icons.logout, size: 20)),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _tick,
        child: ListView(
          padding: const EdgeInsets.all(14),
          children: [
            _controlCard(),
            const SizedBox(height: 12),
            _balanceRow(),
            const SizedBox(height: 12),
            _positionsCard(),
            const SizedBox(height: 12),
            _logsCard(),
          ],
        ),
      ),
    );
  }

  Widget _envBadge(bool mainnet) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: mainnet ? const Color(0xFF43161C) : const Color(0xFF1D3A2B),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(mainnet ? 'MAINNET · 실거래' : 'TESTNET',
            style: TextStyle(
                color: mainnet ? C.red : C.green,
                fontSize: 11, fontWeight: FontWeight.bold)),
      );

  Widget _card({required Widget child}) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: C.card,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: C.line),
        ),
        child: child,
      );

  Widget _controlCard() {
    final state = (_status['state'] ?? 'idle').toString().toUpperCase();
    final uptime = _fmtUptime(_status['uptime_sec']);
    return _card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            _kv('봇 상태', state, color: _stateColor(state)),
            _kv('가동', uptime),
            _kv('tick', '${_status['tick'] ?? '—'}'),
          ]),
          const SizedBox(height: 16),
          if (_presets.isNotEmpty)
            DropdownButtonFormField<String>(
              value: _selectedPreset,
              isExpanded: true,
              dropdownColor: C.card2,
              decoration: InputDecoration(
                filled: true, fillColor: C.card2,
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(10),
                    borderSide: const BorderSide(color: C.line)),
              ),
              items: _presets
                  .map((p) => DropdownMenuItem(
                        value: p['key'] as String,
                        child: Text(p['label'] as String,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: C.text, fontSize: 14)),
                      ))
                  .toList(),
              onChanged: _running ? null : (v) => setState(() => _selectedPreset = v),
            ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: FilledButton(
                onPressed: _running ? null : _start,
                style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF173A28),
                    foregroundColor: C.green,
                    padding: const EdgeInsets.symmetric(vertical: 14)),
                child: const Text('▶ 시작', style: TextStyle(fontWeight: FontWeight.bold)),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                onPressed: _running ? _stop : null,
                style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF3A1A20),
                    foregroundColor: C.red,
                    padding: const EdgeInsets.symmetric(vertical: 14)),
                child: const Text('■ 정지', style: TextStyle(fontWeight: FontWeight.bold)),
              ),
            ),
          ]),
          if (_msg != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_msg!, style: const TextStyle(color: C.muted, fontSize: 13)),
            ),
        ],
      ),
    );
  }

  Widget _balanceRow() {
    final wallet = _balance?['total_wallet_balance'] ?? _status['wallet_balance'];
    final upnl = _balance?['total_unrealized_pnl'] ?? _status['unrealized_pnl'];
    final pnl = _status['pnl'];
    final pnlPct = _status['pnl_pct'];
    return Row(children: [
      Expanded(child: _statCard('지갑 잔고', _fmt(wallet), C.text)),
      const SizedBox(width: 10),
      Expanded(child: _statCard('미실현', _fmt(upnl), signColor(_num(upnl)))),
      const SizedBox(width: 10),
      Expanded(child: _statCard('세션손익',
          pnl == null ? '—' : '${_num(pnl)! >= 0 ? '+' : ''}${_fmt(pnl)}',
          signColor(_num(pnl)),
          sub: pnlPct == null ? null : '${_num(pnlPct)! >= 0 ? '+' : ''}${_fmt(pnlPct)}%')),
    ]);
  }

  Widget _statCard(String label, String value, Color color, {String? sub}) => _card(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: const TextStyle(color: C.muted, fontSize: 12)),
          const SizedBox(height: 6),
          FittedBox(
            child: Text(value,
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: color)),
          ),
          if (sub != null)
            Text(sub, style: TextStyle(color: color, fontSize: 11)),
        ]),
      );

  Widget _positionsCard() {
    final positions = ((_status['positions'] as List?) ?? [])
        .where((p) => (p['position'] ?? 0) != 0)
        .toList();
    return _card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('포지션 ${positions.isEmpty ? '' : '(${positions.length})'}',
            style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 10),
        if (positions.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Center(child: Text('보유 포지션 없음', style: TextStyle(color: C.muted))),
          )
        else
          ...positions.map(_posRow),
      ]),
    );
  }

  Widget _posRow(dynamic p) {
    final side = (p['side'] ?? 'FLAT').toString();
    final isLong = side == 'LONG';
    final peak = isLong ? p['high_water'] : p['low_water'];
    String dist = '—';
    Color distColor = C.muted;
    final mark = _num(p['mark_price']);
    final pk = _num(peak);
    if (pk != null && mark != null && pk != 0) {
      final d = isLong ? (mark - pk) / pk * 100 : (pk - mark) / pk * 100;
      dist = '${d >= 0 ? '+' : ''}${d.toStringAsFixed(2)}%';
      distColor = signColor(d);
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(children: [
        Expanded(flex: 3, child: Text(p['symbol'].toString(),
            style: const TextStyle(fontWeight: FontWeight.w600))),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
          decoration: BoxDecoration(
            color: isLong ? const Color(0xFF173A28)
                : side == 'SHORT' ? const Color(0xFF3A1A20) : C.card2,
            borderRadius: BorderRadius.circular(5),
          ),
          child: Text(side, style: TextStyle(
              color: isLong ? C.green : side == 'SHORT' ? C.red : C.muted,
              fontSize: 11, fontWeight: FontWeight.bold)),
        ),
        Expanded(flex: 3, child: Text(_fmt(p['mark_price']),
            textAlign: TextAlign.right)),
        Expanded(flex: 3, child: Text(dist,
            textAlign: TextAlign.right, style: TextStyle(color: distColor))),
      ]),
    );
  }

  Widget _logsCard() {
    final recent = _logs.length > 60 ? _logs.sublist(_logs.length - 60) : _logs;
    return _card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('로그', style: TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 10),
        Container(
          height: 240,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
              color: const Color(0xFF0A0D13), borderRadius: BorderRadius.circular(10)),
          child: recent.isEmpty
              ? const Center(child: Text('로그 없음', style: TextStyle(color: C.muted)))
              : ListView.builder(
                  reverse: true,
                  itemCount: recent.length,
                  itemBuilder: (_, i) {
                    final l = recent[recent.length - 1 - i];
                    final lvl = (l['level'] ?? 'info').toString();
                    final t = (l['ts'] ?? '').toString();
                    final ts = t.length >= 19 ? t.substring(11, 19) : '';
                    return Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text('$ts  ${l['msg']}',
                          style: TextStyle(
                              fontFamily: 'monospace', fontSize: 11.5,
                              color: lvl == 'error' ? C.red
                                  : lvl == 'warning' ? C.amber : const Color(0xFFBCC7DD))),
                    );
                  },
                ),
        ),
      ]),
    );
  }

  Widget _kv(String k, String v, {Color? color}) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(k, style: const TextStyle(color: C.muted, fontSize: 12)),
          const SizedBox(height: 4),
          Text(v, style: TextStyle(
              fontSize: 17, fontWeight: FontWeight.bold, color: color ?? C.text)),
        ],
      );

  Color _stateColor(String s) {
    switch (s) {
      case 'RUNNING': return C.green;
      case 'STARTING':
      case 'STOPPING': return C.amber;
      case 'ERROR': return C.red;
      default: return C.muted;
    }
  }

  num? _num(dynamic v) => v is num ? v : (v is String ? num.tryParse(v) : null);

  String _fmt(dynamic v) {
    final n = _num(v);
    if (n == null) return '—';
    return n.toStringAsFixed(2).replaceAllMapped(
        RegExp(r'\B(?=(\d{3})+(?!\d))'), (m) => ',');
  }

  String _fmtUptime(dynamic v) {
    final s = _num(v)?.toInt();
    if (s == null) return '—';
    final h = s ~/ 3600, m = (s % 3600) ~/ 60, ss = s % 60;
    if (h > 0) return '${h}h ${m}m';
    if (m > 0) return '${m}m ${ss}s';
    return '${ss}s';
  }
}
